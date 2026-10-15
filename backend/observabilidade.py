"""
Registro de observabilidade das chamadas à API.

Um middleware HTTP intercepta toda requisição, mede a latência no servidor e
grava um registro com os headers de contexto (X-Session-Id, X-Feature), o
timestamp e o resultado. Os endpoints complementam o registro com dados que
só eles conhecem (TAG, idade da leitura, severidade, completude do histórico)
por meio de `request.state.obs`.

A persistência é um arquivo SQLite local — suficiente para o checkpoint e
substituível por um coletor OpenTelemetry sem mudar o contrato de campos.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.contrato import (
    HEADER_FEATURE,
    HEADER_SESSAO,
    RETENCAO_DIAS,
    TAMANHO_MAX_HEADER,
)

RAIZ = Path(__file__).resolve().parents[1]
DB_PADRAO = RAIZ / "data" / "observabilidade.db"

# Rotas que não interessam à governança (documentação automática do FastAPI).
ROTAS_IGNORADAS = ("/docs", "/redoc", "/openapi.json", "/favicon.ico")

_COLUNAS = [
    "request_id", "timestamp_utc", "ts_epoch", "metodo", "rota", "caminho", "tag",
    "session_id", "feature", "cliente", "contexto_completo", "status_code",
    "latencia_ms", "idade_dado_s", "severidade", "completude_historico", "erro",
]

_DDL = """
CREATE TABLE IF NOT EXISTS chamadas (
    request_id            TEXT PRIMARY KEY,
    timestamp_utc         TEXT NOT NULL,
    ts_epoch              REAL NOT NULL,
    metodo                TEXT NOT NULL,
    rota                  TEXT NOT NULL,
    caminho               TEXT NOT NULL,
    tag                   TEXT,
    session_id            TEXT,
    feature               TEXT,
    cliente               TEXT,
    contexto_completo     INTEGER NOT NULL,
    status_code           INTEGER NOT NULL,
    latencia_ms           REAL NOT NULL,
    idade_dado_s          REAL,
    severidade            TEXT,
    completude_historico  REAL,
    erro                  TEXT
);
CREATE INDEX IF NOT EXISTS idx_chamadas_ts ON chamadas (ts_epoch);
"""


class RepositorioObservabilidade:
    """Persistência dos registros de chamada em SQLite."""

    def __init__(self, caminho: Optional[Path] = None):
        self.caminho = Path(caminho or os.getenv("FORZY_DB_PATH") or DB_PADRAO)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conectar() as con:
            con.executescript(_DDL)

    def _conectar(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.caminho)
        con.row_factory = sqlite3.Row
        return con

    # ----------------------------------------------------------------------- #
    def registrar(self, registro: dict) -> None:
        valores = [registro.get(c) for c in _COLUNAS]
        marcadores = ", ".join("?" for _ in _COLUNAS)
        with self._lock, self._conectar() as con:
            con.execute(f"INSERT INTO chamadas ({', '.join(_COLUNAS)}) VALUES ({marcadores})", valores)

    def listar(
        self,
        limite: int = 500,
        desde_epoch: Optional[float] = None,
        rota: Optional[str] = None,
        feature: Optional[str] = None,
        session_id: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> List[dict]:
        """Registros mais recentes primeiro, com filtros opcionais."""
        condicoes, params = [], []
        if desde_epoch is not None:
            condicoes.append("ts_epoch >= ?")
            params.append(desde_epoch)
        for coluna, valor in (("rota", rota), ("feature", feature), ("session_id", session_id), ("tag", tag)):
            if valor:
                condicoes.append(f"{coluna} = ?")
                params.append(valor)
        where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""
        sql = f"SELECT * FROM chamadas {where} ORDER BY ts_epoch DESC LIMIT ?"
        params.append(limite)
        with self._conectar() as con:
            linhas = con.execute(sql, params).fetchall()
        registros = [dict(l) for l in linhas]
        for r in registros:
            r["contexto_completo"] = bool(r["contexto_completo"])
        return registros

    def total(self) -> int:
        with self._conectar() as con:
            return con.execute("SELECT COUNT(*) FROM chamadas").fetchone()[0]

    def expurgar(self, dias: int = RETENCAO_DIAS) -> int:
        """Remove registros mais antigos que a retenção definida no contrato."""
        limite = (datetime.now(timezone.utc) - timedelta(days=dias)).timestamp()
        with self._lock, self._conectar() as con:
            return con.execute("DELETE FROM chamadas WHERE ts_epoch < ?", (limite,)).rowcount


# --------------------------------------------------------------------------- #
# Middleware
# --------------------------------------------------------------------------- #
def _header(request: Request, nome: str) -> Optional[str]:
    valor = (request.headers.get(nome) or "").strip()
    return valor[:TAMANHO_MAX_HEADER] or None


def _iso_agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def criar_middleware(repositorio: RepositorioObservabilidade):
    """Cria o middleware HTTP que registra cada chamada no repositório."""

    async def middleware_observabilidade(request: Request, call_next):
        if request.url.path.startswith(ROTAS_IGNORADAS):
            return await call_next(request)

        request_id = str(uuid.uuid4())
        timestamp_utc = _iso_agora()
        ts_epoch = time.time()
        sessao = _header(request, HEADER_SESSAO)
        feature = _header(request, HEADER_FEATURE)

        # Dicionário compartilhado com o endpoint para dados de negócio.
        request.state.obs = {}
        inicio = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception as exc:  # erro não tratado no endpoint
            response = JSONResponse({"detail": "Erro interno do servidor."}, status_code=500)
            status = 500
            request.state.obs.setdefault("erro", f"{type(exc).__name__}: {exc}")
        latencia_ms = round((time.perf_counter() - inicio) * 1000, 2)

        rota_obj = request.scope.get("route")
        obs = request.state.obs
        repositorio.registrar({
            "request_id": request_id,
            "timestamp_utc": timestamp_utc,
            "ts_epoch": ts_epoch,
            "metodo": request.method,
            "rota": getattr(rota_obj, "path", request.url.path),
            "caminho": request.url.path,
            "tag": obs.get("tag"),
            "session_id": sessao,
            "feature": feature,
            "cliente": (request.headers.get("user-agent") or "")[:120] or None,
            "contexto_completo": int(bool(sessao and feature)),
            "status_code": status,
            "latencia_ms": latencia_ms,
            "idade_dado_s": obs.get("idade_dado_s"),
            "severidade": obs.get("severidade"),
            "completude_historico": obs.get("completude_historico"),
            "erro": obs.get("erro"),
        })

        response.headers["X-Request-Id"] = request_id
        response.headers["X-Response-Time-ms"] = str(latencia_ms)
        if not (sessao and feature):
            response.headers["X-Observabilidade-Aviso"] = (
                f"Chamada sem {HEADER_SESSAO} e/ou {HEADER_FEATURE}; registrada como contexto incompleto."
            )
        return response

    return middleware_observabilidade
