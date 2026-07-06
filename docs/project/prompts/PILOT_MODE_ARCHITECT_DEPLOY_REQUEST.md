# Demande produit — Mode Pilote pour `/new`, `/architect` et `/deploy`

Date: 2026-07-01
Demandeur: MFcv1
Statut: idée produit à cadrer / implémenter plus tard

## Résumé court

Ajouter un **troisième mode de démarrage** dans `/new`, entre `ask_review` et `autopilot` :

```text
Ask Review  |  Pilote  |  Autopilot
```

Le mode **Pilote** serait un mode intermédiaire :

- pas aussi bloquant que `ask_review` ;
- pas aussi aveugle que `autopilot` ;
- il sert à enrichir fortement le contexte au départ ;
- il laisse ensuite l’agent travailler en autonomie une fois les décisions validées.

Objectif : éviter que l’agent parte coder/déployer trop vite sans avoir compris le produit, la stack, les contraintes, l’architecture, les docs/frameworks/providers et les bons choix stratégiques.

## Problème actuel

Aujourd’hui, les modes sont trop binaires :

### `ask_review`

Avantage : sécurisé, l’utilisateur garde la main.

Limite : trop lent si on veut avancer vite après avoir donné le bon contexte.

### `autopilot`

Avantage : rapide, autonome.

Limite : risque de partir avec une mauvaise architecture, mauvais provider, mauvais niveau de complexité ou mauvais setup de départ.

### Besoin utilisateur

Créer un mode qui permette :

1. de recevoir un prompt projet ;
2. d’analyser s’il manque du contexte ;
3. de proposer un cadrage spécialisé ;
4. de poser les bonnes questions au départ ;
5. de choisir entre `architect mode` et `deploy mode` ;
6. puis de laisser l’agent agir en autonomie, sans ask review permanent.

## Proposition : Mode Pilote

Le mode **Pilote** est un mode de conduite guidée.

Il doit fonctionner ainsi :

```text
/new
→ choix projet/repo si nécessaire
→ choix mode: Ask Review / Pilote / Autopilot
→ utilisateur envoie son prompt
→ Hermes analyse le prompt
→ Hermes détecte le besoin principal
→ Hermes propose ou déclenche:
   - Architect Mode
   - Deploy Mode
   - autre mode spécialisé plus tard
→ Hermes pose les questions nécessaires
→ Hermes produit un plan/blueprint/context pack
→ Hermes part en autonomie contrôlée
```

## Différence entre les modes

| Mode | Quand | Comportement |
|---|---|---|
| Ask Review | tâche risquée, besoin de validation fréquente | demande validation avant grosses actions |
| Pilote | utilisateur sait ce qu’il veut globalement mais pas forcément comment structurer | pose beaucoup de questions au départ, crée contexte/structure, puis agit seul |
| Autopilot | tâche claire, contexte suffisant, faible ambiguïté | part directement en autonomie |

## Architect Mode dans Pilote

À utiliser quand le prompt est flou ou stratégique :

- “je veux créer une app/site mais je ne sais pas la stack” ;
- “je veux le meilleur choix infra dès le départ” ;
- “je veux une architecture propre avant de coder” ;
- “je ne sais pas si Astro/Cloudflare, Next/Supabase, Firebase, etc.” ;
- “je veux arborescence + docs `.md` + stratégie.”

### Comportement attendu

Hermes doit charger le contexte/skill :

```text
/architect
project-architect
project-hosting-matrix
cloudflare-astro-platform si Cloudflare/Astro est envisagé
supabase-nextjs-auth si Next/Supabase/Auth est envisagé
skills futurs Vercel/Supabase/Firebase quand ils existeront
```

Il doit ensuite poser des questions structurantes :

- type de produit ;
- cible utilisateur ;
- contenu public vs privé ;
- SEO ;
- auth ;
- dashboard/admin ;
- paiement Stripe ;
- uploads/storage ;
- DB relationnelle ou non ;
- realtime ou non ;
- budget ;
- scalabilité ;
- priorité rapidité vs robustesse ;
- provider déjà configuré ou non.

### Livrable attendu

Après les questions, Hermes produit :

```text
Résumé produit
Stack recommandée
Pourquoi cette stack
Ce qu’on évite pour l’instant
Arborescence projet
Fichiers .md à créer
Plan de build par phases
Risques
Points de bascule futurs
```

Fichiers possibles :

```text
docs/PROJECT_BLUEPRINT.md
docs/ARCHITECTURE.md
docs/TECH_DECISIONS.md
docs/DATA_MODEL.md
docs/DEPLOYMENT.md
docs/SEO.md
docs/SECURITY.md
docs/ROADMAP.md
docs/OPEN_QUESTIONS.md
AGENTS.md
```

Ensuite seulement, il peut passer en autonomie pour créer le projet.

## Deploy Mode dans Pilote

À utiliser quand l’utilisateur a déjà une stack ou un framework assez clair, mais que le déploiement et l’infra doivent être cadrés sérieusement.

Exemples :

```text
Je veux déployer cette app Astro sur Cloudflare.
Je veux mettre mon Next/Supabase en prod.
Je veux choisir entre Cloudflare Pages, Workers, D1, R2, KV.
Je veux un deploy propre avec Stripe, DB, secrets et domaine.
```

### Comportement attendu

Hermes doit charger :

```text
/deploy
project-hosting-matrix
cloudflare-astro-platform si Astro/Cloudflare
skills futurs spécifiques provider
```

Puis poser les questions minimales qui changent le déploiement :

- framework ;
- statique vs SSR ;
- API/runtime ;
- DB ;
- auth ;
- storage/uploads ;
- Stripe/webhooks ;
- domaine ;
- environnement preview/prod ;
- secrets disponibles ;
- provider déjà connecté ;
- budget et contraintes.

### Point important

Même `/deploy` ne doit pas être “juste lancer un deploy”.

Cloudflare, par exemple, peut vouloir dire plusieurs architectures très différentes :

- Astro static Pages ;
- Astro SSR Pages Functions ;
- Workers ;
- D1 ;
- R2 ;
- KV ;
- Durable Objects ;
- Queues ;
- Images ;
- Cache Rules ;
- custom domain ;
- secrets/bindings.

Donc `/deploy` doit aussi questionner si le contexte est incomplet.

## Correction importante sur Firebase

Firebase ne doit pas être présenté comme :

> “utile seulement si le projet dépend déjà beaucoup de Firebase”.

C’est faux si on part de zéro.

Firebase est un **choix d’infrastructure global** possible dès le départ.

Il doit être proposé si les réponses indiquent :

- app mobile-first ;
- realtime / présence / chat ;
- Firebase Auth simple ;
- Firestore adapté au modèle de données ;
- Storage intégré ;
- priorité vitesse de livraison ;
- acceptation du lock-in Firebase/GCP ;
- NoSQL OK.

Il doit être évité si :

- besoin Postgres relationnel sérieux ;
- reporting/requêtes complexes ;
- Stripe/accounting/history très structuré ;
- SEO-first statique ;
- volonté de portabilité provider.

## Limite actuelle des skills déploiement

Actuellement, les skills solides sont surtout :

```text
project-hosting-matrix
cloudflare-astro-platform
supabase-nextjs-auth
```

Mais il manque encore des runbooks de déploiement profonds pour :

```text
vercel-supabase-deploy
firebase-platform-deploy
stripe-production-readiness
nextjs-vercel-production
cloudflare-fullstack-production plus avancé
```

Le mode Pilote doit donc être pensé pour évoluer avec des **stacks préconçues**.

## Idée future : stacks préconçues

Créer une bibliothèque de profils/stacks :

```text
astro-cloudflare-static
astro-cloudflare-fullstack
next-vercel-supabase-saas
next-vercel-stripe-supabase
firebase-realtime-app
firebase-mobile-first
cloudflare-workers-api-d1-r2
landing-seo-minimal
marketplace-stripe-connect
```

Chaque stack devrait contenir :

- quand l’utiliser ;
- quand l’éviter ;
- arborescence ;
- fichiers `.md` ;
- variables d’environnement ;
- provider setup ;
- commandes build/dev/deploy ;
- limites ;
- coûts ;
- sécurité ;
- smoke tests ;
- preuves attendues.

## UX proposée dans Telegram / Repo Cockpit

Après `/new`, ajouter un bouton :

```text
Mode: Ask Review | Pilote | Autopilot
```

Si `Pilote` est choisi :

1. l’utilisateur envoie son prompt ;
2. Hermes répond avec une courte analyse :

```text
J’ai besoin de cadrer avant de coder.
Choisis une direction :
[Architect] [Deploy] [Autre / juste répondre]
```

Ou Hermes peut proposer automatiquement :

```text
Je recommande Architect Mode, car la stack n’est pas encore claire.
```

Puis :

- `Architect` pose beaucoup de questions ;
- `Deploy` pose moins de questions mais vérifie l’infra ;
- après réponse utilisateur, Hermes crée un context pack ;
- ensuite il travaille en autonomie.

## Agent spécialisé docs temps réel

Le mode Pilote devrait permettre à Hermes de se comporter comme un agent spécialisé qui consulte ou charge la bonne connaissance :

- skills internes ;
- docs officielles framework/provider si nécessaire ;
- guides Astro, Cloudflare, Vercel, Supabase, Firebase, Stripe ;
- docs du repo local ;
- fichiers existants ;
- état provider si authentifié.

L’objectif n’est pas juste “conseiller depuis mémoire”, mais **décider avec contexte réel**.

## Critères d’acceptation V1

- `/new` propose `Pilote` à côté de `Ask Review` et `Autopilot`.
- Le choix est persisté dans le contexte de session.
- Après réception du prompt, Hermes détecte si Architect ou Deploy est plus adapté.
- Hermes peut demander confirmation : Architect / Deploy / Continuer normal.
- Architect charge `/architect` ou les skills équivalents.
- Deploy charge `/deploy` ou les skills équivalents.
- En mode Architect, Hermes pose les questions avant de coder.
- En mode Deploy, Hermes pose les questions de déploiement manquantes avant mutation.
- Une fois le contexte complété, Hermes peut agir en autonomie sans ask review permanent.
- Les actions dangereuses restent protégées par les règles de sécurité existantes.

## Risques à surveiller

1. **Trop de friction** : si Pilote pose 25 questions, l’utilisateur va abandonner.
2. **Faux sentiment de sécurité** : autonomie ne veut pas dire absence de preuves.
3. **Confusion Architect vs Deploy** : il faut une UI claire.
4. **Skills incomplets** : Vercel/Supabase/Firebase deploy doivent être renforcés.
5. **Prompt caching/session state** : éviter de changer brutalement le système prompt au milieu d’une conversation.
6. **Telegram UX** : les boutons doivent rester simples et lisibles.
7. **Overengineering** : V1 doit être simple : mode Pilote + choix Architect/Deploy + questions + context pack.

## Mon avis honnête

L’idée est très bonne.

Elle répond à un vrai trou entre :

- “valide tout à chaque étape” ;
- “vas-y fais tout”.

Pour des utilisateurs qui apprennent ou qui ne sont pas sûrs de leur stack, le plus gros gain n’est pas seulement l’autonomie. Le gain, c’est de **ne pas partir dans la mauvaise architecture dès le départ**.

Le mode Pilote peut devenir une vraie force de Hermes/Repo Cockpit : un agent qui ne code pas juste plus vite, mais qui **cadre mieux avant d’agir**.

Ma recommandation :

1. faire une V1 très simple ;
2. ne pas surcharger l’UI ;
3. brancher d’abord `/architect` et `/deploy` ;
4. ensuite créer les stacks préconçues ;
5. seulement après, rendre Deploy vraiment profond pour Vercel/Supabase/Firebase/Stripe.

Priorité V1 :

```text
/new → Pilote → analyse prompt → Architect ou Deploy → questions → context pack → autonomie
```

Pas besoin de résoudre toutes les stacks dès le début.
