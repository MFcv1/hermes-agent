# 04 — Cost Engine : gestion des coûts LLM et infra

> Objectif : l'agent connaît et gère son propre coût. Pas de tâche qui brûle
> silencieusement des tokens, pas de modèle premium pour des tâches triviales.

## 1. Matrice de sélection de modèle (liée aux modes cognitifs)

La matrice est définie en TIERS agnostiques ; le mapping tier→modèle vit dans `policies.yaml`
(section `model_tiers`), jamais en dur dans le code ou les prompts. Stack actuelle : GPT (OpenAI)
+ Composer 2.5 (Grok/xAI). Changer de provider = changer le YAML, rien d'autre.

| Mode cognitif | Tier | Reasoning effort | Usage type |
|---|---|---|---|
| `reflex` | `fast` | low/none | runbooks, formatting, classification, rapports utilisateur |
| `standard` | `mid` | medium | features simples, bugfixes localisés, exécution rapide |
| `deep` | `premium` | high | archi, bugs complexes, refactors |
| `adversarial` | `premium` | xhigh | échecs répétés, prod |

```yaml
# policies.yaml — mapping actuel (à ajuster selon les prix/perfs du moment)
model_tiers:
  fast: gpt-5-mini            # ou équivalent cheap du provider
  mid: composer-2.5           # rapide et fort en exécution de code
  premium: gpt-5.5            # meilleur raisonnement disponible
```

Règle de choix entre providers à tier égal : Composer/Grok pour l'exécution de code rapide
(edits, itérations courtes), GPT pour le raisonnement long et la planification. Ajuster selon
les observations réelles loggées en telemetry (`purpose` par appel).

Overrides : les `policies` apprises via `extract_learning_policy()` (préférences utilisateur par scope planning/deployment/debugging) priment sur la matrice. Persistées et versionnées, jamais en dur dans les prompts.

## 2. Budgets (dans `policies.yaml`, section `budgets`)

```yaml
budgets:
  # par run
  max_tokens_per_run: 400000
  max_self_reviews_per_run: 3
  # par repair attempt
  max_tokens_per_repair_attempt: 150000
  repair_attempt_timeout_seconds: 900
  # par tâche
  max_repair_attempts_per_task: 3
  soft_cost_alert_per_task_usd: 5.00      # alerte Telegram, continue
  hard_cost_stop_per_task_usd: 15.00      # AWAITING_APPROVAL pour continuer
  # par jour (global)
  daily_soft_alert_usd: 20.00
  daily_hard_stop_usd: 50.00              # nouvelles tâches refusées sauf approval
```

Comportement :
- **Soft** : message Telegram informatif, le travail continue.
- **Hard** : transition `AWAITING_APPROVAL` avec rapport (dépensé, restant à faire, estimation). L'utilisateur peut relever le plafond ponctuellement.

## 3. Tracking

Table `events` (telemetry) avec `kind=llm_call` :

```
llm_call: {task_id, run_id, model, input_tokens, output_tokens,
           cost_usd_estimated, purpose (plan|act|repair|self_review|classify)}
```

Agrégations exposées :
- `GET /api/internal/costs/daily` — coût du jour par tâche/modèle.
- Intégré au `/status` Telegram : `💰 Aujourd'hui: $X.XX (tâche active: $Y.YY)`.

## 4. Règles anti-gaspillage

1. **Classification en cascade** : keywords (gratuit) → modèle cheap → modèle premium seulement si toujours ambigu. Jamais premium en premier pour classifier.
2. **Contexte minimal** : le worker charge les fichiers pertinents, pas le repo entier. Le handoff (`resume_hints_json`) sert exactement à ça.
3. **Cache des diagnostics** : même fingerprint d'observation déjà diagnostiqué → réutiliser le diagnostic (table `observations`, champ `diagnosis_json`), pas de nouvel appel LLM.
4. **Pas de retry LLM aveugle** : un appel qui échoue en format → 1 retry avec le message d'erreur, puis fallback déterministe ou escalade. Jamais 5 retries identiques.
5. **Self-reviews plafonnées** (3/run) : au-delà, c'est que le mode cognitif est mal choisi → escalade de mode plutôt que reviews en boucle.

## 5. Estimation avant exécution (modes deep/adversarial)

Avant de lancer un plan en mode deep+, le worker produit une estimation grossière (`{phases: n, est_tokens: range, est_cost_usd: range}`) incluse dans le message de plan Telegram. En pilote, l'utilisateur valide en connaissance de cause. En autopilot, l'estimation sert de baseline : dépassement ×2 → checkpoint self-review obligatoire.

## Leçons apprises

- (vide — format : `YYYY-MM-DD [task_id] — leçon`)
