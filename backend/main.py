"""
Forzy · Sensor Monitoring API (FastAPI).

Expõe o provider de Sensores (leitura atual e histórico) e o relatório de
observabilidade das chamadas recebidas. Toda requisição passa pelo
middleware de observabilidade, que registra os headers de contexto,
o timestamp e a latência.

Rodar:
    uvicorn backend.main:app --reload
Documentação interativa:
    http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request

from backend import contrato
from backend.indicadores import resumir
from backend.observabilidade import RepositorioObservabilidade, criar_middleware
from backend.providers import sensores

repositorio = RepositorioObservabilidade()
repositorio.expurgar()

app = FastAPI(
    title="Forzy · Sensor Monitoring API",
    version="1.0.0",
    description=(
        "Checkpoint integrado Governança em IA × Front-end. Provider de Sensores "
        "(leitura atual e histórico) com registro de observabilidade por chamada. "
        "Envie os headers **X-Session-Id** e **X-Feature** em todas as requisições."
    ),
)
app.middleware("http")(criar_middleware(repositorio))


# --------------------------------------------------------------------------- #
# Headers de contexto (declarados para aparecerem no Swagger)
# --------------------------------------------------------------------------- #
def contexto(
    x_session_id: Annotated[Optional[str], Header(description="Sessão do front que originou a chamada")] = None,
    x_feature: Annotated[Optional[str], Header(description="Funcionalidade do front que originou a chamada")] = None,
) -> dict:
    return {"session_id": x_session_id, "feature": x_feature}


Contexto = Annotated[dict, Depends(contexto)]
Tag = Annotated[str, Path(description="TAG do ativo, ex.: MOT-001", examples=["MOT-001"])]


def _ativo_ou_404(request: Request, tag: str) -> sensores.Ativo:
    try:
        ativo = sensores.obter_ativo(tag)
    except sensores.AtivoNaoEncontrado as exc:
        request.state.obs["tag"] = tag.upper()
        request.state.obs["erro"] = str(exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    request.state.obs["tag"] = ativo.tag
    return ativo


# --------------------------------------------------------------------------- #
# Rotas
# --------------------------------------------------------------------------- #
@app.get("/", tags=["status"])
async def raiz():
    return {
        "servico": app.title,
        "versao": app.version,
        "contrato_observabilidade": f"{contrato.CONTRATO_ID} v{contrato.CONTRATO_VERSAO}",
        "documentacao": "/docs",
    }


@app.get("/v1/sensores", tags=["sensores"])
async def listar_sensores(ctx: Contexto):
    """Ativos disponíveis no provider de Sensores."""
    return {"ativos": sensores.listar_ativos()}


@app.get("/v1/sensores/{tag}/leitura-atual", tags=["sensores"])
async def leitura_atual(request: Request, tag: Tag, ctx: Contexto):
    """Leitura mais recente do ativo, com a severidade e o motivo de cada estado."""
    ativo = _ativo_ou_404(request, tag)
    if sensores.latencia_simulada_ativa():
        await asyncio.sleep(sensores.latencia_leitura_s())
    try:
        leitura = sensores.leitura_atual(ativo.tag)
    except sensores.GatewayIndisponivel as exc:
        request.state.obs["erro"] = str(exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    request.state.obs["idade_dado_s"] = leitura["idade_s"]
    request.state.obs["severidade"] = leitura["severidade"]
    return leitura


@app.get("/v1/sensores/{tag}/historico", tags=["sensores"])
async def historico(
    request: Request,
    tag: Tag,
    ctx: Contexto,
    horas: Annotated[int, Query(ge=1, le=72, description="Janela do histórico, em horas")] = 6,
    intervalo_min: Annotated[int, Query(ge=1, le=60, description="Intervalo entre pontos, em minutos")] = 5,
):
    """Série histórica do ativo, com a completude (pontos recebidos ÷ esperados)."""
    ativo = _ativo_ou_404(request, tag)
    serie = sensores.historico(ativo.tag, horas=horas, intervalo_min=intervalo_min)
    if sensores.latencia_simulada_ativa():
        await asyncio.sleep(sensores.latencia_historico_s(serie["pontos_esperados"]))

    request.state.obs["completude_historico"] = serie["completude"]
    return serie


@app.get("/v1/observabilidade", tags=["observabilidade"])
async def observabilidade(
    ctx: Contexto,
    limite: Annotated[int, Query(ge=1, le=20000, description="Máximo de registros devolvidos")] = 500,
    janela_min: Annotated[Optional[int], Query(ge=1, le=43200, description="Considerar só os últimos N minutos")] = None,
    rota: Annotated[Optional[str], Query(description="Filtrar por rota (template)")] = None,
    feature: Annotated[Optional[str], Query(description="Filtrar por X-Feature")] = None,
    session_id: Annotated[Optional[str], Query(description="Filtrar por X-Session-Id")] = None,
    tag: Annotated[Optional[str], Query(description="Filtrar por TAG")] = None,
):
    """
    Registros de chamadas coletados pelo back-end e indicadores do contrato.

    O resumo é calculado sobre todos os registros do filtro; a lista de
    registros devolvida é limitada por `limite` (mais recentes primeiro).
    """
    desde = time.time() - janela_min * 60 if janela_min else None
    filtrados = repositorio.listar(
        limite=1_000_000, desde_epoch=desde, rota=rota, feature=feature,
        session_id=session_id, tag=tag.upper() if tag else None,
    )
    return {
        "contrato": contrato.descricao_contrato(),
        "filtros": {"janela_min": janela_min, "rota": rota, "feature": feature,
                    "session_id": session_id, "tag": tag},
        "total_base": repositorio.total(),
        "resumo": resumir(filtrados),
        "registros": filtrados[:limite],
    }
