# Phase 1 — rapport de fin locale

Date : 2026-07-06
Branche : `codex/ops-update-readiness`

## Résumé

Phase 1 Autonomie V2 est terminée côté code local : `gateway/platforms/telegram.py` est redevenu un orchestrateur mince, sous le seuil de 2k lignes demandé par l'audit.

Ce qui n'a pas été fait : aucun sync VPS, aucun restart de service, aucun changement volontaire d'UX.

## Résultat taille

Avant Phase 1 :

```text
gateway/platforms/telegram.py ~10093 lignes
```

Après Phase 1 :

```text
gateway/platforms/telegram.py 1745 lignes
```

## Modules extraits

```text
gateway/platforms/telegram_formatting.py
gateway/repo_cockpit_client.py
gateway/repo_cockpit_formatting.py
gateway/repo_cockpit_keyboards.py
gateway/repo_cockpit_text.py
gateway/repo_cockpit_telegram_mixin.py
gateway/telegram_conversations_mixin.py
gateway/telegram_inbound_filter_mixin.py
gateway/telegram_model_picker_mixin.py
gateway/telegram_transport_mixin.py
```

## Découpage final

- `telegram.py` : classe adapter, setup général, callback/command dispatch, event build, orchestration restante.
- `telegram_transport_mixin.py` : connect/disconnect, send/edit, rich messages, prompts interactifs, envoi médias.
- `telegram_inbound_filter_mixin.py` : mentions, gating groupe, observation de messages non mentionnés, cache média observé.
- `telegram_model_picker_mixin.py` : picker modèle/provider Telegram.
- `telegram_conversations_mixin.py` : `/libre`, conversations Repo Cockpit, threads, resume.
- `repo_cockpit_telegram_mixin.py` : commandes Repo Cockpit Telegram, status/runs/audit/jobs/watch/worker.
- `repo_cockpit_*` : client HTTP, formatters, keyboards, textes purs.

## Tests / vérifications

Suite Telegram complète + Repo Cockpit helpers :

```text
716 passed in 34.35s
```

Compile :

```bash
venv/bin/python -m py_compile \
  gateway/platforms/telegram.py \
  gateway/telegram_transport_mixin.py \
  gateway/telegram_inbound_filter_mixin.py \
  gateway/telegram_model_picker_mixin.py \
  gateway/telegram_conversations_mixin.py \
  gateway/repo_cockpit_telegram_mixin.py \
  gateway/repo_cockpit_text.py \
  gateway/repo_cockpit_keyboards.py \
  gateway/repo_cockpit_formatting.py \
  gateway/repo_cockpit_client.py \
  plugins/platforms/telegram/adapter.py
```

OK.

## Compat plugin Telegram

La suite complète a révélé une fragilité ordre-dépendante dans `plugins/platforms/telegram/adapter.py` : la normalisation `ChatType` pouvait retomber en `dm` quand les mocks Telegram étaient remplacés par d'autres tests. Le correctif appliqué est limité à la normalisation du type de chat, avec fallback Telegram réaliste : `channel` reste prioritaire, puis chat id négatif => groupe.

## Risques restants

- Le découpage est mécanique mais massif : surveiller les imports optionnels Telegram en runtime réel.
- `plugins/platforms/telegram/adapter.py` reste une copie séparée du gateway Telegram ; une phase ultérieure devrait décider si cette duplication est encore voulue.
- Le VPS n'a pas reçu ces changements. La prochaine validation humaine doit décider sync/restart avec rollback.

## Rollback

Rollback local :

```bash
git revert <commit-phase1-complete>
```

Si déjà synchronisé VPS plus tard : appliquer la procédure Phase 0 de rollback service/backup avant restart.

## Prochaine phase

Phase 2 selon `AUDIT-AUTONOMIE-V2.md` :

```text
Observation bus + contrats
```

Lire d'abord `docs/brain/03-implementation-contracts.md`, section observation payload v2 + fingerprint/dédup + secret masking. Ne pas démarrer le self-repair v2 avant PolicyEngine/snapshots/rollback.
