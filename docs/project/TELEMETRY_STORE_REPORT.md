# Quick Win 11 — Telemetry Store

Date : 2026-07-07
Branche : `codex/ops-update-readiness`

## Source normative

- `AUDIT-AUTONOMIE-V2.md`, quick win 11.
- `docs/brain/03-implementation-contracts.md`, masquage secrets à l'ingestion.
- `docs/brain/04-cost-engine.md`, événement `kind=llm_call` et coûts journaliers.

## Portée réalisée

Le store de telemetry append-only est terminé côté Repo Cockpit VPS, avec un
sink structuré côté gateway :

- `backend/telemetry.py` ajouté côté Repo Cockpit.
- Table SQLite `events` ajoutée avec index par date, kind et task.
- Endpoint interne d'écriture :
  - `POST /api/internal/telemetry/events`
- Endpoint interne de lecture récente :
  - `GET /api/internal/telemetry/events`
- Endpoint interne coût journalier :
  - `GET /api/internal/costs/daily`
- Sink gateway ajouté :
  - `hermes_logging.emit_structured_telemetry(...)`
  - sortie locale `logs/telemetry.jsonl`
  - permission best-effort `0600`
  - payload nettoyé récursivement avant écriture.

La règle de sécurité est stricte : le store et le sink conservent des
métadonnées, compteurs et références, jamais les messages complets, prompts,
tokens, clés API, bodies bruts ou secrets.

## Fichiers modifiés

Repo Cockpit live :

```text
/home/hermes/repo-cockpit/backend/app.py
/home/hermes/repo-cockpit/backend/telemetry.py
/home/hermes/repo-cockpit/tests/test_telemetry_store.py
```

Gateway live et repo local :

```text
/home/hermes/.hermes/hermes-agent/hermes_logging.py
/home/hermes/.hermes/hermes-agent/tests/test_hermes_logging.py
```

## Backups

```text
/home/hermes/repo-cockpit/backups/telemetry-store-20260706-224307
/home/hermes/gateway-backups/telemetry-sink-20260706-224559
```

## Validation

Repo Cockpit staging puis live :

```bash
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python -m py_compile \
  backend/app.py backend/telemetry.py tests/test_telemetry_store.py

PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_telemetry_store.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_runbook_schema.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_autonomy_status_payload.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_evaluations_store.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_handoff_roundtrip.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_policy_engine.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_runtime_repair_e2e.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/runtime_self_repair_smoke.py
```

Résultat live :

```text
PASS test_telemetry_store
PASS test_runbook_schema
PASS test_autonomy_status_payload
PASS test_evaluations_store
PASS test_handoff_roundtrip
PASS test_policy_engine
PASS test_runtime_repair_e2e
runtime self-repair remote smoke OK
```

Gateway local :

```bash
venv/bin/python -m py_compile hermes_logging.py
venv/bin/python -m pytest tests/test_hermes_logging.py -q -o 'addopts='
```

Résultat local :

```text
63 passed
```

Gateway live :

```text
py_compile hermes_logging.py OK
gateway telemetry sink smoke OK
```

Le venv gateway live ne contient pas `pytest`; le test unitaire complet a donc
été exécuté localement, et le live a été validé par import réel + écriture JSONL
dans un `HERMES_HOME` temporaire.

## Smoke live post-restart

Cockpit :

```text
PID uvicorn final: 371108
events_table [('events',)]
POST /api/internal/telemetry/events -> 200
GET /api/internal/costs/daily -> 200
GET /api/internal/telemetry/events?task_id=quickwin-11-live-smoke -> 200
payload stocké: {"message_omitted": true, "purpose": "live_smoke"}
mask_endpoint_ok: {"user_message_omitted": true, "safe_ref": "artifact://mask-smoke"}
```

Gateway Telegram :

```text
PID initial avant quick win: 367576
PID final après resync sink: 371278
gateway telemetry mask smoke OK
[Telegram] Connected to Telegram (polling mode)
Gateway running with 1 platform(s)
```

## Done

Le scénario cible est couvert :

```text
gateway metadata-only jsonl sink
  -> no prompt/message/secret fields
  -> cockpit append-only events table
  -> recent events endpoint
  -> daily llm cost aggregation endpoint
```

## Limites conservées volontairement

- Le gateway écrit encore dans un JSONL local ; l'envoi automatique vers Cockpit
  peut être ajouté ensuite via un job explicite ou un worker, sans introduire de
  nouveau core tool.
- Le dashboard ne consomme pas encore directement `/api/internal/costs/daily`.
- Aucun contenu utilisateur complet n'est stocké pour faciliter les recherches ;
  c'est volontaire pour respecter le contrat telemetry.

## Rollback

Repo Cockpit :

```bash
cp -a /home/hermes/repo-cockpit/backups/telemetry-store-20260706-224307/backend/app.py /home/hermes/repo-cockpit/backend/app.py
rm -f /home/hermes/repo-cockpit/backend/telemetry.py
rm -f /home/hermes/repo-cockpit/tests/test_telemetry_store.py
```

Gateway :

```bash
cp -a /home/hermes/gateway-backups/telemetry-sink-20260706-224559/hermes_logging.py /home/hermes/.hermes/hermes-agent/hermes_logging.py
cp -a /home/hermes/gateway-backups/telemetry-sink-20260706-224559/test_hermes_logging.py /home/hermes/.hermes/hermes-agent/tests/test_hermes_logging.py
```

La table `events` est additive ; la laisser en place ne casse pas les chemins
précédents.
