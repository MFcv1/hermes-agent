# Phase 0 — procédure de restart VPS (à valider AVANT exécution)

Objectif : rendre actifs les changements Phase 0 après preuves, avec rollback clair.

⚠️ Ne pas exécuter sans validation humaine explicite.

## État préparé

- Branche Hermes poussée : `codex/ops-update-readiness`
- Dernier SHA attendu côté Hermes : `1053e3792d6027c617d5fd5fef4b06c175244ba6`
- Repo Cockpit modifié sur VPS : `/home/hermes/repo-cockpit`
- Gateway modifié sur VPS : `/home/hermes/.hermes/hermes-agent`
- Services concernés :
  - `hermes-repo-cockpit.service`
  - `hermes-gateway.service`

## Commandes prévues

```bash
set -euo pipefail
TS="$(date +%Y%m%d-%H%M%S)"
TAG="autonomie-v2-phase0-pre-restart-${TS}"
BACKUP_DIR="/home/hermes/restart-backups/${TAG}"

# 1) Préflight services
sudo -iu hermes XDG_RUNTIME_DIR=/run/user/$(id -u hermes) \
  systemctl --user --no-pager --plain status hermes-gateway.service hermes-repo-cockpit.service

# 2) Tag git Hermes AVANT restart (rollback code gateway)
cd /home/hermes/.hermes/hermes-agent
git status --short
git tag "${TAG}"

# 3) Backup fichiers + SQLite Repo Cockpit AVANT restart
mkdir -p "${BACKUP_DIR}/repo-cockpit" "${BACKUP_DIR}/hermes-agent"
cp -a /home/hermes/repo-cockpit/data/cockpit.sqlite "${BACKUP_DIR}/repo-cockpit/cockpit.sqlite"
cp -a /home/hermes/repo-cockpit/backend "${BACKUP_DIR}/repo-cockpit/backend"
cp -a /home/hermes/repo-cockpit/scripts "${BACKUP_DIR}/repo-cockpit/scripts"
cp -a /home/hermes/.hermes/hermes-agent/gateway "${BACKUP_DIR}/hermes-agent/gateway"

# 4) Compile avant restart
cd /home/hermes/repo-cockpit
.venv/bin/python -m py_compile backend/app.py backend/deployment_info.py scripts/operation_worker.py tests/test_runtime_self_repair.py
PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/runtime_self_repair_smoke.py

cd /home/hermes/.hermes/hermes-agent
python3 -m py_compile gateway/deployment_info.py gateway/platforms/api_server.py gateway/platforms/telegram.py gateway/libre_orchestrator.py scripts/inventory_symbols.py

# 5) Restart user units
sudo -iu hermes XDG_RUNTIME_DIR=/run/user/$(id -u hermes) \
  systemctl --user restart hermes-repo-cockpit.service hermes-gateway.service

# 6) Vérification health Repo Cockpit
curl -fsS http://127.0.0.1:8765/health

# 7) Vérification logs courts
sudo -iu hermes XDG_RUNTIME_DIR=/run/user/$(id -u hermes) \
  systemctl --user --no-pager --plain status hermes-gateway.service hermes-repo-cockpit.service

# 8) Smoke Telegram/API si validation donnée séparément
# - Repo Cockpit : création tâche → run
# - Telegram Desktop CUA : seulement si chat cible ouvert et envoi validé
```

## Rollback prévu

```bash
set -euo pipefail
TAG="<tag utilisé>"
BACKUP_DIR="/home/hermes/restart-backups/${TAG}"

sudo -iu hermes XDG_RUNTIME_DIR=/run/user/$(id -u hermes) \
  systemctl --user stop hermes-repo-cockpit.service hermes-gateway.service

cp -a "${BACKUP_DIR}/repo-cockpit/cockpit.sqlite" /home/hermes/repo-cockpit/data/cockpit.sqlite
cp -a "${BACKUP_DIR}/repo-cockpit/backend" /home/hermes/repo-cockpit/
cp -a "${BACKUP_DIR}/repo-cockpit/scripts" /home/hermes/repo-cockpit/

cd /home/hermes/.hermes/hermes-agent
git checkout "${TAG}"
cp -a "${BACKUP_DIR}/hermes-agent/gateway" /home/hermes/.hermes/hermes-agent/

sudo -iu hermes XDG_RUNTIME_DIR=/run/user/$(id -u hermes) \
  systemctl --user start hermes-repo-cockpit.service hermes-gateway.service

curl -fsS http://127.0.0.1:8765/health
```

## Validation demandée

À valider séparément :

1. créer le tag git ;
2. backup SQLite + fichiers ;
3. restart `hermes-repo-cockpit.service` ;
4. restart `hermes-gateway.service` ;
5. smoke Telegram live si nécessaire.
