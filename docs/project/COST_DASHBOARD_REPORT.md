# Quick Win 12 — Cost Dashboard

Date : 2026-07-07
Branche : `codex/ops-update-readiness`

## Source normative

- `docs/project/TELEMETRY_STORE_REPORT.md`.
- `docs/brain/04-cost-engine.md`, section tracking et agrégation journalière.
- `docs/project/TODO.md` local, suites Phase 11 Telemetry Store.

## Portée réalisée

La telemetry Phase 11 est maintenant visible et exploitable :

- `backend.telemetry.task_costs(...)` ajoute une agrégation coût par task.
- `GET /api/costs/daily` expose le coût du jour au WebApp avec auth Telegram/dev.
- `GET /api/internal/tasks/{task_id}/autonomy` inclut `cost_summary`.
- Le WebApp Repo Cockpit affiche :
  - coût du jour ;
  - nombre d'appels LLM ;
  - modèle le plus coûteux du jour.
- `/status <task_id>` Telegram peut afficher une ligne coût issue de `cost_summary`.

Le dashboard reste metadata-only : il lit uniquement les montants, modèles,
compteurs et task IDs déjà présents dans `events`.

## Fichiers modifiés

Repo Cockpit live :

```text
/home/hermes/repo-cockpit/backend/app.py
/home/hermes/repo-cockpit/backend/telemetry.py
/home/hermes/repo-cockpit/webapp/index.html
/home/hermes/repo-cockpit/tests/test_telemetry_store.py
/home/hermes/repo-cockpit/tests/test_autonomy_status_payload.py
```

Gateway live :

```text
/home/hermes/.hermes/hermes-agent/gateway/platforms/telegram.py
```

## Backups

```text
/home/hermes/repo-cockpit/backups/cost-dashboard-20260706-230524
/home/hermes/gateway-backups/cost-dashboard-20260706-230524
```

## Validation

Staging VPS :

```text
PASS test_telemetry_store
PASS test_autonomy_status_payload
py_compile gateway/platforms/telegram.py OK
```

Live VPS :

```text
PASS test_telemetry_store
PASS test_autonomy_status_payload
webapp_cost_block_ok
GET /api/internal/costs/daily -> 200
autonomy_cost_summary_ok op_1782918577_c0b80175 0.000321
Gateway Telegram reconnected
```

Non-régression live :

```text
PASS test_runbook_schema
PASS test_evaluations_store
PASS test_handoff_roundtrip
PASS test_policy_engine
PASS test_runtime_repair_e2e
runtime self-repair remote smoke OK
```

## Done

Le scénario cible est couvert :

```text
events(kind=llm_call)
  -> daily_costs / task_costs
  -> WebApp cost strip
  -> autonomy cost_summary
  -> Telegram /status cost line
```

## Limites conservées volontairement

- Le dashboard affiche une synthèse compacte, pas encore une page analytique complète.
- Les budgets soft/hard de `docs/brain/04-cost-engine.md` ne sont pas encore appliqués.
- Le heartbeat VPS Self-Ops reste le prochain chantier séparé.

## Rollback

Repo Cockpit :

```bash
cp -a /home/hermes/repo-cockpit/backups/cost-dashboard-20260706-230524/backend/app.py /home/hermes/repo-cockpit/backend/app.py
cp -a /home/hermes/repo-cockpit/backups/cost-dashboard-20260706-230524/backend/telemetry.py /home/hermes/repo-cockpit/backend/telemetry.py
cp -a /home/hermes/repo-cockpit/backups/cost-dashboard-20260706-230524/webapp/index.html /home/hermes/repo-cockpit/webapp/index.html
cp -a /home/hermes/repo-cockpit/backups/cost-dashboard-20260706-230524/tests/test_telemetry_store.py /home/hermes/repo-cockpit/tests/test_telemetry_store.py
cp -a /home/hermes/repo-cockpit/backups/cost-dashboard-20260706-230524/tests/test_autonomy_status_payload.py /home/hermes/repo-cockpit/tests/test_autonomy_status_payload.py
```

Gateway :

```bash
cp -a /home/hermes/gateway-backups/cost-dashboard-20260706-230524/gateway/platforms/telegram.py /home/hermes/.hermes/hermes-agent/gateway/platforms/telegram.py
```
