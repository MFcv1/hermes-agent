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

## Portée de cette passe locale

Ce repo contient le gateway Hermes. Le backend Repo Cockpit mentionné par l'audit vit côté VPS (`/home/hermes/repo-cockpit`) et n'est pas présent dans ce checkout local.

Cette passe implémente donc la brique gateway Phase 2 sans prétendre terminer la partie serveur :

- helper gateway `gateway/observation_reporter.py` ;
- payload v2 conforme au contrat ;
- payload v1 conservé pour compatibilité avec le serveur Cockpit actuel ;
- masquage gateway avant émission ;
- tests de contrat côté gateway ;
- intégration du runtime observer Telegram via le helper, en mode v1 par défaut.

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
- `fingerprint` omis par défaut : le serveur Cockpit reste responsable du calcul ;
- v1 exact conservé : `{source, task_id, report, captured_at}` ;
- `prefer_v2=False` par défaut tant que le backend Cockpit local n'est pas migré.

## Fichiers modifiés

```text
gateway/observation_reporter.py
gateway/repo_cockpit_client.py
gateway/repo_cockpit_telegram_mixin.py
tests/gateway/test_observation_reporter.py
docs/project/PHASE2_OBSERVATION_BUS_REPORT.md
AGENTS.md
```

## Reste à faire côté Repo Cockpit

À implémenter dans `/home/hermes/repo-cockpit`, pas dans ce checkout :

```text
backend/runtime_observations.py
dedupe_fingerprint(raw)
endpoint compat v1/v2
table observations avec count/first_seen/last_seen
test_observation_dedup.py
test_observation_schema_compat.py
secret_masking.py à l'ingestion
```

## Validation locale

À lancer après modification :

```bash
scripts/run_tests.sh tests/gateway/test_observation_reporter.py
scripts/run_tests.sh tests/gateway/test_repo_cockpit_client.py
scripts/run_tests.sh tests/gateway/test_telegram_pilot_mode.py
```

## Rollback

```bash
git revert <commit-phase2-gateway-observation-reporter>
```

Pas de rollback VPS nécessaire tant qu'aucun sync/restart n'a été fait.
