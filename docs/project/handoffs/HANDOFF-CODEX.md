# Handoff Codex — Hermes Agent Project

## Mise à jour 2026-07-01 — skills déploiement

Changement effectué : amélioration et déploiement des skills personnels Hermes pour `/deploy`.

### Fichiers locaux modifiés

- `/Users/matthis/.hermes/skills/devops/project-hosting-matrix/SKILL.md`
- `/Users/matthis/.hermes/skills/devops/project-hosting-matrix/references/*.md`
- `/Users/matthis/.hermes/skills/devops/cloudflare-astro-platform/SKILL.md`
- `/Users/matthis/.hermes/skills/devops/cloudflare-astro-platform/references/*.md`
- `/Users/matthis/.hermes/skills/devops/cloudflare-astro-platform/templates/api-health-*.ts`
- `/Users/matthis/.hermes/skill-bundles/deploy.yaml`

### Résultat

- `project-hosting-matrix` passe en version `1.1.0` avec triggers, gates anti-overclaim, séparation audit/setup/deploy/preuve.
- `cloudflare-astro-platform` passe en version `1.1.0` avec D1/R2/KV/Images/cache, configs Astro/Wrangler, product boundaries et templates health.
- Bundle `/deploy` charge maintenant explicitement les deux skills et impose URL HTTPS + `curl` avant toute affirmation de déploiement.

### Validations locales

- Frontmatter + liens refs/templates : OK.
- Bundle `/deploy` : OK, skills = `project-hosting-matrix`, `cloudflare-astro-platform`.
- Handler `/myskills` local : OK, les deux skills sont listés.

### Sync VPS

VPS : `root@134.122.73.242`, utilisateur service `hermes`.

- Copie vers `/home/hermes/.hermes/skills/devops/` et `/home/hermes/.hermes/skill-bundles/deploy.yaml` : OK.
- Backup créé côté VPS sous `/home/hermes/.hermes/backups/deploy-skills-before-<timestamp>.tar.gz`.
- `chown -R hermes:hermes` appliqué : OK.
- `systemctl --user restart hermes-gateway` : OK.
- Gateway après restart : `active (running)`.

### Validations VPS

- Validation fichiers VPS : OK, 23 vrais fichiers sous les deux skills + bundle ; checksums Mac/VPS identiques.
- Frontmatter VPS : OK.
- Bundle VPS `/deploy` : OK.
- Handler `/myskills` avec le venv Hermes VPS : OK ; `/project-hosting-matrix`, `/cloudflare-astro-platform`, `/deploy` visibles.

### Notes

- Le log gateway contient d'anciens warnings réseau Telegram et deux erreurs `not enough values to unpack` du 2026-06-30 avant ce changement ; pas lié au sync des skills.
- À vérifier côté Telegram si besoin : envoyer `/myskills`, puis `/deploy` dans le chat cible.

## Mise à jour 2026-07-01 — demande Mode Pilote `/new`

Document de cadrage créé :

- `/Users/matthis/Desktop/Hermes Agent Project/docs/PILOT_MODE_ARCHITECT_DEPLOY_REQUEST.md`

Idée : ajouter un troisième mode dans `/new` — `Pilote` — entre `Ask Review` et `Autopilot`. Après réception du prompt, Hermes analyse le besoin, propose `Architect` ou `Deploy`, pose les questions de contexte nécessaires, construit un context pack, puis travaille en autonomie sans ask review permanent.

Points clés :

- `/architect` pour cadrer stack/architecture/docs avant code.
- `/deploy` pour valider/auditer/déployer une stack plus claire, mais avec questions si contexte incomplet.
- Firebase doit être traité comme un choix d’infra global possible dès zéro, pas seulement comme une dépendance existante.
- Besoin futur : stacks préconçues solides pour Vercel/Supabase/Firebase/Stripe, pas seulement Cloudflare/Astro.

## Mise à jour 2026-07-01 — Mode Pilote implémenté et vérifié

### Changements appliqués

- Gateway Telegram local/VPS : ajout mode `pilote`, normalisation, bouton `/new`, fallback texte `/new pilote`, création tâche autonome en mode Pilote.
- Backend Repo Cockpit VPS : `mode=pilote` accepté dans capabilities/policy/gates, endpoints phase 3 :
  - `GET /api/internal/tasks/pilot-pending/{telegram_user_id}`
  - `POST /api/internal/tasks/{task_id}/pilot-answer`
- Worker Repo Cockpit VPS : route Pilote `architect|deploy|general`, `## Pilot Context Pack`, `PILOT_STATUS`, `PILOT_ROUTE`, statut `pilot_questions_required`, reprise après réponses.
- Garde-fou ajouté : si GPT-5.5 oublie le header Pilote strict, `store_plan()` l’ajoute avant décision/persistance.
- Bug VPS corrigé : fichier manquant `/home/hermes/.hermes/hermes-agent/hermes_cli/managed_scope.py` synchronisé ; `hermes chat` smoke OK.

### Preuves vérifiées

- CUA Telegram `/new` : clavier visible `Ask review | Pilote | Autopilot`.
- CUA Telegram `/new pilote` : panneau visible `Mode : Pilote`, bouton `✓ Pilote`.
- CUA Telegram `/task ... app from scratch inconnue ...` : tâche créée `op_1782912153_8411c5bb`, worker lancé automatiquement.
- Worker VPS produit : `PILOT_STATUS: questions_required`, `PILOT_ROUTE: architect`, `## Pilot Context Pack`.
- CUA Telegram réponse `/task Produit: mini SaaS SEO...` : message visible `Réponse Pilote reçue`, statut `queued_plan`, worker relancé.
- Vérification finale DB dry-run après réponse : status `needs_review`, `has_pack=True`, `ready=True`, `route=True`.
- Services VPS actifs : `hermes-repo-cockpit.service active`, `hermes-gateway.service active`.
- Capabilities live : `['ask_review', 'pilote', 'autopilot']`.
- Aucun process worker/hermes chat restant après vérification finale.

### Tests

- Local Hermes gateway : `python -m pytest tests/gateway/test_telegram_pilot_mode.py tests/gateway/test_telegram_conv_ux.py -q -o 'addopts='` → `17 passed in 0.42s`.
- VPS compile : `backend/app.py`, `scripts/operation_worker.py`, `gateway/platforms/telegram.py` OK.

### Notes

- Les inline buttons Telegram Desktop restent peu fiables via CUA ; le fallback `/new pilote` est volontaire et doit rester.
- Le test final worker a été fait en dry-run pour éviter mutation/commit sur le repo smoke.

## Mise à jour 2026-07-01 — Mode Pilote V2 guided workflow + pré-check stratégie

### Changements appliqués

- Gateway Telegram local/VPS : ajout du workflow guidé `Pilot Intake`.
  - `/new pilote` affiche le wizard avec `Projet GitHub existant` et `Start from scratch`.
  - Après sélection, le prochain message naturel devient le prompt de tâche : plus besoin de `/task` dans le flow normal.
  - Si une tâche Pilote est en `pilot_questions_required`, le prochain message naturel devient une réponse aux questions et relance le worker.
- Repo existant : sous-menu prévu côté gateway pour choisir l’intention :
  - `audit_repo`
  - `feature_work`
  - `debug_fix`
  - `deploy`
  - `review_harden`
  - `pilot_discovery`
- Worker Repo Cockpit VPS : `Spark triage` n’est plus le triage critique.
  - Nouveau `strategy_precheck` : GPT-5.5 medium + Grok/xAI medium.
  - Le pré-check calibre `route`, `complexity`, `plan_reasoning`, `needs_questions`.
  - L’implémentation garde le reasoning choisi dans `/new`; le pré-check ne sert pas à downgrader ou changer l’exécution.
- Rapport Telegram clarifié :
  - `Pré-check stratégie`
  - `Agent principal GPT-5.5`
  - `Tests skipped — aucune commande détectée` au lieu de `passed 0 commande(s)`
  - `Second avis IA`
  - `Pull Request`
  - `Preview publique`
  - `Test URL publique`
  - verdict dry-run clair : `Dry-run terminé — rien n’a été modifié`.

### Preuves vérifiées

- Tests locaux gateway : `19 passed in 0.43s` pour `tests/gateway/test_telegram_pilot_mode.py` + `tests/gateway/test_telegram_conv_ux.py`.
- VPS services actifs : `hermes-gateway.service active`, `hermes-repo-cockpit.service active`.
- VPS gateway compile : `gateway/platforms/telegram.py`, `gateway/platforms/telegram_models_config.py` OK.
- Smoke worker pré-check : tâche `op_1782917632_236539bd` → `needs_review`, `strategy_precheck.ok=True`, `decisions=2`, `route=pilot_discovery`, `plan_reasoning=medium`, `## Pilot Context Pack` présent.
- Carte Telegram V2 visible : `Pré-check stratégie`, `Agent principal GPT-5.5`, `Tests skipped`, `Pull Request`, `Preview publique`, `Test URL publique`.
- Prompt naturel équivalent API : tâche `op_1782918417_e7fd210e` créée sans `/task` via endpoint `from-thread`, worker dry-run OK, pré-check `audit_repo`, `decisions=2`, pack présent.
- Flux questions/réponse/reprise vérifié : tâche smoke `smoke-answer-v2` → premier worker `pilot_questions_required` + `PILOT_STATUS: questions_required`; `pilot-answer` → `queued_plan`; second worker → `needs_review`, `auto_approved`, `PILOT_STATUS: ready`, `precheck_decisions=2`.

### Limite de validation CUA

- Les boutons inline Telegram Desktop ne déclenchent pas toujours les callbacks sous CUA même quand visuellement présents. Le clic `Start from scratch` n’a pas été reçu par Telegram Desktop pendant la vérification CUA.
- Le comportement du callback est couvert par tests gateway et le flux métier a été vérifié via API/worker. Pour validation humaine réelle, cliquer manuellement les boutons dans Telegram devrait déclencher le wizard ; pour validation automatisée, garder les fallbacks texte et les tests API.

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

## Mise à jour 2026-07-06 — Libre V2 local implémenté (non déployé VPS)

### Objectif

Transformer le “chat libre” en mode chef d’orchestre : ne pas hard-reset la mémoire ni tout le contexte, mais sortir proprement d’un chantier Repo Cockpit/Pilote, garder une note de reprise, puis laisser Hermes router naturellement vers chat, Ask review, Pilote ou Autopilot selon le message.

### Changements locaux

- Nouveau module : `gateway/libre_orchestrator.py`
  - `classify_libre_message()` : route conservatrice chat normal vs tâche repo vs switch repo vs règle d’apprentissage.
  - `extract_learning_policy()` : détecte des règles naturelles type “Pour les plans mets toi en GPT-5.5 xhigh”.
  - `ActiveWorkStore` : stocke contexte actif, handoffs et policies dans `~/.hermes/libre/state.json` en runtime.
  - `scan_watch_logs()` : Watch V1 sur logs gateway/repo-cockpit/errors.
- Telegram : `gateway/platforms/telegram.py`
  - ajout état `_libre_chat_states`.
  - nouvelle commande `/libre` (`/reset-libre`, `/chatlibre`) interceptée côté Telegram.
  - `/libre` clear les états transitoires Pilote/new-chat, crée un handoff soft-close, conserve mémoire durable et répond avec un panneau “Mode libre activé”.
  - messages naturels en mode libre :
    - règle modèle/reasoning → enregistrée localement comme policy ;
    - demande de switch repo → ouvre le sélecteur `/new pilote` après note de reprise ;
    - demande repo claire (“corrige bug…”, “déploie…”, “ajoute feature…”) → crée tâche Repo Cockpit avec `mode`/`intent` (`pilote`, `autopilot`, `ask_review`) et source `telegram_libre_router` ;
    - reste → passe au chat Hermes normal.
  - `/libre watch` reste diagnostic manuel ; le daemon Watch V1 est opt-in seulement. Pendant un travail autonome, l’observateur runtime rattache les erreurs logs à la tâche en cours.
  - `_create_task_from_thread_command()` accepte maintenant `mode`, `intent`, `source` pour le routing Libre.
- Registry : `hermes_cli/commands.py`
  - ajoute `/libre` gateway-only avec alias `/reset-libre`, `/chatlibre`.

### Validations locales

```bash
python -m pytest tests/gateway/test_telegram_pilot_mode.py tests/gateway/test_telegram_conv_ux.py tests/gateway/test_libre_orchestrator.py tests/gateway/test_telegram_model_picker.py -q -o 'addopts='
# 40 passed in 1.11s

python -m py_compile gateway/libre_orchestrator.py gateway/platforms/telegram.py gateway/platforms/telegram_models_config.py hermes_cli/commands.py
# OK
```

### Limites / prochaines étapes

- Gateway Telegram code syncé sur le VPS (`~/.hermes/hermes-agent/gateway/platforms/telegram.py`, `gateway/libre_orchestrator.py`) mais service live non redémarré dans cette passe.
- VPS Repo Cockpit modifié côté backend/worker pour Runtime Self-Repair ; service live non redémarré.
- `/libre watch` reste un diagnostic manuel ; le daemon Watch V1 est opt-in seulement (`libre_watch_enabled: true`). Par défaut, pas de checkup périodique hors contexte.
- Observation runtime pendant travail autonome : `_run_autopilot_worker_after_task_create()` passe `runtime_observer={enabled:true, task_id, mode:during_work}` à `/api/worker/run-once`, scanne les logs pendant la boucle worker, déduplique, et attache le signal à `/api/internal/tasks/{task_id}/runtime-observations`.
- Côté VPS Repo Cockpit : ajout table `runtime_observations`, endpoint interne `/api/internal/tasks/{task_id}/runtime-observations`, passage worker `--runtime-observer --task-id`, consommation des observations pendant le worker, phase `runtime_repair`, tests après réparation, statuts `queued_runtime_repair`/`blocked_runtime_repair`.
- Preuves VPS : `.venv/bin/python -m py_compile backend/app.py scripts/operation_worker.py tests/test_runtime_self_repair.py` OK ; `PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/runtime_self_repair_smoke.py` → `runtime self-repair remote smoke OK`.
- Le switch repo ouvre le sélecteur `/new pilote`; il ne sélectionne pas encore un repo par nom libre sans bouton.
- L’auto-commit/stash avant switch n’est pas activé : pour l’instant on fait un handoff soft-close, pas une mutation git automatique.
- Prompt d’audit Claude Fable généré pour audit global autonomie/polyvalence : `docs/CLAUDE_FABLE_AUTONOMY_AUDIT_PROMPT.md`. Objectif : obtenir architecture cible + plan de refactor Runtime Self-Repair / Gateway / Repo Cockpit / mémoire / observabilité / evals.

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
