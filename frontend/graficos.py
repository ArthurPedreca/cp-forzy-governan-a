"""
Gráficos Plotly da interface.

Cada indicador do contrato tem a camada gráfica recomendada pela disciplina de
Governança (linha temporal para tendências, box plot para distribuição,
barras empilhadas para composição). As faixas de limiar são desenhadas no
próprio gráfico para que o estouro seja visível sem consultar tabela.
"""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

FUSO = "America/Sao_Paulo"

COR_OK = "#10b981"
COR_ATENCAO = "#f59e0b"
COR_CRITICO = "#ef4444"
COR_NEUTRA = "#64748b"

CORES_SEVERIDADE = {"normal": COR_OK, "atencao": COR_ATENCAO, "critico": COR_CRITICO}
CORES_STATUS = {"2xx": COR_OK, "4xx": COR_ATENCAO, "5xx": COR_CRITICO}

ROTULOS_ROTA = {
    "/v1/sensores/{tag}/leitura-atual": "leitura-atual",
    "/v1/sensores/{tag}/historico": "historico",
    "/v1/observabilidade": "observabilidade",
    "/v1/sensores": "catalogo",
    "/": "raiz",
}


def _layout(fig: go.Figure, titulo: str, altura: int = 360) -> go.Figure:
    fig.update_layout(
        title=dict(text=titulo, font=dict(size=15)),
        height=altura,
        margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
    )
    return fig


def _linha_limiar(fig: go.Figure, valor: float, cor: str, texto: str) -> None:
    fig.add_hline(y=valor, line_dash="dash", line_color=cor, line_width=1.5,
                  annotation_text=texto, annotation_position="top left",
                  annotation_font_color=cor)


def preparar_registros(registros: list) -> pd.DataFrame:
    df = pd.DataFrame(registros)
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["timestamp_utc"], utc=True).dt.tz_convert(FUSO)
    df["rota_curta"] = df["rota"].map(ROTULOS_ROTA).fillna(df["rota"])
    df["classe_status"] = (df["status_code"] // 100).astype(str) + "xx"
    return df.sort_values("ts")


# --------------------------------------------------------------------------- #
# Histórico do sensor
# --------------------------------------------------------------------------- #
def serie_historico(serie: dict, grandeza: str, rotulo: str, unidade: str) -> go.Figure:
    df = pd.DataFrame(serie["leituras"])
    if df.empty:
        return _layout(go.Figure(), f"{rotulo} — sem leituras no período")
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(FUSO)

    # Reindexa na grade esperada para que as lacunas apareçam como quebras na linha.
    grade = pd.date_range(
        pd.Timestamp(serie["inicio"]).tz_convert(FUSO),
        pd.Timestamp(serie["fim"]).tz_convert(FUSO),
        freq=f"{serie['intervalo_min']}min",
    )
    completa = df.set_index("ts").reindex(grade)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=completa.index, y=completa[grandeza], mode="lines+markers",
                             name=rotulo, line=dict(color="#2563eb", width=2),
                             marker=dict(size=4), connectgaps=False))
    limites = serie.get("limites", {}).get(grandeza)
    if limites:
        _linha_limiar(fig, limites[0], COR_ATENCAO, f"atenção {limites[0]} {unidade}")
        _linha_limiar(fig, limites[1], COR_CRITICO, f"crítico {limites[1]} {unidade}")
    fig.update_yaxes(title_text=unidade)
    return _layout(fig, f"{rotulo} — {serie['tag']} (lacunas aparecem como quebras na linha)")


# --------------------------------------------------------------------------- #
# Observabilidade
# --------------------------------------------------------------------------- #
def latencia_p95_tempo(df: pd.DataFrame, rota_curta: str, titulo: str,
                       atencao: float, critico: float) -> go.Figure:
    base = df[(df["rota_curta"] == rota_curta) & (df["status_code"] < 500)]
    if base.empty:
        return _layout(go.Figure(), f"{titulo} — sem dados")
    p95 = base.set_index("ts")["latencia_ms"].resample("5min").quantile(0.95).reset_index()
    fig = px.line(p95, x="ts", y="latencia_ms", markers=True, labels={"ts": "", "latencia_ms": "ms"})
    fig.update_traces(line_color="#2563eb")
    _linha_limiar(fig, atencao, COR_ATENCAO, f"atenção {atencao:.0f} ms")
    _linha_limiar(fig, critico, COR_CRITICO, f"crítico {critico:.0f} ms")
    fig.update_yaxes(rangemode="tozero")
    return _layout(fig, f"{titulo} (p95 em janelas de 5 min)")


def latencia_box(df: pd.DataFrame) -> go.Figure:
    base = df[df["status_code"] < 500]
    fig = px.box(base, x="rota_curta", y="latencia_ms", color="rota_curta", points="outliers",
                 labels={"rota_curta": "rota", "latencia_ms": "ms"})
    fig.update_layout(showlegend=False)
    return _layout(fig, "Distribuição da latência por rota")


def taxa_freshness_tempo(df: pd.DataFrame, sla_s: float, atencao: float, critico: float) -> go.Figure:
    base = df[(df["rota_curta"] == "leitura-atual") & (df["status_code"] == 200)].copy()
    if base.empty:
        return _layout(go.Figure(), "Taxa de completude de freshness — sem dados")
    base["fresco"] = (base["idade_dado_s"] <= sla_s).astype(float) * 100
    taxa = base.set_index("ts")["fresco"].resample("5min").mean().reset_index()
    fig = px.line(taxa, x="ts", y="fresco", markers=True, labels={"ts": "", "fresco": "%"})
    fig.update_traces(line_color="#2563eb")
    _linha_limiar(fig, atencao, COR_ATENCAO, f"meta {atencao:.0f}%")
    _linha_limiar(fig, critico, COR_CRITICO, f"crítico < {critico:.0f}%")
    fig.update_yaxes(range=[0, 105])
    return _layout(fig, f"IND-04 · Taxa de completude de freshness (janelas de 5 min, SLA {sla_s:.0f} s)")


def idade_por_ativo(df: pd.DataFrame, sla_s: float) -> go.Figure:
    """Pequenos múltiplos: um painel por ativo com a idade p95 em janelas de 5 min."""
    base = df[(df["rota_curta"] == "leitura-atual") & (df["status_code"] == 200)]
    if base.empty:
        return _layout(go.Figure(), "IND-03 · Tempo de atualização — sem dados")
    p95 = (base.set_index("ts").groupby("tag")["idade_dado_s"]
           .resample("5min").quantile(0.95).reset_index())
    fig = px.line(p95, x="ts", y="idade_dado_s", facet_col="tag", markers=True,
                  labels={"ts": "", "idade_dado_s": "idade p95 (s)", "tag": ""})
    fig.update_traces(line_color="#2563eb")
    fig.add_hline(y=sla_s, line_dash="dash", line_color=COR_CRITICO, line_width=1.5)
    fig.for_each_annotation(lambda a: a.update(text=a.text.replace("tag=", "")))
    fig.update_xaxes(showticklabels=False)
    return _layout(fig, f"IND-03 · Idade p95 do dado por ativo (linha tracejada = SLA de {sla_s:.0f} s)",
                   altura=320)


def taxa_5xx_tempo(df: pd.DataFrame, atencao: float, critico: float) -> go.Figure:
    erro = (df.assign(e5xx=(df["status_code"] >= 500).astype(float) * 100)
            .set_index("ts")["e5xx"].resample("5min").mean().reset_index())
    fig = px.line(erro, x="ts", y="e5xx", markers=True, labels={"ts": "", "e5xx": "% de respostas 5xx"})
    fig.update_traces(line_color="#2563eb")
    _linha_limiar(fig, atencao, COR_ATENCAO, f"atenção {atencao:g}%")
    _linha_limiar(fig, critico, COR_CRITICO, f"crítico {critico:g}%")
    fig.update_yaxes(rangemode="tozero")
    return _layout(fig, "IND-06 · Taxa de erro do servidor (5xx) em janelas de 5 min")


def status_por_rota(df: pd.DataFrame) -> go.Figure:
    contagem = df.groupby(["rota_curta", "classe_status"]).size().reset_index(name="chamadas")
    fig = px.bar(contagem, x="rota_curta", y="chamadas", color="classe_status",
                 color_discrete_map=CORES_STATUS, labels={"rota_curta": "rota", "classe_status": "status"})
    return _layout(fig, "IND-06 · Status HTTP por rota")


def contexto_por_cliente(df: pd.DataFrame, meta: float) -> go.Figure:
    taxa = (df.assign(completo=df["contexto_completo"].astype(float) * 100,
                      cliente=df["cliente"].fillna("(sem User-Agent)"))
            .groupby("cliente")["completo"].agg(["mean", "size"]).reset_index())
    taxa.columns = ["cliente", "taxa", "chamadas"]
    taxa["cor"] = taxa["taxa"].apply(lambda v: COR_OK if v >= meta else COR_CRITICO)
    fig = go.Figure(go.Bar(x=taxa["taxa"], y=taxa["cliente"], orientation="h",
                           marker_color=taxa["cor"], text=taxa["chamadas"].map(lambda n: f"{n} chamadas"),
                           textposition="inside"))
    fig.add_vline(x=meta, line_dash="dash", line_color=COR_ATENCAO,
                  annotation_text=f"meta {meta:.0f}%", annotation_font_color=COR_ATENCAO)
    fig.update_xaxes(range=[0, 105], title_text="% com X-Session-Id e X-Feature")
    return _layout(fig, "IND-05 · Completude dos metadados de contexto por cliente", altura=300)


def volume_por_minuto(df: pd.DataFrame) -> go.Figure:
    volume = df.set_index("ts").resample("1min").size().reset_index(name="chamadas")
    fig = px.line(volume, x="ts", y="chamadas", markers=True, labels={"ts": ""})
    fig.update_traces(line_color=COR_NEUTRA)
    return _layout(fig, "Volume de chamadas por minuto", altura=300)


def chamadas_por_feature(df: pd.DataFrame) -> go.Figure:
    contagem = df["feature"].fillna("(ausente)").value_counts().reset_index()
    contagem.columns = ["feature", "chamadas"]
    fig = px.bar(contagem, x="chamadas", y="feature", orientation="h")
    fig.update_traces(marker_color="#2563eb")
    return _layout(fig, "Chamadas por funcionalidade (X-Feature)", altura=300)


def severidade_por_ativo(df: pd.DataFrame) -> go.Figure:
    base = df[(df["rota_curta"] == "leitura-atual") & df["severidade"].notna()]
    contagem = base.groupby(["tag", "severidade"]).size().reset_index(name="leituras")
    contagem["pct"] = 100 * contagem["leituras"] / contagem.groupby("tag")["leituras"].transform("sum")
    fig = px.bar(contagem, x="tag", y="pct", color="severidade",
                 color_discrete_map=CORES_SEVERIDADE, hover_data=["leituras"],
                 labels={"pct": "% das leituras", "tag": ""},
                 category_orders={"severidade": ["normal", "atencao", "critico"]})
    fig.update_yaxes(range=[0, 100])
    return _layout(fig, "IND-08 · Severidade devolvida por ativo (100%)")


def completude_historico_por_ativo(df: pd.DataFrame, atencao: float, critico: float) -> go.Figure:
    base = df[(df["rota_curta"] == "historico") & df["completude_historico"].notna()]
    media = (base.groupby("tag")["completude_historico"].mean() * 100).reset_index()
    media["cor"] = media["completude_historico"].apply(
        lambda v: COR_OK if v >= atencao else (COR_ATENCAO if v >= critico else COR_CRITICO))
    fig = go.Figure(go.Bar(x=media["tag"], y=media["completude_historico"], marker_color=media["cor"],
                           text=media["completude_historico"].map(lambda v: f"{v:.1f}%"),
                           textposition="outside"))
    _linha_limiar(fig, atencao, COR_ATENCAO, f"meta {atencao:.0f}%")
    fig.update_yaxes(range=[80, 101], title_text="%")
    return _layout(fig, "IND-07 · Completude média do histórico por ativo")


def limiares(indicadores: list) -> Dict[str, dict]:
    return {i["chave"]: i for i in indicadores}


def valor_formatado(valor: Optional[float], unidade: str) -> str:
    if valor is None:
        return "—"
    if unidade == "%":
        return f"{valor:.1f}%"
    if unidade == "ms":
        return f"{valor:.0f} ms"
    return f"{valor:.1f} {unidade}"
