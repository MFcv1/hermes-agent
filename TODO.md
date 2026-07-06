# TODO — suites Phase 11 Telemetry Store

Source : `docs/project/TELEMETRY_STORE_REPORT.md`

## Priorité 1 — Dashboard fiable

- [x] Brancher le dashboard Repo Cockpit sur les coûts journaliers.
- [x] Afficher le coût du jour, les appels LLM et le modèle principal.
- [x] Exposer le coût par task dans le payload `/autonomy`.
- Ajouter une vue task qui liste les événements telemetry récents :
  - décisions policy ;
  - actions worker ;
  - appels LLM ;
  - durées ;
  - refs de run/artifacts.
- Afficher le détail coût par modèle/task dans une vue analytique plus complète.
- Garder le dashboard metadata-only : aucune fuite de prompt, message complet,
  token, body brut ou secret.

## Priorité 2 — Evals alimentées par traces propres

- Relier les batches d'evals aux événements `events`.
- Enregistrer pour chaque eval :
  - `task_id` / `run_id` ;
  - type d'eval ;
  - modèle ;
  - résultat ;
  - coût estimé ;
  - refs vers artifacts/logs nettoyés.
- Ajouter une commande ou endpoint qui reconstruit un rapport eval depuis la
  telemetry au lieu de relire les logs bruts.
- Utiliser les traces pour repérer les régressions de routing, repair et policy.

## Priorité 3 — Autonomie basée sur l'historique

- Construire une synthèse périodique des événements par task :
  - ce qui a marché ;
  - ce qui a échoué ;
  - combien de réparations ont été tentées ;
  - quand Hermes a escaladé à l'humain ;
  - quel runbook ou quelle policy a aidé.
- Ajouter une boucle d'analyse qui produit des recommandations d'amélioration
  sans modifier automatiquement le système.
- Alimenter les futurs choix de policy/runbook avec cet historique, en gardant
  une validation humaine pour tout changement risqué.
- Préparer le lien avec le prochain chantier : VPS Self-Ops heartbeat et cost
  dashboard.

## Contraintes à garder

- Pas de contenu utilisateur complet dans `events`.
- Pas de nouveau core tool pour ça.
- Pas de watcher global non attaché à une task.
- Tout événement exploitable doit rester corrélé à `task_id`, `run_id`,
  `kind`, `source` et timestamp.
