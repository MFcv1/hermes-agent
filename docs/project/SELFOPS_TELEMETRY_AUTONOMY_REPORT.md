# Quick Win 13 — Self-Ops, Telemetry Timeline, Eval Events, Cost Guard

Date : 2026-07-07
Branche : `codex/ops-update-readiness`

## Source normative

- `docs/brain/05-vps-selfops.md`
- `docs/brain/04-cost-engine.md`
- `docs/project/TELEMETRY_STORE_REPORT.md`
- `docs/project/COST_DASHBOARD_REPORT.md`

## Portée réalisée

Quatre suites post-telemetry ont été implémentées ensemble côté VPS :

1. **VPS Self-Ops heartbeat**
   - `backend/selfops.py` ajouté.
   - `scripts/selfops_heartbeat.py` ajouté : one-shot `collect -> evaluate -> persist -> alert`.
   - `packaging/hermes-selfops.service` et `packaging/hermes-selfops.timer` ajoutés.
   - Timer systemd utilisateur activé toutes les 15 min.
   - Persistence dans `events(kind=selfops_sample)`.
   - Cooldown d'alerte par métrique via `selfops_alert_state`.

2. **Vue telemetry par task**
   - `GET /api/internal/tasks/{task_id}/telemetry`
   - `GET /api/tasks/{task_id}/telemetry`
   - `/api/internal/tasks/{task_id}/autonomy` inclut maintenant :
     - `telemetry_events`
     - `selfops`
     - `cost_guard`

3. **Evals alimentées par telemetry**
   - Table `evaluations` migrée avec :
     - `task_id`
     - `run_id`
     - `model`
     - `cost_usd_estimated`
   - Chaque record eval interne écrit aussi `events(kind=evaluation)`.

4. **Cost guard / budgets**
   - `policies.yaml` contient maintenant :
     - `soft_cost_alert_per_task_usd`
     - `hard_cost_stop_per_task_usd`
     - `daily_soft_alert_usd`
     - `daily_hard_stop_usd`
   - `GET /api/internal/costs/guard`
   - La création de task refuse un nouveau lancement si le hard stop journalier est atteint.

## Fichiers modifiés

Repo Cockpit live :

```text
/home/hermes/repo-cockpit/backend/app.py
/home/hermes/repo-cockpit/backend/evaluations.py
/home/hermes/repo-cockpit/backend/selfops.py
/home/hermes/repo-cockpit/backend/telemetry.py
/home/hermes/repo-cockpit/scripts/selfops_heartbeat.py
/home/hermes/repo-cockpit/packaging/hermes-selfops.service
/home/hermes/repo-cockpit/packaging/hermes-selfops.timer
/home/hermes/repo-cockpit/policies.yaml
/home/hermes/repo-cockpit/tests/test_selfops.py
/home/hermes/repo-cockpit/tests/test_telemetry_store.py
/home/hermes/repo-cockpit/tests/test_evaluations_store.py
/home/hermes/repo-cockpit/tests/test_autonomy_status_payload.py
```

Gateway live :

```text
/home/hermes/.hermes/hermes-agent/gateway/platforms/telegram.py
```

## Backups

```text
/home/hermes/repo-cockpit/backups/selfops-suite-20260706-232107
/home/hermes/gateway-backups/selfops-suite-20260706-232107
```

## Validation

Staging puis live :

```text
PASS test_selfops
PASS test_telemetry_store
PASS test_evaluations_store
PASS test_autonomy_status_payload
py_compile gateway/platforms/telegram.py OK
```

Heartbeat live :

```text
{"ok": true, "overall": "ok", "event_id": "evt_0b780101d568491d82eeb04be198497a"}
selfops_summary_keys_ok 12
```

Endpoints live :

```text
GET /api/internal/selfops/status -> 200
GET /api/internal/costs/guard?task_id=op_1782918577_c0b80175 -> status ok
GET /api/internal/tasks/op_1782918577_c0b80175/telemetry -> 200
GET /api/internal/tasks/op_1782918577_c0b80175/autonomy -> selfops + telemetry_events + cost_guard OK
```

Timer live :

```text
hermes-selfops.timer active
NEXT Mon 2026-07-06 23:37:31 UTC
```

Telegram live :

```text
Gateway Telegram reconnected
Bot API smoke OK, message_id=761
```

Note : l'outil CUA Driver n'est pas exposé dans ce contexte Codex. Le test
Telegram a donc été réalisé par le chemin réel Bot API + gateway live, sans
inspection visuelle CUA.

## Done

Le scénario cible est couvert :

```text
selfops timer
  -> events(kind=selfops_sample)
  -> /api/internal/selfops/status
  -> /status Telegram VPS line

eval records
  -> evaluations table with task/run/model/cost
  -> events(kind=evaluation)

task telemetry
  -> /api/tasks/{task_id}/telemetry
  -> /autonomy telemetry_events

cost guard
  -> policies.yaml budgets
  -> /api/internal/costs/guard
  -> hard stop before new task creation
```

## Limites conservées volontairement

- Les actions automatiques de nettoyage/restart restent non déclenchées ; cette phase observe, alerte et expose.
- Les alertes Telegram ne sont envoyées que sur seuil non-OK avec cooldown.
- Le resize/scaling reste explicitement humain.
- Le timer systemd est installé côté VPS live ; le code reste one-shot et testable.

## Rollback

Repo Cockpit :

```bash
cp -a /home/hermes/repo-cockpit/backups/selfops-suite-20260706-232107/backend/app.py /home/hermes/repo-cockpit/backend/app.py
cp -a /home/hermes/repo-cockpit/backups/selfops-suite-20260706-232107/backend/evaluations.py /home/hermes/repo-cockpit/backend/evaluations.py
cp -a /home/hermes/repo-cockpit/backups/selfops-suite-20260706-232107/backend/telemetry.py /home/hermes/repo-cockpit/backend/telemetry.py
cp -a /home/hermes/repo-cockpit/backups/selfops-suite-20260706-232107/policies.yaml /home/hermes/repo-cockpit/policies.yaml
rm -f /home/hermes/repo-cockpit/backend/selfops.py
rm -f /home/hermes/repo-cockpit/scripts/selfops_heartbeat.py
rm -f /home/hermes/repo-cockpit/tests/test_selfops.py
rm -f /home/hermes/.config/systemd/user/hermes-selfops.service
rm -f /home/hermes/.config/systemd/user/hermes-selfops.timer
su -s /bin/bash hermes -c 'XDG_RUNTIME_DIR=/run/user/$(id -u hermes) systemctl --user daemon-reload'
```

Gateway :

```bash
cp -a /home/hermes/gateway-backups/selfops-suite-20260706-232107/gateway/platforms/telegram.py /home/hermes/.hermes/hermes-agent/gateway/platforms/telegram.py
```

Les tables/colonnes ajoutées sont additives.
