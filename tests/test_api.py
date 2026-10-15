"""Testes da API e do registro de observabilidade."""

import os
import tempfile
import uuid
from pathlib import Path

# Banco isolado e sem latência simulada — precisa vir antes do import da app.
os.environ["FORZY_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "obs_teste.db")
os.environ["FORZY_LATENCIA_SIMULADA"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402
from backend.providers import sensores  # noqa: E402

client = TestClient(app)


def _headers(feature="painel-leitura-atual"):
    sessao = str(uuid.uuid4())
    return sessao, {"X-Session-Id": sessao, "X-Feature": feature}


def _registro(sessao):
    resp = client.get("/v1/observabilidade", params={"session_id": sessao})
    registros = resp.json()["registros"]
    assert registros, "a chamada deveria ter sido registrada"
    return registros[0]


def test_leitura_atual_devolve_severidade_e_e_registrada():
    sessao, headers = _headers()
    resp = client.get("/v1/sensores/MOT-001/leitura-atual", headers=headers)
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["severidade"] in {"normal", "atencao", "critico"}
    assert len(corpo["grandezas"]) == 5
    assert resp.headers["X-Request-Id"]

    reg = _registro(sessao)
    assert reg["request_id"] == resp.headers["X-Request-Id"]
    assert reg["rota"] == "/v1/sensores/{tag}/leitura-atual"
    assert reg["feature"] == "painel-leitura-atual"
    assert reg["contexto_completo"] is True
    assert reg["latencia_ms"] >= 0
    assert reg["idade_dado_s"] == corpo["idade_s"]
    assert reg["severidade"] == corpo["severidade"]
    assert reg["timestamp_utc"].endswith("Z")


def test_historico_registra_completude():
    sessao, headers = _headers("painel-historico")
    resp = client.get("/v1/sensores/BMB-002/historico", params={"horas": 2, "intervalo_min": 5}, headers=headers)
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["pontos_esperados"] == 24
    assert 0 < corpo["completude"] <= 1
    assert _registro(sessao)["completude_historico"] == corpo["completude"]


def test_tag_inexistente_devolve_404_e_registra_erro():
    sessao, headers = _headers()
    resp = client.get("/v1/sensores/XYZ-999/leitura-atual", headers=headers)
    assert resp.status_code == 404
    reg = _registro(sessao)
    assert reg["status_code"] == 404
    assert reg["tag"] == "XYZ-999"
    assert "não cadastrada" in reg["erro"]


def test_chamada_sem_headers_e_marcada_como_contexto_incompleto():
    resp = client.get("/v1/sensores/MOT-001/leitura-atual")
    assert resp.status_code == 200
    assert "X-Observabilidade-Aviso" in resp.headers
    obs = client.get("/v1/observabilidade", params={"limite": 50}).json()
    reg = next(r for r in obs["registros"] if r["request_id"] == resp.headers["X-Request-Id"])
    assert reg["contexto_completo"] is False
    assert reg["session_id"] is None


def test_resumo_traz_os_oito_indicadores_do_contrato():
    obs = client.get("/v1/observabilidade").json()
    codigos = [i["codigo"] for i in obs["resumo"]["indicadores"]]
    assert codigos == [f"IND-0{n}" for n in range(1, 9)]
    assert obs["contrato"]["headers_obrigatorios"] == ["X-Session-Id", "X-Feature"]


def test_provider_e_deterministico_e_coerente():
    t = 1_790_000_000.0
    a = sensores.leitura_atual("CMP-003", agora=t)
    b = sensores.leitura_atual("CMP-003", agora=t)
    assert a == b
    assert a["idade_s"] >= 0


def test_gateway_intermitente_fica_indisponivel_no_minuto_12():
    # Instante cujo minuto do ciclo de 15 min é 12.
    t = (1_790_000_000 // 900) * 900 + 12 * 60 + 10
    try:
        sensores.leitura_atual("MOT-005", agora=t)
    except sensores.GatewayIndisponivel:
        return
    raise AssertionError("MOT-005 deveria estar indisponível no minuto 12 do ciclo")
