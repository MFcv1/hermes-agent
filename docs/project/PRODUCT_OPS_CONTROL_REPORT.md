# Product Ops Control — recommandations, approvals, coûts 7j, reporting

Date : 2026-07-08

## Objectif

Ajouter la couche de pilotage produit au-dessus du socle Autonomie V2 déjà posé :

- voir les recommandations Self-Ops ouvertes dans Cockpit ;
- demander et trancher une approval humaine pour les actions risquées ;
- lire les coûts LLM sur 7 jours par jour, modèle et task ;
- envoyer un rapport Telegram hebdomadaire ;
- terminer la dette Phase 1 en synchronisant le gateway Telegram modulaire sur le VPS.

## Livré côté Repo Cockpit

- `backend/ops_control.py` ajouté :
  - `selfops_recommendations()` ;
  - `create_ops_approval()` ;
  - `list_ops_approvals()` ;
  - `decide_ops_approval()` ;
  - `weekly_ops_report()` ;
  - `format_weekly_ops_report()`.
- `backend/telemetry.py` expose `cost_timeseries(days=7)` avec :
  - séries journalières ;
  - agrégats `by_model` ;
  - agrégats `by_task` ;
  - anomalies simples si un jour dépasse 2x la moyenne de fenêtre.
- `backend/app.py` expose les endpoints internes et webapp :
  - `/api/internal/selfops/recommendations` et `/api/selfops/recommendations` ;
  - `/api/internal/ops/approvals` et `/api/ops/approvals` ;
  - `/api/internal/ops/approvals/{approval_id}/decide` et version publique ;
  - `/api/internal/costs/timeseries` et `/api/costs/timeseries` ;
  - `/api/internal/ops/weekly-report` et `/api/ops/weekly-report`.
- `webapp/index.html` ajoute la vue `?view=ops` :
  - résumé santé/coût/tasks/evals ;
  - graphe coût 7 jours ;
  - recommandations Self-Ops ouvertes ;
  - approvals ops avec `Approve` / `Deny`.
- `scripts/weekly_ops_report.py` envoie le rapport Telegram hebdo.
- `packaging/hermes-weekly-ops.service` et `packaging/hermes-weekly-ops.timer` ajoutés.

## Flow approval humain

Les approvals ops restent volontairement metadata-only et non exécutantes :

- `scale_infra` ;
- `spend_money` ;
- `budget_override` ;
- `selfops_critical_cleanup` ;
- `restart_service` ;
- fallback `non_reversible_action`.

Une approval approuvée enregistre la décision humaine dans `events(kind=approval_decision)`.
Elle ne déclenche pas encore automatiquement un resize, une dépense ou une action irréversible.
C'est le bon niveau pour cette phase : Cockpit sait demander et tracer la validation, sans ouvrir
un chemin dangereux d'exécution automatique.

## Déploiement VPS

Commit Cockpit live :

```text
cd690f7 feat(ops): add selfops approvals and weekly reporting
```

Timer live :

```text
hermes-weekly-ops.timer active
NEXT Sun 2026-07-12 18:04:08 UTC
```

Health vérifiée :

- `hermes-repo-cockpit.service` active ;
- `/health` OK ;
- `/api/internal/selfops/recommendations` OK ;
- `/api/internal/costs/timeseries?days=7` OK ;
- `/api/internal/ops/approvals?limit=3` OK ;
- `/api/internal/ops/weekly-report?formatted=1` OK.

Tests live :

```text
PASS test_telemetry_store
PASS test_selfops
PASS test_ops_control
```

Dry-run rapport hebdo :

```text
Hermes weekly ops
Santé VPS: ok
Coûts 7j: $0.00
Tasks: 18 total
```

## Dette Phase 1 synchronisée

La version modulaire locale du gateway Telegram a été synchronisée sur le VPS.

Avant :

```text
gateway/platforms/telegram.py : 10329 lignes
```

Après :

```text
gateway/platforms/telegram.py : 1745 lignes
```

Modules synchronisés :

- `gateway/platforms/telegram_formatting.py` ;
- `gateway/repo_cockpit_client.py` ;
- `gateway/repo_cockpit_formatting.py` ;
- `gateway/repo_cockpit_keyboards.py` ;
- `gateway/repo_cockpit_telegram_mixin.py` ;
- `gateway/repo_cockpit_text.py` ;
- `gateway/telegram_conversations_mixin.py` ;
- `gateway/telegram_inbound_filter_mixin.py` ;
- `gateway/telegram_model_picker_mixin.py` ;
- `gateway/telegram_transport_mixin.py`.

Commit gateway live :

```text
5d1589848 refactor(telegram): finish modular gateway phase1 sync
```

Vérifications gateway :

- `py_compile` sur `telegram.py` et tous les modules extraits ;
- import `TelegramAdapter` OK ;
- `hermes-gateway.service` active après restart ;
- Bot API `getMe` OK sur `Hermes_Matthis_bot` ;
- smoke Telegram réel envoyé, `message_id=765`.

## Garde-fous

- Les événements restent metadata-only : pas de prompt/message complet, pas de token, pas de body brut.
- Les approvals humaines ne déclenchent pas d'action irréversible automatiquement.
- Aucun nouveau core tool Hermes n'a été ajouté.
- Les changements live ont été commités explicitement par périmètre, sans nettoyer les autres fichiers sales du VPS.
