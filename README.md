# Monitor de Menções — Intellicore

Backend Python do sistema de monitoramento automático de menções de **Hudson Viana Borges** (nome completo, CPF e CNPJ) em fontes públicas brasileiras.

## Arquitetura

```
pg_cron (Supabase, 10h UTC)
    └─► Edge Function intellicore-monitor (Supabase)
            └─► POST /api/internal/run-monitor (este backend, Render)
                    └─► run_daily.py → monitor_completo.py → Email SMTP Gmail
                                └─► Supabase (seen_ids + execucoes)
```

## Fontes monitoradas

- DOU — Diário Oficial da União
- Querido Diário (API) — diários municipais
- Diários Estaduais (API Querido Diário) — SP, RJ, MG, RS, PR, SC, BA, PE, CE, GO, DF
- Bancas de concursos — IADES, FGV, CEBRASPE, VUNESP, CESGRANRIO, IBFC, QUADRIX
- Editais culturais — ProAC, Funarte, BNDES, Caixa Cultural, SESC-SP, Itaú Cultural, etc.
- Busca Web Geral — Brave Search

## Termos de busca

Apenas o **nome completo** é utilizado para evitar falsos positivos:
- `Hudson Viana Borges`
- CPF: `828.258.071-68`
- CNPJ: `32.309.482/0001-52`

## Endpoints

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/health` | Health check público |
| POST | `/api/internal/run-monitor` | Executa o monitor (com idempotência) |
| POST | `/api/internal/run-monitor-force` | Executa ignorando o lock do dia |
| POST | `/api/internal/run-monitor-force-send` | Executa e força envio do email |

Todos os endpoints `POST` requerem `Authorization: Bearer <INTERNAL_API_KEY>`.

## Variáveis de ambiente (Render)

| Variável | Descrição |
|----------|-----------|
| `INTERNAL_API_KEY` | Token de autenticação interna (gerado pelo Render) |
| `GMAIL_SMTP_USER` | Endereço Gmail de envio |
| `GMAIL_APP_PASSWORD` | Senha de aplicativo Gmail (16 caracteres) |
| `SUPABASE_URL` | URL do projeto Supabase |
| `SUPABASE_SERVICE_ROLE_KEY` | Chave service_role do Supabase |

## Idempotência

O lock de idempotência é gerenciado exclusivamente pelo Supabase (tabela `monitor_execucoes`).
Não há dependência de arquivos locais ou estado de sandbox.

## Deploy

Serviço Docker no Render conectado a este repositório GitHub.
O Render faz rebuild automático a cada push na branch `main`.
