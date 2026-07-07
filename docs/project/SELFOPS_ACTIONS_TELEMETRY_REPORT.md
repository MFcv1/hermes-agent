# Quick Win 14 — Self-Ops Actions, Task Analytics, Eval Reports, Cost Guard Enforcement

Date : 2026-07-07
Branche : `codex/ops-update-readiness`

## Source normative

- `docs/brain/05-vps-selfops.md`
- `docs/brain/04-cost-engine.md`
- `docs/project/SELFOPS_TELEMETRY_AUTONOMY_REPORT.md`

## Portée réalisée

Suite directe du heartbeat Self-Ops :

1. **Actions Self-Ops sûres**
   - `backend/selfops.py` exécute maintenant des actions policy-gated :
     - `disk_full_cleanup`
     - `service_down_restart`
     - `sqlite_archive_vacuum`
   - Chaque action écrit `events(kind=selfops_action)`.
   - Cooldown anti-boucle via `selfops_action_state`.
   - `SELFOPS_DRY_RUN=1` permet de tester sans nettoyage/restart.

2. **Cost guard durci**
   - Nouveau module `backend/cost_guard.py`.
   - `GET /api/internal/costs/guard?task_id=...&enforce=1` peut passer une task en `AWAITING_APPROVAL` si le hard stop task est atteint.
   - Le heartbeat envoie les alertes budget via Telegram avec cooldown.
   - `/status` Telegram affiche maintenant la ligne budget si le guard n'est pas OK.

3. **Vue analytics par task**
   - `GET /api/tasks/{task_id}/telemetry`
   - `GET /api/tasks/{task_id}/analysis`
   - La WebApp Cockpit affiche une vue task si l'URL contient `?task_id=...`.
   - La vue montre coût task, appels LLM, evals, synthèse et timeline telemetry.

4. **Rapports eval depuis traces propres**
   - `GET /api/internal/evaluations/report`
   - Agrège pass/fail, coût estimé, modèles, tasks et régressions depuis la table `evaluations`.

5. **Boucle d'analyse historique**
   - `backend.telemetry.persist_active_task_history_summaries(...)`.
   - Le heartbeat écrit périodiquement `events(kind=task_history_summary)` pour les tasks actives.
   - La synthèse reste metadata-only : counts, décisions, repairs, escalades, coût, recommandations.

## Fichiers live modifiés

Repo Cockpit :

```text
/home/hermes/repo-cockpit/backend/app.py
/home/hermes/repo-cockpit/backend/cost_guard.py
/home/hermes/repo-cockpit/backend/evaluations.py
/home/hermes/repo-cockpit/backend/selfops.py
/home/hermes/repo-cockpit/backend/telemetry.py
/home/hermes/repo-cockpit/scripts/selfops_heartbeat.py
/home/hermes/repo-cockpit/policies.yaml
/home/hermes/repo-cockpit/webapp/index.html
/home/hermes/repo-cockpit/tests/test_selfops.py
/home/hermes/repo-cockpit/tests/test_telemetry_store.py
/home/hermes/repo-cockpit/tests/test_evaluations_store.py
```

Gateway :

```text
/home/hermes/.hermes/hermes-agent/gateway/platforms/telegram.py
```

## Commits live

```text
Cockpit: 34f2646 feat(selfops): add safe actions and telemetry reports
Gateway: 8123c79c7 feat(telegram): surface cost guard telemetry summary
```

## Validation

```text
PASS test_selfops
PASS test_telemetry_store
PASS test_evaluations_store
PASS test_autonomy_status_payload
py_compile gateway/platforms/telegram.py OK
```

Smokes live :

```text
GET /api/internal/selfops/status -> 200
GET /api/internal/selfops/actions -> 200
GET /api/internal/evaluations/report -> 200
GET /api/internal/tasks/op_1782918577_c0b80175/telemetry -> summary OK
GET /api/internal/tasks/op_1782918577_c0b80175/analysis -> summary OK
SELFOPS_DRY_RUN=1 scripts/selfops_heartbeat.py -> overall ok, task_summaries=18
Telegram Bot API smoke OK, message_id=763
```

## Backups

```text
/home/hermes/repo-cockpit/backups/nextops-20260707-125955
/home/hermes/gateway-backups/nextops-20260707-125955
```

## Limites conservées

- Le resize infra reste humain.
- Les actions Self-Ops dangereuses restent refusées ou demandent approval.
- Les summaries ne contiennent ni prompt, ni message complet, ni secret.
- Le CUA Driver n'est toujours pas exposé dans ce contexte Codex ; Telegram a été validé via Bot API réel.

## Prochaine cible

Construire une UI plus avancée autour des recommandations :

- liste des recommandations Self-Ops ouvertes ;
- bouton approval pour actions humaines (`scale_infra`, cleanup critique étendu) ;
- graphe coût / modèle / task sur 7 jours ;
- rapport automatique hebdomadaire Telegram.
