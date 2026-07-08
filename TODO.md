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

## Contraintes à garder

- Pas de contenu utilisateur complet dans `events`.
- Pas de nouveau core tool pour ça.
- Pas de watcher global non attaché à une task.
- Tout événement exploitable doit rester corrélé à `task_id`, `run_id`,
  `kind`, `source` et timestamp.
