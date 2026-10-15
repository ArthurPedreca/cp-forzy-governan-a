"""
Contrato de observabilidade (Metric Contract) do endpoint de Sensores.

Este módulo é a fonte única das regras definidas pela disciplina de
Governança em IA: quais metadados toda chamada deve carregar, quais campos o
back-end registra, quais indicadores são calculados sobre esses registros e
em que limiar cada indicador deixa de estar "ok".

A API, o relatório de observabilidade e o documento de governança leem os
mesmos valores daqui — se um limiar mudar, muda em um lugar só e ganha uma
nova versão do contrato.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

CONTRATO_ID = "FORZY-OBS-SENSORES"
CONTRATO_VERSAO = "1.0.0"
CONTRATO_VIGENCIA = "2026-10-08"

# --------------------------------------------------------------------------- #
# Headers de contexto enviados pelo front em toda chamada
# --------------------------------------------------------------------------- #
HEADER_SESSAO = "X-Session-Id"
HEADER_FEATURE = "X-Feature"

# Funcionalidades conhecidas que podem aparecer em X-Feature.
FEATURES_CATALOGO: Dict[str, str] = {
    "catalogo-ativos": "Carga da lista de ativos no seletor do front",
    "painel-leitura-atual": "Tela de leitura atual do sensor",
    "painel-historico": "Tela de histórico do sensor",
    "painel-observabilidade": "Relatório de observabilidade",
    "script-consumo": "Script de consumo via requests (linha de comando)",
    "relatorio-governanca": "Extração dos registros pela disciplina de Governança em IA",
}

# Tamanho máximo aceito para os headers de contexto (evita registro abusivo).
TAMANHO_MAX_HEADER = 64

# --------------------------------------------------------------------------- #
# Parâmetros operacionais do provider de Sensores
# --------------------------------------------------------------------------- #
PERIODO_PUBLICACAO_S = 5            # o gateway publica uma amostra a cada 5 s
FRESHNESS_FATOR = 3                 # dado vencido a partir de 3 períodos
FRESHNESS_SLA_S = PERIODO_PUBLICACAO_S * FRESHNESS_FATOR   # 15 s
RETENCAO_DIAS = 30                  # registros mais antigos são expurgados

# --------------------------------------------------------------------------- #
# Campos registrados a cada chamada
# --------------------------------------------------------------------------- #
CAMPOS_REGISTRO: List[Dict[str, str]] = [
    {"campo": "request_id", "origem": "back-end", "descricao": "UUID único da chamada, devolvido no header X-Request-Id"},
    {"campo": "timestamp_utc", "origem": "back-end", "descricao": "Instante de chegada da requisição (UTC, ISO 8601)"},
    {"campo": "metodo", "origem": "requisição", "descricao": "Método HTTP"},
    {"campo": "rota", "origem": "back-end", "descricao": "Rota no formato de template (ex.: /v1/sensores/{tag}/leitura-atual)"},
    {"campo": "caminho", "origem": "requisição", "descricao": "Caminho efetivamente chamado"},
    {"campo": "tag", "origem": "requisição", "descricao": "TAG do ativo consultado, quando houver"},
    {"campo": "session_id", "origem": "header X-Session-Id", "descricao": "Sessão do front que originou a chamada"},
    {"campo": "feature", "origem": "header X-Feature", "descricao": "Funcionalidade do front que originou a chamada"},
    {"campo": "cliente", "origem": "header User-Agent", "descricao": "Aplicação cliente (front, script, gerador de carga)"},
    {"campo": "contexto_completo", "origem": "back-end", "descricao": "1 se X-Session-Id e X-Feature vieram preenchidos"},
    {"campo": "status_code", "origem": "back-end", "descricao": "Código HTTP devolvido"},
    {"campo": "latencia_ms", "origem": "back-end", "descricao": "Tempo de processamento no servidor, em milissegundos"},
    {"campo": "idade_dado_s", "origem": "provider", "descricao": "Idade da leitura devolvida (freshness), em segundos"},
    {"campo": "severidade", "origem": "provider", "descricao": "Severidade devolvida na leitura atual"},
    {"campo": "completude_historico", "origem": "provider", "descricao": "Pontos recebidos ÷ pontos esperados no histórico"},
    {"campo": "erro", "origem": "back-end", "descricao": "Motivo do erro quando status ≥ 400"},
]

# --------------------------------------------------------------------------- #
# Indicadores e limiares
# --------------------------------------------------------------------------- #
OK = "ok"
ATENCAO = "atencao"
CRITICO = "critico"
SEM_DADOS = "sem_dados"

MAIOR_MELHOR = "maior_melhor"
MENOR_MELHOR = "menor_melhor"


@dataclass(frozen=True)
class Indicador:
    """Um indicador do contrato, com seus limiares e a resposta prevista."""

    codigo: str
    chave: str
    nome: str
    unidade: str
    sentido: str                    # MAIOR_MELHOR ou MENOR_MELHOR
    limiar_atencao: float
    limiar_critico: float
    escopo: str                     # quais chamadas entram no cálculo
    acao_atencao: str
    acao_critico: str
    grafico: str                    # camada gráfica recomendada

    def classificar(self, valor: Optional[float]) -> str:
        if valor is None:
            return SEM_DADOS
        if self.sentido == MENOR_MELHOR:
            if valor > self.limiar_critico:
                return CRITICO
            if valor > self.limiar_atencao:
                return ATENCAO
            return OK
        if valor < self.limiar_critico:
            return CRITICO
        if valor < self.limiar_atencao:
            return ATENCAO
        return OK

    def acao(self, status: str) -> str:
        if status == CRITICO:
            return self.acao_critico
        if status == ATENCAO:
            return self.acao_atencao
        return ""

    def to_dict(self) -> dict:
        return asdict(self)


INDICADORES: List[Indicador] = [
    Indicador(
        codigo="IND-01",
        chave="latencia_p95_leitura_ms",
        nome="Latência p95 — leitura atual",
        unidade="ms",
        sentido=MENOR_MELHOR,
        limiar_atencao=250,
        limiar_critico=800,
        escopo="Chamadas a /v1/sensores/{tag}/leitura-atual com status < 500",
        acao_atencao="Abrir chamado de desempenho e verificar a latência do gateway de borda.",
        acao_critico="Acionar o plantão de TI; o painel passa a exibir aviso de lentidão ao operador.",
        grafico="Linha temporal do p95 em janelas de 5 min, com linhas de limiar",
    ),
    Indicador(
        codigo="IND-02",
        chave="latencia_p95_historico_ms",
        nome="Latência p95 — histórico",
        unidade="ms",
        sentido=MENOR_MELHOR,
        limiar_atencao=600,
        limiar_critico=1500,
        escopo="Chamadas a /v1/sensores/{tag}/historico com status < 500",
        acao_atencao="Revisar o tamanho das janelas consultadas e o índice do historiador.",
        acao_critico="Limitar a janela máxima do histórico até a causa ser corrigida.",
        grafico="Linha temporal do p95 em janelas de 5 min + box plot da distribuição por rota",
    ),
    Indicador(
        codigo="IND-03",
        chave="idade_dado_p95_s",
        nome="Tempo de atualização (idade p95 do dado)",
        unidade="s",
        sentido=MENOR_MELHOR,
        limiar_atencao=FRESHNESS_SLA_S,
        limiar_critico=60,
        escopo="Leituras atuais devolvidas com sucesso",
        acao_atencao="Verificar a conectividade do gateway do ativo com maior idade.",
        acao_critico="Marcar o ativo como “dado vencido” no painel e acionar a manutenção de campo.",
        grafico="Pequenos múltiplos por ativo da idade p95 em janelas de 5 min, com linha do SLA de 15 s",
    ),
    Indicador(
        codigo="IND-04",
        chave="taxa_freshness_pct",
        nome="Taxa de completude de freshness",
        unidade="%",
        sentido=MAIOR_MELHOR,
        limiar_atencao=95,
        limiar_critico=90,
        escopo="Leituras atuais devolvidas com sucesso (idade ≤ 15 s conta como fresca)",
        acao_atencao="Investigar quais ativos concentram leituras vencidas.",
        acao_critico="Suspender decisões automáticas baseadas na leitura atual até a taxa voltar a 95%.",
        grafico="Linha temporal da taxa em janelas de 5 min, com linhas de meta (95%) e crítico (90%)",
    ),
    Indicador(
        codigo="IND-05",
        chave="taxa_contexto_pct",
        nome="Completude dos metadados de contexto",
        unidade="%",
        sentido=MAIOR_MELHOR,
        limiar_atencao=99,
        limiar_critico=95,
        escopo="Todas as chamadas (X-Session-Id e X-Feature preenchidos)",
        acao_atencao="Identificar pelo User-Agent o cliente que não envia os headers.",
        acao_critico="Emitir o relatório com ressalva de rastreabilidade e notificar o responsável pelo cliente, com prazo de correção.",
        grafico="Cartão de KPI com meta + barras horizontais por cliente (User-Agent)",
    ),
    Indicador(
        codigo="IND-06",
        chave="taxa_erro_servidor_pct",
        nome="Taxa de erro do servidor (5xx)",
        unidade="%",
        sentido=MENOR_MELHOR,
        limiar_atencao=1,
        limiar_critico=5,
        escopo="Todas as chamadas",
        acao_atencao="Analisar os registros de erro do período e o ativo envolvido.",
        acao_critico="Abrir incidente e comunicar a indisponibilidade aos usuários do painel.",
        grafico="Linha temporal da taxa de 5xx em janelas de 5 min + barras empilhadas de status por rota",
    ),
    Indicador(
        codigo="IND-07",
        chave="completude_historico_pct",
        nome="Completude do histórico",
        unidade="%",
        sentido=MAIOR_MELHOR,
        limiar_atencao=98,
        limiar_critico=95,
        escopo="Históricos devolvidos com sucesso (pontos recebidos ÷ esperados)",
        acao_atencao="Sinalizar as lacunas no gráfico do histórico para o analista.",
        acao_critico="Não usar o período como base de tendência; reprocessar a série no historiador.",
        grafico="Barras por ativo com linha de meta de 98%",
    ),
    Indicador(
        codigo="IND-08",
        chave="taxa_leituras_criticas_pct",
        nome="Proporção de leituras críticas devolvidas",
        unidade="%",
        sentido=MENOR_MELHOR,
        limiar_atencao=25,
        limiar_critico=40,
        escopo="Leituras atuais devolvidas com sucesso",
        acao_atencao="Comparar com a baseline: confirmar se é piora real dos ativos ou desvio do classificador.",
        acao_critico="Encaminhar ao Engenheiro de Manutenção para validação humana antes de novas ações.",
        grafico="Barras empilhadas 100% de severidade por ativo",
    ),
]

INDICADORES_POR_CHAVE: Dict[str, Indicador] = {i.chave: i for i in INDICADORES}


def descricao_contrato() -> dict:
    """Resumo do contrato devolvido junto com o relatório de observabilidade."""
    return {
        "id": CONTRATO_ID,
        "versao": CONTRATO_VERSAO,
        "vigencia": CONTRATO_VIGENCIA,
        "headers_obrigatorios": [HEADER_SESSAO, HEADER_FEATURE],
        "features_catalogo": FEATURES_CATALOGO,
        "periodo_publicacao_s": PERIODO_PUBLICACAO_S,
        "freshness_sla_s": FRESHNESS_SLA_S,
        "retencao_dias": RETENCAO_DIAS,
        "campos_registro": CAMPOS_REGISTRO,
        "indicadores": [i.to_dict() for i in INDICADORES],
    }
