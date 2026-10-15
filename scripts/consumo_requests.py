"""
Script de consumo da Sensor Monitoring API via `requests`.

Chama os três endpoints do checkpoint enviando os headers de contexto
(X-Session-Id e X-Feature) e mostra no terminal o que cada um devolveu,
junto com o X-Request-Id e a latência registrados pelo back-end.

Uso (com a API no ar):
    python scripts/consumo_requests.py
    python scripts/consumo_requests.py --tag CMP-003 --api http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import sys
import uuid

import requests

FEATURE = "script-consumo"


def chamar(sessao: requests.Session, url: str, **params) -> dict:
    resp = sessao.get(url, params=params, timeout=15)
    print(f"  -> {resp.status_code} | X-Request-Id {resp.headers.get('X-Request-Id')} | "
          f"latência no servidor {resp.headers.get('X-Response-Time-ms')} ms")
    if not resp.ok:
        print(f"  !! {resp.json().get('detail', resp.text)}")
        return {}
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="Consome os 3 endpoints da Sensor Monitoring API.")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--tag", default="MOT-001")
    args = parser.parse_args()
    api = args.api.rstrip("/")

    session_id = str(uuid.uuid4())
    sessao = requests.Session()
    sessao.headers.update({
        "X-Session-Id": session_id,
        "X-Feature": FEATURE,
        "User-Agent": "forzy-script-consumo/1.0",
    })
    print(f"API: {api}\nX-Session-Id: {session_id}\nX-Feature: {FEATURE}\n")

    try:
        print(f"[1/3] GET /v1/sensores/{args.tag}/leitura-atual")
        leitura = chamar(sessao, f"{api}/v1/sensores/{args.tag}/leitura-atual")
        if leitura:
            print(f"  Severidade: {leitura['severidade'].upper()} | leitura de {leitura['timestamp_leitura']} "
                  f"(idade {leitura['idade_s']} s)")
            for g in leitura["grandezas"]:
                print(f"    {g['nome']:<16} {g['valor']:>9} {g['unidade']:<5} [{g['nivel']}]")

        print(f"\n[2/3] GET /v1/sensores/{args.tag}/historico?horas=6&intervalo_min=10")
        serie = chamar(sessao, f"{api}/v1/sensores/{args.tag}/historico", horas=6, intervalo_min=10)
        if serie:
            print(f"  {serie['pontos_recebidos']}/{serie['pontos_esperados']} pontos "
                  f"(completude {serie['completude']:.1%}) entre {serie['inicio']} e {serie['fim']}")
            for p in serie["leituras"][-3:]:
                print(f"    {p['timestamp']}  temp {p['temperatura_c']} °C  vib {p['vibracao_mms']} mm/s  "
                      f"[{p['severidade']}]")

        print(f"\n[3/3] GET /v1/observabilidade?session_id={session_id}")
        obs = chamar(sessao, f"{api}/v1/observabilidade", session_id=session_id, limite=10)
        if obs:
            print(f"  {len(obs['registros'])} chamadas desta sessão registradas pelo back-end:")
            for r in reversed(obs["registros"]):
                print(f"    {r['timestamp_utc']}  {r['rota']:<34} {r['status_code']}  "
                      f"{r['latencia_ms']:>7} ms  feature={r['feature']}")
            print("  (a chamada [3/3] é registrada depois de responder, por isso ainda não aparece na lista)")
    except requests.ConnectionError:
        print(f"\nNão foi possível conectar em {api}. A API está rodando? (uvicorn backend.main:app)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
