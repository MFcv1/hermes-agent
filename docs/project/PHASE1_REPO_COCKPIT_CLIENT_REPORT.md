# Phase 1 — extraction client Repo Cockpit gateway

Date : 2026-07-06 19:57 CEST
Branche : `codex/ops-update-readiness`

## Objectif

Continuer Phase 1 selon `AUDIT-AUTONOMIE-V2.md` : sortir la logique HTTP Repo Cockpit de `gateway/platforms/telegram.py` sans changer les routes, payloads, erreurs, ni l'UX Telegram.

## Changement fait

Création du module :

```text
gateway/repo_cockpit_client.py
```

Il contient :

```text
RepoCockpitClient.api_sync()
cockpit_webapp_url()
```

`TelegramAdapter` garde les shims historiques pour limiter le diff :

```python
_cockpit_api_sync(...)
_repo_cockpit_url(...)
```

mais ils délèguent maintenant au module dédié.

## Comportement conservé

`RepoCockpitClient.api_sync()` conserve les formes existantes :

- base locale : `http://127.0.0.1:8765` ;
- JSON UTF-8 avec `ensure_ascii=False` ;
- `Content-Type: application/json` ;
- `HTTPError` → `{"ok": False, "error_code": ..., "description": ...}` ;
- exceptions réseau/autres → `{"ok": False, "description": ...}`.

`cockpit_webapp_url()` conserve :

- `REPO_COCKPIT_URL` si présent ;
- défaut sslip existant ;
- conservation query existante ;
- params additionnels ;
- cache-buster `v=int(time.time())`.

## Tests ajoutés

```text
tests/gateway/test_repo_cockpit_client.py
```

Couvre :

- POST JSON UTF-8 ;
- shape d'erreur HTTP ;
- construction URL WebApp avec query + cache bust.

## Vérifications

Tests ciblés larges :

```text
224 passed in 3.54s
```

Commande :

```bash
python -m pytest \
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
  gateway/platforms/telegram_formatting.py \
  gateway/repo_cockpit_client.py
```

OK.

## Impact inventaire

Après cette extraction :

```text
gateway/platforms/telegram.py 9744 lignes, 258 symboles
gateway/repo_cockpit_client.py 75 lignes, 2 symboles
```

## Risques

Faible à moyen : le shim limite le changement côté Telegram, mais le module HTTP est central pour Repo Cockpit.

Points surveillés :

- Les nombreux appels `asyncio.to_thread(self._cockpit_api_sync, ...)` restent en place pour éviter un grand diff.
- Le module extrait ne change pas les endpoints ni les timeouts existants.
- Pas de sync/restart VPS effectué pour cette Phase 1 partielle.

## Rollback

Avant push :

```bash
git checkout -- gateway/platforms/telegram.py
rm gateway/repo_cockpit_client.py tests/gateway/test_repo_cockpit_client.py
```

Après commit :

```bash
git revert <commit-phase1-cockpit-client>
```

## Prochaine étape Phase 1

Prochaine extraction recommandée, toujours petite :

```text
formatters de panels Repo Cockpit / status / PR summaries
```

But : continuer à sortir du texte/présentation de `telegram.py` avant de toucher aux flows asynchrones ou callbacks complexes.
