# Phase 1 — extraction keyboards Repo Cockpit gateway

Date : 2026-07-06
Branche : `codex/ops-update-readiness`

## Objectif

Continuer Phase 1 selon `AUDIT-AUTONOMIE-V2.md` : sortir du monolithe Telegram les builders de claviers Repo Cockpit, sans déplacer les callbacks ni modifier l'UX.

## Changement fait

Création du module :

```text
gateway/repo_cockpit_keyboards.py
```

Il contient maintenant :

```text
new_chat_keyboard()
pilot_existing_intent_keyboard()
repo_button_label()
repo_new_chat_keyboard()
repo_selected_keyboard()
pending_prs_keyboard()
autonomy_keyboard()
```

`TelegramAdapter` garde les méthodes historiques pour limiter le diff :

```text
_new_chat_keyboard()
_pilot_existing_intent_keyboard()
_repo_button_label()
_repo_new_chat_keyboard()
_repo_selected_keyboard()
_pending_prs_keyboard()
_autonomy_keyboard()
```

mais elles délèguent maintenant au module dédié.

## Détail important

Le module injecte `InlineKeyboardButton`, `InlineKeyboardMarkup` et `WebAppInfo` au lieu d'importer directement `python-telegram-bot`.

Raison : le SDK Telegram est optionnel dans l'environnement de test/import ; `gateway/platforms/telegram.py` garde déjà un fallback lazy-install, et l'extraction ne doit pas rendre ce module obligatoire.

## Tests ajoutés

```text
tests/gateway/test_repo_cockpit_keyboards.py
```

Couvre :

- sélection de mode `/new` et callbacks `rcn:*` ;
- routes Pilote existantes ;
- liste repos + WebApp/URL fallback ;
- clavier repo sélectionné ;
- PR pending : liens PR/preview et actions status/runs/résumé ;
- status/runs autonomie avec preview bloquée.

## Vérifications

Tests ciblés :

```text
16 passed in 0.17s
```

Tests Phase 1 larges :

```text
234 passed in 1.84s
```

Compile :

```bash
venv/bin/python -m py_compile \
  gateway/platforms/telegram.py \
  gateway/platforms/telegram_formatting.py \
  gateway/repo_cockpit_client.py \
  gateway/repo_cockpit_formatting.py \
  gateway/repo_cockpit_keyboards.py
```

OK.

## Impact

Avant cette extraction :

```text
gateway/platforms/telegram.py 9509 lignes
```

Après :

```text
gateway/platforms/telegram.py       9420 lignes
gateway/repo_cockpit_keyboards.py    159 lignes
```

## Risques

Faible : extraction de builders purs uniquement.

Points surveillés :

- Les callbacks restent dans `TelegramAdapter`.
- Le module ne dépend pas du SDK Telegram au moment de l'import.
- Pas de sync/restart VPS effectué pour cette Phase 1 partielle.

## Rollback

Avant push :

```bash
git checkout -- gateway/platforms/telegram.py docs/project/README.md docs/project/AUTONOMIE_V2_IMPLEMENTATION_STATUS.md
rm gateway/repo_cockpit_keyboards.py tests/gateway/test_repo_cockpit_keyboards.py docs/project/PHASE1_REPO_COCKPIT_KEYBOARDS_REPORT.md
```

Après commit :

```bash
git revert <commit-phase1-keyboards>
```

## Prochaine étape Phase 1

Prochaine extraction recommandée :

```text
textes purs Repo Cockpit / Pilot intake
```

Cible : sortir `_new_chat_text()`, `_repo_selected_text()`, `_pilot_waiting_prompt_text()` et leurs helpers de texte, en gardant les shims dans `TelegramAdapter`.
