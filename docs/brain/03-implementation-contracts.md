# 03 — Implementation Contracts (NORMATIF)

> Contrats techniques précis. Toute implémentation doit s'y conformer.
> Version des contrats : 1. Tout changement = bump + note de migration ici.

## 1. Task State Machine

Enum unique dans `repo-cockpit/backend/tasks.py` :

```python
class TaskStatus(str, Enum):
    DRAFT = "draft"                        # créée, pas encore planifiée
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    QUEUED_RUNTIME_REPAIR = "queued_runtime_repair"
    RUNNING_RUNTIME_REPAIR = "running_runtime_repair"
    AWAITING_APPROVAL = "awaiting_approval"    # gate humaine en attente
    PAUSED = "paused"                          # switch propre, handoff écrit
    BLOCKED_RUNTIME_REPAIR = "blocked_runtime_repair"
    BLOCKED = "blocked"                        # bloqué hors repair (secret, perm...)
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

LEGAL_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.DRAFT: {QUEUED, CANCELLED},
    TaskStatus.QUEUED: {PLANNING, CANCELLED},
    TaskStatus.PLANNING: {RUNNING, AWAITING_APPROVAL, BLOCKED, CANCELLED},
    TaskStatus.RUNNING: {DONE, FAILED, PAUSED, AWAITING_APPROVAL,
                         QUEUED_RUNTIME_REPAIR, BLOCKED, CANCELLED},
    TaskStatus.QUEUED_RUNTIME_REPAIR: {RUNNING_RUNTIME_REPAIR, CANCELLED},
    TaskStatus.RUNNING_RUNTIME_REPAIR: {RUNNING, BLOCKED_RUNTIME_REPAIR, FAILED},
    TaskStatus.AWAITING_APPROVAL: {RUNNING, PLANNING, CANCELLED, FAILED},  # selon décision
    TaskStatus.PAUSED: {QUEUED, CANCELLED},
    TaskStatus.BLOCKED_RUNTIME_REPAIR: {QUEUED, CANCELLED},   # reprise humaine
    TaskStatus.BLOCKED: {QUEUED, CANCELLED},
}

def transition(task_id: str, to: TaskStatus, *, reason: str, actor: str) -> Task:
    """SEUL point de mutation de statut. Lève IllegalTransition sinon.
    Écrit un event telemetry à chaque appel."""
```

## 2. PolicyEngine

`repo-cockpit/backend/policy_engine.py` :

```python
@dataclass(frozen=True)
class ActionRequest:
    action: str          # merge|push|deploy_prod|restart_service|access_secret|
                         # spend_money|run_repair|delete_outside_repo|force_push|scale_infra
    task_id: str
    repo: str
    mode: str            # ask_review|pilote|autopilot
    details: dict        # contexte libre (branche cible, montant, service...)

@dataclass(frozen=True)
class PolicyDecision:
    decision: str        # "allow" | "ask_human" | "deny"
    matched_rule: str    # id de la règle YAML, ou "default"
    reason: str
    ttl_seconds: int | None = None   # pour les approvals: expiration

def evaluate(req: ActionRequest) -> PolicyDecision:
    """Charge policies.yaml (caché, invalidé par mtime). Défaut absolu:
    action inconnue → ask_human. JAMAIS allow par défaut."""
```

### `policies.yaml` — schéma

```yaml
version: 1
defaults:
  unknown_action: ask_human
budgets:
  max_repair_attempts_per_task: 3
  max_self_reviews_per_run: 3
  repair_attempt_timeout_seconds: 900
rules:
  - id: merge-always-human
    action: merge
    decision: ask_human
  - id: repair-auto-in-autopilot
    action: run_repair
    mode: autopilot
    when: {severity_max: medium}     # high/critical → ask_human
    decision: allow
  - id: secrets-never
    action: access_secret
    decision: ask_human
repos:
  autopilot_allowlist: []            # vide = autopilot demande confirmation par repo
```

## 3. Observation payload v2 (JSON)

POST `/api/internal/tasks/{task_id}/runtime-observations` :

```json
{
  "schema_version": 2,
  "task_id": "t_123",
  "run_id": "r_456",
  "source": "telegram_autonomous_worker",
  "phase": "test",
  "command": "pytest tests/test_auth.py",
  "severity": "medium",
  "raw_excerpt": "<max 4000 chars, tronqué côté émetteur>",
  "detected_at": "2026-07-06T15:00:00Z",
  "fingerprint": "<sha256 hex, calculé côté serveur si absent>"
}
```

Règles serveur : `task_id` inexistant → 404 (pas de création implicite). `schema_version: 1` accepté pendant la transition (phase/command/fingerprint nullables). Rejet 422 si `raw_excerpt` > 4000 chars.

**Payload v1 RÉEL constaté dans `gateway/platforms/telegram.py` (~ligne 7989)** — le serveur doit accepter exactement cette forme pendant la transition :

```json
{"source": "telegram_runtime_observer", "task_id": "...", "report": {"status": "attention", "error_count": 3, "items": [...]}, "captured_at": 1720000000}
```

Mapping v1→v2 côté serveur : chaque `report.items[]` devient une observation, `raw_excerpt = item.line`, `phase/command = null`, fingerprint calculé serveur.

## 4. Algorithme de fingerprint (dédup)

`repo-cockpit/backend/runtime_observations.py::dedupe_fingerprint(raw: str) -> str`

Normalisation, dans l'ordre :
1. Extraire le "cœur" : dernière ligne de type `ExceptionType: message` d'un traceback Python, ou première ligne matchant `error|failed|exception` sinon.
2. Remplacements regex :
   - timestamps ISO/epoch → `<TS>` ; UUIDs → `<UUID>` ; PIDs `pid[= ]\d+` → `<PID>`
   - adresses mémoire `0x[0-9a-f]+` → `<ADDR>` ; ports `:\d{2,5}\b` → `:<PORT>`
   - chemins absolus → garder uniquement les 2 derniers segments
   - nombres isolés > 2 chiffres → `<N>`
3. Lowercase, collapse whitespace.
4. `fingerprint = sha256(f"{exception_type}|{fichier_relatif}|{message_normalisé}")`.

Dédup : même fingerprint + même `task_id` + fenêtre 30 min → `count += 1`, `last_seen` mis à jour, PAS de nouveau repair déclenché si un repair est déjà `queued/running` pour ce fingerprint.

## 5. Runbook YAML — schéma + exemple

Schéma (validé au chargement par `backend/runbooks.py`, rejet si `verify` ou `rollback` absent) :

```yaml
# runbooks/missing_dependency.yaml
name: missing_dependency
version: 1
description: "ModuleNotFoundError / ImportError sur dépendance absente"
triggers:                      # matché par le classifieur d'observations
  categories: [dependency_missing]
preconditions:
  - "manifest de dépendances présent (requirements.txt|pyproject.toml|package.json)"
steps:
  - id: identify
    run: "extraire le nom du module depuis l'observation"
  - id: add
    run: "ajouter le package au manifest (version: dernière compatible)"
  - id: install
    run: "pip install -r requirements.txt  # ou équivalent détecté"
verify:
  - "replay de la commande fautive → exit 0"
  - "detect_and_run_tests() → pas de nouveau rouge"
rollback:
  - "git checkout <snapshot_ref> -- <manifest>"
  - "réinstaller les dépendances d'origine"
escalate_if:
  - "le package n'existe pas sur le registry"
  - "conflit de versions non résoluble automatiquement"
```

Runbooks initiaux à écrire : `missing_dependency`, `port_in_use`, `service_down_restart`, `env_var_missing` (escalade directe — secret possible), `disk_full_cleanup`, `migration_pending`, `deploy_node_app`, `deploy_python_app`.

## 6. Classifieur d'observations — catégories

```
dependency_missing | port_conflict | service_down | syntax_error | test_regression |
config_missing | permission_denied | auth_failure | disk_full | oom | network_transient |
migration_pending | unknown
```

Court-circuits NORMATIFS (jamais de repair, escalade `approval` directe) :
`permission_denied`, `auth_failure`, tout ce qui matche `(401|403|secret|token|api[_ ]?key|password)`.

## 7. Masquage des secrets (transversal, NORMATIF)

Pattern validé par l'état de l'art (OpenHands SDK `SecretRegistry`) :

- Toute chaîne persistée (observations `raw_excerpt`, artifacts, telemetry) ou envoyée sur Telegram passe par `mask_secrets(text) -> str` AVANT écriture.
- Implémentation : `repo-cockpit/backend/secret_masking.py` — regex sur les valeurs des variables d'env chargées + patterns génériques (`(api[_-]?key|token|secret|password|bearer)\s*[:=]\s*\S+`), remplacées par `<secret-hidden>`.
- Test : `test_secret_masking.py` — injecter une valeur d'env dans un faux log, vérifier qu'elle n'apparaît ni en DB ni dans le rapport.
- Le masquage est fait à l'INGESTION, pas à l'affichage : un secret ne doit jamais toucher le disque en clair hors `.env`.

## 8. Runbooks vs Skills existants (clarification)

Le système a DÉJÀ des skills (`~/.hermes/skills/`, bundles `~/.hermes/skill-bundles/architect.yaml` et `deploy.yaml`, avec gates `blocked_auth`/`missing_secret`). Ne pas dupliquer :

- **Skills** (existant, côté agent LLM) : procédures/connaissances injectées dans le contexte du modèle. Restent la référence pour le COMMENT métier (deploy Vercel, Cloudflare...).
- **Runbooks** (nouveau, côté Cockpit) : procédures machine-exécutables du self-repair engine, avec `verify`/`rollback` obligatoires. Un runbook PEUT référencer un skill (`uses_skill: devops/project-hosting-matrix`) pour le contexte LLM de ses étapes non déterministes.
- Les gates des bundles existants (`blocked_auth`, `missing_secret`) doivent être absorbées par le PolicyEngine (§2) — une seule source de décision, les bundles la référencent.

## 9. Contrat de rapport Telegram (OutboundReport)

```python
@dataclass
class OutboundReport:
    kind: str            # progress | repair | blocked | done | approval_request
    task_ref: str
    fait: list[str]      # phrases courtes, langage débutant
    teste: list[str]
    change: list[str]    # fichiers touchés, en relatif
    reste: list[str]
    bloque: str | None   # ce qu'il faut de l'humain, ou None
```

Rendu par `gateway/platforms/telegram/formatting.py` — un seul template, max ~15 lignes, logs bruts jamais inline (lien/artifact sur demande).

## Historique des versions

- v1 (2026-07-06) : version initiale.
