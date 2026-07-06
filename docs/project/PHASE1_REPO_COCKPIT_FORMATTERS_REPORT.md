# Phase 1 — extraction formatters Repo Cockpit gateway

Date : 2026-07-06 20:24 CEST
Branche : `codex/ops-update-readiness`

## Objectif

Continuer Phase 1 selon `AUDIT-AUTONOMIE-V2.md` : sortir du monolithe Telegram les formatters purs des panels Repo Cockpit, sans modifier les callbacks, les endpoints, ni l'UX.

## Changement fait

Création du module pur :

```text
gateway/repo_cockpit_formatting.py
```

Il contient maintenant :

```text
pending_pr_label()
format_pending_prs()
status_badge()
latest_items()
preview_is_blocked()
status_is_problem()
format_pr_summary()
format_autonomy_status()
format_runs_status()
```

`TelegramAdapter` garde les méthodes historiques pour limiter le diff :

```text
_pending_pr_label()
_format_pending_prs()
_format_pr_summary()
_status_badge()
_latest_items()
_format_autonomy_status()
_format_runs_status()
_preview_is_blocked()
_status_is_problem()
```

mais elles délèguent maintenant au module dédié.

## Tests ajoutés

```text
tests/gateway/test_repo_cockpit_formatting.py
```

Couvre :

- label PR pending avec suffixe `task_id` ;
- rendu HTML des PRs en attente ;
- résumé PR : branche, PR URL, preview, smoke, provider checks, runs ;
- status/runs autonomie : badges, preview bloquée, dernière erreur, checks.

## Vérifications

Tests ciblés larges :

```text
228 passed in 3.57s
```

Commande :

```bash
python -m pytest \
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
```

Compile :

```bash
python -m py_compile \
  gateway/platforms/telegram.py \
  gateway/repo_cockpit_formatting.py \
  gateway/repo_cockpit_client.py \
  gateway/platforms/telegram_formatting.py
```

OK.

## Impact

`gateway/platforms/telegram.py` perd environ 255 lignes de logique de présentation pure.

Inventaire courant :

```text
gateway/platforms/telegram.py 9509 lignes, 257 symboles
```

## Risques

Faible : extraction de fonctions pures uniquement.

Points surveillés :

- Les méthodes Telegram restent comme shims pour ne pas toucher aux callbacks.
- Les claviers Telegram ne sont pas encore extraits.
- Pas de sync/restart VPS effectué pour cette Phase 1 partielle.

## Rollback

Avant push :

```bash
git checkout -- gateway/platforms/telegram.py
rm gateway/repo_cockpit_formatting.py tests/gateway/test_repo_cockpit_formatting.py
```

Après commit :

```bash
git revert <commit-phase1-formatters>
```

## Prochaine étape Phase 1

Prochaine extraction recommandée :

```text
builders de keyboards Repo Cockpit
```

Cible : sortir les constructions `InlineKeyboardMarkup` pures/isolables, sans toucher à `_handle_callback_query`.
