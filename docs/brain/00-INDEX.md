# Brain Library — Index

> Bibliothèque d'implémentation du cerveau Hermes Autonomie V2.
> Compagnon de `AUDIT-AUTONOMIE-V2.md` (racine du repo) qui définit l'architecture et les phases.
> Ici : le COMMENT précis — raisonnement, discipline, contrats, coûts, self-ops.

## Comment utiliser cette bibliothèque (instructions pour Hermes)

1. `AUDIT-AUTONOMIE-V2.md` = le plan directeur (phases, composants, règles dures). Toujours le lire d'abord.
2. Avant d'implémenter un composant, lire le fichier brain correspondant ci-dessous.
3. Les contrats de `03-implementation-contracts.md` sont NORMATIFS : toute implémentation qui s'en écarte doit être justifiée et documentée dans le fichier concerné.
4. Cette bibliothèque est vivante : chaque leçon apprise en production doit être ajoutée dans le fichier pertinent (section "Leçons apprises" en bas de chaque doc).

## Fichiers

| Fichier | Contenu | Consommé par |
|---|---|---|
| `01-cognitive-engine.md` | Modes de raisonnement adaptatifs, échelle d'escalade, auto-remise en question, politique d'innovation | Orchestrator, worker |
| `02-git-discipline.md` | Branches, commits, protocole de switch propre, migration, reprise de contexte | Worker, self-repair |
| `03-implementation-contracts.md` | Signatures, schémas JSON/SQL/YAML normatifs (PolicyEngine, observations v2, fingerprint, runbooks, state machine) | Toutes les phases |
| `04-cost-engine.md` | Budgets tokens/€, matrice de sélection de modèle, tracking | Orchestrator, worker, repair |
| `05-vps-selfops.md` | Auto-monitoring, gestion stockage, seuils de scaling, alertes proactives | Cockpit, gateway |
| `06-prompts-and-evals.md` | Prompts structurés (classifieur, diagnostiqueur, self-review) + golden scenarios seed | Orchestrator, evals |

## Règle de maintenance

- Aucun fichier brain > 500 lignes. Si ça déborde, découper.
- Toute modification d'un contrat normatif (03) = bump de version du contrat + note de migration.
- Les leçons apprises sont datées et référencent un `task_id` quand possible.
