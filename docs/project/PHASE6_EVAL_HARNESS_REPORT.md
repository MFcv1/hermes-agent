# Phase 6 — Eval Harness / Golden Scenarios

Date : 2026-07-06
Branche : `codex/ops-update-readiness`

## Source normative

- `AUDIT-AUTONOMIE-V2.md`, Phase 6.
- `docs/brain/03-implementation-contracts.md`.

## Portée réalisée

Phase 6 est terminée côté Hermes local, Repo Cockpit VPS et gateway Telegram live :

- Suite déterministe `routing` ajoutée avec 55 phrases FR annotées.
- Les scénarios couvrent `chat`, `repo_task`, `resume`, `status`, `policy`, `switch_repo`, `deploy`, `debug_fix`, `feature_work`, `audit_repo` et `autopilot`.
- Runner `scripts/run_evals.py` ajouté avec :
  - `--suite routing|repair`
  - `--report text|json`
  - `--store-sqlite <path>` pour persister les résultats dans une table `evaluations`.
- Suite `repair` ajoutée sous forme de manifests rejouables/manuels pour les classes d'incident self-repair.
- Le classifieur Libre a été durci contre les faux positifs :
  - questions explicatives qui restent en `chat`,
  - `preview` qui ne déclenche plus `review`,
  - `prépare` qui ne matche plus `répare`,
  - `status` séparé des demandes autopilot,
  - `switch_repo` exposé comme intention de `repo_task`,
  - `policy` remplace le vieux nom interne `learn_policy`.
- Repo Cockpit stocke désormais les évaluations dans une table `evaluations`.
- Endpoints internes Cockpit ajoutés :
  - `POST /api/internal/evaluations`
  - `POST /api/internal/evaluations/batch`
  - `GET /api/internal/evaluations`

## Fichiers locaux modifiés

```text
gateway/libre_orchestrator.py
gateway/telegram_conversations_mixin.py
scripts/run_evals.py
tests/evals/routing_golden.jsonl
tests/evals/repair_scenarios/*/scenario.json
tests/evals/test_run_evals.py
tests/gateway/test_libre_orchestrator.py
```

## Fichiers VPS modifiés

Repo Cockpit :

```text
/home/hermes/repo-cockpit/backend/app.py
/home/hermes/repo-cockpit/backend/evaluations.py
/home/hermes/repo-cockpit/scripts/operation_worker.py
/home/hermes/repo-cockpit/tests/test_evaluations_store.py
```

Gateway live :

```text
/home/hermes/.hermes/hermes-agent/gateway/libre_orchestrator.py
/home/hermes/.hermes/hermes-agent/gateway/platforms/telegram.py
/home/hermes/.hermes/hermes-agent/scripts/run_evals.py
/home/hermes/.hermes/hermes-agent/tests/evals/
/home/hermes/.hermes/hermes-agent/tests/gateway/test_libre_orchestrator.py
```

Note : le gateway live est encore sur le monolithe Telegram. Le patch live applique donc seulement l'adaptation minimale dans `gateway/platforms/telegram.py`, tandis que le repo local garde l'implémentation structurée dans `gateway/telegram_conversations_mixin.py`.

## Backups

```text
/home/hermes/repo-cockpit/backups/phase6-eval-harness-20260706-212332
/home/hermes/gateway-backups/phase6-eval-harness-20260706-212557
```

## Validation

Local Hermes :

```bash
venv/bin/python -m py_compile \
  gateway/libre_orchestrator.py gateway/telegram_conversations_mixin.py scripts/run_evals.py

venv/bin/python scripts/run_evals.py --suite routing --report text
venv/bin/python scripts/run_evals.py --suite repair --report text
```

Résultat :

```text
routing: 55/55 passed (score=1.0)
repair: 7/7 passed (score=1.0)
```

Staging puis live Repo Cockpit :

```bash
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python -m py_compile \
  backend/app.py backend/evaluations.py scripts/operation_worker.py tests/test_evaluations_store.py

PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_evaluations_store.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_handoff_roundtrip.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_policy_engine.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/test_runtime_repair_e2e.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/runtime_self_repair_smoke.py
```

Résultat live :

```text
PASS test_evaluations_store
PASS test_handoff_roundtrip
PASS test_policy_engine
PASS test_runtime_repair_e2e
runtime self-repair remote smoke OK
```

Gateway live :

```bash
venv/bin/python -m py_compile gateway/libre_orchestrator.py gateway/platforms/telegram.py scripts/run_evals.py
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
Cockpit pid: 365481
Gateway pid: 366020
cockpit_ok
evaluations_table True
Telegram connected
```

## Done

Le scénario cible est couvert :

```text
message utilisateur Libre
  -> classification déterministe
  -> comparaison expected/actual JSON
  -> sortie text/json exploitable par CI
  -> stockage optionnel dans SQLite ou Cockpit evaluations
```

## Limites conservées volontairement

- La suite `routing` est déterministe et bloquante ; la suite `repair` enregistre les scénarios de référence, mais l'exécution complète reste manuelle/nightly.
- Les golden scenarios vérifient des sous-ensembles JSON attendus plutôt que des snapshots complets.
- La table `evaluations` est additive et interne ; aucune nouvelle surface utilisateur n'est ajoutée en dashboard pendant cette phase.

## Rollback

Repo Cockpit :

```bash
cp -a /home/hermes/repo-cockpit/backups/phase6-eval-harness-20260706-212332/backend/app.py /home/hermes/repo-cockpit/backend/app.py
cp -a /home/hermes/repo-cockpit/backups/phase6-eval-harness-20260706-212332/scripts/operation_worker.py /home/hermes/repo-cockpit/scripts/operation_worker.py
rm -f /home/hermes/repo-cockpit/backend/evaluations.py
rm -f /home/hermes/repo-cockpit/tests/test_evaluations_store.py
```

Gateway :

```bash
cp -a /home/hermes/gateway-backups/phase6-eval-harness-20260706-212557/gateway/libre_orchestrator.py /home/hermes/.hermes/hermes-agent/gateway/libre_orchestrator.py
cp -a /home/hermes/gateway-backups/phase6-eval-harness-20260706-212557/gateway/platforms/telegram.py /home/hermes/.hermes/hermes-agent/gateway/platforms/telegram.py
rm -f /home/hermes/.hermes/hermes-agent/scripts/run_evals.py
rm -rf /home/hermes/.hermes/hermes-agent/tests/evals
```

La migration DB est additive ; laisser `evaluations` en place ne casse pas les chemins précédents.
