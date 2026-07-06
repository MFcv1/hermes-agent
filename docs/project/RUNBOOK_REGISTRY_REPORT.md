# Quick Win 9 — Skill / Runbook Registry

Date : 2026-07-06
Branche : `codex/ops-update-readiness`

## Source normative

- `AUDIT-AUTONOMIE-V2.md`, quick win 9.
- `docs/brain/03-implementation-contracts.md`, section `Runbook YAML`.
- `docs/brain/05-vps-selfops.md`.

## Portée réalisée

Le registry de runbooks est terminé côté Repo Cockpit VPS :

- `backend/runbooks.py` ajouté.
- Validation stricte au chargement :
  - `name` obligatoire et aligné avec le nom de fichier,
  - `version >= 1`,
  - `description` obligatoire,
  - `steps` non vide avec `id` + `run`,
  - `verify` non vide obligatoire,
  - `rollback` non vide obligatoire,
  - `triggers.categories`, `preconditions`, `escalate_if` typés en listes de strings.
- Table SQLite `runbooks` ajoutée avec hash de fichier, version, catégories, skill référencé et payload public.
- Endpoint interne ajouté :
  - `GET /api/internal/runbooks`
- Huit runbooks initiaux ajoutés :
  - `missing_dependency`
  - `port_in_use`
  - `service_down_restart`
  - `env_var_missing`
  - `disk_full_cleanup`
  - `migration_pending`
  - `deploy_node_app`
  - `deploy_python_app`

La phase pose le catalogue et la validation. Elle ne branche pas encore l'exécution automatique dans le worker, afin d'éviter d'ajouter une action dangereuse hors policy explicite.

## Fichiers VPS modifiés

Repo Cockpit :

```text
/home/hermes/repo-cockpit/backend/app.py
/home/hermes/repo-cockpit/backend/runbooks.py
/home/hermes/repo-cockpit/runbooks/*.yaml
/home/hermes/repo-cockpit/tests/test_runbook_schema.py
```

## Backup

```text
/home/hermes/repo-cockpit/backups/runbook-registry-20260706-215948
```

## Validation

Staging puis live Repo Cockpit :

```bash
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python -m py_compile \
  backend/app.py backend/runbooks.py tests/test_runbook_schema.py

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
PASS test_runbook_schema
PASS test_autonomy_status_payload
PASS test_evaluations_store
PASS test_handoff_roundtrip
PASS test_policy_engine
PASS test_runtime_repair_e2e
runtime self-repair remote smoke OK
```

Post-restart live :

```text
Cockpit pid: 368934
cockpit_ok
runbooks_table True
runbooks_rows 8
endpoint True 8
```

## Done

Le scénario cible est couvert :

```text
runbooks/*.yaml
  -> validation stricte
  -> rejet si verify/rollback absent
  -> hash + payload public en SQLite
  -> endpoint interne listable par Cockpit/worker
```

## Limites conservées volontairement

- Le worker ne consomme pas encore automatiquement ces runbooks.
- `env_var_missing` reste explicitement `human_required`.
- Les runbooks de deploy référencent `uses_skill: devops/project-hosting-matrix`, mais ne dupliquent pas les skills agent existants.

## Rollback

Repo Cockpit :

```bash
cp -a /home/hermes/repo-cockpit/backups/runbook-registry-20260706-215948/backend/app.py /home/hermes/repo-cockpit/backend/app.py
rm -f /home/hermes/repo-cockpit/backend/runbooks.py
rm -f /home/hermes/repo-cockpit/tests/test_runbook_schema.py
rm -rf /home/hermes/repo-cockpit/runbooks
```

La table `runbooks` est additive ; la laisser en place ne casse pas les chemins précédents.
