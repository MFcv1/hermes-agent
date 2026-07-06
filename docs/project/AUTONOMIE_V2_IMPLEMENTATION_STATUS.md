# Autonomie V2 — état de reprise

Date : 2026-07-07
Branche : `codex/ops-update-readiness`

## Références à lire dans cet ordre

1. `AUDIT-AUTONOMIE-V2.md` — plan directeur et phases.
2. `docs/brain/00-INDEX.md` — index de la bibliothèque d'implémentation.
3. `docs/brain/03-implementation-contracts.md` — contrats normatifs avant tout nouveau composant.
4. Ce fichier — état réel déjà implémenté et prochain point de reprise.

Ces documents sont la source à suivre pour les prochaines sessions. En cas de conflit entre un rapport de phase ancien et `AUDIT-AUTONOMIE-V2.md`/`docs/brain/03-implementation-contracts.md`, les contrats normatifs gagnent.

## État par phase

| Phase | Statut | Preuves |
|---|---|---|
| Phase 0 — stabilisation/inventaire | Terminé côté code local + déploiement VPS documenté | `PHASE0_COMPLETION_REPORT.md`, `gateway/deployment_info.py`, `scripts/inventory_symbols.py`, `docs/project/autonomie-v2-symbol-inventory.json` |
| Phase 1 — extraction modules gateway | Terminé côté code local, sync VPS non fait | `PHASE1_COMPLETION_REPORT.md` |
| Phase 2 — observation bus + contrats | Terminé côté gateway + backend VPS | `PHASE2_OBSERVATION_BUS_REPORT.md` |
| Phase 3 — worker runtime engine | Terminé côté backend/worker VPS | `PHASE3_WORKER_RUNTIME_ENGINE_REPORT.md` |
| Phase 4 — self-repair v2 | Terminé côté backend/worker VPS | `PHASE4_SELF_REPAIR_V2_REPORT.md` |
| Phase 5 — memory/handoff unifié | Terminé côté backend/gateway VPS | `PHASE5_MEMORY_HANDOFF_STORE_REPORT.md` |
| Phase 6 — eval harness | Terminé côté local + backend/gateway VPS | `PHASE6_EVAL_HARNESS_REPORT.md` |
| Phase 7 — dashboard/admin UX | Terminé côté backend/gateway VPS | `PHASE7_AUTONOMY_STATUS_UX_REPORT.md` |

## Phase 1 déjà faite

- Formatting Telegram Markdown extrait dans `gateway/platforms/telegram_formatting.py`.
- Client HTTP Repo Cockpit extrait dans `gateway/repo_cockpit_client.py`.
- Formatters de panels/status/PR Repo Cockpit extraits dans `gateway/repo_cockpit_formatting.py`.
- Builders de keyboards Repo Cockpit extraits dans `gateway/repo_cockpit_keyboards.py`.
- Textes purs `/new` / Pilote / sélection repo / `/tasks` / audit dry-run extraits dans `gateway/repo_cockpit_text.py`.
- Mixins extraits : `gateway/telegram_transport_mixin.py`, `gateway/telegram_inbound_filter_mixin.py`, `gateway/telegram_model_picker_mixin.py`, `gateway/telegram_conversations_mixin.py`, `gateway/repo_cockpit_telegram_mixin.py`.
- `gateway/platforms/telegram.py` fait maintenant 1745 lignes.

Le pattern actuel est volontairement conservateur :

- les méthodes historiques de `TelegramAdapter` restent comme shims ;
- les callbacks et handlers async ne sont pas déplacés ;
- les nouveaux modules sont purs ou injectent leurs dépendances Telegram ;
- chaque extraction a un test de caractérisation dédié.

## Phase 4 déjà faite

- `policies.yaml` ajouté côté Repo Cockpit avec `feature_flags.self_repair_v2`.
- `backend/policy_engine.py` ajouté : unknown action -> `ask_human`, secrets -> `ask_human`, repair auto seulement en `autopilot` severity <= medium.
- `scripts/worker/self_repair.py` ajouté : replay déterministe, snapshot git `repair/<task>/<attempt>`, budget par task, rollback `git reset --hard` + `git clean -fd`.
- `operation_worker.py` bascule vers v2 si le flag est actif ; l'ancien chemin V1 reste derrière le flag.
- Tests live : policy, e2e repair, rollback on worsen, budget exhausted, secret escalation no repair.

## Phase 5 déjà faite

- Table `handoffs` côté Repo Cockpit + endpoints internes task-scoped.
- `operation_queue.parent_task_id` ajouté pour garder le lineage de reprise.
- `gateway/memory/handoff_store.py` ajouté : cache SQLite avec migration de l'ancien JSON `ActiveWorkStore`.
- `/libre` écrit maintenant un handoff lié au dernier `task_id` Cockpit quand disponible.
- Le classifieur Libre reconnaît l'intention `resume`; la task suivante peut être créée avec `parent_task_id`.
- Tests live : `test_handoff_roundtrip`, gateway smoke, anciens tests Phase 2/3/4.

## Phase 6 déjà faite

- `scripts/run_evals.py` ajouté pour exécuter `routing` ou `repair` avec sortie `text`/`json`.
- `tests/evals/routing_golden.jsonl` contient 55 phrases FR annotées.
- `tests/evals/repair_scenarios/` contient 7 manifests de scénarios self-repair.
- Le classifieur Libre est couvert pour `chat`, `repo_task`, `resume`, `status`, `policy`, `switch_repo`, `deploy`, `debug_fix`, `feature_work`, `audit_repo` et `autopilot`.
- Repo Cockpit expose une table `evaluations` et des endpoints internes batch/list.
- Tests live : routing 55/55, repair 7/7, Cockpit evaluation store, anciens tests Phase 3/4/5.

## Phase 7 déjà faite

- `/api/internal/tasks/{task_id}/autonomy` expose désormais runs, repairs, observations runtime, approvals, evals et lineage.
- `/status` Telegram affiche l'état complet de la task en un message riche.
- `/runs` reste le détail technique.
- Tests live : payload autonomy enrichi, anciens tests Phase 4/5/6, gateway routing/repair evals.

## Point de reprise recommandé

La roadmap principale Autonomie V2 est terminée jusqu'à la Phase 7.

Quick wins réalisés après la Phase 7 :

- Quick win 9 — Skill / Runbook Registry : `RUNBOOK_REGISTRY_REPORT.md`.
- Quick win 11 — Telemetry Store : `TELEMETRY_STORE_REPORT.md`.
- Quick win 12 — Cost Dashboard : `COST_DASHBOARD_REPORT.md`.

Prochaine cible possible :

```text
VPS Self-Ops heartbeat
```

Ordre conseillé :

1. Relire `docs/brain/03-implementation-contracts.md`.
2. Relire `PHASE7_AUTONOMY_STATUS_UX_REPORT.md`, `RUNBOOK_REGISTRY_REPORT.md`, `TELEMETRY_STORE_REPORT.md` et `COST_DASHBOARD_REPORT.md`.
3. Relire `docs/brain/04-cost-engine.md` et `docs/brain/05-vps-selfops.md`.
4. Ajouter un heartbeat VPS explicite qui écrit dans `events(kind=selfops_sample)`.
5. Garder les événements metadata-only ; jamais de secrets ni messages complets.

## À ne pas faire maintenant

- Ne pas synchroniser/restart VPS sans validation humaine explicite, sauf reprise directe d'une phase déjà demandée en live.
- Ne pas ajouter de watcher global : toute observation doit rester attachée à un `task_id`.
- Ne pas étendre le self-repair v2 à des actions externes tant que la policy ne les autorise pas explicitement.
- Ne pas supprimer l'ancien JSON Libre tant que la migration n'a pas été observée sur plusieurs reprises réelles.

## Commandes de vérification Phase 1

```bash
venv/bin/python -m pytest \
  tests/gateway/test_repo_cockpit_keyboards.py \
  tests/gateway/test_repo_cockpit_text.py \
  tests/gateway/test_repo_cockpit_formatting.py \
  tests/gateway/test_repo_cockpit_client.py \
  tests/gateway/test_telegram_formatting_module.py \
  tests/gateway/test_telegram_format.py \
  tests/gateway/test_telegram_rich_messages.py \
  tests/gateway/test_telegram_rich_newlines.py \
  tests/gateway/test_telegram_pilot_mode.py \
  tests/gateway/test_telegram_conv_ux.py \
  tests/gateway/test_libre_orchestrator.py \
  tests/gateway/test_telegram_model_picker.py \
  -q -o 'addopts='

venv/bin/python -m py_compile \
  gateway/platforms/telegram.py \
  gateway/telegram_transport_mixin.py \
  gateway/telegram_inbound_filter_mixin.py \
  gateway/telegram_model_picker_mixin.py \
  gateway/telegram_conversations_mixin.py \
  gateway/repo_cockpit_telegram_mixin.py \
  gateway/platforms/telegram_formatting.py \
  gateway/repo_cockpit_client.py \
  gateway/repo_cockpit_formatting.py \
  gateway/repo_cockpit_keyboards.py \
  gateway/repo_cockpit_text.py
```
