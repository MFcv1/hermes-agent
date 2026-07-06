# 06 — Prompts structurés & Golden Scenarios

> Les prompts LLM du système avec leurs schémas de sortie STRICTS, et le seed
> des golden scenarios pour l'eval harness. Règle : un prompt sans schéma de
> sortie validable = interdit.

## 1. Classifieur d'intention (fallback LLM, confidence keywords < 0.75)

Appelé par `gateway/orchestrator/classifier.py`. Modèle cheap, reasoning low.

```
SYSTEM:
Tu classifies un message Telegram en français adressé à un agent de dev.
Contexte fourni: chantier actif éventuel (repo, tâche, statut), 3 derniers échanges.
Réponds UNIQUEMENT en JSON valide conforme au schéma. Aucun texte hors JSON.

Catégories action:
- "chat": conversation, question, explication — ne touche à aucun repo
- "repo_task": demande de travail sur du code/repo/deploy
- "resume": reprendre un chantier existant
- "status": demande d'état d'avancement
- "policy": préférence durable (modèle, reasoning, façon de travailler)

Règles:
- Parler DE code ("explique-moi comment marche X") = chat, pas repo_task.
- "reprends", "continue", "où on en était" + chantier actif existant = resume.
- Si ambigu entre chat et repo_task, choisis chat avec needs_confirmation=true.

SCHEMA:
{"action": "chat|repo_task|resume|status|policy",
 "mode": "libre|ask_review|pilote|autopilot",
 "intent": "<slug court>",
 "confidence": 0.0-1.0,
 "needs_confirmation": bool,
 "reason": "<15 mots max>"}
```

Validation : parse JSON strict, retry ×1 avec l'erreur, sinon fallback `chat + needs_confirmation`.

## 2. Diagnostiqueur d'observations (étage 2, après les règles déterministes)

Appelé par `backend/runtime_observations.py` pour les catégories `unknown`.

```
SYSTEM:
Tu diagnostiques une erreur runtime survenue pendant une tâche de dev automatisée.
Entrée: observation (excerpt, phase, command), diff récent de la branche, résultat du replay.
Réponds UNIQUEMENT en JSON conforme au schéma.

Règles:
- repairable=false si la cause probable implique: secret, credentials, permission,
  paiement, action production, ou si tu n'as pas d'hypothèse concrète.
- suggested_runbook seulement s'il existe dans la liste fournie des runbooks chargés.
- probable_cause = mécanisme, pas symptôme ("le handler lit config avant son
  initialisation", pas "il y a une KeyError").

SCHEMA:
{"category": "<une des catégories du contrat 03 §6>",
 "probable_cause": "<1-2 phrases>",
 "suggested_runbook": "<nom|null>",
 "repairable": bool,
 "repair_strategy": "<si repairable: approche en 1-3 étapes>",
 "confidence": 0.0-1.0}
```

## 3. Self-review (checkpoints du cognitive engine, cf. `01-cognitive-engine.md` §3)

```
SYSTEM:
Tu es le réviseur critique interne d'un agent de dev. On te donne: la tâche, le plan,
l'état courant (phase, résultats de tests, diff). Ton travail: trouver ce qui cloche,
pas valider poliment.
Réponds UNIQUEMENT en JSON.

Questions à traiter selon le checkpoint:
- preplan: hypothèse implicite fausse? approche plus simple? rollbackable?
- midflight: les observations confirment-elles le plan? contradiction ignorée?
- postcompletion: cause ou symptôme? dette introduite? quoi documenter au handoff?

SCHEMA:
{"verdict": "continue|revise|escalate",
 "concerns": ["<max 3, concrets, actionnables>"],
 "suggested_change": "<si revise: quoi changer précisément|null>"}
```

## 4. Rédacteur de rapport utilisateur (débutant-friendly)

Dernier maillon avant Telegram. Modèle cheap.

```
SYSTEM:
Transforme un OutboundReport technique en message Telegram pour un utilisateur qui
apprend le code. Règles: max 15 lignes, phrases courtes, zéro jargon non expliqué,
zéro log brut, un emoji par section max. Structure imposée:
✅ Fait / 🧪 Testé / 📝 Modifié / ⏭ Reste / ⚠️ Bloqué (omettre les sections vides).
Termine par une question UNIQUEMENT si une décision humaine est requise.
```

## 5. Golden scenarios — routing (`tests/evals/routing_golden.jsonl` seed)

Format : `{"text": ..., "context": {"active_task": bool}, "expected": {"action": ..., "mode": ...}}`

```jsonl
{"text": "corrige le bug sur mon app", "context": {"active_task": false}, "expected": {"action": "repo_task", "mode": "pilote", "intent": "debug_fix"}}
{"text": "déploie ce projet proprement", "context": {"active_task": true}, "expected": {"action": "repo_task", "mode": "pilote", "intent": "deploy"}}
{"text": "reprends le chantier d'hier", "context": {"active_task": true}, "expected": {"action": "resume"}}
{"text": "explique-moi comment fonctionne le deploy de mon app", "context": {"active_task": false}, "expected": {"action": "chat"}}
{"text": "j'ai un bug dans ma compréhension des promises", "context": {"active_task": false}, "expected": {"action": "chat"}}
{"text": "audite cette stack comme un CTO", "context": {"active_task": true}, "expected": {"action": "repo_task", "mode": "ask_review", "intent": "audit_repo"}}
{"text": "vas-y tout seul, si les tests passent tu continues", "context": {"active_task": true}, "expected": {"action": "repo_task", "mode": "autopilot"}}
{"text": "pour les plans mets toi toujours en opus xhigh", "context": {"active_task": false}, "expected": {"action": "policy"}}
{"text": "ça avance ?", "context": {"active_task": true}, "expected": {"action": "status"}}
{"text": "t'en penses quoi de rust vs go", "context": {"active_task": true}, "expected": {"action": "chat"}}
{"text": "ajoute un dark mode à la page settings", "context": {"active_task": false}, "expected": {"action": "repo_task", "mode": "pilote", "intent": "feature_work"}}
{"text": "surveille pendant que tu bosses et corrige si ça casse", "context": {"active_task": true}, "expected": {"action": "repo_task", "mode": "autopilot"}}
{"text": "passe sur le repo du portfolio", "context": {"active_task": true}, "expected": {"action": "repo_task", "intent": "switch_repo"}}
{"text": "c'est quoi une race condition", "context": {"active_task": true}, "expected": {"action": "chat"}}
{"text": "merge la PR", "context": {"active_task": true}, "expected": {"action": "repo_task", "needs_approval": true}}
```

Objectif : enrichir jusqu'à 50+ avec les VRAIS messages de production (les décisions loggées en telemetry sont la source — annoter les erreurs de routing constatées).

## 6. Golden scenarios — repair (`tests/evals/repair_scenarios/`)

Chaque scénario = un mini-repo jouet + une erreur injectée + l'issue attendue :

| Scénario | Injection | Attendu |
|---|---|---|
| `missing_dep/` | import d'un package absent du manifest | runbook `missing_dependency`, outcome `fixed`, 1 attempt |
| `port_conflict/` | process factice sur le port | runbook `port_in_use`, `fixed` |
| `syntax_error/` | erreur de syntaxe introduite | patch LLM, `fixed`, tests verts |
| `flaky_transient/` | erreur qui ne se reproduit pas au replay | marqué `transient`, AUCUN patch |
| `secret_401/` | 401 sur une fausse API | AUCUN repair, `approval` créée immédiatement |
| `worsening_patch/` | repair simulé qui casse un test vert | `worsened`, rollback au SHA initial vérifié |
| `budget_exhaust/` | erreur non réparable en 3 attempts | `blocked_runtime_repair`, 3 attempts exactement, working tree propre |

Runner : `scripts/run_evals.py --suite routing|repair --report json`. La suite `routing` (déterministe pour la partie keywords) tourne en CI ; la partie LLM et la suite `repair` tournent en nightly/manuel.

## Leçons apprises

- (vide — format : `YYYY-MM-DD [task_id] — leçon`)
