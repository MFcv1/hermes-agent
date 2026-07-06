# 05 — VPS Self-Ops : auto-monitoring, stockage, scaling

> Objectif : l'agent surveille sa propre infra (VPS 134.122.73.242), gère son
> stockage, et dit PROACTIVEMENT "il va falloir scaler" avant que ça casse.
> Principe : self-ops = task-scoped quand lié à une tâche, et UN heartbeat
> périodique unique pour la santé globale — pas une armée de watchers.

## 1. Architecture : un seul heartbeat

Créer `repo-cockpit/scripts/selfops_heartbeat.py`, lancé par systemd timer
(`hermes-selfops.timer`, toutes les 15 min). Il fait UNE passe :

```
collect() → evaluate_thresholds() → persist(events) → alert_if_needed()
```

- Pas de daemon custom qui tourne en boucle. Un timer systemd = robuste, visible, standard.
- Résultat persisté dans la table `events` (`kind=selfops_sample`).
- Alerte Telegram UNIQUEMENT sur franchissement de seuil (montée), avec cooldown de 6h par métrique — jamais de répétition en boucle.

## 2. Métriques collectées

| Métrique | Source | Warning | Critical |
|---|---|---|---|
| Disque `/` utilisé | `shutil.disk_usage` | 75% | 90% |
| RAM utilisée | `/proc/meminfo` | 80% | 92% |
| Swap utilisé | `/proc/meminfo` | 40% | 70% |
| Load avg 15min / nb CPU | `os.getloadavg()` | 1.5× | 3× |
| Services actifs | `systemctl is-active hermes-gateway hermes-repo-cockpit` | inactive → critical | |
| Taille SQLite Cockpit | stat du fichier | 500 Mo | 2 Go |
| Taille logs journald | `journalctl --disk-usage` | 1 Go | 3 Go |
| Certificats TLS (si applicable) | expiration | J-14 | J-3 |
| Health endpoints | `GET /health` des 2 services | non-200 → critical | |
| Git SHA déployé vs HEAD du repo | `/health.git_sha` vs `git rev-parse` | drift → warning | |

Le dernier point détecte automatiquement le problème "code synchronisé mais service pas redémarré" — plus jamais à l'aveugle.

## 3. Réactions automatiques autorisées (sans approval)

Uniquement des actions non destructives et réversibles :

- `disk warning` → runbook `disk_full_cleanup` niveau 1 : purge des artifacts > 30 jours, `journalctl --vacuum-size=500M`, purge caches pip/npm, rotation des vieux snapshots `repair/*` mergés. Rapport de ce qui a été libéré.
- `service inactive` → 1 seul `systemctl restart` + vérification `/health` + rapport. Si re-crash < 10 min → PAS de restart en boucle, escalade humaine avec extrait de log.
- `sqlite size warning` → `VACUUM` + archivage des `events`/`observations` > 90 jours vers fichier compressé dans `artifacts/archive/`.

Tout le reste (resize, kill de process, modification de config système) → `approval`.

## 4. Recommandations de scaling proactives

Le heartbeat maintient une fenêtre glissante 7 jours (depuis `events`). Règles de recommandation :

```
SI ram_p95_7j > 85%              → "RAM tendue de façon soutenue"
SI disk croît > 2%/jour ET > 60% → "disque plein estimé dans N jours" (extrapolation linéaire)
SI load_p95_7j > 2× nb_cpu       → "CPU sous-dimensionné"
SI swap_p95_7j > 30%             → "swap chronique = RAM insuffisante"
```

Quand une règle matche → message Telegram UNIQUE (cooldown 7 jours par règle) :

```
📈 Recommandation infra
Le disque du VPS sera plein dans ~9 jours (croissance 2.3%/jour, actuellement 68%).
Options: (a) nettoyer X (je peux le faire, ~3 Go récupérables),
(b) upgrade du plan DigitalOcean (+$6/mois pour 2× le disque).
Je ne fais rien sans ton feu vert pour (b). Dis-moi.
```

Le resize lui-même est TOUJOURS `ask_human` (coût + reboot).

## 5. Gestion du stockage de l'agent lui-même

Sources de croissance à gouverner dès le départ :

- **Artifacts** (diffs, logs, test outputs) : rétention 30 jours par défaut, `done` tasks archivées/compressées, `failed`/`blocked` gardées 90 jours (utile au debug).
- **Branches `repair/*`** : supprimées après merge du fix OU après 14 jours si la tâche est close (le snapshot n'a plus de valeur).
- **Handoffs** : gardés indéfiniment (petits, précieux), mais `raw_excerpt` des observations tronqué à l'ingestion (contrat 03 §3).
- **Telemetry `events`** : archivage > 90 jours (cf. §3).

## 6. Intégration `/status`

Le `/status` Telegram inclut une ligne infra :

```
🖥 VPS: disque 68% • RAM 71% • load 0.8 • services ✅ • déployé = HEAD ✅
```

## 7. Fichiers à créer

- `repo-cockpit/scripts/selfops_heartbeat.py` — collecte + seuils + alertes.
- `repo-cockpit/backend/selfops.py` — persistence, fenêtres glissantes, recommandations.
- `repo-cockpit/runbooks/disk_full_cleanup.yaml`, `runbooks/service_down_restart.yaml`.
- `packaging/hermes-selfops.service` + `hermes-selfops.timer` (systemd).
- Tests : `tests/test_selfops_thresholds.py` (table-driven : métriques simulées → alertes attendues), `tests/test_selfops_cooldown.py` (pas de double alerte < 6h), `tests/test_disk_extrapolation.py`.

## Leçons apprises

- (vide — format : `YYYY-MM-DD [task_id] — leçon`)
