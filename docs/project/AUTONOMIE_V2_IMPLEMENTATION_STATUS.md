# Autonomie V2 — état de reprise

Date : 2026-07-06
Branche : `codex/ops-update-readiness`

## Références à lire dans cet ordre

1. `AUDIT-AUTONOMIE-V2.md` — plan directeur et phases.
2. `docs/brain/00-INDEX.md` — index de la bibliothèque d'implémentation.
3. `docs/brain/03-implementation-contracts.md` — contrats normatifs avant tout nouveau composant.
4. Ce fichier — état réel déjà implémenté et prochain point de reprise.

## État par phase

| Phase | Statut | Preuves |
|---|---|---|
| Phase 0 — stabilisation/inventaire | Terminé côté code local + déploiement VPS documenté | `PHASE0_COMPLETION_REPORT.md`, `gateway/deployment_info.py`, `scripts/inventory_symbols.py`, `docs/project/autonomie-v2-symbol-inventory.json` |
| Phase 1 — extraction modules gateway | En cours | `PHASE1_FORMATTING_EXTRACTION_REPORT.md`, `PHASE1_REPO_COCKPIT_CLIENT_REPORT.md`, `PHASE1_REPO_COCKPIT_FORMATTERS_REPORT.md`, `PHASE1_REPO_COCKPIT_KEYBOARDS_REPORT.md` |
| Phase 2 — observation bus + contrats | Pas commencé | Lire `docs/brain/03-implementation-contracts.md` avant d'écrire le contrat v2 |
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

Le pattern actuel est volontairement conservateur :

- les méthodes historiques de `TelegramAdapter` restent comme shims ;
- les callbacks et handlers async ne sont pas déplacés ;
- les nouveaux modules sont purs ou injectent leurs dépendances Telegram ;
- chaque extraction a un test de caractérisation dédié.

## Point de reprise recommandé

Continuer Phase 1 avec une extraction mécanique, une seule responsabilité à la fois.

Prochaine cible proposée :

```text
textes Repo Cockpit / Pilot intake encore mêlés à TelegramAdapter
```

Ordre conseillé :

1. Extraire les textes purs liés à `/new` / Pilote dans un module dédié (`gateway/repo_cockpit_text.py` ou package équivalent).
2. Garder les méthodes `_new_chat_text()`, `_repo_selected_text()`, `_pilot_waiting_prompt_text()` comme shims.
3. Ajouter les tests de caractérisation avant de déplacer le code.
4. Ne pas déplacer `_handle_callback_query()` ni les flows async tant que les helpers purs ne sont pas sortis.

## À ne pas faire maintenant

- Ne pas démarrer Phase 2 tant que Phase 1 n'a pas réduit davantage `gateway/platforms/telegram.py`.
- Ne pas transformer `gateway/platforms/telegram.py` en package en une seule passe.
- Ne pas ajouter de watcher global : toute observation doit rester attachée à un `task_id`.
- Ne pas synchroniser/restart VPS sans validation humaine explicite.

## Commandes de vérification Phase 1

```bash
venv/bin/python -m pytest \
  tests/gateway/test_repo_cockpit_keyboards.py \
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
  gateway/platforms/telegram_formatting.py \
  gateway/repo_cockpit_client.py \
  gateway/repo_cockpit_formatting.py \
  gateway/repo_cockpit_keyboards.py
```
