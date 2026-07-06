# Phase 1 — extraction formatting Telegram

Date : 2026-07-06 19:46 CEST
Branche : `codex/ops-update-readiness`

## Objectif

Démarrer Phase 1 selon `AUDIT-AUTONOMIE-V2.md` : extraire un premier morceau pur de `gateway/platforms/telegram.py` sans changer l'UX ni le comportement runtime.

## Changement fait

Création du module pur :

```text
gateway/platforms/telegram_formatting.py
```

Il contient maintenant :

```text
escape_mdv2()
strip_mdv2()
wrap_markdown_tables()
format_telegram_markdown()
```

`TelegramAdapter.format_message()` délègue maintenant à :

```python
format_telegram_markdown(content)
```

Compatibilité conservée pour les anciens imports/tests :

```text
_escape_mdv2
_strip_mdv2
_wrap_markdown_tables
```

## Tests ajoutés

```text
tests/gateway/test_telegram_formatting_module.py
```

Ce test caractérise le module extrait directement :

- conversion Markdown → MarkdownV2 ;
- réécriture des tables Markdown pour le chemin legacy ;
- helpers d'échappement/strip conservés.

## Vérifications

Tests ciblés larges :

```text
221 passed in 3.32s
```

Commande :

```bash
python -m pytest \
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
python -m py_compile gateway/platforms/telegram.py gateway/platforms/telegram_formatting.py
```

OK.

## Impact inventaire

Avant Phase 1, inventaire Phase 0 :

```text
gateway/platforms/telegram.py ~10093 lignes, 264 symboles
```

Après extraction :

```text
gateway/platforms/telegram.py            9767 lignes, 258 symboles
gateway/platforms/telegram_formatting.py  241 lignes, 7 symboles
```

## Risques

Faible : extraction de fonctions pures uniquement.

Points surveillés :

- MarkdownV2 Telegram est fragile ; les tests `test_telegram_format.py` et rich messages couvrent les principaux cas.
- Le vrai contrat `OutboundReport` normatif n'est pas encore implémenté ; cette extraction prépare seulement le terrain.
- Pas de sync/restart VPS effectué pour cette Phase 1 partielle.

## Rollback

Rollback local :

```bash
git revert <commit-phase1-formatting>
```

Ou avant commit :

```bash
git checkout -- gateway/platforms/telegram.py
rm gateway/platforms/telegram_formatting.py tests/gateway/test_telegram_formatting_module.py
```

## Prochaine étape Phase 1

Continuer avec une extraction mécanique, une seule responsabilité :

```text
Repo Cockpit HTTP client côté gateway
```

Cible proposée :

```text
gateway/repo_cockpit_client.py
```

Avant déplacement : écrire tests de caractérisation sur les appels HTTP existants Telegram → Repo Cockpit.
