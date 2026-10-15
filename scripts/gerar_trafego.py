"""
Gerador de tráfego para a Sensor Monitoring API.

Simula várias sessões do front usando as funcionalidades ao mesmo tempo, para
popular a base de observabilidade que a disciplina de Governança analisa.
Todas as chamadas saem com User-Agent "forzy-gerador-trafego", então o
tráfego sintético é sempre distinguível do uso real da interface.

Comportamentos incluídos de propósito, para exercitar os indicadores:
- ~4% das sessões simulam um cliente legado que não envia X-Session-Id/X-Feature;
- ~4% das ações consultam uma TAG digitada errado (404).

Uso:
    python scripts/gerar_trafego.py --minutos 30 --sessoes 4
"""

from __future__ import annotations

import argparse
import random
import threading
import time
import uuid

import requests

UA_GERADOR = "forzy-gerador-trafego/1.0 (front simulado)"
UA_LEGADO = "cliente-legado-sem-instrumentacao/0.9"

TAGS = ["MOT-001", "BMB-002", "CMP-003", "VEN-004", "MOT-005"]
TAGS_INVALIDAS = ["MOT-01", "CMP-030", "BMB002", "VEN-040"]

_lock = threading.Lock()
_contagem = {"chamadas": 0, "erros": 0}


def _chamar(sessao: requests.Session, url: str, feature: str | None, sid: str | None, **params) -> None:
    headers = {}
    if sid and feature:
        headers = {"X-Session-Id": sid, "X-Feature": feature}
    try:
        resp = sessao.get(url, headers=headers, params=params, timeout=10)
        erro = resp.status_code >= 400
    except requests.RequestException:
        erro = True
    with _lock:
        _contagem["chamadas"] += 1
        _contagem["erros"] += int(erro)


def simular_sessao(api: str, fim: float, rng: random.Random) -> None:
    legado = rng.random() < 0.04
    sessao = requests.Session()
    sessao.headers["User-Agent"] = UA_LEGADO if legado else UA_GERADOR
    sid = None if legado else str(uuid.uuid4())
    termino = min(fim, time.time() + rng.uniform(120, 480))
    tag_favorita = rng.choice(TAGS)

    _chamar(sessao, f"{api}/v1/sensores", "catalogo-ativos", sid)
    while time.time() < termino:
        acao = rng.choices(["leitura", "historico", "observabilidade", "tag_invalida"],
                           weights=[62, 28, 6, 4])[0]
        tag = tag_favorita if rng.random() < 0.4 else rng.choice(TAGS)
        if acao == "leitura":
            _chamar(sessao, f"{api}/v1/sensores/{tag}/leitura-atual", "painel-leitura-atual", sid)
        elif acao == "historico":
            _chamar(sessao, f"{api}/v1/sensores/{tag}/historico", "painel-historico", sid,
                    horas=rng.choice([1, 6, 6, 24]), intervalo_min=rng.choice([5, 5, 10, 15]))
        elif acao == "observabilidade":
            _chamar(sessao, f"{api}/v1/observabilidade", "painel-observabilidade", sid,
                    limite=200, janela_min=60)
        else:
            _chamar(sessao, f"{api}/v1/sensores/{rng.choice(TAGS_INVALIDAS)}/leitura-atual",
                    "painel-leitura-atual", sid)
        time.sleep(rng.uniform(0.8, 3.5))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--minutos", type=float, default=30)
    parser.add_argument("--sessoes", type=int, default=4, help="sessões simultâneas")
    parser.add_argument("--semente", type=int, default=None)
    args = parser.parse_args()

    api = args.api.rstrip("/")
    fim = time.time() + args.minutos * 60
    semente = random.Random(args.semente)
    ativas: list[threading.Thread] = []
    proximo_log = time.time() + 60

    print(f"Gerando tráfego em {api} por {args.minutos} min com {args.sessoes} sessões simultâneas...", flush=True)
    while time.time() < fim:
        ativas = [t for t in ativas if t.is_alive()]
        while len(ativas) < args.sessoes:
            t = threading.Thread(target=simular_sessao, args=(api, fim, random.Random(semente.random())), daemon=True)
            t.start()
            ativas.append(t)
        if time.time() >= proximo_log:
            with _lock:
                print(f"[{time.strftime('%H:%M:%S')}] chamadas={_contagem['chamadas']} "
                      f"respostas_com_erro={_contagem['erros']}", flush=True)
            proximo_log += 60
        time.sleep(1)

    for t in ativas:
        t.join(timeout=15)
    print(f"Fim. chamadas={_contagem['chamadas']} respostas_com_erro={_contagem['erros']}", flush=True)


if __name__ == "__main__":
    main()
