# TODO — suites Phase 11 Telemetry Store

Source : `docs/project/TELEMETRY_STORE_REPORT.md`

## Priorité 1 — Dashboard fiable

- [x] Brancher le dashboard Repo Cockpit sur les coûts journaliers.
- [x] Afficher le coût du jour, les appels LLM et le modèle principal.
- [x] Exposer le coût par task dans le payload `/autonomy`.
- [x] Ajouter une vue task qui liste les événements telemetry récents :
  - décisions policy ;
  - actions worker ;
  - appels LLM ;
  - durées ;
  - refs de run/artifacts.
- [x] Afficher le détail coût par modèle/task dans une vue analytique plus complète.
- [x] Garder le dashboard metadata-only : aucune fuite de prompt, message complet,
  token, body brut ou secret.

## Priorité 2 — Evals alimentées par traces propres

- [x] Relier les batches d'evals aux événements `events`.
- [x] Enregistrer pour chaque eval :
  - `task_id` / `run_id` ;
  - type d'eval ;
  - modèle ;
  - résultat ;
  - coût estimé ;
  - refs vers artifacts/logs nettoyés.
- [x] Ajouter une commande ou endpoint qui reconstruit un rapport eval depuis la
  telemetry au lieu de relire les logs bruts.
- [x] Utiliser les traces pour repérer les régressions de routing, repair et policy.

## Priorité 3 — Autonomie basée sur l'historique

- [x] Poser le heartbeat Self-Ops périodique VPS dans `events(kind=selfops_sample)`.
- [x] Exposer les événements telemetry récents par task.
- [x] Construire une synthèse périodique des événements par task :
  - ce qui a marché ;
  - ce qui a échoué ;
  - combien de réparations ont été tentées ;
  - quand Hermes a escaladé à l'humain ;
  - quel runbook ou quelle policy a aidé.
- [x] Ajouter une boucle d'analyse qui produit des recommandations d'amélioration
  sans modifier automatiquement le système.
- [x] Alimenter les futurs choix de policy/runbook avec cet historique, en gardant
  une validation humaine pour tout changement risqué.
- [x] Préparer le lien avec le prochain chantier : VPS Self-Ops heartbeat et cost
  dashboard.

## Priorité 4 — Product Ops Control

- [x] Ajouter une UI des recommandations Self-Ops ouvertes.
- [x] Ajouter un flow approval pour les actions humaines (`scale_infra`, cleanup critique étendu, dépenses).
- [x] Ajouter un graphe coût / modèle / task sur 7 jours.
- [x] Ajouter un rapport Telegram hebdomadaire automatique.
- [x] Synchroniser la décomposition Phase 1 du gateway Telegram sur le VPS live.

Rapport : `docs/project/PRODUCT_OPS_CONTROL_REPORT.md`.

## Demain — Sessions de travail globales + Codex Supervisor Mode

Objectif : créer un système global de sessions de travail, comparable aux
clavardages Codex, pour structurer tous les workflows Hermes/Codex au lieu de
dépendre du fil Telegram unique. `@supervisormode` doit être le premier
consommateur de ce modèle, pas un silo séparé.

### 1. Sessions de travail structurées

- [ ] Créer un store de sessions de travail côté Codex/Hermes :
  - `work_session_id` ;
  - titre lisible ;
  - statut ;
  - type de workflow (`supervisor`, `pilote`, `autopilot`, `ask_review`, `libre`, `debug`, `deploy`, etc.) ;
  - canal d'origine (`codex`, `telegram`, `cockpit`, `cli`) ;
  - repo cible ;
  - provider cible ;
  - `task_id` Cockpit courant ;
  - session Hermes/gateway liée si disponible ;
  - branche GitHub ;
  - PR éventuelle ;
  - URL preview/live ;
  - chemins des briefs, rapports et screenshots CUA.
- [ ] Ajouter une règle : nouvelle mission = nouvelle session de travail, sauf
  reprise explicite d'un `task_id`.
- [ ] Ajouter des commandes/actions de reprise :
  - lister les sessions de travail récentes ;
  - reprendre une session ;
  - clore une session ;
  - rattacher une session à un `task_id` Cockpit trouvé après coup.
- [ ] Permettre de filtrer par repo, provider, statut, workflow, date et canal
  d'origine.
- [ ] Stocker chaque brief envoyé à Hermes ou à un worker comme artefact propre,
  sans dépendre de l'ancien historique Telegram.
- [ ] Produire un rapport final par session, avec liens GitHub/Cockpit/hosting.
- [ ] Brancher `@supervisormode` sur ce store global au lieu de créer une logique
  de session dédiée uniquement au superviseur.

### 2. Automatisation des limites actuelles du superviseur

- [ ] Extraire automatiquement un nouveau `task_id` depuis les réponses Telegram,
  les endpoints Cockpit ou le dernier thread actif.
- [ ] Ajouter une boucle de relance intelligente supervisée :
  - Hermes pose une question ;
  - Hermes bloque sur approval ;
  - Hermes travaille sur le mauvais repo ;
  - Hermes produit des docs mais ne pousse rien sur GitHub ;
  - smoke deploy échoue ;
  - task stagne/timed out.
- [ ] Transformer chaque relance en message Telegram traçable et l'ajouter au
  rapport Markdown/JSON.
- [ ] Ajouter un flow "repo + deploy + URL" piloté par le superviseur :
  - création repo si autorisée ;
  - branche dédiée ;
  - tâche Hermes ;
  - vérification GitHub ;
  - deploy preview Cloudflare/Vercel/Supabase selon provider ;
  - smoke URL ;
  - rapport final.
- [ ] Garder les approvals humaines pour production, DNS, coûts, secrets,
  actions irréversibles et merge vers `main`.

## Contraintes à garder

- Pas de contenu utilisateur complet dans `events`.
- Pas de nouveau core tool pour ça.
- Pas de watcher global non attaché à une task.
- Tout événement exploitable doit rester corrélé à `task_id`, `run_id`,
  `kind`, `source` et timestamp.
