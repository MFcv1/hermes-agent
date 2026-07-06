# Phase 4 — Self-Repair V2

Date : 2026-07-06
Branche : `codex/ops-update-readiness`

## Source normative

- `AUDIT-AUTONOMIE-V2.md`, Phase 4.
- `docs/brain/03-implementation-contracts.md`, sections :
  - PolicyEngine ;
  - escalade secrets/permissions ;
  - snapshots et rollback ;
  - budget de repair par task ;
  - relation observation -> run -> repair.

## Portée réalisée

Phase 4 est terminée côté Repo Cockpit VPS (`/home/hermes/repo-cockpit`) :

- `policies.yaml` : feature flag `self_repair_v2`, décisions par action, budget `max_repair_attempts_per_task`.
- `backend/policy_engine.py` : évaluation centralisée `allow | ask_human | deny`, fallback inconnu -> `ask_human`.
- `scripts/worker/self_repair.py` : moteur runtime self-repair v2 avec replay, snapshot git, budget, escalade secret, rollback.
- `scripts/operation_worker.py` : bascule vers v2 quand `self_repair_v2` est activé ; ancien chemin V1 conservé comme rollback feature-flag.
- `backend/app.py` : migration additive au démarrage.
- Tables/colonnes Phase 4 :
  - table `policy_decisions` ;
  - colonnes `repair_attempts.observation_id`, `run_id`, `strategy`, `snapshot_ref`, `started_at`, `finished_at`, `outcome`, `replay_json`, `tests_json`, `rollback_json`.

Le moteur refuse les réparations automatiques quand le signal touche aux secrets, tokens, permissions, 401/403 ou credentials. Ces cas passent par `policy_decisions` avec action `access_secret` et bloquent en `blocked_runtime_repair`.

## Fichiers VPS modifiés

```text
/home/hermes/repo-cockpit/policies.yaml
/home/hermes/repo-cockpit/backend/app.py
/home/hermes/repo-cockpit/backend/policy_engine.py
/home/hermes/repo-cockpit/scripts/operation_worker.py
/home/hermes/repo-cockpit/scripts/worker/self_repair.py
/home/hermes/repo-cockpit/tests/test_policy_engine.py
/home/hermes/repo-cockpit/tests/test_runtime_repair_e2e.py
/home/hermes/repo-cockpit/tests/test_repair_rollback_on_worsen.py
/home/hermes/repo-cockpit/tests/test_repair_budget_exhausted.py
/home/hermes/repo-cockpit/tests/test_secret_error_escalates_no_repair.py
```

Backup créé :

```text
/home/hermes/repo-cockpit/backups/phase4-self-repair-v2-20260706-204205
```

## Validation

Staging VPS `/tmp/repo-cockpit-phase4-stage`, puis live VPS :

```bash
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python -m py_compile \
  backend/app.py backend/policy_engine.py scripts/operation_worker.py \
  scripts/worker/self_repair.py tests/test_policy_engine.py \
  tests/test_runtime_repair_e2e.py tests/test_repair_rollback_on_worsen.py \
  tests/test_repair_budget_exhausted.py tests/test_secret_error_escalates_no_repair.py

PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_policy_engine.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_runtime_repair_e2e.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_repair_rollback_on_worsen.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_repair_budget_exhausted.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_secret_error_escalates_no_repair.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_task_state_machine.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_command_spans.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_observation_dedup.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_observation_schema_compat.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/runtime_self_repair_smoke.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_runtime_self_repair.py
```

Résultats live :

```text
PASS test_policy_engine
PASS test_runtime_repair_e2e
PASS test_repair_rollback_on_worsen
PASS test_repair_budget_exhausted
PASS test_secret_error_escalates_no_repair
PASS test_task_state_machine
PASS test_command_spans
PASS test_observation_dedup
PASS test_observation_schema_compat
runtime self-repair remote smoke OK
```

Vérifications post-redémarrage :

```text
Repo Cockpit uvicorn direct : /home/hermes/repo-cockpit/.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
API root OK
phase4_policy_decisions True
phase4_repair_cols_ok True
phase4_missing_cols []
repair_attempts_cols 19
```

Note opérationnelle : au moment de cette phase, Repo Cockpit n'était pas lancé par une unit systemd visible. Il tournait comme process direct `uvicorn` sous l'utilisateur `hermes` sur `127.0.0.1:8765`. Le redémarrage a été fait en relançant ce process depuis `/home/hermes/repo-cockpit`.

## Limites conservées volontairement

- Le self-repair v2 corrige seulement les signaux runtime liés à une task et ne démarre pas de watcher global.
- Les commandes de replay dangereuses (`git`, `gh`, `rm`, `curl`, `ssh`, `scp`) sont refusées.
- L'ancien chemin runtime self-repair V1 reste disponible en désactivant `feature_flags.self_repair_v2`.
- Aucune action de merge, force-push, secret, dépense ou restart service n'est autorisée automatiquement par la policy.

## Rollback

Option la plus légère :

1. Mettre `feature_flags.self_repair_v2` à `false` dans `/home/hermes/repo-cockpit/policies.yaml`.
2. Redémarrer le process Repo Cockpit.

Rollback fichiers :

```bash
cp -a /home/hermes/repo-cockpit/backups/phase4-self-repair-v2-20260706-204205/app.py /home/hermes/repo-cockpit/backend/app.py
cp -a /home/hermes/repo-cockpit/backups/phase4-self-repair-v2-20260706-204205/operation_worker.py /home/hermes/repo-cockpit/scripts/operation_worker.py
cp -a /home/hermes/repo-cockpit/backups/phase4-self-repair-v2-20260706-204205/policies.yaml /home/hermes/repo-cockpit/policies.yaml 2>/dev/null || true
```

Les migrations DB sont additives. Les colonnes et `policy_decisions` peuvent rester en place sans casser le chemin V1.
