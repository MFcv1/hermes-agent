# Audit Autonomie V2 — Hermes / Telegram / Repo Cockpit

> Audit d'architecture réalisé le 2026-07-06 par Claude (architecte principal).
> Objectif : transformer le système en agent Telegram autonome de production —
> coder, corriger, déployer, auditer, se réparer dans le flux, rester sûr.
> Ce document est la base de connaissance de référence pour l'implémentation.
>
> **Bibliothèque d'implémentation détaillée : `docs/brain/` (commencer par `docs/brain/00-INDEX.md`).**
> Les contrats de `docs/brain/03-implementation-contracts.md` sont NORMATIFS.

## Contexte vérifié

- Gateway local : `/Users/matthis/.hermes/hermes-agent` (branche `codex/ops-update-readiness`)
- Gateway VPS : `/home/hermes/.hermes/hermes-agent` (134.122.73.242)
- Repo Cockpit VPS : `/home/hermes/repo-cockpit`
- Services live : `hermes-gateway.service`, `hermes-repo-cockpit.service`
- ⚠️ Les services live n'ont pas forcément été redémarrés après les dernières modifications.
- Tailles constatées : `gateway/run.py` ~858 Ko (~18k lignes), `gateway/platforms/telegram.py` ~472 Ko (~10k lignes),
  `gateway/slash_commands.py` ~194 Ko, `gateway/platforms/api_server.py` ~203 Ko, `gateway/platforms/base.py` ~224 Ko.
- `gateway/libre_orchestrator.py` : 244 lignes, propre (dataclass frozen, store JSON atomique).

---

# A. Diagnostic brutal

## Ce qui est sain

- **`libre_orchestrator.py`** : petit, pur, testable, écriture atomique (`tmp.replace`). Référence de qualité à généraliser.
- **Direction runtime self-repair** : observation task-scoped, statuts explicites (`queued_runtime_repair` / `blocked_runtime_repair`), tests après repair. Bonne boucle, implémentation jeune.
- **Modes `ask_review` / `pilote` / `autopilot`** : bon modèle de gradation d'autonomie.
- **`/libre` = soft-close avec handoff** (pas un reset) : bonne décision.
- **Contrat `runtime_observer` dans le payload worker** : explicite, versionnable, task-scoped.

## Ce qui est fragile

- **`classify_libre_message()` = classifieur à mots-clés.** Faux positifs prévisibles ("explique-moi le deploy" → deploy ; "bug dans ma compréhension" → debug_fix). Pas de contexte conversationnel, pas de fallback LLM, pas de gate de confidence (à 0.68 on route quand même vers un worker).
- **`ActiveWorkStore` = fichier JSON unique.** Pas de lock, pas de concurrence, `handoffs` append-only sans purge → croissance infinie.
- **`scan_watch_logs()`** : regex `error|failed` sans dédup ni corrélation temporelle. OK en diagnostic manuel ; machine à faux positifs si branché sur un cron. Doit rester manuel.
- **Statuts runtime repair = strings dans le status de la tâche.** Mélange cycle de vie tâche / cycle de vie réparation. Pas d'objet `repair_attempt` distinct → pas d'historique, pas de limite d'itérations traçable, pas de rollback ciblé.

## Ce qui bloque l'autonomie

1. **Monolithicité** : `run.py` 18k lignes, `telegram.py` 10k lignes. Chaque capacité nouvelle = patch dans un fichier non raisonnable globalement. Blocage n°1.
2. **Pas de modèle de données unifié** : contexte actif dans `ActiveWorkStore` (JSON gateway), tâches dans Cockpit (SQLite VPS), observations dans `runtime_observations`. Trois sources de vérité, aucune relation formelle. "Reprends le chantier d'hier" ne peut pas être fiable tant que le handoff gateway ne pointe pas vers un `task_id` Cockpit réel.
3. **Pas de policy engine** : gates humaines (secret, merge, prod) = code éparpillé + conventions. Sans module unique `allow / ask_human / deny`, impossible d'augmenter l'autonomie en confiance.
4. **Pas d'eval harness** : impossible de savoir si un changement de prompt/classifieur améliore ou dégrade le routing.

## Ce qui est dangereux en prod

- **Code déployé ≠ code sur disque** (services non redémarrés). Il faut exposer `git SHA + start timestamp` dans le `/health` de chaque service. Quick win n°1.
- **`hermes_remediate_runtime()` sans limite d'itérations formalisée ni rollback** → risque de boucle repair infinie.
- **Repair sans snapshot pré-repair** : si le repair aggrave le bug, pas de retour arrière automatique.
- **`.env.example` de 23 Ko** : de la config comportementale vit dans l'env. Contrainte ".env = secrets only" pas encore respectée.

## Dette technique

- ~2 Mo de Python dans 5 fichiers (`run.py`, `telegram.py`, `slash_commands.py`, `api_server.py`, `base.py`).
- `backend/app.py` côté Cockpit sur la même trajectoire (endpoints + métier + DB dans un fichier). À stopper maintenant.
- Duplication probable de la notion de "contexte actif" entre `session.py`, `session_context.py`, `cockpit_thread_prefs.py`, `ActiveWorkStore`.

---

# B. Architecture cible

## 1. Telegram Gateway / UX Adapter
- **Responsabilités** : uniquement I/O Telegram — parsing updates, envoi, formatage, wizard `/new`, rate limiting. Zéro logique de décision.
- **I/O** : updates Telegram → `InboundMessage` normalisé ; `OutboundReport` → messages formatés.
- **Migration** : découper `gateway/platforms/telegram.py` en package `gateway/platforms/telegram/` : `transport.py`, `formatting.py`, `wizard.py`, `commands.py`, `inbound.py`. Y intégrer `telegram_network.py`, `telegram_models_config.py`.
- **Persistance** : aucune (états wizard éphémères).
- **Tests** : `tests/telegram/test_inbound_normalization.py`, `tests/telegram/test_wizard_flow.py` (/new → mode → modèle → source → prompt).
- **Piège** : remettre du routing d'intention dans ce layer.

## 2. Conversation Orchestrator
- **Responsabilités** : classifier l'intention (chat / repo_task / resume / policy), consulter le contexte actif, décider le mode, déléguer.
- **I/O** : `InboundMessage` + `ActiveContext` → `Decision {action, mode, task_ref, confidence, needs_confirmation}`.
- **Migration** : promouvoir `libre_orchestrator.py` en package `gateway/orchestrator/` : `classifier.py` (keywords V1 + fallback LLM structuré si confidence < 0.75), `router.py`, `decisions.py`.
- **Persistance** : décisions loggées en telemetry (alimente l'eval harness).
- **Tests** : `tests/orchestrator/test_classifier_golden.py` — 50+ phrases FR réelles annotées (dataset d'eval initial).
- **Piège** : classifieur LLM sans schéma strict ; routing à basse confidence sans confirmation utilisateur.

## 3. Repo Cockpit Client (gateway)
- **Responsabilités** : client HTTP typé unique vers l'API Cockpit.
- **Fichier** : créer `gateway/repo_cockpit_client.py` — `create_task()`, `get_task()`, `post_runtime_observation()`, `list_active_tasks()`, `get_handoff()`. Dataclasses par payload.
- **Tests** : `tests/test_repo_cockpit_client.py` avec mock HTTP, incluant timeout/503 (dégradation propre si VPS down).
- **Piège** : dupliquer les modèles sans contrat versionné → mettre `schema_version` dans chaque payload.

## 4. Task Runtime Engine (Repo Cockpit)
- **Responsabilités** : cycle de vie tâches/runs — plan, exécution worker, phases (plan/edit/test/deploy), transitions d'état.
- **Migration** : extraire de `backend/app.py` → `backend/tasks.py` (state machine explicite), `backend/runs.py` ; extraire d'`operation_worker.py` → `scripts/worker/engine.py`, `scripts/worker/phases.py`.
- **Persistance** : tables `tasks`, `runs`.
- **Tests** : `tests/test_task_state_machine.py` — chaque transition légale acceptée, illégales refusées.
- **Piège** : états implicites via strings. Un enum unique + fonction `transition(task, event)` = seul point de mutation de statut.

## 5. Observation Bus
- **Responsabilités** : recevoir toute observation runtime (erreur log, exit code, health check failed, test rouge), normaliser, corréler à `task_id + run_id + phase + command`, déduper.
- **Fichiers** : créer `repo-cockpit/backend/runtime_observations.py` (extraction depuis `app.py`) — `ingest()`, `dedupe_fingerprint()`, `correlate()` ; côté gateway `gateway/observation_reporter.py`.
- **Persistance** : table `observations` avec fingerprint (hash signature d'erreur normalisée).
- **Tests** : `test_observation_dedup.py` (même stack trace 10× → 1 observation count=10), `test_observation_correlation.py`.
- **Règle dure** : AUCUNE observation sans `task_id`. Pas de canal global. C'est la garde anti-watcher-spam.

## 6. Runtime Self-Repair Engine
- **Responsabilités** : consommer observations, classifier, décider réparabilité, patch/test/retry avec budget, rollback. Détail en section E.
- **Migration** : extraire d'`operation_worker.py` → `scripts/worker/self_repair.py` (regroupe `consume_runtime_observations`, `runtime_observations_as_failed_tests`, `hermes_remediate_runtime`, `try_runtime_self_repair`).
- **Persistance** : table `repair_attempts`.
- **Piège** : repair sans snapshot git préalable (branche `repair/<task_id>/<n>` ou stash).

## 7. Policy & Safety Gate Engine
- **Responsabilités** : point de décision unique `evaluate(action_request) → allow | ask_human | deny`. Actions : `merge`, `push`, `deploy_prod`, `restart_service`, `access_secret`, `spend_money`, `run_repair`, `delete_file_outside_repo`.
- **Fichiers** : créer `repo-cockpit/backend/policy_engine.py` + `policies.yaml` versionné dans le repo (PAS dans `.env`). Côté gateway : `gateway/orchestrator/policy_gate.py` (miroir léger pré-tâche).
- **Persistance** : table `approvals`.
- **Tests** : `test_policy_engine.py` — table-driven : chaque action × chaque mode → décision attendue. Test le plus important du système.
- **Règle dure** : une seule porte. Le worker ne peut pas merger sans `evaluate()`. Action inconnue → défaut `ask_human`, jamais `allow`.

## 8. Memory / Handoff Store
- **Responsabilités** : source unique du chantier actif et des handoffs, liés à des `task_id` Cockpit réels.
- **Migration** : `ActiveWorkStore` → `gateway/memory/handoff_store.py` (SQLite, pas JSON) avec référence `cockpit_task_id`. Table `handoffs` côté Cockpit = vraie source of truth ; le gateway garde un cache.
- **Tests** : `test_handoff_roundtrip.py` — soft-close → "reprends le chantier" → même `task_id` récupéré.
- **Règle** : Cockpit détient les tâches ; le gateway détient seulement "quel chat pointe vers quelle tâche".

## 9. Skill / Runbook Registry
- **Responsabilités** : catalogue de procédures nommées (deploy Node app, restart systemd avec rollback, migration DB) avec préconditions, étapes, vérifications, rollback.
- **Fichiers** : `repo-cockpit/runbooks/*.yaml` + `backend/runbooks.py` (loader + validateur de schéma). Le dossier `skills/` d'hermes-agent sert de modèle.
- **Tests** : `test_runbook_schema.py` — tout runbook doit avoir `verify` et `rollback`, sinon rejet au chargement.
- **Piège** : runbooks en prose dans des prompts. Format structuré exécutable + section prose pour le LLM.

## 10. Evaluation Harness
- **Responsabilités** : rejouer des scénarios golden (phrases FR → routing attendu ; erreur injectée → repair attendu), score avant chaque changement.
- **Fichiers** : `tests/evals/routing_golden.jsonl`, `tests/evals/repair_scenarios/`, `scripts/run_evals.py`. `runtime_self_repair_smoke.py` existant = embryon à généraliser.
- **Piège** : evals LLM en CI sans budget → séparer evals déterministes (CI) et evals LLM (nightly/manuel).

## 11. Telemetry Store
- **Responsabilités** : journal append-only des décisions, actions, durées, coûts. Alimente evals + futur dashboard.
- **Fichiers** : `repo-cockpit/backend/telemetry.py`, table `events`. Côté gateway : `hermes_logging.py` + sink JSON structuré.
- **Piège** : logger des secrets ou messages complets. Métadonnées et refs uniquement.

---

# C. Modèle de données cible

**Source of truth : SQLite de Repo Cockpit (VPS).** Le gateway ne détient que le mapping conversation → tâche et le cache handoff.

```
tasks
  id, repo, branch, mode (ask_review|pilote|autopilot), status (enum state machine),
  intent, created_by_chat_id, created_at, parent_task_id (reprise de chantier)

runs                      -- 1 task → N runs (run initial, repairs, retries)
  id, task_id FK, kind (work|repair|verify), phase, started_at, ended_at,
  exit_status, git_sha_before, git_sha_after, worker_args_json

observations              -- 1 run → N observations
  id, task_id FK, run_id FK (nullable si tâche pas running), phase, source,
  fingerprint, first_seen, last_seen, count, raw_excerpt (tronqué), severity

repair_attempts           -- 1 observation → N attempts, budget PAR TASK
  id, task_id FK, observation_id FK, run_id FK, attempt_number,
  strategy (runbook_id | llm_patch), snapshot_ref (NOT NULL, branche/stash git),
  outcome (fixed|failed|worsened|rolled_back), tests_result_json

approvals
  id, task_id FK, action (merge|deploy_prod|restart|secret|payment),
  requested_at, prompt_text, decided_by, decision (approved|denied|expired), decided_at

handoffs
  id, task_id FK, created_at, reason, summary, resume_hints_json, consumed_at

policies                  -- YAML versionné dans le repo ; la table = audit des évaluations
  id, action, mode, decision, matched_rule, task_id FK, evaluated_at

runbooks                  -- fichiers YAML ; table = registre chargé + version/hash
  id, name, version, file_hash, loaded_at

artifacts
  id, task_id FK, run_id FK, kind (diff|log|report|test_output), path, created_at

evaluations
  id, suite, scenario, expected_json, actual_json, passed, git_sha, run_at
```

**Relations clés** : `task 1—N runs 1—N observations 1—N repair_attempts`.
Budget de repair **par task** (ex. 3), pas par observation (sinon 5 obs × 3 tentatives = 15 runs).
`handoffs.task_id` rend "reprends le chantier d'hier" déterministe.

---

# D. Boucle d'autonomie cible

1. **Receive user intent** — `gateway/platforms/telegram/inbound.py` : normalise en `InboundMessage {chat_id, text, reply_context}`.
2. **Classify intent + risk** — `gateway/orchestrator/classifier.py` : keywords puis fallback LLM structuré si confidence < 0.75. Risk pré-évalué par `policy_gate.py`.
3. **Create/attach active task** — `gateway/orchestrator/router.py` : consulte `handoff_store` ; chantier actif compatible → attach (`parent_task_id`), sinon `repo_cockpit_client.create_task()`. Confirmation Telegram si confidence moyenne.
4. **Plan** — worker Cockpit, `scripts/worker/phases.py` phase `plan` : plan persisté en artifact, résumé Telegram (pilote : validation ; autopilot : information).
5. **Act** — `scripts/worker/engine.py` : phases exécutées, chaque commande loggée avec `run_id + phase` (base de la corrélation).
6. **Observe runtime** — `gateway/observation_reporter.py` + hooks worker : stderr/exit codes/health checks → POST `/api/internal/tasks/{id}/runtime-observations` avec `phase` et `command`.
7. **Diagnose** — `backend/runtime_observations.py` : fingerprint, dédup, sévérité, corrélation phase/commande.
8. **Repair if allowed** — `policy_engine.evaluate("run_repair", ...)` : allow → `scripts/worker/self_repair.py` ; ask_human → approval Telegram ; budget épuisé → `blocked_runtime_repair`.
9. **Verify** — `detect_and_run_tests()` + section `verify` du runbook. Repair sans verify = échec par définition.
10. **Report** — `gateway/platforms/telegram/formatting.py` : template fixe — *fait / testé / changé / reste / bloqué*. Jamais de logs bruts (artifact sur demande).
11. **Persist handoff/memory** — écriture `handoffs` côté Cockpit + màj mapping gateway. Déclenché en fin de tâche, sur `/libre`, et sur blocage.

---

# E. Runtime Self-Repair V2

1. **Classification** — dans `backend/runtime_observations.py`, deux étages : règles déterministes d'abord (`ModuleNotFoundError` → dépendance ; `EADDRINUSE` → port ; `ECONNREFUSED` → service down ; syntax error → patch code ; `401/403` → **secret/permission → escalade directe, JAMAIS de repair**), puis LLM avec sortie `{category, probable_cause, suggested_runbook, repairable: bool}`.
2. **Observation bus** — fingerprint = hash(type erreur + fichier + message normalisé sans timestamps/adresses). Même fingerprint pendant un repair en cours → increment `count`, pas de nouveau repair.
3. **Corrélation** — wrapper `CommandSpan` (context manager) dans `scripts/worker/engine.py` : enregistre `(run_id, phase, command, started_at)`. Observation à T corrélée à la commande active à T.
4. **Replay d'erreur** — rejouer la commande fautive isolément avant de patcher. Non reproductible → observation `transient`, pas de patch.
5. **Runbooks d'abord, LLM ensuite** — si `suggested_runbook` existe (ex. `runbooks/missing_dependency.yaml`), l'exécuter. Sinon patch LLM avec contexte : observation + phase + diff récent + replay output.
6. **Patch/test/retry** — chaque tentative : snapshot (`git branch repair/<task>/<n>`), patch, `detect_and_run_tests()`, replay commande fautive. Issues : `fixed` (merge dans la branche de travail), `failed` (rollback + tentative suivante avec learnings), `worsened` (plus de tests rouges qu'avant → rollback immédiat + escalade).
7. **Limites** — `MAX_REPAIR_ATTEMPTS_PER_TASK = 3` dans `policies.yaml` (pas `.env`), timeout par tentative, budget tokens. Épuisé → `blocked_runtime_repair` + rapport.
8. **Rollback** — `git checkout <snapshot_ref>` systématique sur échec. Test : `test_repair_rollback_on_worsen.py` — injecter un repair qui casse un test vert, vérifier retour au SHA pré-repair.
9. **Résumé utilisateur** — 1 seul message Telegram par cycle : "⚠️ Erreur X pendant [phase]. J'ai [action]. Tests : ✅. Je continue." ou "🔴 Pas réussi (3 tentatives). Cause probable : Y. Tout remis en état. Il me faut : Z."
10. **Escalade** — `secret`, `permission`, `payment`, `prod` court-circuitent tout → `approval` immédiate.

**Test e2e clé** : `tests/test_runtime_repair_e2e.py` — tâche `running`, POST observation avec erreur reproductible simulée, vérifier `queued_runtime_repair`, worker `--runtime-observer --task-id`, vérifier `repair_attempt` avec `snapshot_ref`, statut final `fixed` ou `blocked_runtime_repair` selon scénario.

---

# F. Plan de refactor priorisé

## Phase 0 — Stabilisation et inventaire (1–2 jours)
- **Objectif** : code déployé = code sur disque, carte du terrain.
- **Actions** : git SHA + start timestamp dans `/health` du Cockpit et équivalent gateway ; restart propre des deux services avec snapshot rollback (tag git + copie SQLite avant restart) ; `scripts/inventory_symbols.py` : inventaire des fonctions de `run.py` et `telegram.py` (nom, lignes, appels sortants).
- **Tests** : smoke post-restart (message Telegram → réponse ; création tâche → run).
- **Rollback** : `git checkout <tag>` + restore DB + restart.
- **Done** : `/health` renvoie le SHA sur les deux services, smoke vert.

## Phase 1 — Extraction modules gateway (1–2 semaines)
- **Objectif** : `telegram.py` < 2k lignes, comportement inchangé.
- **Actions** : package `gateway/platforms/telegram/` (transport, formatting, wizard, commands, inbound) ; `gateway/repo_cockpit_client.py` avec tous les appels HTTP Cockpit ; `run.py` : extraire uniquement le flux Telegram→Cockpit (pas tout d'un coup).
- **Méthode** : extraction mécanique déplacement + réimport, un module par PR. Tests de caractérisation AVANT de bouger (`test_wizard_flow.py` sur le flow /new existant).
- **⚠️ Pattern déjà établi à suivre** : `gateway/kanban_watchers.py` a déjà été extrait de `run.py` selon un pattern documenté ("god-file decomposition", mixin héritée par `GatewayRunner`, `self` state intact, move behavior-neutral, logger conservé `gateway.run`). Utiliser EXACTEMENT ce pattern pour les extractions suivantes — ne pas en inventer un autre. Une décomposition est donc déjà en cours : s'aligner sur sa numérotation de phases interne.
- **Risques** : imports circulaires, état global partagé. **Rollback** : PRs petites, revert unitaire.
- **Done** : /new et /libre inchangés en prod, `telegram.py` réduit à l'orchestration.

## Phase 2 — Observation bus + contrats (1 semaine)
- **Objectif** : contrat d'observation formalisé, dédup, corrélation.
- **Actions** : `backend/runtime_observations.py` (extraction depuis `app.py`) avec fingerprint/dédup ; payload `schema_version: 2` (ajout `phase`, `command`, `fingerprint`) rétro-compatible v1 ; `gateway/observation_reporter.py`.
- **Tests** : `test_observation_dedup.py`, `test_observation_schema_compat.py`.
- **Rollback** : endpoint accepte v1 et v2 pendant la transition.
- **Done** : 10 erreurs identiques = 1 observation count=10 ; chaque observation a phase+command.

## Phase 3 — Worker Runtime Engine (1–2 semaines)
- **Objectif** : state machine explicite + spans de commandes.
- **Actions** : `backend/tasks.py` (enum statuts + `transition()` unique) ; `scripts/worker/engine.py` + `phases.py` extraits d'`operation_worker.py` ; `CommandSpan` ; table `runs`.
- **Tests** : `test_task_state_machine.py`, `test_command_spans.py`.
- **Rollback** : mapping ancien statut ↔ nouveau conservé.
- **Done** : toute mutation de statut passe par `transition()` ; chaque run a ses spans.

## Phase 4 — Self-Repair V2 (2 semaines)
- **Objectif** : boucle E complète.
- **Actions** : `scripts/worker/self_repair.py`, table `repair_attempts`, snapshots git, replay, budget, `policy_engine.py` + `policies.yaml` (premier consommateur critique = le repair).
- **Tests** : `test_runtime_repair_e2e.py`, `test_repair_rollback_on_worsen.py`, `test_repair_budget_exhausted.py`, `test_policy_engine.py`, `test_secret_error_escalates_no_repair.py`.
- **Risque** : le plus élevé — repair qui casse un repo. **Rollback** : feature flag `self_repair_v2` dans `policies.yaml`, V1 conservée derrière le flag.
- **Done** : e2e vert, rollback prouvé par test, escalade secrets prouvée par test.

## Phase 5 — Memory/Handoff unifié (1 semaine)
- **Objectif** : "reprends le chantier d'hier" fiable.
- **Actions** : table `handoffs` côté Cockpit + endpoints `POST/GET /api/internal/tasks/{id}/handoff` ; `gateway/memory/handoff_store.py` (SQLite) remplace le JSON d'`ActiveWorkStore` ; le classifieur reconnaît l'intention `resume`.
- **Tests** : `test_handoff_roundtrip.py` (soft-close → resume → même lineage via `parent_task_id`).
- **Done** : reprise cross-jour démontrée en réel sur Telegram.

## Phase 6 — Eval harness + golden scenarios (setup 1 semaine, puis continu)
- **Objectif** : mesurer chaque changement routing/repair.
- **Actions** : `tests/evals/routing_golden.jsonl` (50+ phrases FR annotées d'usage réel), `tests/evals/repair_scenarios/` (repos jouets avec erreurs injectées), `scripts/run_evals.py` avec score, CI pour la partie déterministe.
- **Done** : un PR qui dégrade le routing fait échouer la CI.

## Phase 7 — Autonomy dashboard / admin UX (après le reste)
- **Objectif** : visibilité — tâches actives, repairs, approvals, taux de succès.
- **Actions** : `/status` Telegram riche d'abord (lit `tasks`/`runs`/`repair_attempts`) ; page web Cockpit ensuite si besoin. Ne pas commencer par le web.
- **Done** : `/status` montre l'état complet en un message.

---

# G. Quick wins (par priorité)

1. **Redémarrer les services live** avec tag git de rollback — on opère à l'aveugle sinon.
2. **Git SHA dans `/health`** des deux services (~10 lignes chacun).
3. **Créer `gateway/repo_cockpit_client.py`** — petite extraction, gros gain.
4. **Formaliser `MAX_REPAIR_ATTEMPTS_PER_TASK`** + test `test_repair_budget_exhausted.py`.
5. **Snapshot git avant tout repair** (branche `repair/<task>/<n>`) — ~20 lignes dans `try_runtime_self_repair()`.
6. **Fingerprint/dédup des observations** — évite le spam de repairs sur la même erreur.
7. **Gate de confidence** : < 0.75 → message de confirmation au lieu de lancer un worker.
8. **Template de rapport Telegram fixe** (fait/testé/changé/reste/bloqué) dans un `formatting.py` unique.
9. **Court-circuit escalade sur `401/403/permission/secret`** — jamais de repair là-dessus.
10. **Purge/rotation des `handoffs` dans `ActiveWorkStore`** (garder les 20 derniers) en attendant Phase 5.

---

# H. Stop doing / pièges

- **Watchers globaux** : ne jamais brancher `scan_watch_logs()` sur un cron/loop. Observation sans `task_id` = rejetée.
- **Prompt spaghetti** : pas de logique métier (limites, gates, budgets) dans les prompts. Les prompts décrivent, `policies.yaml` décide.
- **Fichiers monolithiques** : règle dure — aucun fichier > 2k lignes ; nouveau code = nouveau module. Check CI `scripts/check_file_sizes.py` avec allowlist décroissante pour les monolithes existants.
- **Trop de commandes Telegram** : toute nouvelle capacité passe par le classifieur naturel. `/new`, `/libre`, `/status` suffisent presque.
- **Refactor sans tests de caractérisation** : jamais déplacer du code de `run.py`/`telegram.py` sans avoir figé son comportement par un test.
- **Autonomie sans gates** : jamais d'autopilot sur une action non couverte par `policy_engine`. Défaut action inconnue = `ask_human`, jamais `allow`.
- **Repair sans rollback** : patch sans `snapshot_ref` = bug. Rendre impossible structurellement (colonne NOT NULL).
- **Alertes Telegram inutiles** : max 1 message par cycle de repair, agrégé. Le bruit tue la confiance.
- **Config comportementale dans `.env`** : migrer seuils/budgets/flags vers `policies.yaml` versionné.
- **Tout refactorer d'un coup** : `run.py` 18k lignes = extraction incrémentale ou prod cassée.

---

# I. Questions critiques (à trancher avant/pendant les phases)

1. **Cockpit reste-t-il la source of truth des tâches, ou faut-il un mode dégradé gateway-sans-VPS ?** → détermine cache read-only vs réplique.
2. **Multi-utilisateur prévu ?** Si oui, `approvals`, `policies`, mapping chat→task portent un `user_id` dès maintenant.
3. **Le worker de repair utilise-t-il le même provider/modèle LLM que le chat ?** Si budgets différents → budget tokens par `repair_attempt` dans `policies.yaml` dès Phase 4.
4. **Quels dépôts en autopilot ?** Allowlist de repos par mode dans `policies.yaml` (scope par repo vs global).
5. **`hermes-agent` Mac et VPS partagent-ils le même code gateway ?** Définir la référence de déploiement AVANT Phase 1, sinon double refactor.

---

# J. Prompt pour Hermes (implémentation phase par phase)

```
Tu implémentes le plan de refactor "Autonomie V2" décrit dans AUDIT-AUTONOMIE-V2.md, phase par phase.

CONTEXTE
- Gateway: /home/hermes/.hermes/hermes-agent (miroir local Mac: /Users/matthis/.hermes/hermes-agent, branche codex/ops-update-readiness)
- Repo Cockpit: /home/hermes/repo-cockpit
- Source of truth des tâches: SQLite Repo Cockpit. Le gateway ne stocke que le mapping chat→task et un cache handoff.

RÈGLES DURES
1. Une phase à la fois, dans l'ordre. Ne commence pas la suivante sans ma validation.
2. Aucun fichier ne doit grossir: nouveau code = nouveau module. telegram.py et run.py ne reçoivent que des suppressions/réimports.
3. Avant de déplacer du code existant, écris un test de caractérisation qui fige le comportement actuel.
4. Jamais de merge/push/restart de service sans me demander.
5. Toute config comportementale (budgets, seuils, flags) va dans policies.yaml versionné, jamais dans .env.
6. Tout repair doit avoir un snapshot git (snapshot_ref NOT NULL) et un rollback testé.
7. Les observations sans task_id sont rejetées. Pas de watcher global.
8. Fin de chaque phase: rapport court — fichiers créés/modifiés, tests ajoutés (verts), risques restants, commande de rollback.

PHASE EN COURS: Phase 0.
- Ajouter git SHA + start timestamp au /health du Cockpit (backend/app.py) et au health du gateway.
- Écrire scripts/inventory_symbols.py qui liste les fonctions de gateway/run.py et gateway/platforms/telegram.py (nom, lignes, taille).
- Préparer la procédure de restart des services avec tag git + backup SQLite, me la présenter AVANT exécution.
Definition of done: /health renvoie le SHA, inventaire généré, procédure de restart validée par moi et exécutée, smoke test vert (message Telegram → réponse, création tâche → run).
```

---

## Résumé exécutif

La vision et la boucle self-repair sont bonnes ; `libre_orchestrator.py` est le modèle de qualité à généraliser.
Trois vrais blocages : les monolithes (`run.py`/`telegram.py`), l'absence de source of truth unique task/handoff, l'absence de policy engine centralisé.
Commencer par la Phase 0 immédiatement — le restart des services avec version exposée est non négociable avant tout le reste.
