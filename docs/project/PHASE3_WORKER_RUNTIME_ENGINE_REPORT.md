# Phase 3 — Worker Runtime Engine

Date : 2026-07-06
Branche : `codex/ops-update-readiness`

## Source normative

- `AUDIT-AUTONOMIE-V2.md`, Phase 3.
- `docs/brain/03-implementation-contracts.md`, sections :
  - Task State Machine ;
  - modèle `tasks` / `runs` ;
  - corrélation `CommandSpan` ;
  - relation observation -> run -> repair.

## Portée réalisée

Phase 3 est terminée côté Repo Cockpit VPS (`/home/hermes/repo-cockpit`) avec une migration compatible avec l'existant :

- `backend/tasks.py` : enum `TaskStatus`, `LEGAL_TRANSITIONS`, `transition()` unique, mapping ancien statut -> statut canonique ;
- tables Phase 3 : `task_status_events`, `runs`, `command_spans` ;
- `scripts/worker/engine.py` : `worker_runtime_context`, `CommandSpan`, création/fin de run ;
- `scripts/worker/phases.py` : mapping phase worker -> statut canonique ;
- `operation_worker.py` branché sur `transition()` via `claim_next`, `claim_task`, `update_task`, `update_task_blocked`, `heartbeat` ;
- `operation_worker.run()` enveloppe chaque commande dans un `CommandSpan` quand un contexte de run est actif ;
- `record_run()` alimente l'ancienne table `task_runs` et la nouvelle table `runs` ;
- ingestion runtime Phase 2 enrichie : si `run_id/phase/command` sont absents, corrélation depuis le `command_span` actif ;
- endpoints backend exposés (`approve`, `pause`, `cancel`, `merge`, réponse Pilote) branchés sur `transition()`.

Le champ historique `operation_queue.status` conserve les anciens libellés (`queued_plan`, `running_gpt55`, `needs_merge_approval`, etc.) pour ne pas casser le dashboard, Telegram et les timers. Le statut canonique est calculé et journalisé à chaque transition dans `task_status_events`.

## Fichiers VPS modifiés

```text
/home/hermes/repo-cockpit/backend/app.py
/home/hermes/repo-cockpit/backend/runtime_observations.py
/home/hermes/repo-cockpit/backend/tasks.py
/home/hermes/repo-cockpit/scripts/operation_worker.py
/home/hermes/repo-cockpit/scripts/worker/__init__.py
/home/hermes/repo-cockpit/scripts/worker/engine.py
/home/hermes/repo-cockpit/scripts/worker/phases.py
/home/hermes/repo-cockpit/tests/test_task_state_machine.py
/home/hermes/repo-cockpit/tests/test_command_spans.py
```

Backup créé :

```text
/home/hermes/repo-cockpit/backups/phase3-runtime-engine-20260706-201602
```

## Validation

Staging VPS `/tmp/repo-cockpit-phase3-stage` puis live VPS :

```bash
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python -m py_compile \
  backend/tasks.py backend/runtime_observations.py backend/app.py \
  scripts/worker/__init__.py scripts/worker/engine.py scripts/worker/phases.py \
  scripts/operation_worker.py tests/test_task_state_machine.py tests/test_command_spans.py \
  tests/test_observation_dedup.py tests/test_observation_schema_compat.py

PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_task_state_machine.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_command_spans.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_observation_dedup.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_observation_schema_compat.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/runtime_self_repair_smoke.py
```

Résultats :

```text
PASS test_task_state_machine
PASS test_command_spans
PASS test_observation_dedup
PASS test_observation_schema_compat
runtime self-repair remote smoke OK
```

Service :

```text
hermes-repo-cockpit.service restarted
/health OK
phase3_tables_ok True
```

Tables vérifiées :

```text
runs(id, task_id, kind, phase, started_at, ended_at, exit_status, ...)
command_spans(id, run_id, task_id, phase, command, started_at, ended_at, exit_status, ...)
task_status_events(id, ts, task_id, from_status, to_status, from_canonical, to_canonical, actor, reason, ...)
```

## Limites conservées volontairement

- `operation_queue.status` reste le champ lu par l'UI et les timers : les anciens statuts restent visibles.
- Phase 3 ne démarre pas le self-repair v2 complet : pas de runbooks, snapshots/rollback par tentative, ni budget repair par task. C'est la Phase 4.
- Le service gateway Telegram n'a pas été redémarré ni modifié pendant cette phase.

## Rollback

Restaurer depuis le backup, puis redémarrer Repo Cockpit :

```bash
sudo -u hermes XDG_RUNTIME_DIR=/run/user/$(id -u hermes) systemctl --user restart hermes-repo-cockpit.service
```

Les nouvelles tables sont additives ; les laisser en place ne casse pas l'ancien code si rollback fichier seulement.
