"""
Provider de Sensores — leitura atual e histórico dos ativos Forzy.

Os dados são **fictícios** e gerados de forma determinística: a mesma TAG no
mesmo instante sempre produz os mesmos valores. Isso permite que a leitura
atual e o histórico sejam coerentes entre si e que uma consulta possa ser
reproduzida durante uma auditoria.

Além das grandezas físicas, o provider simula o comportamento da cadeia de
aquisição, que é o que a observabilidade precisa enxergar:

- **atraso de publicação** do gateway de borda (idade do dado / freshness);
- **lacunas** no histórico (amostras que não chegaram ao historiador);
- **indisponibilidade** do gateway (a leitura atual falha com 503);
- **latência** de I/O ao consultar o gateway ou o historiador.

Perfis de conectividade:
    estavel      — atraso de 0 a 4 s, sem falhas
    oscilante    — como o estável, com 3% das amostras atrasadas 20–40 s
    intermitente — em cada ciclo de 15 min: minutos 9–11 com atraso de
                   25–90 s e minuto 12 com o gateway fora do ar
"""

from __future__ import annotations

import math
import os
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from backend.contrato import FRESHNESS_SLA_S, PERIODO_PUBLICACAO_S

# Níveis de severidade devolvidos pelo provider
NORMAL = "normal"
ATENCAO = "atencao"
CRITICO = "critico"
_ORDEM = {NORMAL: 0, ATENCAO: 1, CRITICO: 2}

# Limites operacionais (mesmos critérios do dashboard Forzy)
TEMPERATURA_ATENCAO_C = 70.0
TEMPERATURA_CRITICO_C = 90.0
VIBRACAO_ATENCAO_MMS = 2.8      # ISO 10816, zona B/C
VIBRACAO_CRITICO_MMS = 4.5      # ISO 10816, zona C/D
CORRENTE_ATENCAO_FATOR = 1.10
CORRENTE_CRITICO_FATOR = 1.20
DESVIO_ATENCAO = 0.05           # tensão e rotação
DESVIO_CRITICO = 0.10


class AtivoNaoEncontrado(LookupError):
    """TAG não cadastrada no provider."""


class GatewayIndisponivel(RuntimeError):
    """O gateway de borda do ativo não respondeu."""


@dataclass(frozen=True)
class Ativo:
    tag: str
    nome: str
    tipo: str
    planta: str
    area: str
    tensao_nominal_v: float
    corrente_nominal_a: float
    rotacao_nominal_rpm: int
    perfil_saude: str           # saudavel / degradacao / critico
    perfil_conectividade: str   # estavel / oscilante / intermitente

    def to_dict(self) -> dict:
        return asdict(self)


ATIVOS: Dict[str, Ativo] = {
    a.tag: a
    for a in [
        Ativo("MOT-001", "Motor da Extrusora Principal", "Motor Elétrico",
              "Planta Industrial Sul", "Setor A - Extrusão",
              380, 28.5, 1750, "saudavel", "estavel"),
        Ativo("BMB-002", "Bomba de Recalque de Água Gelada", "Bomba Centrífuga",
              "Planta Industrial Sul", "Casa de Máquinas",
              380, 15.2, 3500, "saudavel", "oscilante"),
        Ativo("CMP-003", "Compressor de Ar Principal", "Compressor",
              "Planta Industrial Norte", "Utilidades",
              440, 52.0, 1780, "critico", "estavel"),
        Ativo("VEN-004", "Exaustor do Galpão Produtivo", "Ventilador / Exaustor",
              "Planta Industrial Norte", "Galpão Produtivo",
              220, 9.8, 1150, "degradacao", "estavel"),
        Ativo("MOT-005", "Motor da Esteira de Alimentação", "Motor Elétrico",
              "Planta Industrial Sul", "Setor A - Extrusão",
              380, 21.0, 1740, "saudavel", "intermitente"),
    ]
}


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _rng(*partes) -> random.Random:
    """Gerador pseudoaleatório determinístico a partir das partes informadas."""
    return random.Random(":".join(str(p) for p in partes))


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def obter_ativo(tag: str) -> Ativo:
    ativo = ATIVOS.get(tag.strip().upper())
    if ativo is None:
        raise AtivoNaoEncontrado(f"TAG '{tag}' não cadastrada no provider de Sensores.")
    return ativo


def listar_ativos() -> List[dict]:
    return [a.to_dict() for a in ATIVOS.values()]


# --------------------------------------------------------------------------- #
# Grandezas físicas
# --------------------------------------------------------------------------- #
def _valores(ativo: Ativo, t: float) -> Dict[str, float]:
    """Valores das grandezas do ativo no instante t (determinístico)."""
    rng = _rng(ativo.tag, int(t))
    ciclo_dia = math.sin(2 * math.pi * (t % 86400) / 86400)       # carga ao longo do dia
    onda_hora = math.sin(2 * math.pi * (t % 3600) / 3600)          # oscilação lenta

    if ativo.perfil_saude == "critico":
        temperatura = 87 + 5 * onda_hora + 3 * ciclo_dia + rng.gauss(0, 1.5)
        vibracao = 4.3 + 0.5 * onda_hora + rng.gauss(0, 0.25)
        fator_corrente = 1.15 + 0.05 * onda_hora + rng.gauss(0, 0.02)
        escorregamento = 0.965
    elif ativo.perfil_saude == "degradacao":
        temperatura = 70 + 6 * onda_hora + 2 * ciclo_dia + rng.gauss(0, 1.5)
        vibracao = 3.0 + 0.4 * onda_hora + rng.gauss(0, 0.2)
        fator_corrente = 1.04 + 0.04 * onda_hora + rng.gauss(0, 0.02)
        escorregamento = 0.985
    else:
        temperatura = 56 + 3 * ciclo_dia + rng.gauss(0, 1.2)
        vibracao = 1.7 + 0.2 * onda_hora + rng.gauss(0, 0.15)
        fator_corrente = 0.86 + 0.03 * ciclo_dia + rng.gauss(0, 0.015)
        escorregamento = 0.995

    return {
        "temperatura_c": round(temperatura, 2),
        "vibracao_mms": round(max(vibracao, 0.0), 3),
        "corrente_a": round(ativo.corrente_nominal_a * fator_corrente, 2),
        "tensao_v": round(ativo.tensao_nominal_v * (1 + rng.gauss(0, 0.012)), 1),
        "rpm": round(ativo.rotacao_nominal_rpm * escorregamento * (1 + rng.gauss(0, 0.003))),
    }


def _nivel_limite(valor: float, atencao: float, critico: float) -> str:
    if valor >= critico:
        return CRITICO
    if valor >= atencao:
        return ATENCAO
    return NORMAL


def _nivel_desvio(valor: float, nominal: float) -> Tuple[str, float]:
    desvio = abs(valor - nominal) / nominal if nominal else 0.0
    return _nivel_limite(desvio, DESVIO_ATENCAO, DESVIO_CRITICO), desvio


def classificar(valores: Dict[str, float], ativo: Ativo) -> Tuple[str, List[dict]]:
    """
    Classifica cada grandeza e devolve (severidade geral, grandezas detalhadas).

    A severidade geral é o pior nível entre as grandezas. Cada grandeza traz
    o motivo textual do seu estado, para que a interface explique a cor.
    """
    grandezas: List[dict] = []

    t = valores["temperatura_c"]
    nivel = _nivel_limite(t, TEMPERATURA_ATENCAO_C, TEMPERATURA_CRITICO_C)
    motivo = {
        CRITICO: f"Temperatura {t:.1f} °C excede o limite crítico de {TEMPERATURA_CRITICO_C:.0f} °C.",
        ATENCAO: f"Temperatura {t:.1f} °C na faixa de atenção (a partir de {TEMPERATURA_ATENCAO_C:.0f} °C).",
        NORMAL: f"Temperatura {t:.1f} °C abaixo de {TEMPERATURA_ATENCAO_C:.0f} °C.",
    }[nivel]
    grandezas.append({"chave": "temperatura_c", "nome": "Temperatura", "valor": t, "unidade": "°C",
                      "nivel": nivel, "motivo": motivo})

    v = valores["vibracao_mms"]
    nivel = _nivel_limite(v, VIBRACAO_ATENCAO_MMS, VIBRACAO_CRITICO_MMS)
    motivo = {
        CRITICO: f"Vibração {v:.2f} mm/s acima de {VIBRACAO_CRITICO_MMS} mm/s (ISO 10816, zona C/D).",
        ATENCAO: f"Vibração {v:.2f} mm/s entre {VIBRACAO_ATENCAO_MMS} e {VIBRACAO_CRITICO_MMS} mm/s (zona B/C).",
        NORMAL: f"Vibração {v:.2f} mm/s abaixo de {VIBRACAO_ATENCAO_MMS} mm/s (zona A/B).",
    }[nivel]
    grandezas.append({"chave": "vibracao_mms", "nome": "Vibração (RMS)", "valor": v, "unidade": "mm/s",
                      "nivel": nivel, "motivo": motivo})

    c = valores["corrente_a"]
    pct = c / ativo.corrente_nominal_a
    nivel = _nivel_limite(pct, CORRENTE_ATENCAO_FATOR, CORRENTE_CRITICO_FATOR)
    motivo = f"Corrente {c:.1f} A = {pct:.0%} da nominal ({ativo.corrente_nominal_a} A)."
    grandezas.append({"chave": "corrente_a", "nome": "Corrente", "valor": c, "unidade": "A",
                      "nivel": nivel, "motivo": motivo})

    tv = valores["tensao_v"]
    nivel, desvio = _nivel_desvio(tv, ativo.tensao_nominal_v)
    motivo = f"Tensão {tv:.0f} V, desvio de {desvio:.1%} do nominal ({ativo.tensao_nominal_v:.0f} V)."
    grandezas.append({"chave": "tensao_v", "nome": "Tensão", "valor": tv, "unidade": "V",
                      "nivel": nivel, "motivo": motivo})

    r = valores["rpm"]
    nivel, desvio = _nivel_desvio(r, ativo.rotacao_nominal_rpm)
    motivo = f"Rotação {r:.0f} RPM, desvio de {desvio:.1%} do nominal ({ativo.rotacao_nominal_rpm} RPM)."
    grandezas.append({"chave": "rpm", "nome": "Rotação", "valor": r, "unidade": "RPM",
                      "nivel": nivel, "motivo": motivo})

    severidade = max((g["nivel"] for g in grandezas), key=lambda n: _ORDEM[n])
    return severidade, grandezas


# --------------------------------------------------------------------------- #
# Cadeia de aquisição (atraso, lacunas, indisponibilidade)
# --------------------------------------------------------------------------- #
def _minuto_do_ciclo(t: float) -> int:
    return int(t // 60) % 15


def _atraso_publicacao(ativo: Ativo, agora: float) -> float:
    """Atraso, em segundos, entre a amostra mais recente e o instante atual."""
    janela = int(agora // PERIODO_PUBLICACAO_S)
    rng = _rng(ativo.tag, "atraso", janela)

    if ativo.perfil_conectividade == "intermitente":
        minuto = _minuto_do_ciclo(agora)
        if minuto == 12:
            raise GatewayIndisponivel(
                f"Gateway de borda do ativo {ativo.tag} sem resposta (janela de indisponibilidade)."
            )
        if minuto in (9, 10, 11):
            return rng.uniform(25, 90)

    if ativo.perfil_conectividade == "oscilante" and rng.random() < 0.03:
        return rng.uniform(20, 40)

    return rng.uniform(0, 4)


def _ponto_perdido(ativo: Ativo, t: float) -> bool:
    """Indica se a amostra do instante t não chegou ao historiador."""
    rng = _rng(ativo.tag, "lacuna", int(t))
    if ativo.perfil_conectividade == "intermitente":
        if _minuto_do_ciclo(t) == 12:
            return True
        return rng.random() < 0.03
    if ativo.perfil_conectividade == "oscilante":
        return rng.random() < 0.02
    return rng.random() < 0.003


# --------------------------------------------------------------------------- #
# Latência simulada de I/O (gateway / historiador)
# --------------------------------------------------------------------------- #
def latencia_simulada_ativa() -> bool:
    return os.getenv("FORZY_LATENCIA_SIMULADA", "1") != "0"


def latencia_leitura_s() -> float:
    if random.random() < 0.03:
        return random.uniform(0.5, 1.2)        # pico ocasional do gateway
    return random.uniform(0.015, 0.06)


def latencia_historico_s(pontos: int) -> float:
    base = 0.04 + pontos * 0.00015 + random.uniform(0, 0.08)
    if random.random() < 0.03:
        base += random.uniform(0.8, 1.6)        # consulta lenta no historiador
    return base


# --------------------------------------------------------------------------- #
# API pública do provider
# --------------------------------------------------------------------------- #
def leitura_atual(tag: str, agora: float | None = None) -> dict:
    """Leitura mais recente publicada pelo gateway do ativo, com a severidade."""
    ativo = obter_ativo(tag)
    agora = time.time() if agora is None else agora

    atraso = _atraso_publicacao(ativo, agora)
    t_amostra = math.floor((agora - atraso) / PERIODO_PUBLICACAO_S) * PERIODO_PUBLICACAO_S
    idade = round(agora - t_amostra, 1)

    severidade, grandezas = classificar(_valores(ativo, t_amostra), ativo)
    return {
        "tag": ativo.tag,
        "ativo": ativo.to_dict(),
        "timestamp_leitura": _iso(t_amostra),
        "timestamp_consulta": _iso(agora),
        "idade_s": idade,
        "dado_fresco": idade <= FRESHNESS_SLA_S,
        "severidade": severidade,
        "motivos": [g["motivo"] for g in grandezas if g["nivel"] != NORMAL],
        "grandezas": grandezas,
        "origem_dados": "simulada (dados fictícios para o checkpoint)",
    }


def historico(tag: str, horas: int = 6, intervalo_min: int = 5, agora: float | None = None) -> dict:
    """Série histórica do ativo em pontos regulares de `intervalo_min` minutos."""
    ativo = obter_ativo(tag)
    agora = time.time() if agora is None else agora

    passo = intervalo_min * 60
    fim = math.floor(agora / passo) * passo
    esperados = int(horas * 3600 // passo)
    inicio = fim - (esperados - 1) * passo

    leituras: List[dict] = []
    for i in range(esperados):
        t = inicio + i * passo
        if _ponto_perdido(ativo, t):
            continue
        valores = _valores(ativo, t)
        severidade, _ = classificar(valores, ativo)
        leituras.append({"timestamp": _iso(t), **valores, "severidade": severidade})

    return {
        "tag": ativo.tag,
        "ativo": ativo.to_dict(),
        "inicio": _iso(inicio),
        "fim": _iso(fim),
        "intervalo_min": intervalo_min,
        "pontos_esperados": esperados,
        "pontos_recebidos": len(leituras),
        "completude": round(len(leituras) / esperados, 4) if esperados else 0.0,
        "limites": {
            "temperatura_c": [TEMPERATURA_ATENCAO_C, TEMPERATURA_CRITICO_C],
            "vibracao_mms": [VIBRACAO_ATENCAO_MMS, VIBRACAO_CRITICO_MMS],
            "corrente_a": [round(ativo.corrente_nominal_a * CORRENTE_ATENCAO_FATOR, 2),
                           round(ativo.corrente_nominal_a * CORRENTE_CRITICO_FATOR, 2)],
        },
        "leituras": leituras,
        "origem_dados": "simulada (dados fictícios para o checkpoint)",
    }
