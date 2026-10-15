# Forzy · Sensor Monitoring API — Observabilidade de um endpoint de IA

Checkpoint integrado das disciplinas **Governança em IA** e **Front-end** (Tecnólogo em IA — FIAP).

O projeto expõe o **provider de Sensores** da solução Forzy · Digital Twin (leitura atual e histórico) como uma API **FastAPI**, registra a cada chamada os metadados definidos pelo contrato de observabilidade da disciplina de Governança (headers de contexto, timestamp e latência) e consome a API via **requests** em uma interface **Streamlit**.

## Integrantes — Grupo 4M · Turma 2TIPAY

| Nome | RM |
|---|---|
| Bruno Lanchariche Fitipaldi Lopes | 564129 |
| Gustavo Civita Gonçalves | 561932 |
| Rafael de Paiva Ramos | 563978 |
| Thiago Santana Danyluk | 562286 |
| Arthur Pedreca de Almeida Vasco | 525615 |

## Entregáveis

| Item | Arquivo |
|---|---|
| Vídeo de demonstração (2 min, narrado) | [`docs/Video_Demonstracao_Forzy.mp4`](docs/Video_Demonstracao_Forzy.mp4) |
| Documento ABNT de Governança em IA (contrato, indicadores, limiares, gráficos, plano de resposta e validação) | [`docs/Documento_ABNT_Observabilidade_Forzy_Grupo4M.pdf`](docs/Documento_ABNT_Observabilidade_Forzy_Grupo4M.pdf) |
| Evidências da coleta usadas no documento (5.357 chamadas em CSV, resposta do endpoint de observabilidade, diagnóstico de latência) | [`docs/evidencias/`](docs/evidencias/) |

---

## Arquitetura

```
 Streamlit (frontend/app.py)            scripts/consumo_requests.py
   Leitura atual | Histórico | Observabilidade        │
            │                                         │
            │  requests + headers de contexto         │
            │  X-Session-Id: <uuid da sessão>         │
            │  X-Feature:    <funcionalidade>         │
            ▼                                         ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ FastAPI (backend/main.py)                                   │
 │                                                             │
 │  Middleware de observabilidade                              │
 │   • mede a latência   • lê os headers   • gera X-Request-Id │
 │   • grava 1 registro por chamada ──────────▶ SQLite         │
 │                                     data/observabilidade.db │
 │  Rotas                                                      │
 │   GET /v1/sensores/{tag}/leitura-atual ─┐                   │
 │   GET /v1/sensores/{tag}/historico ─────┼─▶ provider de     │
 │                                         │   Sensores        │
 │                                         │   (dados          │
 │                                         │    fictícios)     │
 │   GET /v1/observabilidade ──▶ registros + indicadores       │
 │                               do contrato (8 indicadores)   │
 └─────────────────────────────────────────────────────────────┘
```

| Camada | Arquivo | Responsabilidade |
|---|---|---|
| Contrato | `backend/contrato.py` | Headers obrigatórios, campos registrados, 8 indicadores com limiares e ações (fonte única das regras de Governança) |
| Provider | `backend/providers/sensores.py` | Leitura atual e histórico de 5 ativos Forzy com **dados fictícios determinísticos**, incluindo atraso de publicação, lacunas e indisponibilidade do gateway |
| Observabilidade | `backend/observabilidade.py` | Middleware HTTP + repositório SQLite dos registros de chamada |
| Indicadores | `backend/indicadores.py` | Cálculo dos indicadores do contrato sobre os registros |
| API | `backend/main.py` | Rotas FastAPI |
| Cliente HTTP | `frontend/api_client.py` | Envia `X-Session-Id` e `X-Feature` em toda chamada |
| Interface | `frontend/app.py`, `frontend/graficos.py` | Telas Streamlit e gráficos Plotly |
| Scripts | `scripts/consumo_requests.py` | Consumo dos 3 endpoints via `requests` |
| | `scripts/gerar_trafego.py` | Simula sessões simultâneas para popular a base de observabilidade |
| Testes | `tests/test_api.py` | Testes da API e do registro de observabilidade |
| Entregáveis | `docs/` | Documento ABNT, vídeo de demonstração e evidências da coleta |

---

## Endpoints

| Funcionalidade | Endpoint | Consumo no front |
|---|---|---|
| Catálogo de ativos | `GET /v1/sensores` | Seletor de ativos |
| Leitura atual | `GET /v1/sensores/{tag}/leitura-atual` | Leitura, severidade retornada, motivo de cada estado e idade do dado |
| Histórico | `GET /v1/sensores/{tag}/historico?horas=6&intervalo_min=5` | Gráfico com faixas de limite, tabela e completude da série |
| Observabilidade | `GET /v1/observabilidade?janela_min=60&rota=&feature=&session_id=&tag=&limite=` | Registros de chamadas, indicadores do contrato e gráficos |

Documentação interativa (Swagger): `http://127.0.0.1:8000/docs`.

> Use `127.0.0.1` em vez de `localhost` no Windows: `localhost` tenta IPv6 antes do IPv4 e cada conexão nova perde cerca de 2 s (diagnóstico feito com o próprio rastreio de latência da interface).

TAGs disponíveis: `MOT-001`, `BMB-002`, `CMP-003`, `VEN-004`, `MOT-005`.

## Contrato de observabilidade (resumo)

**Headers enviados pelo front em toda chamada:** `X-Session-Id` (UUID da sessão do Streamlit) e `X-Feature` (funcionalidade que originou a chamada). Chamadas sem esses headers **não são rejeitadas**, mas ficam registradas como `contexto_completo = false` e recebem o header de resposta `X-Observabilidade-Aviso`.

**Campos registrados por chamada:** `request_id`, `timestamp_utc`, `metodo`, `rota`, `caminho`, `tag`, `session_id`, `feature`, `cliente` (User-Agent), `contexto_completo`, `status_code`, `latencia_ms`, `idade_dado_s`, `severidade`, `completude_historico`, `erro`. O IP do cliente não é armazenado (minimização de dados). Retenção: 30 dias.

| Código | Indicador | Meta | Crítico |
|---|---|---|---|
| IND-01 | Latência p95 — leitura atual | ≤ 250 ms | > 800 ms |
| IND-02 | Latência p95 — histórico | ≤ 600 ms | > 1500 ms |
| IND-03 | Tempo de atualização (idade p95 do dado) | ≤ 15 s | > 60 s |
| IND-04 | Taxa de completude de freshness | ≥ 95% | < 90% |
| IND-05 | Completude dos metadados de contexto | ≥ 99% | < 95% |
| IND-06 | Taxa de erro do servidor (5xx) | ≤ 1% | > 5% |
| IND-07 | Completude do histórico | ≥ 98% | < 95% |
| IND-08 | Proporção de leituras críticas devolvidas | ≤ 25% | > 40% |

## Dados fictícios

O enunciado limita o checkpoint ao provider de Sensores. Os dados são **simulados** e determinísticos (a mesma TAG no mesmo instante produz sempre os mesmos valores), com perfis que exercitam os indicadores:

| TAG | Ativo | Saúde | Conectividade do gateway |
|---|---|---|---|
| MOT-001 | Motor da Extrusora Principal | saudável | estável |
| BMB-002 | Bomba de Recalque de Água Gelada | saudável | oscilante (3% das amostras atrasam 20–40 s; 2% de lacunas) |
| CMP-003 | Compressor de Ar Principal | crítico | estável |
| VEN-004 | Exaustor do Galpão Produtivo | em degradação | estável |
| MOT-005 | Motor da Esteira de Alimentação | saudável | intermitente (a cada 15 min: 3 min com atraso de 25–90 s e 1 min fora do ar → 503) |

---

## Como rodar localmente

Pré-requisito: **Python 3.10+**.

```bash
git clone https://github.com/ArthurPedreca/cp-forzy-governan-a.git
cd cp-forzy-governan-a

python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux / macOS

pip install -r requirements.txt
```

**1. Subir a API** (terminal 1):

```bash
uvicorn backend.main:app --reload
```

**2. Subir a interface** (terminal 2):

```bash
streamlit run frontend/app.py
```

A interface abre em `http://localhost:8501`. Para apontar para outra URL da API, use o campo **URL da API** na barra lateral ou a variável de ambiente `FORZY_API_URL`.

**3. Script de consumo via requests** (terminal 3):

```bash
python scripts/consumo_requests.py --tag CMP-003
```

**4. (Opcional) Gerar tráfego para o relatório de observabilidade:**

```bash
python scripts/gerar_trafego.py --minutos 10 --sessoes 4
```

**5. Testes:**

```bash
pytest
```

### Variáveis de ambiente

| Variável | Padrão | Uso |
|---|---|---|
| `FORZY_API_URL` | `http://127.0.0.1:8000` | URL da API usada pelo Streamlit |
| `FORZY_DB_PATH` | `data/observabilidade.db` | Arquivo SQLite dos registros de observabilidade |
| `FORZY_LATENCIA_SIMULADA` | `1` | `0` desliga a latência simulada de I/O do provider |

## Pacotes

| Pacote | Para quê |
|---|---|
| `fastapi`, `uvicorn` | API e servidor ASGI |
| `streamlit` | Interface |
| `requests` | Consumo da API pelo front e pelo script |
| `pandas`, `plotly` | Tabelas e gráficos do front |
| `pytest`, `httpx` | Testes (o `TestClient` do FastAPI usa `httpx`) |

## Mapa dos requisitos (Front-end)

| Requisito | Onde está |
|---|---|
| Projeto FastAPI com leitura atual, histórico e observabilidade do provider de Sensores | `backend/main.py`, `backend/providers/sensores.py` |
| Registro, a cada chamada, dos metadados de Governança (headers de contexto, timestamp, latência) | `backend/observabilidade.py` → `criar_middleware` |
| Script de consumo via requests dos três endpoints | `scripts/consumo_requests.py` |
| Interface Streamlit exibindo leitura atual, histórico e relatório de observabilidade | `frontend/app.py` |
| Envio de `X-Session-Id` e `X-Feature` em cada chamada do front | `frontend/api_client.py` → `ForzyApiClient._get` |
| README com integrantes, arquitetura e pacotes | este arquivo |
| Vídeo simples de demonstração | `docs/Video_Demonstracao_Forzy.mp4` |

## Fora do escopo deste checkpoint

Autenticação/autorização e a migração dos providers de Equipamentos e Plantas ficam para a Sprint 4.

## Uso de IA

Ferramentas de IA generativa foram usadas como apoio na estruturação do código e da documentação. Toda a solução foi revisada, executada e testada pelo grupo. A declaração detalhada está na seção 7 do documento ABNT.
