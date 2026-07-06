# Phase 2 — Observation bus + contrats

Date : 2026-07-06
Branche : `codex/ops-update-readiness`

## Source normative

- `AUDIT-AUTONOMIE-V2.md`, Phase 2.
- `docs/brain/03-implementation-contracts.md`, sections :
  - Observation payload v2 ;
  - fingerprint/dédup ;
  - masquage des secrets ;
  - interdiction des observations sans `task_id`.

## Portée réalisée

Phase 2 est terminée sur les deux côtés du flux :

- helper gateway `gateway/observation_reporter.py` ;
- payload v2 conforme au contrat ;
- payload v1 conservé pour compatibilité de transition ;
- masquage gateway avant émission ;
- tests de contrat côté gateway ;
- intégration du runtime observer Telegram via le helper, en mode v2 ;
- backend Repo Cockpit VPS `/home/hermes/repo-cockpit` avec ingestion v1/v2 ;
- fingerprint/dédup serveur avec fenêtre 30 min ;
- table `runtime_observations` enrichie avec `fingerprint`, `count`, `first_seen`, `last_seen`, `raw_excerpt`, `phase`, `command`, `schema_version`.

## Comportement

`gateway/observation_reporter.py` fournit :

```text
build_runtime_observation_v2()
runtime_observations_from_watch_report()
build_legacy_runtime_observation_payload()
post_runtime_observations()
mask_observation_secrets()
```

Règles appliquées :

- `task_id` obligatoire ;
- `raw_excerpt` tronqué à 4000 caractères ;
- `schema_version=2` pour le payload v2 ;
- `fingerprint` omis par défaut côté gateway : le serveur Cockpit reste responsable du calcul ;
- v1 exact conservé : `{source, task_id, report, captured_at}` ;
- le runtime observer Telegram émet maintenant avec `prefer_v2=True`.

## Fichiers modifiés

```text
gateway/observation_reporter.py
gateway/repo_cockpit_client.py
gateway/repo_cockpit_telegram_mixin.py
tests/gateway/test_observation_reporter.py
docs/project/PHASE2_OBSERVATION_BUS_REPORT.md
AGENTS.md
```

## Fichiers VPS modifiés

Repo Cockpit :

```text
/home/hermes/repo-cockpit/backend/app.py
/home/hermes/repo-cockpit/backend/runtime_observations.py
/home/hermes/repo-cockpit/backend/secret_masking.py
/home/hermes/repo-cockpit/scripts/operation_worker.py
/home/hermes/repo-cockpit/tests/test_observation_dedup.py
/home/hermes/repo-cockpit/tests/test_observation_schema_compat.py
```

Gateway live monolithe VPS, patch minimal sans déployer la Phase 1 complète :

```text
/home/hermes/.hermes/hermes-agent/gateway/platforms/telegram.py
/home/hermes/.hermes/hermes-agent/gateway/observation_reporter.py
```

Backups créés :

```text
/home/hermes/repo-cockpit/backups/phase2-observations-20260706-200037
/home/hermes/.hermes/hermes-agent/backups/phase2-observation-reporter-20260706-200351
```

## Validation

Hermes gateway local :

```bash
venv/bin/python -m py_compile gateway/observation_reporter.py gateway/repo_cockpit_telegram_mixin.py
scripts/run_tests.sh tests/gateway/test_observation_reporter.py
scripts/run_tests.sh tests/gateway/test_repo_cockpit_client.py
scripts/run_tests.sh tests/gateway/test_telegram_pilot_mode.py
```

Résultat : 26 tests passés.

Repo Cockpit live VPS :

```bash
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python -m py_compile \
  backend/secret_masking.py backend/runtime_observations.py backend/app.py \
  scripts/operation_worker.py tests/test_observation_dedup.py tests/test_observation_schema_compat.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_observation_dedup.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_observation_schema_compat.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/runtime_self_repair_smoke.py
```

Résultat : `PASS test_observation_dedup`, `PASS test_observation_schema_compat`, `runtime self-repair remote smoke OK`.

Smoke endpoint live :

```text
10 erreurs identiques -> 1 observation count=10
payload v2 avec phase/command -> observation attachée
raw_excerpt > 4000 -> HTTP 422
task inexistante -> HTTP 404
```

Services redémarrés et vérifiés :

```text
hermes-repo-cockpit.service active, /health OK
hermes-gateway.service active après restart, aucun log d'erreur récent
```

## Rollback

```bash
git revert <commit-phase2-gateway-observation-reporter>
```

Rollback VPS manuel depuis les backups ci-dessus si nécessaire, puis :

```bash
sudo -u hermes XDG_RUNTIME_DIR=/run/user/$(id -u hermes) systemctl --user restart hermes-repo-cockpit.service
sudo -u hermes XDG_RUNTIME_DIR=/run/user/$(id -u hermes) systemctl --user restart hermes-gateway.service
```
