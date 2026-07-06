# Phase 7 — Autonomy Dashboard / Admin UX

Date : 2026-07-06
Branche : `codex/ops-update-readiness`

## Source normative

- `AUDIT-AUTONOMIE-V2.md`, Phase 7.
- `docs/brain/03-implementation-contracts.md`.
- `docs/project/PHASE4_SELF_REPAIR_V2_REPORT.md`
- `docs/project/PHASE5_MEMORY_HANDOFF_STORE_REPORT.md`
- `docs/project/PHASE6_EVAL_HARNESS_REPORT.md`

## Portée réalisée

Phase 7 est terminée côté Repo Cockpit VPS et gateway Telegram live.

La roadmap demandait de commencer par `/status` Telegram riche, pas par une page web. Le résultat :

- `/status <task_id>` et `/status` sur conversation active affichent maintenant un état complet en un message.
- Le payload Cockpit `/api/internal/tasks/{task_id}/autonomy` expose :
  - `task_runs`
  - `repair_attempts`
  - `runtime_observations`
  - `approvals`
  - `evaluation_summary`
  - dernières `evaluations`
  - `parent_task_id`
- Le message Telegram affiche :
  - identité task/repo/statut/phase/mode/reprise,
  - preview si disponible,
  - vue rapide runs/repairs/observations/approvals/evals,
  - dernière erreur classée,
  - provider checks,
  - smoke tests,
  - runs récents,
  - réparations,
  - observations runtime,
  - approvals,
  - runbooks appliqués,
  - lien vers `/runs <task_id>` pour le détail technique.

## Fichiers locaux modifiés

```text
gateway/repo_cockpit_formatting.py
tests/gateway/test_repo_cockpit_formatting.py
docs/project/PHASE7_AUTONOMY_STATUS_UX_REPORT.md
docs/project/AUTONOMIE_V2_IMPLEMENTATION_STATUS.md
docs/project/README.md
AGENTS.md
```

## Fichiers VPS modifiés

Repo Cockpit :

```text
/home/hermes/repo-cockpit/backend/app.py
/home/hermes/repo-cockpit/tests/test_autonomy_status_payload.py
```

Gateway live :

```text
/home/hermes/.hermes/hermes-agent/gateway/platforms/telegram.py
```

Note : le gateway live reste sur le monolithe Telegram pour cette zone. Le repo local conserve la version structurée dans `gateway/repo_cockpit_formatting.py`.

## Backups

```text
/home/hermes/repo-cockpit/backups/phase7-autonomy-status-20260706-213455
/home/hermes/gateway-backups/phase7-autonomy-status-20260706-213735
```

## Validation

Local Hermes :

```bash
venv/bin/python -m py_compile gateway/repo_cockpit_formatting.py gateway/repo_cockpit_telegram_mixin.py
venv/bin/python -m pytest \
  tests/gateway/test_repo_cockpit_formatting.py \
  tests/gateway/test_repo_cockpit_keyboards.py \
  tests/gateway/test_telegram_pilot_mode.py \
  -q -o 'addopts='
venv/bin/python scripts/run_evals.py --suite routing --report text
venv/bin/python scripts/run_evals.py --suite repair --report text
```

Résultat :

```text
25 passed
routing: 55/55 passed (score=1.0)
repair: 7/7 passed (score=1.0)
```

Repo Cockpit live :

```bash
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python -m py_compile backend/app.py tests/test_autonomy_status_payload.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_autonomy_status_payload.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_evaluations_store.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_handoff_roundtrip.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_policy_engine.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_runtime_repair_e2e.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/runtime_self_repair_smoke.py
```

Résultat live :

```text
PASS test_autonomy_status_payload
PASS test_evaluations_store
PASS test_handoff_roundtrip
PASS test_policy_engine
PASS test_runtime_repair_e2e
runtime self-repair remote smoke OK
```

Gateway live :

```bash
venv/bin/python -m py_compile gateway/platforms/telegram.py
venv/bin/python scripts/run_evals.py --suite routing --report text
venv/bin/python scripts/run_evals.py --suite repair --report text
```

Résultat live :

```text
routing: 55/55 passed (score=1.0)
repair: 7/7 passed (score=1.0)
```

Post-restart live :

```text
Cockpit pid: 367056
Gateway pid: 367576
cockpit_ok
Telegram connected
Payload autonomy enrichi: task_runs, repair_attempts, runtime_observations, approvals, evaluation_summary, evaluations
```

## Done

Le scénario cible est couvert :

```text
/status op_xxx
  -> lit la source de vérité Cockpit
  -> résume task/runs/repairs/observations/approvals/evals
  -> renvoie un seul message Telegram riche
```

## Limites conservées volontairement

- Pas de nouvelle page web : la roadmap demandait Telegram d'abord.
- `/runs` reste disponible pour les listes techniques plus longues.
- Les évaluations ne sont pas liées à une task spécifique dans le modèle de Phase 6 ; `/status` affiche donc un résumé global récent par suite.
- Le gateway live monolithique reçoit un patch minimal ; la synchronisation complète avec les modules Phase 1 reste séparée.

## Rollback

Repo Cockpit :

```bash
cp -a /home/hermes/repo-cockpit/backups/phase7-autonomy-status-20260706-213455/backend/app.py /home/hermes/repo-cockpit/backend/app.py
rm -f /home/hermes/repo-cockpit/tests/test_autonomy_status_payload.py
```

Gateway :

```bash
cp -a /home/hermes/gateway-backups/phase7-autonomy-status-20260706-213735/gateway/platforms/telegram.py /home/hermes/.hermes/hermes-agent/gateway/platforms/telegram.py
```

Les changements DB sont nuls en Phase 7 ; la phase lit seulement les tables existantes.
