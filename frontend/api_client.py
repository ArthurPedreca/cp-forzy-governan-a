"""
Cliente HTTP da Sensor Monitoring API usado pelo front.

Toda chamada sai com os headers de contexto do contrato de observabilidade:
    X-Session-Id — identificador da sessão do Streamlit (um UUID por aba aberta)
    X-Feature    — funcionalidade da interface que originou a chamada
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

USER_AGENT = "forzy-front-streamlit/1.0"


@dataclass
class Resposta:
    ok: bool
    status: int
    dados: Any
    request_id: Optional[str]
    latencia_servidor_ms: Optional[float]
    latencia_cliente_ms: float
    headers_enviados: dict
    erro: Optional[str] = None


class ForzyApiClient:
    def __init__(self, base_url: str, session_id: str, timeout: float = 15):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.timeout = timeout
        self._http = requests.Session()
        self._http.headers["User-Agent"] = USER_AGENT

    # ----------------------------------------------------------------------- #
    def _get(self, caminho: str, feature: str, params: Optional[dict] = None) -> Resposta:
        """GET com os headers de contexto; `feature` é o valor enviado em X-Feature."""
        headers = {"X-Session-Id": self.session_id, "X-Feature": feature}
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        inicio = time.perf_counter()
        try:
            resp = self._http.get(f"{self.base_url}{caminho}", headers=headers,
                                  params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            return Resposta(False, 0, None, None, None,
                            round((time.perf_counter() - inicio) * 1000, 1), headers,
                            erro=f"API inacessível em {self.base_url}: {exc.__class__.__name__}")
        latencia_cliente = round((time.perf_counter() - inicio) * 1000, 1)

        try:
            dados = resp.json()
        except ValueError:
            dados = None
        erro = None
        if not resp.ok:
            erro = (dados or {}).get("detail") if isinstance(dados, dict) else resp.text[:200]
        servidor = resp.headers.get("X-Response-Time-ms")
        return Resposta(
            ok=resp.ok,
            status=resp.status_code,
            dados=dados,
            request_id=resp.headers.get("X-Request-Id"),
            latencia_servidor_ms=float(servidor) if servidor else None,
            latencia_cliente_ms=latencia_cliente,
            headers_enviados=headers,
            erro=str(erro) if erro else None,
        )

    # ----------------------------------------------------------------------- #
    def listar_ativos(self) -> Resposta:
        return self._get("/v1/sensores", "catalogo-ativos")

    def leitura_atual(self, tag: str) -> Resposta:
        return self._get(f"/v1/sensores/{tag}/leitura-atual", "painel-leitura-atual")

    def historico(self, tag: str, horas: int, intervalo_min: int) -> Resposta:
        return self._get(f"/v1/sensores/{tag}/historico", "painel-historico",
                         {"horas": horas, "intervalo_min": intervalo_min})

    def observabilidade(self, limite: int = 20000, janela_min: Optional[int] = None,
                        rota: Optional[str] = None, feature: Optional[str] = None) -> Resposta:
        return self._get("/v1/observabilidade", "painel-observabilidade",
                         {"limite": limite, "janela_min": janela_min, "rota": rota, "feature": feature})
