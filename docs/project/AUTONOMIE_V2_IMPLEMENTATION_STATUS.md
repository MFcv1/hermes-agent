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
| Phase 2 — observation bus + contrats | En cours côté gateway, backend Cockpit restant | `PHASE2_OBSERVATION_BUS_REPORT.md` |
| Phase 3 — worker runtime engine | Pas commencé | Aucun `CommandSpan`/state machine v2 extrait côté Cockpit dans ce repo |
| Phase 4 — self-repair v2 | Pas commencé | Ne pas démarrer sans `policy_engine` + snapshot/rollback testés |
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

## Point de reprise recommandé

Continuer Phase 2 côté Repo Cockpit après validation de la brique gateway locale.

Prochaine cible :

```text
Observation bus serveur + contrats v2
```

Ordre conseillé :

1. Relire `docs/brain/03-implementation-contracts.md`.
2. Implémenter `backend/runtime_observations.py` côté Repo Cockpit.
3. Ajouter compat endpoint v1/v2.
4. Implémenter fingerprint/dédup et masquage à l'ingestion.
5. Basculer `gateway/observation_reporter.py` en `prefer_v2=True` seulement quand le serveur est validé.

## À ne pas faire maintenant

- Ne pas synchroniser/restart VPS sans validation humaine explicite.
- Ne pas ajouter de watcher global : toute observation doit rester attachée à un `task_id`.
- Ne pas démarrer le self-repair v2 avant PolicyEngine + snapshots + rollback testés.

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
