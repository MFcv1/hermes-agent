# Autonomie V2 — état de reprise

Date : 2026-07-06
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
| Phase 5 — memory/handoff unifié | Pas commencé | `ActiveWorkStore` JSON existe encore |
| Phase 6 — eval harness | Pas commencé | À créer après extraction orchestrator/classifier |
| Phase 7 — dashboard/admin UX | Pas commencé | À repousser après les fondations |

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

## Point de reprise recommandé

Passer à la Phase 5 après validation humaine du résultat Phase 4.

Prochaine cible :

```text
Memory / handoff unifié
```

Ordre conseillé :

1. Relire `docs/brain/03-implementation-contracts.md`.
2. Relire `PHASE2_OBSERVATION_BUS_REPORT.md`, `PHASE3_WORKER_RUNTIME_ENGINE_REPORT.md` et `PHASE4_SELF_REPAIR_V2_REPORT.md`.
3. Remplacer `ActiveWorkStore` JSON par une source durable unifiée.
4. Brancher les handoffs worker/gateway sur cette source.
5. Garder la compatibilité lecture avec les anciens fichiers JSON jusqu'à migration complète.

## À ne pas faire maintenant

- Ne pas synchroniser/restart VPS sans validation humaine explicite, sauf reprise directe d'une phase déjà demandée en live.
- Ne pas ajouter de watcher global : toute observation doit rester attachée à un `task_id`.
- Ne pas étendre le self-repair v2 à des actions externes tant que la policy ne les autorise pas explicitement.

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
