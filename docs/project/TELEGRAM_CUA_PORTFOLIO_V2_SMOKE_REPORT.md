# Test supervise Hermes + Portfolio V2

Date: 2026-07-08

## Resume

Le test supervise Telegram/CUA a ete execute de bout en bout sur le live Hermes,
avec creation d'un repo prive Portfolio V2 de test et lancement d'une task
Cockpit en mode Pilote.

Verdict court:
- CUA peut capturer et envoyer les commandes Telegram.
- `/new`, `/new pilote`, `/libre`, `/tasks` et `/status` repondent apres la
  refonte gateway.
- `/libre` cree bien une task, mais ne change pas de repo depuis un nom de repo
  mentionne en langage naturel: il a reutilise le projet actif precedent.
- `/new scratch` a correctement cree le repo prive
  `MFcv1/portfolio-v2-hermes-test`.
- Le worker a produit un plan d'architecture GPT-5.5 et s'est arrete en
  `pilot_questions_required`, sans scaffold, deploy, merge, ni modification
  non validee.
- Le plan recommande objectivement Astro + Cloudflare pour une V1 portfolio
  SEO/statique/hybride, et garde Next.js + Cloudflare/OpenNext comme meilleur
  choix si le projet devient une app produit avec auth, espace client,
  paiements ou logique serveur forte.

## Perimetre

Source design/code:
- `MFcv1/Portfolio`
- statut: read-only pendant le test
- stack observee: Vite, React, React Router, GSAP, Lenis, Tailwind, Firebase
  Hosting

Repo cible cree:
- `MFcv1/portfolio-v2-hermes-test`
- URL: `https://github.com/MFcv1/portfolio-v2-hermes-test`
- visibilite: prive
- branche par defaut: `main`
- contenu final verifie: `README.md` uniquement
- commit bootstrap: `71e2108 chore: bootstrap supervised portfolio v2 test repo`

## Preflight

Services live verifies actifs:
- `hermes-gateway.service`
- `hermes-repo-cockpit.service`
- `hermes-weekly-ops.timer`

Endpoints Cockpit verifies:
- `/health`
- `/api/internal/selfops/recommendations`
- `/api/internal/ops/weekly-report?formatted=1`

GitHub:
- lecture `MFcv1/Portfolio` OK
- creation repo prive V2 OK via Hermes `/new scratch`

Cloudflare:
- aucune action effectuee
- aucun deploy
- aucune depense

## Smoke Telegram/CUA

Evidence principale sous `~/.hermes/telegram-gui-smoke/`:

| Commande | Resultat | Evidence |
|---|---|---|
| `/version` | reponse Telegram capturee | `20260708T160305Z-version.jpg` |
| `/new` | panneau nouveau chat Hermes OK | `20260708T160335Z-new.jpg` |
| `/new pilote` | mode Pilote et boutons OK | `20260708T160358Z-new-pilote.jpg` |
| `/libre` | mode libre active | `20260708T160420Z-libre.jpg` |
| `/libre watch` | status attention + timeout Telegram recent affiche | `20260708T160442Z-libre-watch.jpg` |
| `/tasks` | aucune task au depart | `20260708T160505Z-tasks.jpg` |
| `/status op_1783527433_cf7413d8` | status autonomie final capture | `20260708T163623Z-status-op-1783527433-cf7413d8.jpg` |

Note: le helper doit etre lance avec `venv/bin/python`, pas `python3`, sinon le
module `mcp` peut manquer dans l'environnement macOS systeme.

## Tasks Cockpit

| Task | Repo | Resultat |
|---|---|---|
| `op_1783526789_6831afbf` | `MFcv1/hermes-tennis-autopilot-smoke-20260623-171356` | creee par `/libre`, puis annulee car mauvais repo |
| `op_1783527098_77623930` | `MFcv1/portfolio-v2-hermes-test` | bloquee sur repo GitHub vide sans commit, puis annulee |
| `op_1783527433_cf7413d8` | `MFcv1/portfolio-v2-hermes-test` | plan GPT-5.5 complete, statut final `pilot_questions_required` |

Etat final visible dans Telegram pour `op_1783527433_cf7413d8`:
- status: `pilot_questions_required`
- phase: `plan_ready`
- mode: `pilote`
- runs: `1 completed`
- repairs: `0`
- observations runtime: `3`
- approvals: `0`

## Ce que Hermes a produit

La phase plan GPT-5.5 a repondu avec:
- comparaison Astro + Cloudflare vs Next.js + Cloudflare/OpenNext
- inventaire des contraintes design/animations du portfolio source
- recommandation stack
- gates avant ecriture documentaire
- risques et blocages
- criteres de validation

Decision proposee par Hermes:
- Astro + Cloudflare pour la V1 portfolio publique, SEO, rapide, peu couteuse.
- Next.js + Cloudflare/OpenNext a garder si Portfolio V2 devient une vraie app
  produit avec auth, espace client, paiements, API ou logique serveur importante.

Point important: Hermes n'a pas ecrit `ARCHITECTURE.md` ni `MIGRATION_PLAN.md`
parce que le mode Pilote a demande une validation humaine explicite avant la
phase WRITE:

```text
OK pour créer ARCHITECTURE.md et MIGRATION_PLAN.md dans le repo cible.
Stack recommandée acceptée: Astro + Cloudflare.
Pas de scaffold app.
Pas de deploy.
Pas de commit/push sans demande explicite.
```

Ce comportement respecte le mode supervise safe.

## Findings

### 1. `/new` et `/new pilote` fonctionnent

Les panneaux Telegram sont rendus correctement apres la refonte gateway. Le mode
Pilote affiche bien le cadrage, les boutons projet existant / scratch, le modele
et le niveau de reflexion.

### 2. `/libre` route bien, mais ne selectionne pas le repo depuis le texte

La demande naturelle mentionnait `MFcv1/Portfolio`, mais Hermes a attache la
task au repo actif precedent. Le router libre doit soit:
- detecter explicitement un repo mentionne dans le texte,
- soit demander confirmation avant d'utiliser le projet actif quand le texte
  cite un repo different.

### 3. Worker fragile sur repo vide

Le premier run sur un repo GitHub sans commit a echoue:

```text
fatal: 'HEAD' is not a commit and a branch 'hermes/...' cannot be created from it
```

Un README initial a ete ajoute pour debloquer le test. Cela reste une dette
worker: `prepare_branch()` doit gerer les repos vides ou cloner un `origin/main`
nouvellement cree.

### 4. Workspace local stale apres bootstrap remote

Apres le commit README sur GitHub, le workspace local du worker etait encore:

```text
## No commits yet on main...origin/main [gone]
```

Il a fallu deplacer le clone local en backup pour forcer un clone propre. Dette:
le worker doit recuperer proprement un clone local reste dans un etat empty-repo
apres initialisation du remote.

### 5. Runtime observer trop large

Des observations `telegram.error.TimedOut: Timed out` ont ete attachees a la task
Portfolio V2 et ont pousse la task en `queued_runtime_repair` alors que le plan
n'avait pas encore tourne. Dette: scorer/correler les observations par run,
commande, fenetre temporelle et source avant de bloquer une task.

### 6. Run manuel worker: environnement a normaliser

Le lancement manuel root a echoue avec:

```text
[Errno 2] No such file or directory: 'hermes'
```

Le service a `PATH=/home/hermes/.local/bin:...`. Pour reproduire un run manuel,
il faut lancer en utilisateur `hermes` avec `HOME=/home/hermes` et `PATH`
explicite.

### 7. Provider secondaire peut ralentir le precheck

Le precheck secondaire `xai-oauth/grok-4.20-reasoning` a pris plusieurs minutes
avant de rendre la main. Il a fini par passer, mais la latence doit etre visible
dans Cockpit/Telegram pour eviter de croire a une task morte.

## Criteres d'acceptation

| Critere | Statut |
|---|---|
| CUA peut capturer et envoyer les commandes Telegram | OK |
| `/new` fonctionne apres refacto gateway | OK |
| `/new pilote` fonctionne apres refacto gateway | OK |
| `/libre` transforme une demande naturelle en task Cockpit | Partiel: OK task, mauvais repo |
| Hermes ne modifie pas `MFcv1/Portfolio` | OK |
| Hermes compare objectivement Next vs Astro | OK |
| Aucune depense/deploy/merge sans approval | OK |
| Cockpit expose task/telemetry/status | OK |
| Premier livrable docs V2 | Bloque volontairement par validation humaine Pilote |

## Prochaine action recommandee

Avant de demander a Hermes d'ecrire les fichiers dans le repo V2, choisir une des
deux validations:

```text
OK pour créer ARCHITECTURE.md et MIGRATION_PLAN.md dans MFcv1/portfolio-v2-hermes-test.
Stack recommandée acceptée: Astro + Cloudflare.
Pas de scaffold app.
Pas de deploy.
Pas de commit/push sans demande explicite.
```

ou:

```text
OK pour créer ARCHITECTURE.md et MIGRATION_PLAN.md dans MFcv1/portfolio-v2-hermes-test.
Stack cible imposée: Next.js + Cloudflare/OpenNext.
Documenter pourquoi on privilégie l'evolution app/client/paiement malgré le coût de complexité.
Pas de scaffold app.
Pas de deploy.
Pas de commit/push sans demande explicite.
```

Dettes techniques a traiter ensuite:
- corriger selection repo en mode Libre,
- gerer les repos vides dans `prepare_branch()`,
- recuperer les workspaces stale apres bootstrap remote,
- mieux scope le runtime observer,
- documenter une commande officielle `worker run --task-id` avec env identique
  au service.
