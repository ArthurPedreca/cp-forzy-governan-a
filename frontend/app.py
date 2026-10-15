"""
Interface Streamlit da Sensor Monitoring API (Forzy).

A interface não acessa dados diretamente: tudo passa pela API via `requests`,
com os headers de contexto X-Session-Id e X-Feature em cada chamada.

Rodar (com a API já no ar):
    streamlit run frontend/app.py
"""

from __future__ import annotations

import os
import uuid

import pandas as pd
import streamlit as st

import graficos as g
from api_client import ForzyApiClient, Resposta

st.set_page_config(page_title="Forzy · Sensor Monitoring", page_icon="📡", layout="wide")

NIVEIS = {
    "normal": ("🟢", "Normal", "green"),
    "atencao": ("🟡", "Atenção", "orange"),
    "critico": ("🔴", "Crítico", "red"),
}
STATUS_INDICADOR = {
    "ok": ("OK", "green"),
    "atencao": ("Atenção", "orange"),
    "critico": ("Crítico", "red"),
    "sem_dados": ("Sem dados", "gray"),
}
GRANDEZAS = {
    "temperatura_c": ("Temperatura", "°C"),
    "vibracao_mms": ("Vibração (RMS)", "mm/s"),
    "corrente_a": ("Corrente", "A"),
    "tensao_v": ("Tensão", "V"),
    "rpm": ("Rotação", "RPM"),
}
JANELAS = {"Últimos 15 min": 15, "Última 1 h": 60, "Últimas 6 h": 360, "Últimas 24 h": 1440, "Toda a base": None}


# --------------------------------------------------------------------------- #
# Estado da sessão
# --------------------------------------------------------------------------- #
def _iniciar_sessao() -> None:
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    # 127.0.0.1 e não "localhost": no Windows, "localhost" tenta IPv6 (::1) antes do
    # IPv4 e cada conexão nova perde ~2 s até o fallback (medido no rastreio da chamada).
    st.session_state.setdefault("api_url", os.getenv("FORZY_API_URL", "http://127.0.0.1:8000"))


def _nova_sessao() -> None:
    st.session_state.session_id = str(uuid.uuid4())
    for chave in ("ativos", "leitura", "historico", "observabilidade", "cliente_http"):
        st.session_state.pop(chave, None)


def _cliente() -> ForzyApiClient:
    """Cliente HTTP reaproveitado entre reruns, para manter a conexão aberta (keep-alive)."""
    cliente = st.session_state.get("cliente_http")
    if cliente is None or cliente.base_url != st.session_state.api_url.rstrip("/"):
        cliente = ForzyApiClient(st.session_state.api_url, st.session_state.session_id)
        st.session_state.cliente_http = cliente
    return cliente


def _rastreio(resp: Resposta) -> None:
    """Mostra o que foi registrado pela observabilidade nesta chamada."""
    with st.expander("Rastreio desta chamada (observabilidade)"):
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**X-Request-Id**  \n`{resp.request_id or '—'}`")
        c2.markdown(f"**Latência no servidor**  \n{resp.latencia_servidor_ms or '—'} ms")
        c3.markdown(f"**Latência percebida no front**  \n{resp.latencia_cliente_ms} ms")
        st.markdown("**Headers de contexto enviados**")
        st.json(resp.headers_enviados)


def _erro(resp: Resposta) -> None:
    st.error(f"**{resp.status or 'Sem resposta'}** — {resp.erro or 'Erro desconhecido.'}")
    if resp.request_id:
        st.caption(f"X-Request-Id: `{resp.request_id}` (use este id para localizar o registro no relatório)")


def _carregar_ativos() -> list:
    if "ativos" not in st.session_state:
        resp = _cliente().listar_ativos()
        if not resp.ok:
            _erro(resp)
            st.stop()
        st.session_state.ativos = resp.dados["ativos"]
    return st.session_state.ativos


def _seletor_tag(chave: str) -> str:
    ativos = _carregar_ativos()
    rotulos = {a["tag"]: f"{a['tag']} — {a['nome']}" for a in ativos}
    return st.selectbox("Ativo", list(rotulos), format_func=rotulos.get, key=chave)


# --------------------------------------------------------------------------- #
# Barra lateral
# --------------------------------------------------------------------------- #
def barra_lateral() -> str:
    with st.sidebar:
        st.markdown("## 📡 Forzy · Sensor Monitoring")
        st.caption("Checkpoint integrado — Governança em IA × Front-end")
        pagina = st.radio(
            "Navegação",
            ["Leitura atual", "Histórico", "Observabilidade"],
            captions=["GET /v1/sensores/{tag}/leitura-atual",
                      "GET /v1/sensores/{tag}/historico",
                      "GET /v1/observabilidade"],
        )
        st.divider()
        st.text_input("URL da API", key="api_url")
        st.markdown("**Sessão desta aba** (`X-Session-Id`)")
        st.code(st.session_state.session_id, language=None)
        st.button("Iniciar nova sessão", on_click=_nova_sessao, width="stretch")
        st.caption("Dados fictícios do provider de Sensores — não são leituras de produção.")
    return pagina


# --------------------------------------------------------------------------- #
# Página: leitura atual
# --------------------------------------------------------------------------- #
def pagina_leitura_atual() -> None:
    st.title("Leitura atual do sensor")
    col_tag, col_botao = st.columns([3, 1], vertical_alignment="bottom")
    with col_tag:
        tag = _seletor_tag("tag_leitura")
    if col_botao.button("Consultar leitura", type="primary", width="stretch"):
        st.session_state.leitura = (tag, _cliente().leitura_atual(tag))

    consultado, resp = st.session_state.get("leitura", (None, None))
    if resp is None or consultado != tag:
        st.info("Escolha um ativo e clique em **Consultar leitura**.")
        return
    if not resp.ok:
        _erro(resp)
        _rastreio(resp)
        return

    dados = resp.dados
    ativo = dados["ativo"]
    st.caption(f"{ativo['planta']} › {ativo['area']} › **{ativo['tag']}** — {ativo['nome']} ({ativo['tipo']})")

    emoji, rotulo, cor = NIVEIS[dados["severidade"]]
    motivos = "".join(f"\n- {m}" for m in dados["motivos"]) or "\n- Todas as grandezas dentro dos limites operacionais."
    texto = f"**Severidade retornada: {emoji} {rotulo}**{motivos}"
    {"green": st.success, "orange": st.warning, "red": st.error}[cor](texto)

    hora = pd.Timestamp(dados["timestamp_leitura"]).tz_convert(g.FUSO).strftime("%d/%m/%Y %H:%M:%S")
    if dados["dado_fresco"]:
        st.badge(f"Leitura de {hora} · idade {dados['idade_s']:.1f} s · dentro do SLA de 15 s",
                 icon=":material/schedule:", color="green")
    else:
        st.badge(f"DADO VENCIDO · leitura de {hora} · idade {dados['idade_s']:.1f} s (SLA 15 s)",
                 icon=":material/warning:", color="red")

    colunas = st.columns(len(dados["grandezas"]))
    for col, grandeza in zip(colunas, dados["grandezas"]):
        emoji_g, rotulo_g, cor_g = NIVEIS[grandeza["nivel"]]
        with col.container(border=True):
            st.markdown(f"**{grandeza['nome']}**")
            st.markdown(f"#### {grandeza['valor']:g} {grandeza['unidade']}")
            st.badge(f"{emoji_g} {rotulo_g}", color=cor_g)
            st.caption(grandeza["motivo"])
    _rastreio(resp)


# --------------------------------------------------------------------------- #
# Página: histórico
# --------------------------------------------------------------------------- #
def pagina_historico() -> None:
    st.title("Histórico do sensor")
    c1, c2, c3, c4 = st.columns([3, 1, 1, 1], vertical_alignment="bottom")
    with c1:
        tag = _seletor_tag("tag_historico")
    horas = c2.selectbox("Janela", [1, 6, 24, 72], index=1, format_func=lambda h: f"{h} h")
    intervalo = c3.selectbox("Intervalo", [1, 5, 10, 15, 30, 60], index=1, format_func=lambda m: f"{m} min")
    parametros = (tag, horas, intervalo)
    if c4.button("Consultar histórico", type="primary", width="stretch"):
        st.session_state.historico = (parametros, _cliente().historico(tag, horas, intervalo))

    consultado, resp = st.session_state.get("historico", (None, None))
    if resp is None or consultado != parametros:
        st.info("Escolha o ativo e a janela e clique em **Consultar histórico**.")
        return
    if not resp.ok:
        _erro(resp)
        _rastreio(resp)
        return

    serie = resp.dados
    ativo = serie["ativo"]
    st.caption(f"{ativo['planta']} › {ativo['area']} › **{ativo['tag']}** — {ativo['nome']}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Pontos esperados", serie["pontos_esperados"], border=True)
    m2.metric("Pontos recebidos", serie["pontos_recebidos"], border=True)
    completude = serie["completude"] * 100
    m3.metric("Completude do histórico", f"{completude:.1f}%",
              delta=f"{completude - 98:+.1f} p.p. vs meta de 98%", border=True)
    m4.metric("Intervalo", f"{serie['intervalo_min']} min", border=True)

    grandeza = st.segmented_control("Grandeza", list(GRANDEZAS), format_func=lambda k: GRANDEZAS[k][0],
                                    default="temperatura_c")
    if grandeza:
        rotulo, unidade = GRANDEZAS[grandeza]
        st.plotly_chart(g.serie_historico(serie, grandeza, rotulo, unidade), width="stretch")

    tabela = pd.DataFrame(serie["leituras"])
    if not tabela.empty:
        tabela["timestamp"] = pd.to_datetime(tabela["timestamp"], utc=True).dt.tz_convert(g.FUSO)
        st.dataframe(tabela.sort_values("timestamp", ascending=False), hide_index=True, height=320)
        st.download_button("Baixar histórico (CSV)", tabela.to_csv(index=False).encode("utf-8"),
                           file_name=f"historico_{serie['tag']}.csv", mime="text/csv", on_click="ignore")
    _rastreio(resp)


# --------------------------------------------------------------------------- #
# Página: observabilidade
# --------------------------------------------------------------------------- #
def _cartao_indicador(col, ind: dict) -> None:
    rotulo, cor = STATUS_INDICADOR[ind["status"]]
    comparador = "≤" if ind["sentido"] == "menor_melhor" else "≥"
    with col.container(border=True):
        st.caption(f"{ind['codigo']} · {ind['nome']}")
        st.markdown(f"#### {g.valor_formatado(ind['valor'], ind['unidade'])}")
        st.badge(rotulo, color=cor)
        st.caption(f"Meta {comparador} {ind['limiar_atencao']:g} {ind['unidade']} · "
                   f"crítico {'>' if comparador == '≤' else '<'} {ind['limiar_critico']:g} {ind['unidade']} · "
                   f"n = {ind['amostra']}")
        if ind["acao"]:
            st.caption(f"**Ação:** {ind['acao']}")


def pagina_observabilidade() -> None:
    st.title("Relatório de observabilidade")
    st.caption("Registros coletados pelo back-end a cada chamada — evidência para a disciplina de Governança em IA.")

    c1, c2, c3, c4 = st.columns([2, 2, 2, 1], vertical_alignment="bottom")
    janela = c1.selectbox("Período", list(JANELAS), index=1)
    rota = c2.selectbox("Rota", ["(todas)", *g.ROTULOS_ROTA], format_func=lambda r: g.ROTULOS_ROTA.get(r, r))
    feature = c3.selectbox("Funcionalidade (X-Feature)",
                           ["(todas)", "catalogo-ativos", "painel-leitura-atual", "painel-historico",
                            "painel-observabilidade", "script-consumo", "relatorio-governanca"])
    atualizar = c4.button("Atualizar relatório", type="primary", width="stretch")

    if atualizar or "observabilidade" not in st.session_state:
        st.session_state.observabilidade = _cliente().observabilidade(
            janela_min=JANELAS[janela],
            rota=None if rota == "(todas)" else rota,
            feature=None if feature == "(todas)" else feature,
        )
    resp: Resposta = st.session_state.observabilidade
    if not resp.ok:
        _erro(resp)
        return

    dados = resp.dados
    contrato = dados["contrato"]
    resumo = dados["resumo"]
    st.markdown(
        f"Contrato **{contrato['id']} v{contrato['versao']}** · headers obrigatórios "
        f"`{'`, `'.join(contrato['headers_obrigatorios'])}` · SLA de freshness {contrato['freshness_sla_s']} s · "
        f"**{resumo['total_chamadas']}** chamadas no filtro ({dados['total_base']} na base) · "
        f"**{resumo['sessoes_distintas']}** sessões"
    )

    indicadores = resumo["indicadores"]
    for inicio in range(0, len(indicadores), 4):
        for col, ind in zip(st.columns(4), indicadores[inicio:inicio + 4]):
            _cartao_indicador(col, ind)

    df = g.preparar_registros(dados["registros"])
    if df.empty:
        st.info("Nenhuma chamada registrada no período.")
        return
    lim = g.limiares(indicadores)
    sla = contrato["freshness_sla_s"]

    abas = st.tabs(["Latência", "Freshness", "Erros", "Contexto e uso", "Severidade e histórico", "Registros"])
    with abas[0]:
        c_a, c_b = st.columns(2)
        ind = lim["latencia_p95_leitura_ms"]
        c_a.plotly_chart(g.latencia_p95_tempo(df, "leitura-atual", "IND-01 · Latência leitura atual",
                                              ind["limiar_atencao"], ind["limiar_critico"]), width="stretch")
        ind = lim["latencia_p95_historico_ms"]
        c_b.plotly_chart(g.latencia_p95_tempo(df, "historico", "IND-02 · Latência histórico",
                                              ind["limiar_atencao"], ind["limiar_critico"]), width="stretch")
        st.plotly_chart(g.latencia_box(df), width="stretch")
    with abas[1]:
        ind = lim["taxa_freshness_pct"]
        st.plotly_chart(g.taxa_freshness_tempo(df, sla, ind["limiar_atencao"], ind["limiar_critico"]), width="stretch")
        st.plotly_chart(g.idade_por_ativo(df, sla), width="stretch")
    with abas[2]:
        ind = lim["taxa_erro_servidor_pct"]
        st.plotly_chart(g.taxa_5xx_tempo(df, ind["limiar_atencao"], ind["limiar_critico"]), width="stretch")
        st.plotly_chart(g.status_por_rota(df), width="stretch")
        erros = df[df["status_code"] >= 400][["ts", "rota_curta", "tag", "status_code", "erro", "request_id"]]
        st.dataframe(erros.sort_values("ts", ascending=False), hide_index=True, height=260)
    with abas[3]:
        st.plotly_chart(g.contexto_por_cliente(df, lim["taxa_contexto_pct"]["limiar_atencao"]), width="stretch")
        c_a, c_b = st.columns(2)
        c_a.plotly_chart(g.chamadas_por_feature(df), width="stretch")
        c_b.plotly_chart(g.volume_por_minuto(df), width="stretch")
    with abas[4]:
        c_a, c_b = st.columns(2)
        c_a.plotly_chart(g.severidade_por_ativo(df), width="stretch")
        ind = lim["completude_historico_pct"]
        c_b.plotly_chart(g.completude_historico_por_ativo(df, ind["limiar_atencao"], ind["limiar_critico"]),
                         width="stretch")
    with abas[5]:
        colunas = ["ts", "request_id", "session_id", "feature", "metodo", "rota", "tag", "status_code",
                   "latencia_ms", "idade_dado_s", "severidade", "completude_historico", "contexto_completo",
                   "cliente", "erro"]
        st.dataframe(df[colunas].sort_values("ts", ascending=False), hide_index=True, height=420)
        st.download_button("Baixar registros (CSV)", df[colunas].to_csv(index=False).encode("utf-8"),
                           file_name="registros_observabilidade.csv", mime="text/csv", on_click="ignore")
    _rastreio(resp)


# --------------------------------------------------------------------------- #
_iniciar_sessao()
PAGINAS = {
    "Leitura atual": pagina_leitura_atual,
    "Histórico": pagina_historico,
    "Observabilidade": pagina_observabilidade,
}
PAGINAS[barra_lateral()]()
