# Phase 5 — Memory / Handoff Store

Date : 2026-07-06
Branche : `codex/ops-update-readiness`

## Source normative

- `AUDIT-AUTONOMIE-V2.md`, Phase 5.
- `docs/brain/03-implementation-contracts.md`, section `Memory / Handoff Store`.

## Portée réalisée

Phase 5 est terminée côté Repo Cockpit VPS et gateway Telegram live :

- Repo Cockpit devient la source de vérité des handoffs.
- `operation_queue.parent_task_id` est ajouté pour relier une reprise à la task source.
- Table `handoffs` ajoutée côté Cockpit avec `task_id`, `conversation_key`, `summary`, `resume_hints_json`, `consumed_at`.
- Endpoints internes ajoutés :
  - `POST /api/internal/tasks/{id}/handoff`
  - `GET /api/internal/tasks/{id}/handoff`
  - `GET /api/internal/handoffs/latest?conversation_key=...`
  - `POST /api/internal/handoffs/{id}/consume`
- Le endpoint `GET /api/internal/threads/active/{telegram_user_id}` expose maintenant `last_task_id`, `last_task_title`, `last_task_status`, `parent_task_id`.
- Gateway local : `gateway/memory/handoff_store.py` remplace le stockage JSON append-only par un cache SQLite.
- `ActiveWorkStore` reste une façade compatible, mais écrit dans SQLite et migre l'ancien JSON si présent.
- Le classifieur Libre reconnaît `resume` (`reprends`, `continue`, `chantier d'hier`, etc.).
- `/libre` soft-close écrit un handoff local et le pousse vers Cockpit quand un `task_id` est connu.
- Après `reprends le chantier`, la prochaine task repo créée depuis Libre envoie `parent_task_id` à Cockpit.

## Fichiers VPS modifiés

Repo Cockpit :

```text
/home/hermes/repo-cockpit/backend/app.py
/home/hermes/repo-cockpit/backend/handoffs.py
/home/hermes/repo-cockpit/scripts/operation_worker.py
/home/hermes/repo-cockpit/tests/test_handoff_roundtrip.py
```

Gateway live :

```text
/home/hermes/.hermes/hermes-agent/gateway/libre_orchestrator.py
/home/hermes/.hermes/hermes-agent/gateway/memory/__init__.py
/home/hermes/.hermes/hermes-agent/gateway/memory/handoff_store.py
/home/hermes/.hermes/hermes-agent/gateway/platforms/telegram.py
/home/hermes/.hermes/hermes-agent/tests/gateway/test_libre_orchestrator.py
/home/hermes/.hermes/hermes-agent/tests/gateway/test_telegram_pilot_mode.py
```

Note : le gateway live n'avait pas encore la Phase 1 locale complète (`telegram_conversations_mixin.py` absent). Le déploiement live applique donc le patch Phase 5 minimal dans le monolithe `gateway/platforms/telegram.py`, tandis que le repo local garde l'implémentation structurée dans `gateway/telegram_conversations_mixin.py`.

## Backups

```text
/home/hermes/repo-cockpit/backups/phase5-handoff-store-20260706-210838
/home/hermes/gateway-backups/phase5-handoff-store-20260706-211146
```

## Validation

Local Hermes :

```bash
venv/bin/python -m py_compile \
  gateway/libre_orchestrator.py gateway/memory/handoff_store.py \
  gateway/telegram_conversations_mixin.py gateway/repo_cockpit_telegram_mixin.py

venv/bin/python -m pytest \
  tests/gateway/test_libre_orchestrator.py \
  tests/gateway/test_telegram_pilot_mode.py \
  -q -o 'addopts='
```

Résultat :

```text
24 passed
```

Staging puis live Repo Cockpit :

```bash
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python -m py_compile \
  backend/app.py backend/handoffs.py scripts/operation_worker.py tests/test_handoff_roundtrip.py

PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_handoff_roundtrip.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_policy_engine.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_runtime_repair_e2e.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_task_state_machine.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_command_spans.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_observation_dedup.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_observation_schema_compat.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/runtime_self_repair_smoke.py
```

Résultat live :

```text
PASS test_handoff_roundtrip
PASS test_policy_engine
PASS test_runtime_repair_e2e
PASS test_task_state_machine
PASS test_command_spans
PASS test_observation_dedup
PASS test_observation_schema_compat
runtime self-repair remote smoke OK
```

Gateway live :

```text
venv/bin/python -m py_compile gateway/libre_orchestrator.py gateway/memory/handoff_store.py gateway/platforms/telegram.py
gateway phase5 smoke OK
```

Post-restart live :

```text
Cockpit pid: 363750
Gateway pid: 364412
cockpit_api_ok
handoffs_table True
parent_task_id_col True
```

## Done

Le scénario cible est couvert :

```text
/libre
  -> écrit handoff task-scoped dans Cockpit
reprends le chantier d'hier
  -> retrouve le handoff par conversation_key/task_id
message de travail repo suivant
  -> crée une task avec parent_task_id = task source
```

## Limites conservées volontairement

- Le gateway garde un cache local SQLite ; Cockpit reste la source de vérité.
- L'ancien JSON `~/.hermes/libre/state.json` n'est pas supprimé automatiquement : il est lu comme source de migration.
- Le live gateway est encore sur le monolithe Telegram ; la version locale structurée est prête mais la synchronisation complète Phase 1 reste à planifier séparément.

## Rollback

Repo Cockpit :

```bash
cp -a /home/hermes/repo-cockpit/backups/phase5-handoff-store-20260706-210838/backend/app.py /home/hermes/repo-cockpit/backend/app.py
cp -a /home/hermes/repo-cockpit/backups/phase5-handoff-store-20260706-210838/scripts/operation_worker.py /home/hermes/repo-cockpit/scripts/operation_worker.py
rm -f /home/hermes/repo-cockpit/backend/handoffs.py
```

Gateway :

```bash
cp -a /home/hermes/gateway-backups/phase5-handoff-store-20260706-211146/gateway/libre_orchestrator.py /home/hermes/.hermes/hermes-agent/gateway/libre_orchestrator.py
cp -a /home/hermes/gateway-backups/phase5-handoff-store-20260706-211146/gateway/platforms/telegram.py /home/hermes/.hermes/hermes-agent/gateway/platforms/telegram.py
```

Les migrations DB sont additives ; laisser `handoffs` et `parent_task_id` en place ne casse pas l'ancien chemin.
