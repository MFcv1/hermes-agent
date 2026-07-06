# 01 — Cognitive Engine : raisonnement adaptatif

> Objectif : que l'agent adapte son mode de raisonnement à la difficulté réelle,
> se remette en question, innove quand c'est justifié, et ne traite jamais
> "ce qui a déjà été fait" comme une vérité absolue.

## 1. Les 4 modes de raisonnement

L'agent choisit un mode par tâche/sous-tâche, et peut ESCALADER en cours de route.

| Mode | Quand | Modèle/effort | Comportement |
|---|---|---|---|
| `reflex` | Action triviale, runbook connu, risque nul (typo, dep manquante, restart connu) | Modèle rapide, reasoning low | Exécuter le runbook, vérifier, reporter. Pas de plan. |
| `standard` | Tâche connue, périmètre clair (feature simple, bugfix localisé) | Modèle standard, reasoning medium | Plan court (3-5 étapes), exécuter, tester. |
| `deep` | Ambiguïté, bug non reproductible, refactor, décision d'archi | Meilleur modèle, reasoning high | Explorer AVANT de plan : lire le code, formuler 2-3 hypothèses, les tester, puis plan. |
| `adversarial` | Échec répété (2+), prod touchée, symptômes contradictoires | Meilleur modèle, reasoning xhigh | Remettre en question ses propres hypothèses ET l'existant. Chercher la cause racine, pas le symptôme. |

**Implémentation** : champ `reasoning_mode` sur `runs` (table Cockpit). Le worker le reçoit dans ses args et configure modèle/effort via la matrice de `04-cost-engine.md`.

## 2. Échelle d'escalade automatique

Règles déterministes, implémentées dans `scripts/worker/engine.py` :

```
ESCALADE reflex → standard    si: le runbook échoue à la vérification
ESCALADE standard → deep      si: 1er repair attempt failed, OU tests rouges inattendus,
                                  OU le plan initial est invalidé par une découverte
ESCALADE deep → adversarial   si: 2e repair attempt failed, OU outcome=worsened,
                                  OU 2 hypothèses consécutives réfutées
JAMAIS de désescalade automatique pendant une même tâche.
```

Chaque escalade est loggée en telemetry (`event: reasoning_escalation`) avec la raison — c'est un signal clé pour les evals.

## 3. Boucle d'auto-remise en question (self-review)

À intégrer dans le worker à 3 points de contrôle :

### 3a. Pre-plan review (modes deep/adversarial uniquement)
Avant d'exécuter le plan, le worker se pose (prompt `self_review_preplan` dans `06-prompts-and-evals.md`) :
- Quelle hypothèse implicite fais-je qui pourrait être fausse ?
- Existe-t-il une approche plus simple qui atteint 90% du résultat ?
- Qu'est-ce qui casserait si je me trompe ? Est-ce rollbackable ?

### 3b. Mid-flight checkpoint
Après chaque phase (edit → test → deploy), question unique :
- Les résultats observés confirment-ils le plan ? Si non → STOP, retour en plan, escalade éventuelle.
- Interdiction de "forcer" : si un test échoue 2× pour la même raison, on ne le contourne pas, on remet le diagnostic en question.

### 3c. Post-completion review (avant le rapport utilisateur)
- Ai-je résolu la cause ou le symptôme ?
- Le code laissé est-il meilleur ou pire qu'avant (dette introduite) ?
- Qu'est-ce que je documente dans le handoff pour la reprise ?

**Implémentation** : ces reviews sont des appels LLM courts avec sortie structurée `{verdict: continue|revise|escalate, concerns: []}`. Résultat persisté en artifact `kind=self_review`. Budget : max 3 self-reviews par run (voir `04-cost-engine.md`).

## 4. Politique d'innovation vs convention

Le piège des agents : recopier le pattern existant du repo même quand il est mauvais. Règles :

1. **Par défaut, suivre les conventions du repo** (style, structure, libs). La cohérence > la préférence personnelle.
2. **MAIS challenger quand** : le pattern existant est la cause du bug, ou la tâche demande explicitement une amélioration, ou le pattern viole une règle dure de l'audit (monolithe, config dans .env, action sans gate).
3. **Quand il challenge**, l'agent doit : (a) nommer explicitement ce qu'il fait différemment et pourquoi, dans le rapport ET dans le message de commit ; (b) proposer, pas imposer, si le changement dépasse le scope de la tâche → créer une note `docs/brain/proposals/<date>-<sujet>.md` et la mentionner à l'utilisateur.
4. **Interdiction** de réécrire du code hors-scope "parce que c'est mieux". L'innovation est task-scoped comme tout le reste.

## 5. Critère de choix "le plus performant/qualitatif"

Quand plusieurs approches sont possibles, ordre de priorité NORMATIF :

1. **Sûreté** : rollbackable > non rollbackable.
2. **Vérifiabilité** : testable automatiquement > vérifiable manuellement > invérifiable (à éviter).
3. **Simplicité** : moins de pièces mobiles, moins de dépendances nouvelles.
4. **Réversibilité de la décision** : privilégier ce qui ne ferme pas de portes.
5. **Performance brute** : seulement après les 4 critères ci-dessus, sauf si la perf EST la tâche.

Un choix qui gagne sur la perf mais perd sur la sûreté est un mauvais choix. Point.

## 5bis. Stuck detection (pattern validé par l'état de l'art)

Détection déterministe de boucle, dans `scripts/worker/engine.py` :

- **Même action + même résultat 3× de suite** (hash de la commande + hash du résultat) → stuck.
- **Même fichier édité 4× dans le même run sans test nouveau vert** → stuck.
- **Alternance A→B→A→B** (revert de sa propre édition) → stuck immédiat.

Réaction : STOP de la stratégie courante, escalade de mode cognitif (§2), et si déjà en `adversarial` → rapport honnête + escalade humaine. Jamais une 4e tentative identique.
Pour les tâches longues : utiliser la compression de contexte existante (`trajectory_compressor.py`) plutôt que de laisser le contexte déborder et dégrader le raisonnement.

## 6. Gestion de la difficulté imprévue

Quand l'agent se retrouve bloqué (pas d'hypothèse restante, docs contradictoires) :

1. Reproduire minimalement le problème (réduire au plus petit cas qui échoue).
2. Chercher dans les leçons apprises de cette bibliothèque et les handoffs passés (`handoffs` table).
3. Chercher à l'extérieur si l'outil le permet (docs officielles > issues GitHub > blogposts).
4. Si toujours bloqué après le budget du mode courant : rapport honnête à l'utilisateur — "voilà ce que j'ai essayé, voilà mes 2 hypothèses restantes, voilà ce dont j'ai besoin". JAMAIS de solution inventée pour masquer un blocage.

## Leçons apprises

- (vide — à alimenter en production, format : `YYYY-MM-DD [task_id] — leçon`)
