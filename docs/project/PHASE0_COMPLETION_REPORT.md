# Phase 0 — rapport de fin partielle prête Phase 1

Date : 2026-07-06
Branche : `codex/ops-update-readiness`
Dernier commit poussé : `cc28c1084eeeaad13a6f714c71b1b4b7b4be95d7`

## Résumé

Phase 0 Autonomie V2 est suffisamment sécurisée pour commencer la Phase 1 après validation humaine.

Fait :

- commit Phase 0 poussé vers origin ;
- `/health` Repo Cockpit expose `git_sha` + `started_at` ;
- Gateway expose les mêmes champs dans le code `api_server` et le process live reçoit `HERMES_GIT_SHA` ;
- inventaire statique des monolithes généré ;
- procédure restart/rollback écrite ;
- services VPS redémarrés avec backup SQLite/fichiers avant restart ;
- runtime status Gateway vérifié : `running`, Telegram `connected`.

## Commits / artefacts locaux

Commit Phase 0 :

```text
cc28c1084e feat(phase0): expose deploy health and inventory symbols
```

Fichiers principaux :

```text
gateway/deployment_info.py
gateway/platforms/api_server.py
scripts/inventory_symbols.py
tests/gateway/test_api_server_deploy_health.py
tests/scripts/test_inventory_symbols.py
docs/project/autonomie-v2-symbol-inventory.json
docs/project/PHASE0_RESTART_PROCEDURE.md
```

## VPS — état de déploiement

Backup principal :

```text
/home/hermes/restart-backups/autonomie-v2-phase0-pre-restart-20260706-160418
```

Services :

```text
hermes-gateway.service: active
hermes-repo-cockpit.service: active
```

Repo Cockpit `/health` :

```json
{
  "ok": true,
  "owner": "MFcv1",
  "db": "/home/hermes/repo-cockpit/data/cockpit.sqlite",
  "git_sha": "eaa0df9b122373bcbac7ddfaea05daed2cbac8f2",
  "started_at": "2026-07-06T16:06:47+00:00"
}
```

Gateway runtime status :

```json
{
  "gateway_state": "running",
  "pid": 352570,
  "exit_reason": null,
  "platforms": {
    "telegram": {
      "state": "connected",
      "error_code": null,
      "error_message": null
    }
  },
  "active_agents": 0
}
```

Gateway systemd env :

```text
HERMES_GIT_SHA=cc28c1084eeeaad13a6f714c71b1b4b7b4be95d7
```

## Traceabilité Repo Cockpit

Découverte Phase 0 : `/home/hermes/repo-cockpit` n’était pas un checkout git.

Action faite : création d’un repo git local de snapshot, avec `.gitignore` excluant :

```text
.env*
data/
backups/
.venv/
workspaces/
runs/
supabase/.temp/
```

SHA snapshot actuel :

```text
eaa0df9b122373bcbac7ddfaea05daed2cbac8f2
```

## Tests / vérifications

Local :

```text
43 passed in 1.37s
```

VPS :

```text
runtime self-repair remote smoke OK
py_compile OK sur Gateway + Repo Cockpit
Repo Cockpit /health OK
Gateway runtime status OK, Telegram connected
```

## Limites restantes

- Le Gateway live Telegram ne publie pas actuellement un endpoint HTTP `:8642/health` parce que la plateforme `api_server` n’est pas activée dans la config VPS. Le code `/health` existe et expose `git_sha`/`started_at` si l’API server est activé ; pour le service live, la preuve utilisée est `gateway.status` + systemd env.
- Le checkout Gateway VPS reste dirty sur `main`. Je n’ai pas reset/rebase pour préserver l’état live. La Phase 1 doit extraire des modules depuis la branche propre locale, puis synchroniser explicitement.
- Repo Cockpit a désormais un git local de snapshot, mais pas encore un remote GitHub propre. À décider plus tard si on veut en faire un vrai repo distant.

## Rollback

Rollback court :

```bash
TAG="autonomie-v2-phase0-pre-restart-20260706-160418"
BACKUP_DIR="/home/hermes/restart-backups/${TAG}"

sudo -iu hermes XDG_RUNTIME_DIR=/run/user/$(id -u hermes) \
  systemctl --user stop hermes-repo-cockpit.service hermes-gateway.service

cp -a "${BACKUP_DIR}/repo-cockpit/cockpit.sqlite" \
  /home/hermes/repo-cockpit/data/cockpit.sqlite

# Restaurer les tarballs source si nécessaire :
# tar -C /home/hermes/repo-cockpit -xzf "${BACKUP_DIR}/repo-cockpit/source-before.tar.gz"
# tar -C /home/hermes/.hermes/hermes-agent -xzf "${BACKUP_DIR}/hermes-agent/gateway-before.tar.gz"

rm -f /home/hermes/.config/systemd/user/hermes-gateway.service.d/phase0-deploy.conf
rm -f /home/hermes/.config/systemd/user/hermes-repo-cockpit.service.d/phase0-deploy.conf

sudo -iu hermes XDG_RUNTIME_DIR=/run/user/$(id -u hermes) systemctl --user daemon-reload
sudo -iu hermes XDG_RUNTIME_DIR=/run/user/$(id -u hermes) \
  systemctl --user start hermes-repo-cockpit.service hermes-gateway.service
```

## Feu vert Phase 1 proposé

Commencer Phase 1 par une extraction minimale, testée, sans grossir les monolithes :

1. extraire uniquement le formatting Telegram `OutboundReport` / rapports ;
2. créer `gateway/platforms/telegram/formatting.py` ou nom équivalent validé par l’audit ;
3. ajouter tests de caractérisation avant déplacement ;
4. aucun changement UX volontaire dans le même commit.
