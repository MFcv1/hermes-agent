# Prompt Claude Fable — audit architecture Hermes / Repo Cockpit pour autonomie maximale

Date de préparation : 2026-07-06 16:46 CEST

> Objectif : envoyer ce prompt à Claude Fable pour obtenir un audit stratégique + technique de la structure actuelle, puis un plan d’amélioration vers un agent Telegram/VPS beaucoup plus autonome, polyvalent, fiable et sûr.

---

## Prompt à copier dans Claude Fable

Tu es Claude Fable. Je veux que tu agisses comme **architecte principal d’un système d’agents autonomes de production**, avec une exigence forte sur : autonomie réelle, fiabilité, sécurité, UX Telegram, orchestration multi-agent, capacité de self-repair, mémoire/reprise de contexte, observabilité, et facilité d’usage pour un utilisateur débutant.

Tu dois auditer l’architecture existante ci-dessous et me dire **comment l’améliorer en profondeur**. Je ne veux pas une réponse vague. Je veux un diagnostic précis, priorisé, avec architecture cible, phases de migration, risques, garde-fous et fichiers/sous-systèmes à modifier.

Réponds en français, de manière directe, structurée, sans bullshit. Si une idée est mauvaise, dis-le clairement. Si une partie est déjà correcte, dis-le aussi.

---

## 1. Vision produit recherchée

Je construis un système type **“Emily / Cavalier / Écurie”** :

- un agent Telegram 24/7 ;
- capable de discuter naturellement ;
- capable de piloter des travaux de code/repo via Repo Cockpit ;
- capable de choisir entre Ask Review, Pilote, Autopilot ;
- capable de reprendre un chantier sans perdre le contexte ;
- capable de voir les erreurs pendant qu’il travaille ;
- capable de corriger ces erreurs dans le flux, pas juste envoyer une alerte ;
- capable de demander à l’humain seulement quand il faut vraiment une décision, un secret, une permission ou un choix produit ;
- capable d’expliquer simplement ce qu’il fait à un utilisateur qui apprend le code ;
- capable d’évoluer vers une autonomie polyvalente : dev, debug, déploiement, audit, monitoring, documentation, gestion de tâches, génération de plans, sécurité.

Je ne veux pas un bot qui attend des commandes partout. Je veux un agent qui comprend naturellement :

```text
“corrige le bug sur mon app”
“déploie ce projet proprement”
“surveille pendant que tu travailles et corrige si ça casse”
“reprends le chantier d’hier”
“audite cette stack comme un CTO”
```

Le mot clé : **autonomie utile, task-scoped, avec garde-fous**.

---

## 2. Environnement réel

### Machine locale Mac

Repo Hermes principal :

```text
/Users/matthis/.hermes/hermes-agent
/Users/matthis/Desktop/Hermes Agent Project  # symlink/canonique côté utilisateur
```

Branche locale actuelle :

```text
codex/ops-update-readiness
```

Fichiers locaux importants :

```text
gateway/platforms/telegram.py                    # adapter Telegram, gros fichier historique
gateway/libre_orchestrator.py                    # Libre V2, classifier, ActiveWorkStore, Watch diagnostic
hermes_cli/commands.py                           # registry slash commands
gateway/slash_commands.py                        # slash commands gateway
gateway/run.py                                   # gateway backend historique
tests/gateway/test_telegram_pilot_mode.py        # tests Telegram / Pilote / runtime observer
tests/gateway/test_libre_orchestrator.py         # tests Libre orchestrator
HANDOFF-CODEX.md                                 # handoff principal
handoff-repo-cockpit/docs/STATE.md               # état Repo Cockpit
```

État test local actuel :

```bash
python -m pytest tests/gateway/test_telegram_pilot_mode.py tests/gateway/test_telegram_conv_ux.py tests/gateway/test_libre_orchestrator.py tests/gateway/test_telegram_model_picker.py -q -o 'addopts='
# 40 passed in ~1.09s
```

Compile locale actuelle :

```bash
python -m py_compile gateway/libre_orchestrator.py gateway/platforms/telegram.py gateway/platforms/telegram_models_config.py hermes_cli/commands.py
# OK
```

### VPS

Host :

```text
134.122.73.242
user applicatif : hermes
```

Repo Cockpit :

```text
/home/hermes/repo-cockpit
```

Gateway Hermes sur VPS :

```text
/home/hermes/.hermes/hermes-agent
```

Services user systemd actifs au moment du snapshot :

```text
hermes-gateway.service       active depuis 2026-07-01 ; code syncé récemment mais service pas redémarré après sync
hermes-repo-cockpit.service  active depuis 2026-07-01 ; backend/worker modifiés récemment mais service pas redémarré après sync
```

Important : le code a été syncé/testé, mais les services live n’ont pas été redémarrés pendant cette passe pour éviter de toucher la prod sans validation.

---

## 3. Ce qui existe déjà

### 3.1 Modes Repo Cockpit

Le système distingue :

```text
ask_review  # produit plan/review, bloque avant action irréversible
pilote      # guide l’utilisateur, pose questions si contexte insuffisant
autopilot   # avance plus loin avec gates, PR/preview/validation selon règles
```

Objectif UX :

```text
/new → choix mode → choix modèle/reasoning → source projet → prompt naturel → worker
```

### 3.2 `/libre` / Libre V2

`/libre` n’est pas censé hard-reset l’agent. Il doit :

- sortir proprement d’un flow actif ;
- nettoyer les états transitoires Telegram/wizard ;
- créer un handoff/reprise ;
- garder la mémoire durable ;
- permettre ensuite un chat naturel ;
- router naturellement vers Ask Review / Pilote / Autopilot si le message parle clairement d’un repo.

Fichier :

```text
gateway/libre_orchestrator.py
```

Fonctions importantes :

```text
classify_libre_message()
extract_learning_policy()
ActiveWorkStore
scan_watch_logs()
```

Note importante : `/libre watch` existe mais doit rester **diagnostic manuel**, pas le modèle d’autonomie principal.

### 3.3 Runtime observation pendant le travail

La direction correcte :

```text
agent travaille
↓
worker actif
↓
les logs sont observés pendant l’exécution
↓
erreur détectée
↓
erreur attachée à la tâche active
↓
worker tente réparation dans le contexte
```

Côté Telegram gateway local/VPS :

```text
gateway/platforms/telegram.py
```

Repères dans le fichier local actuel :

```text
runtime_observer autour des lignes ~7973-8024
runtime-observations autour de ~7998
/libre autour de ~8299 et ~9116
```

Le gateway passe au worker :

```json
{
  "runtime_observer": {
    "enabled": true,
    "task_id": "...",
    "source": "telegram_autonomous_worker",
    "mode": "during_work"
  }
}
```

Il attache les erreurs à :

```text
/api/internal/tasks/{task_id}/runtime-observations
```

### 3.4 Runtime Self-Repair côté Repo Cockpit VPS

Fichiers VPS modifiés :

```text
/home/hermes/repo-cockpit/backend/app.py
/home/hermes/repo-cockpit/scripts/operation_worker.py
/home/hermes/repo-cockpit/tests/test_runtime_self_repair.py
/home/hermes/repo-cockpit/tests/runtime_self_repair_smoke.py
```

Repères :

```text
backend/app.py:
- RuntimeObservationRequest
- table runtime_observations
- endpoint /api/internal/tasks/{task_id}/runtime-observations
- /api/worker/run-once accepte runtime_observer

scripts/operation_worker.py:
- consume_runtime_observations()
- runtime_observations_as_failed_tests()
- hermes_remediate_runtime()
- try_runtime_self_repair()
- --runtime-observer
- statuts queued_runtime_repair / blocked_runtime_repair
```

Flux actuel :

```text
runtime_observation reçue
↓
stockée dans runtime_observations
↓
si tâche non running → status queued_runtime_repair
↓
worker lancé avec --runtime-observer --task-id
↓
consume_runtime_observations()
↓
runtime_observations_as_failed_tests()
↓
hermes_remediate_runtime()
↓
detect_and_run_tests()
↓
continue ou blocked_runtime_repair
```

Preuves VPS :

```bash
cd /home/hermes/repo-cockpit
.venv/bin/python -m py_compile backend/app.py scripts/operation_worker.py tests/test_runtime_self_repair.py
# OK

PYTHONPATH=/home/hermes/repo-cockpit .venv/bin/python tests/runtime_self_repair_smoke.py
# runtime self-repair remote smoke OK
```

Gateway VPS compile :

```bash
sudo -iu hermes bash -lc "cd ~/.hermes/hermes-agent && python3 -m py_compile gateway/platforms/telegram.py gateway/libre_orchestrator.py"
# OK
```

---

## 4. Contraintes fortes à respecter

### Sécurité / prod

- Ne jamais exposer ni copier de secrets : tokens Telegram, API keys, OAuth, `.env`, auth.json.
- Si un secret manque, l’agent doit demander/indiquer la variable, pas inventer.
- Ne jamais merge/push/release automatiquement sans gate humaine explicite.
- Les actions dangereuses doivent passer par approvals/gates.
- Les redémarrages live doivent être explicites, tracés, avec rollback.

### Autonomie

- L’autonomie doit être **task-scoped**, pas “watcher global qui spamme”.
- Les erreurs doivent être reliées à une tâche active, un repo, une phase, une cause probable, un runbook.
- L’agent doit corriger dans le flux s’il peut le faire sans secret/permission humaine.
- Il doit savoir s’arrêter proprement si la réparation devient risquée.

### UX Telegram

- L’utilisateur ne doit pas devoir connaître des commandes complexes.
- Les boutons sont acceptables, mais le naturel doit être prioritaire.
- Le bot doit expliquer simplement : action en cours, blocage, décision requise, résultat.
- Éviter le bruit : pas de spam de logs bruts.
- Les rapports doivent être lisibles pour débutant.

### Maintenabilité

- `gateway/platforms/telegram.py` est devenu très gros (~10k lignes) : risque de dette majeure.
- `gateway/run.py` est très gros (~18k lignes) : risque de dette majeure.
- `backend/app.py` et `operation_worker.py` grossissent côté Repo Cockpit : besoin de modularisation.
- Il faut préserver tests et behavior existants.

---

## 5. Problèmes ou limites déjà visibles

Je veux ton avis sur ces points :

1. **Telegram adapter énorme**  
   `gateway/platforms/telegram.py` centralise trop de responsabilités : UI Telegram, callbacks, mode/pilot/libre, runtime observer, Repo Cockpit client, formatting, state machine. Comment découper proprement sans tout casser ?

2. **Repo Cockpit backend/worker monolithiques**  
   `backend/app.py` et `scripts/operation_worker.py` portent API, DB schema, worker, runbooks, deploy, tests, PR, repair. Quelle architecture modulaire proposer ?

3. **Runtime Self-Repair V1 encore simpliste**  
   Aujourd’hui les observations runtime sont transformées en “failed test-like payload”. C’est pratique, mais peut-être trop pauvre. Comment améliorer classification, causalité, runbooks, preuve, rollback ?

4. **Pas encore de vraie boucle agentique robuste**  
   Il faut peut-être un orchestrateur explicite : Plan → Act → Observe → Diagnose → Repair → Verify → Report → Handoff. Où le mettre ? Gateway, Repo Cockpit, Hermes core, ou nouveau service ?

5. **Mémoire / reprise chantier**  
   ActiveWorkStore existe côté Libre, mais l’état réel est dispersé : sessions Hermes, operation_queue, thread_events, resume_md, runtime_observations. Comment unifier sans créer un monstre ?

6. **Autonomie polyvalente**  
   L’agent doit gérer dev, audit, deploy, infra, docs, monitoring. Faut-il des “skills/runbooks” plus formels, un planner, un router, un registry d’intents, un workflow engine ?

7. **Garde-fous**  
   Comment définir clairement : ce que l’agent peut faire seul, ce qui demande validation, ce qui est interdit ?

8. **Observabilité**  
   Comment faire des logs/metrics/traces utiles : task_id, phase, repo, event, observation, repair attempt, test evidence, notification Telegram ?

9. **Évaluation**  
   Comment tester l’autonomie ? Tests unitaires, smoke tests, scénarios simulés, chaos tests, replay de logs, evals d’intents, golden transcripts Telegram ?

10. **Déploiement / rollout**  
   Comment migrer vers l’architecture cible sans casser le bot live ?

---

## 6. Ce que je veux dans ta réponse

Structure ta réponse comme ceci :

### A. Diagnostic brutal de l’architecture actuelle

- Ce qui est sain.
- Ce qui est fragile.
- Ce qui va bloquer l’autonomie à moyen terme.
- Ce qui est dangereux en prod.
- Ce qui est surtout de la dette technique.

### B. Architecture cible recommandée

Propose une architecture claire avec composants nommés. Par exemple, mais pas limité à :

```text
Telegram Gateway / UX Adapter
Conversation Orchestrator
Repo Cockpit API
Task Runtime Engine
Observation Bus
Runtime Self-Repair Engine
Policy & Safety Gate Engine
Memory / Handoff Store
Skill/Runbook Registry
Evaluation Harness
Telemetry Store
```

Pour chaque composant :

- responsabilités ;
- entrées/sorties ;
- fichiers/modules existants à migrer ;
- données persistées ;
- tests attendus ;
- erreurs à éviter.

### C. Modèle de données cible

Propose les tables ou objets principaux :

```text
tasks
runs
observations
repair_attempts
approvals
handoffs
policies
runbooks
artifacts
evaluations
```

Explique les relations et ce qui doit être source of truth.

### D. Boucle d’autonomie cible

Décris exactement une boucle :

```text
Receive user intent
↓
Classify intent + risk
↓
Create/attach active task
↓
Plan
↓
Act
↓
Observe runtime
↓
Diagnose
↓
Repair if allowed
↓
Verify
↓
Report
↓
Persist handoff/memory
```

Dis où implémenter chaque étape.

### E. Runtime Self-Repair V2

Propose comment passer de la V1 actuelle à une V2 sérieuse :

- meilleure classification ;
- observation bus ;
- corrélation logs ↔ task ↔ phase ↔ command ;
- replay d’erreur ;
- runbooks ;
- patch/test/retry ;
- limites d’itérations ;
- rollback ;
- résumé utilisateur ;
- escalade humaine.

### F. Plan de refactor priorisé

Donne un plan en phases :

```text
Phase 0 — stabilisation et inventaire
Phase 1 — extraction modules gateway
Phase 2 — observation bus + contrats
Phase 3 — worker runtime engine
Phase 4 — self-repair V2
Phase 5 — memory/handoff unifié
Phase 6 — eval harness + golden scenarios
Phase 7 — autonomy dashboard / admin UX
```

Pour chaque phase :

- objectif ;
- fichiers à créer/modifier ;
- tests ;
- risques ;
- rollback ;
- définition de “done”.

### G. Quick wins

Liste 10 améliorations concrètes faisables rapidement, avec ordre de priorité.

### H. Stop doing / pièges

Liste ce qu’il ne faut surtout pas faire : watchers globaux, prompt spaghetti, fichiers monolithiques, trop de commandes, absence de tests, etc.

### I. Questions critiques à me poser

Pose uniquement les questions qui changent vraiment l’architecture.

### J. Prompt pour aider Hermes ensuite

À la fin, donne un prompt court que je pourrai renvoyer à Hermes pour implémenter ta recommandation phase par phase.

---

## 7. Niveau de précision attendu

Je veux une réponse actionnable. Évite :

```text
“il faudrait améliorer la modularité”
“ajouter plus de tests”
“mettre de l’observabilité”
```

Remplace par :

```text
Créer gateway/telegram/libre_handler.py avec telle interface.
Extraire gateway/repo_cockpit_client.py.
Créer repo-cockpit/backend/runtime_observations.py.
Ajouter test X qui simule une erreur pendant worker et vérifie queued_runtime_repair.
Ajouter table observation_events avec colonnes exactes.
```

Je veux que ta réponse aide directement Hermes/Codex à implémenter.

---

## 8. Critères de succès finaux

À terme, le système est réussi si :

1. Je peux parler naturellement à Telegram sans connaître les commandes.
2. L’agent sait choisir chat normal / ask_review / pilote / autopilot.
3. Il conserve et reprend proprement un chantier actif.
4. Pendant qu’il travaille, il observe les erreurs liées à sa tâche.
5. Il corrige automatiquement les erreurs simples/moyennes.
6. Il bloque proprement sur secrets, permissions, paiements, merges, prod dangereuse.
7. Il explique clairement ce qu’il a fait, testé, modifié, et ce qui reste.
8. Les changements sont testés, traçables, rollbackables.
9. Les fichiers ne deviennent pas des monolithes impossibles à maintenir.
10. Le système devient progressivement plus autonome via skills/runbooks/evals, pas via hacks ponctuels.

---

## 9. Résumé très court de la demande

Audit tout ce système comme si tu devais en faire un agent Telegram autonome de production, capable de coder, corriger, déployer, auditer, se réparer dans le flux, et rester sûr. Donne une architecture cible + plan de refactor concret + ordre d’implémentation.
