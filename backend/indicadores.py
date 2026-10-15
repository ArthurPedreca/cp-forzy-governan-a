"""
Cálculo dos indicadores do contrato de observabilidade.

Recebe a lista de registros gravada pelo middleware e devolve, para cada
indicador do contrato, o valor medido, o tamanho da amostra, o status em
relação aos limiares e a ação prevista quando o limiar é estourado.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable, Dict, Iterable, List, Optional

from backend.contrato import FRESHNESS_SLA_S, INDICADORES_POR_CHAVE

ROTA_LEITURA = "/v1/sensores/{tag}/leitura-atual"
ROTA_HISTORICO = "/v1/sensores/{tag}/historico"


def percentil(valores: Iterable[float], p: float) -> Optional[float]:
    """Percentil p (0–100) com interpolação linear; None se não houver valores."""
    ordenados = sorted(v for v in valores if v is not None)
    if not ordenados:
        return None
    if len(ordenados) == 1:
        return ordenados[0]
    posicao = (len(ordenados) - 1) * p / 100
    baixo = int(posicao)
    alto = min(baixo + 1, len(ordenados) - 1)
    return ordenados[baixo] + (ordenados[alto] - ordenados[baixo]) * (posicao - baixo)


def _taxa(registros: List[dict], condicao: Callable[[dict], bool]) -> Optional[float]:
    if not registros:
        return None
    return 100 * sum(1 for r in registros if condicao(r)) / len(registros)


def _medidas(registros: List[dict]) -> Dict[str, tuple]:
    """Valor e tamanho da amostra de cada indicador."""
    leituras_ok = [r for r in registros if r["rota"] == ROTA_LEITURA and r["status_code"] == 200]
    leituras_srv = [r for r in registros if r["rota"] == ROTA_LEITURA and r["status_code"] < 500]
    historicos_ok = [r for r in registros if r["rota"] == ROTA_HISTORICO and r["status_code"] == 200]
    historicos_srv = [r for r in registros if r["rota"] == ROTA_HISTORICO and r["status_code"] < 500]
    completudes = [r["completude_historico"] for r in historicos_ok if r["completude_historico"] is not None]

    return {
        "latencia_p95_leitura_ms": (percentil([r["latencia_ms"] for r in leituras_srv], 95), len(leituras_srv)),
        "latencia_p95_historico_ms": (percentil([r["latencia_ms"] for r in historicos_srv], 95), len(historicos_srv)),
        "idade_dado_p95_s": (percentil([r["idade_dado_s"] for r in leituras_ok], 95), len(leituras_ok)),
        "taxa_freshness_pct": (
            _taxa(leituras_ok, lambda r: r["idade_dado_s"] is not None and r["idade_dado_s"] <= FRESHNESS_SLA_S),
            len(leituras_ok),
        ),
        "taxa_contexto_pct": (_taxa(registros, lambda r: bool(r["contexto_completo"])), len(registros)),
        "taxa_erro_servidor_pct": (_taxa(registros, lambda r: r["status_code"] >= 500), len(registros)),
        "completude_historico_pct": (
            100 * sum(completudes) / len(completudes) if completudes else None,
            len(completudes),
        ),
        "taxa_leituras_criticas_pct": (_taxa(leituras_ok, lambda r: r["severidade"] == "critico"), len(leituras_ok)),
    }


def calcular_indicadores(registros: List[dict]) -> List[dict]:
    resultado = []
    for chave, (valor, amostra) in _medidas(registros).items():
        indicador = INDICADORES_POR_CHAVE[chave]
        status = indicador.classificar(valor)
        resultado.append({
            "codigo": indicador.codigo,
            "chave": chave,
            "nome": indicador.nome,
            "unidade": indicador.unidade,
            "valor": None if valor is None else round(valor, 2),
            "amostra": amostra,
            "status": status,
            "limiar_atencao": indicador.limiar_atencao,
            "limiar_critico": indicador.limiar_critico,
            "sentido": indicador.sentido,
            "acao": indicador.acao(status),
        })
    return resultado


def resumir(registros: List[dict]) -> dict:
    """Visão agregada de um conjunto de registros."""
    if registros:
        epochs = [r["ts_epoch"] for r in registros]
        inicio = min(registros, key=lambda r: r["ts_epoch"])["timestamp_utc"]
        fim = max(registros, key=lambda r: r["ts_epoch"])["timestamp_utc"]
        duracao_min = (max(epochs) - min(epochs)) / 60
    else:
        inicio = fim = None
        duracao_min = 0.0

    return {
        "total_chamadas": len(registros),
        "inicio": inicio,
        "fim": fim,
        "duracao_min": round(duracao_min, 1),
        "sessoes_distintas": len({r["session_id"] for r in registros if r["session_id"]}),
        "por_rota": dict(Counter(r["rota"] for r in registros)),
        "por_feature": dict(Counter(r["feature"] or "(ausente)" for r in registros)),
        "por_status": {str(k): v for k, v in Counter(r["status_code"] for r in registros).items()},
        "por_cliente": dict(Counter(r["cliente"] or "(ausente)" for r in registros)),
        "indicadores": calcular_indicadores(registros),
    }
