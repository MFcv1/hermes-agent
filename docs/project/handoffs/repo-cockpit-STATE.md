# Repo Cockpit State

## 2026-07-01 — `/deploy` skills améliorés et synchronisés

### État vérifié

- Mac/default profile : skills personnels présents dans `~/.hermes/skills/devops/`.
- VPS `134.122.73.242` : skills copiés dans `/home/hermes/.hermes/skills/devops/`.
- Gateway VPS : `hermes-gateway.service active (running)` après restart.
- `/myskills` côté VPS : liste `/project-hosting-matrix`, `/cloudflare-astro-platform`, et indique le raccourci `/deploy`.

### Skills concernés

- `project-hosting-matrix` `1.1.0`
  - Choix provider MFcv1 : Vercel, Supabase, Cloudflare, Firebase.
  - Gates : audit safe, provider identity, secrets, mutation explicite.
  - Checklists profils : Next/Vercel, Next/Supabase, Astro/Cloudflare static/fullstack, Firebase-heavy.

- `cloudflare-astro-platform` `1.1.0`
  - Astro Cloudflare Pages/Workers.
  - D1/R2/KV/Images/cache.
  - `astro.config.mjs`, `wrangler.toml`, local dev, preview deploy, product boundaries.
  - Templates health endpoints : D1, R2, KV.

- Bundle `/deploy`
  - Charge les deux skills.
  - Interdit d'affirmer un deploy sans URL publique HTTPS + `curl` OK.
  - En cas d'auth/secrets manquants, répondre `blocked_auth` / `missing_secret` avec noms seulement.

### Fichiers source locaux

- `/Users/matthis/.hermes/skills/devops/project-hosting-matrix/`
- `/Users/matthis/.hermes/skills/devops/cloudflare-astro-platform/`
- `/Users/matthis/.hermes/skill-bundles/deploy.yaml`

### Fichiers VPS

- `/home/hermes/.hermes/skills/devops/project-hosting-matrix/`
- `/home/hermes/.hermes/skills/devops/cloudflare-astro-platform/`
- `/home/hermes/.hermes/skill-bundles/deploy.yaml`
- Backup avant remplacement : `/home/hermes/.hermes/backups/deploy-skills-before-<timestamp>.tar.gz`

### Vérifications déjà faites

- Frontmatter local : OK.
- Liens `references/` et `templates/` : OK.
- Bundle local : OK.
- `/myskills` local : OK.
- SSH VPS : OK.
- Sync VPS + chown : OK.
- Restart gateway : OK.
- Frontmatter/bundle VPS : OK.
- Checksums Mac/VPS : OK, 23 vrais fichiers identiques après nettoyage des fichiers macOS parasites `._*`.
- `/myskills` VPS via `/home/hermes/.hermes/hermes-agent/venv/bin/python` : OK.

## 2026-07-01 — cadrage Mode Pilote `/new`

### Nouveau document

- `/Users/matthis/Desktop/Hermes Agent Project/docs/PILOT_MODE_ARCHITECT_DEPLOY_REQUEST.md`

### Idée produit

Ajouter un troisième mode dans `/new` :

```text
Ask Review | Pilote | Autopilot
```

`Pilote` reçoit le prompt, analyse s’il faut partir sur `Architect Mode` ou `Deploy Mode`, pose les questions de contexte, crée un context pack, puis laisse l’agent travailler en autonomie.

### Notes importantes

- `/architect` = cadrage fort avant code : stack, arborescence, `.md`, risques, décisions.
- `/deploy` = stack plus claire, mais questions minimales si contexte incomplet.
- Firebase doit être considéré comme un choix infra possible dès zéro selon le contexte, pas seulement si le projet dépend déjà de Firebase.
- Il manque encore des deploy skills solides pour Vercel/Supabase/Firebase/Stripe.

## 2026-07-01 — Mode Pilote `/new` implémenté, déployé, vérifié

### État live

- Gateway VPS : actif.
- Repo Cockpit VPS : actif.
- Capabilities live : `['ask_review', 'pilote', 'autopilot']`.
- Fallback texte CUA : `/new pilote` fonctionne et affiche `Mode : Pilote`.
- Task e2e utilisée : `op_1782912153_8411c5bb` sur repo smoke `MFcv1/hermes-tennis-autopilot-smoke-20260623-171356`.

### Flux vérifié

1. `/new` dans Telegram Desktop via CUA : clavier visible avec `Ask review`, `Pilote`, `Autopilot`.
2. `/new pilote` via CUA : panneau visible `Mode : Pilote`, bouton `✓ Pilote`.
3. `/task ... app from scratch inconnue ...` via CUA : tâche créée et worker lancé.
4. Worker : produit `## Pilot Context Pack`, `PILOT_STATUS: questions_required`, `PILOT_ROUTE: architect`.
5. `/task Produit: mini SaaS SEO...` via CUA : réponse captée par endpoint `pilot-answer`, statut `queued_plan`, worker relancé.
6. Vérification finale safe dry-run : `status=needs_review`, `ready=True`, `route=True`, `has_pack=True`.

### Fixes appliqués pendant vérif

- Ajout de `/new pilote` car les inline buttons Telegram Desktop sont peu fiables avec CUA.
- Ajout endpoints backend phase 3 : `pilot-pending` et `pilot-answer`.
- Correction VPS Hermes : `hermes_cli/managed_scope.py` manquant, synchronisé depuis le repo local ; smoke `hermes chat` OK.
- Correction worker : dry-run Pilote pose des questions pour `from scratch inconnue` tant qu’aucune réponse utilisateur n’est présente.
- Correction worker : `store_plan()` ajoute un header Pilote strict si GPT-5.5 l’oublie, pour ne pas bypasser les gates.

### Validations

- Tests locaux gateway : `17 passed in 0.42s` pour `test_telegram_pilot_mode.py` + `test_telegram_conv_ux.py`.
- Compile VPS : `backend/app.py`, `scripts/operation_worker.py`, `gateway/platforms/telegram.py` OK.
- Services VPS actifs après restart.
- Aucun process `operation_worker`/`hermes chat` restant après test final.

### Important

- Le test final a été fait en dry-run pour éviter mutation/commit/PR sur le repo smoke.
- Ne pas retirer `/new pilote`; c’est le chemin de vérification fiable avec CUA.

## 2026-07-01 — Mode Pilote V2 guided workflow + pré-check stratégie

### État live

- Gateway VPS : actif.
- Repo Cockpit VPS : actif.
- `/new pilote` affiche le wizard V2 : `Projet GitHub existant`, `Start from scratch`, modèle, réflexion.
- `Spark triage` est remplacé comme gate critique par `Pré-check stratégie` : GPT-5.5 medium + Grok/xAI medium.
- Spark reste utile pour quota/handoff/info, mais plus comme décideur principal du workflow Pilote.

### Règle reasoning validée

```text
user_selected_reasoning = niveau choisi dans /new
precheck_reasoning = medium côté GPT-5.5 + Grok/xAI
plan_reasoning = max(user_selected_reasoning, precheck_recommended_plan_reasoning)
implementation_reasoning = user_selected_reasoning
```

Le pré-check calibre surtout le plan. Il ne downgrade pas l’implémentation.

### Validations effectuées

- Tests locaux gateway : `19 passed in 0.43s`.
- VPS compile gateway : OK.
- Services VPS : `hermes-gateway.service active`, `hermes-repo-cockpit.service active`.
- Smoke pré-check worker : `op_1782917632_236539bd` → `strategy_precheck.ok=True`, `decisions=2`, route `pilot_discovery`, plan `medium`, `## Pilot Context Pack` présent.
- Carte Telegram Review V2 visible : verdict `Dry-run terminé — rien n’a été modifié`; labels `Pré-check stratégie`, `Agent principal GPT-5.5`, `Tests skipped`, `Second avis IA`, `Pull Request`, `Preview publique`, `Test URL publique`.
- Prompt naturel API équivalent sans `/task` : `op_1782918417_e7fd210e` → worker `needs_review`, pré-check `audit_repo`, décisions `2`, pack présent.
- Questions/réponse/reprise : premier worker `pilot_questions_required`; endpoint `pilot-answer` passe `queued_plan`; second worker `needs_review`, `PILOT_STATUS: ready`, `precheck_decisions=2`.

### Limite connue

CUA/Telegram Desktop ne déclenche pas toujours les inline callbacks. Visuellement, les boutons sont là, mais le clic `Start from scratch` n’a pas été reçu pendant le test CUA. Les callbacks sont couverts par tests gateway ; les flux métier sont vérifiés via API/worker. Pour un smoke humain final, cliquer manuellement les boutons Telegram.

## Mise à jour 2026-07-01 — Skills V3 `/architect` + `/deploy`

### Objectif

Rendre `/architect` et `/deploy` cohérents avec le mode Pilote V2 : vrais presets produit, deep dives conditionnels, Firebase depuis zéro, Supabase/Auth, Stripe/e-commerce/marketplace, Cloudflare/Astro, et sync VPS.

### Changements locaux

Bundles mis à jour :

- `/Users/matthis/.hermes/skill-bundles/deploy.yaml`
- `/Users/matthis/.hermes/skill-bundles/architect.yaml`

`/deploy` charge maintenant :

```yaml
- project-hosting-matrix
- cloudflare-astro-platform
- firebase-app-hosting-platform
- supabase-nextjs-auth
- stripe-marketplace-architecture
```

`/architect` charge maintenant :

```yaml
- project-architect
- project-hosting-matrix
- cloudflare-astro-platform
- firebase-app-hosting-platform
- supabase-nextjs-auth
- stripe-marketplace-architecture
```

Skills ajoutés/alignés localement :

- `devops/firebase-app-hosting-platform` créé.
- `software-development/supabase-nextjs-auth` ajouté localement depuis le VPS puis nettoyé.
- `project-hosting-matrix` enrichi avec presets :
  - `next-vercel-static`
  - `next-vercel-supabase`
  - `next-vercel-stripe-saas`
  - `next-firebase-apphosting`
  - `astro-cloudflare-static`
  - `astro-cloudflare-fullstack`
  - `astro-supabase-catalogue`
  - `cloudflare-workers-postgres`
  - `firebase-ecommerce-stripe-simple`
  - `marketplace-stripe-connect-postgres`
- `project-architect` enrichi avec :
  - triggers deep dives Stripe/Supabase/Firebase/Cloudflare ;
  - presets produit ;
  - table de scoring stack obligatoire pour les architectures sérieuses.

### Sync VPS

Fichiers synchronisés vers `/home/hermes/.hermes/` :

- `skill-bundles/deploy.yaml`
- `skill-bundles/architect.yaml`
- `skills/devops/project-hosting-matrix/`
- `skills/devops/cloudflare-astro-platform/`
- `skills/devops/firebase-app-hosting-platform/`
- `skills/software-development/project-architect/`
- `skills/software-development/supabase-nextjs-auth/`
- `skills/software-development/stripe-marketplace-architecture/`

Gateway VPS redémarré : `hermes-gateway.service active`.

### Vérifications

Local : YAML bundles valides, frontmatter skills OK.

VPS checksums principaux :

- `deploy.yaml` sha `d06c195a2d44`, skills = `project-hosting-matrix, cloudflare-astro-platform, firebase-app-hosting-platform, supabase-nextjs-auth, stripe-marketplace-architecture`.
- `architect.yaml` sha `8cd056291e30`, skills = `project-architect, project-hosting-matrix, cloudflare-astro-platform, firebase-app-hosting-platform, supabase-nextjs-auth, stripe-marketplace-architecture`.
- `project-hosting-matrix` sha `f619b04454df`.
- `cloudflare-astro-platform` sha `a46464897509`.
- `firebase-app-hosting-platform` sha `43fa55332441`.
- `project-architect` sha `e5e6290c29c1`.
- `supabase-nextjs-auth` sha `003f5e5c0fb5`.
- `stripe-marketplace-architecture` sha `c002a5afda05`.

VPS file scan confirme présence :

```text
devops/cloudflare-astro-platform/SKILL.md
devops/firebase-app-hosting-platform/SKILL.md
devops/project-hosting-matrix/SKILL.md
software-development/project-architect/SKILL.md
software-development/stripe-marketplace-architecture/SKILL.md
software-development/supabase-nextjs-auth/SKILL.md
```

`hermes skills list` VPS montre au moins `project-hosting-matrix`, `project-architect`, `supabase-nextjs-auth` enabled ; les autres sont présents en filesystem et référencés par les bundles.

### Notes

- `/architect` était absent côté VPS avant cette passe ; il est maintenant sync avec son skill.
- `/deploy` VPS était plus vieux que local ; il est maintenant aligné V3.
- Le skill Firebase V3 sert à éviter de traiter Firebase comme “secondaire” : App Hosting/Auth/Firestore/Storage/Functions/App Check/Stripe sont cadrés.

## Mise à jour 2026-07-01 — Audit docs officielles `/deploy` + `/architect`

Rapport écrit :

- `/Users/matthis/Desktop/Hermes Agent Project/.hermes/plans/2026-07-01-architect-deploy-official-docs-audit.md`

### Méthode

- Sous-agents lancés pour Cloudflare/Astro, Firebase, Vercel/Supabase/Stripe ; ils n'ont pas rendu avant clôture du rapport.
- Audit direct réalisé via docs officielles récupérées par `curl`/Python car `web_extract` Firecrawl n'était pas configuré.

### Corrections critiques appliquées

1. **Firebase App Hosting**
   - Correction du faux chemin `npx firebase deploy` comme commande par défaut App Hosting.
   - App Hosting utilise maintenant les commandes officielles :
     - `firebase apphosting:backends:list/get`
     - `firebase apphosting:rollouts:create BACKEND_ID --git_branch ... --git_commit ...`
     - `firebase apphosting:secrets:set`
   - `firebase deploy` conservé uniquement pour Firebase Hosting classique/framework-aware Hosting.

2. **Supabase Auth / Next.js**
   - Ajout des noms modernes :
     - `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`
     - `SUPABASE_SECRET_KEY`
   - Les anciens noms restent reconnus pour repos existants :
     - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
     - `SUPABASE_SERVICE_ROLE_KEY`

3. **Cloudflare / Astro**
   - Ajout note Astro 6 / `@astrojs/cloudflare` v13 : Cloudflare env résolu au build via Vite plugin ; ne pas supposer qu'un build unique peut être réutilisé pour tous les envs.
   - `nodejs_compat` clarifié : ce n'est pas un runtime Node complet ; toujours vérifier runtime + `compatibility_date`.

4. **project-hosting-matrix**
   - Gate D précise maintenant `firebase apphosting:rollouts:create` et distingue `firebase deploy` pour Hosting classique.

### Sync VPS

Gateway redémarré : `hermes-gateway.service active`.

Checksums VPS après sync :

```text
project-hosting-matrix/SKILL.md       230 lines d31ca6dda1dd
cloudflare-astro-platform/SKILL.md    186 lines b460605d0bd3
firebase-app-hosting-platform/SKILL.md 246 lines 252399485ad4
supabase-nextjs-auth/SKILL.md         177 lines d5e298169dd4
```

### À faire plus tard

- Enrichir Firebase avec exemples `apphosting.yaml`, `apphosting:secrets:grantaccess`, `apphosting:config:export`.
- Enrichir Supabase avec structure officielle SSR `client.ts/server.ts/middleware.ts` et `getUser()` server-side.
- Enrichir Cloudflare avec tableau Pages direct upload vs Git Pages vs Workers deploy.
- Enrichir Stripe avec matrice d'events par produit.
- Enrichir Vercel avec `vercel pull`, `vercel build`, `vercel deploy --prebuilt`, `vercel inspect`.

## Mise à jour 2026-07-01 — Rapport amendé sous-agents `/deploy` + `/architect`

Rapport amendé :

- `/Users/matthis/Desktop/Hermes Agent Project/.hermes/plans/2026-07-01-architect-deploy-official-docs-audit-amended.md`

### Ce qui a changé après retour sous-agents

1. **Cloudflare/Astro**
   - Astro SSR/fullstack moderne = **Cloudflare Workers + Workers Static Assets** par défaut.
   - Pages SSR = **legacy/version-pinned seulement**.
   - Templates santé remplacés : plus de `Astro.locals.runtime`, usage `import { env } from 'cloudflare:workers'`.
   - Ajout/rewrite runbooks : Workers SSR, Pages SSR legacy, wrangler.jsonc, env/secrets, DO, Queues, Hyperdrive, Images, Caching.

2. **Firebase App Hosting**
   - Skill réécrit pour distinguer : GitHub/live branch rollouts, manual rollouts, local source deploy `firebase-tools >=14.4.0`.
   - Bare `npx firebase` évité ; utiliser `firebase` CLI ou `npx firebase-tools@latest`.
   - Ajout : Blaze/runtime/lockfile, `apphosting.yaml`, secrets `grantaccess`, Functions v2, Auth server verification, Firestore/Storage rules tests, App Check.

3. **Supabase/Vercel**
   - Supabase Auth recentré sur `@supabase/ssr`.
   - Ajout `/auth/callback` + `exchangeCodeForSession`.
   - Site URL = prod/officiel ; Vercel previews via allow-list wildcard.
   - Ajout Vercel `pull`, `build`, `deploy --prebuilt`, `--prod`, `--build-env` vs `--env`.

4. **Stripe**
   - Ajout recettes runtime webhook : Next App Router Node `request.text()`, Firebase `req.rawBody`, Cloudflare Worker `request.text()`.
   - Ajout ack rapide `2xx`, queue/idempotency, Stripe API idempotency keys, Billing events, Connect responsibilities.
   - Remplacement “Postgres obligatoire” par “Postgres fortement recommandé par défaut pour marketplace financière sérieuse ; pas obligation Stripe”.

### Sync VPS

Gateway actif après sync : `active`.

Checksums VPS :

```text
project-hosting-matrix              266 lines 2b9db0a1a935
cloudflare-astro-platform           235 lines a9df6e4733c3
firebase-app-hosting-platform       536 lines 486f71ff3cf6
project-architect                   418 lines 0a51afc30b92
supabase-nextjs-auth                321 lines 78a87a7ed86f
stripe-marketplace-architecture     391 lines f7f04ec45da1
```

## 2026-07-06 — Libre V2 local implémenté (non déployé VPS)

### État local

- Nouveau `gateway/libre_orchestrator.py` pour classifier les messages Libre, apprendre des policies modèle/reasoning, stocker active context/handoffs et scanner les logs Watch V1.
- Telegram intercepte `/libre`, `/reset-libre`, `/chatlibre`.
- `/libre` ne hard-reset pas Hermes : il sort du flow chantier/wizard, vide les états transitoires Pilote/new-chat, crée une note de reprise et garde mémoire durable.
- En mode libre :
  - chat normal reste chat normal ;
  - règles naturelles type “Pour les plans mets toi en GPT-5.5 high” sont enregistrées ;
  - demandes de switch repo ouvrent `/new pilote` ;
  - demandes repo claires créent une tâche Repo Cockpit avec `mode`/`intent` choisis par le router ;
  - `/libre watch` reste diagnostic manuel ; le daemon périodique est opt-in. Pendant un travail autonome, l’observateur runtime rattache les erreurs logs à la tâche en cours.
- `/libre` ajouté dans `hermes_cli/commands.py` en gateway-only.

### Preuves locales

```bash
python -m pytest tests/gateway/test_libre_orchestrator.py tests/gateway/test_telegram_pilot_mode.py -q -o 'addopts='
# 17 passed in 0.46s

python -m pytest tests/gateway/test_telegram_pilot_mode.py tests/gateway/test_telegram_conv_ux.py tests/gateway/test_libre_orchestrator.py tests/gateway/test_telegram_model_picker.py -q -o 'addopts='
# 40 passed in 1.11s

python -m pytest tests/gateway/test_telegram_model_picker.py -q -o 'addopts='
# 6 passed in 0.63s

python -m py_compile gateway/libre_orchestrator.py gateway/platforms/telegram.py gateway/platforms/telegram_models_config.py hermes_cli/commands.py
# OK
```

### Non fait volontairement / état actuel

- Gateway Telegram code syncé sur le VPS (`~/.hermes/hermes-agent/gateway/platforms/telegram.py`, `gateway/libre_orchestrator.py`) mais service live non redémarré dans cette passe.
- Repo Cockpit VPS backend/worker modifié pour Runtime Self-Repair ; service live non redémarré.
- Watch V1 n’est plus un checkup par défaut : le daemon périodique est opt-in (`libre_watch_enabled: true`) et `/libre watch` reste diagnostic manuel.
- Le chemin principal est l’observation runtime pendant travail autonome : l’appel worker transporte `runtime_observer={enabled:true, task_id, mode:during_work}`, scanne les logs pendant la boucle worker et rattache les signaux à la tâche en cours.
- Côté VPS : `runtime_observations`, endpoint interne `/api/internal/tasks/{task_id}/runtime-observations`, `--runtime-observer --task-id`, phase `runtime_repair`, tests après réparation, blocage propre `blocked_runtime_repair`.
- Preuves VPS : compile backend/worker/test OK ; smoke `runtime self-repair remote smoke OK`.
- Pas encore de sélection repo par nom libre ; switch repo ouvre le sélecteur.
- Pas encore d’auto-commit/stash avant switch : handoff soft-close seulement.
- Prompt d’audit Claude Fable généré : `docs/CLAUDE_FABLE_AUTONOMY_AUDIT_PROMPT.md` côté Hermes local, à utiliser pour obtenir architecture cible + plan de refactor autonomie/polyvalence.

## Mise à jour 2026-07-06 — Autonomie V2 Phase 0 terminée

- Commit Phase 0 poussé : `cc28c1084e feat(phase0): expose deploy health and inventory symbols`.
- VPS redémarré après backup : `hermes-gateway.service` et `hermes-repo-cockpit.service` actifs.
- Backup principal : `/home/hermes/restart-backups/autonomie-v2-phase0-pre-restart-20260706-160418`.
- Repo Cockpit `/health` expose `git_sha=eaa0df9b122373bcbac7ddfaea05daed2cbac8f2` et `started_at`.
- Gateway runtime status vérifié : `running`, Telegram `connected`, env `HERMES_GIT_SHA=cc28c1084eeeaad13a6f714c71b1b4b7b4be95d7`.
- Repo Cockpit n’était pas un git checkout : initialisation d’un repo git local de snapshot, avec runtime dirs ignorés (`data/`, `workspaces/`, `runs/`, `supabase/.temp/`, `.venv/`).
- Rapport complet : `docs/project/PHASE0_COMPLETION_REPORT.md`.
- Limite connue : le Gateway live Telegram ne publie pas `:8642/health` car `api_server` n’est pas activé ; le code health est prêt, mais la preuve live est via `gateway.status` + systemd env.
- Prochain départ Phase 1 recommandé : extraction minimale du formatting/OutboundReport Telegram avec tests de caractérisation avant déplacement.

## Mise à jour 2026-07-06 — Autonomie V2 Phase 1 extraction formatting Telegram

- Première extraction Phase 1 effectuée sans changement UX : helpers MarkdownV2/table/formatting déplacés vers `gateway/platforms/telegram_formatting.py`.
- `TelegramAdapter.format_message()` délègue à `format_telegram_markdown(content)`.
- Tests ajoutés : `tests/gateway/test_telegram_formatting_module.py`.
- Validation : `221 passed in 3.32s` sur formatting/rich/Pilote/Libre/model picker/conv UX ; `py_compile` OK.
- Impact inventaire : `gateway/platforms/telegram.py` passe à 9767 lignes / 258 symboles ; nouveau module formatting = 241 lignes / 7 symboles.
- Rapport : `docs/project/PHASE1_FORMATTING_EXTRACTION_REPORT.md`.
- Pas de sync/restart VPS effectué pour cette extraction partielle.
- Prochaine extraction recommandée : `gateway/repo_cockpit_client.py`, avec tests de caractérisation avant déplacement.

## Mise à jour 2026-07-06 — Autonomie V2 Phase 1 extraction client Repo Cockpit

- Commit formatting Telegram poussé sur `origin/codex/ops-update-readiness`.
- Deuxième extraction Phase 1 effectuée : logique HTTP Repo Cockpit sortie de `gateway/platforms/telegram.py` vers `gateway/repo_cockpit_client.py`.
- `TelegramAdapter` garde les shims `_cockpit_api_sync()` et `_repo_cockpit_url()` pour limiter le diff ; ils délèguent au nouveau module.
- Tests ajoutés : `tests/gateway/test_repo_cockpit_client.py`.
- Validation : `224 passed in 3.54s` sur client, formatting, rich, Pilote, Libre, model picker, conv UX ; `py_compile` OK.
- Impact inventaire : `gateway/platforms/telegram.py` = 9744 lignes / 258 symboles ; nouveau client = 75 lignes / 2 symboles.
- Rapport : `docs/project/PHASE1_REPO_COCKPIT_CLIENT_REPORT.md`.
- Pas de sync/restart VPS effectué pour cette extraction partielle.
- Prochaine extraction recommandée : formatters de panels/status/PR summaries avant flows callbacks.
