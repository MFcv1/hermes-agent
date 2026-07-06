# 02 — Git Discipline : workflow d'une vraie équipe de dev

> Objectif : branches propres, commits traçables, jamais de switch de projet
> sans état propre + documentation de reprise. C'est ce qui rend la reprise
> de chantier fiable et le travail auditable.

## 1. Nommage des branches (NORMATIF)

```
feat/<task_id>-<slug-court>        nouvelle fonctionnalité
fix/<task_id>-<slug-court>         correction de bug
repair/<task_id>/<attempt_n>       snapshot self-repair (auto, jamais manuel)
refactor/<phase>-<slug>            phases du plan Autonomie V2
chore/<slug>                       maintenance sans task_id
```

- Le `task_id` Cockpit dans le nom de branche = corrélation automatique branche ↔ tâche ↔ conversation Telegram.
- Une branche = une tâche. Si une tâche en révèle une autre → nouvelle tâche Cockpit, nouvelle branche.

## 2. Convention de commits

Format : Conventional Commits + trailer de traçabilité.

```
<type>(<scope>): <résumé impératif, ≤ 72 chars>

<corps: le POURQUOI, pas le quoi — le diff montre déjà le quoi>
<si déviation d'un pattern existant: la nommer et la justifier ici>

Task: <task_id>
Run: <run_id>
```

Types : `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `repair` (réservé au self-repair engine).

Règles :
- **Commits atomiques** : un commit = un changement logique. Jamais "wip" ou "fixes".
- **Tests dans le même commit** que le code qu'ils couvrent.
- Le worker committe à la fin de chaque phase verte (edit ✅ tests → commit), pas en fin de tâche seulement. Ça rend le rollback granulaire.
- **Jamais de `--force` sur une branche partagée.** `--force-with-lease` uniquement sur ses propres branches de travail, et seulement après approbation.

## 3. Protocole de switch propre (LE point critique)

**Règle absolue : on ne quitte JAMAIS un chantier dans un état sale.**
Implémenté comme une checklist bloquante dans le worker (`scripts/worker/engine.py`, fonction `pre_switch_checklist()`), déclenchée par : `/libre`, changement de repo demandé, fin de session, ou tâche mise en pause.

```
PRE-SWITCH CHECKLIST (toutes obligatoires, sinon le switch est refusé et rapporté)
[ ] 1. git status propre: tout est soit committé, soit stashé avec message
       `stash: <task_id> <raison>` — jamais de fichiers orphelins.
[ ] 2. Tests: état connu et documenté (verts, ou rouges LISTÉS dans le handoff
       avec cause probable — jamais "je sais pas pourquoi ça casse").
[ ] 3. Branche pushée sur le remote (backup) — sauf refus explicite policy.
[ ] 4. Handoff écrit (voir §4) et persisté via POST /api/internal/tasks/{id}/handoff.
[ ] 5. Statut Cockpit mis à jour: paused | done | blocked (jamais laissé "running").
[ ] 6. Message Telegram: résumé 5 lignes — fait / testé / reste / où est le code / comment reprendre.
```

Si l'utilisateur insiste pour switcher immédiatement malgré un état sale : l'agent stash tout avec message horodaté, écrit un handoff dégradé marqué `dirty: true`, et prévient explicitement.

## 4. Format du handoff (contenu de `resume_hints_json`)

```json
{
  "schema_version": 1,
  "task_id": "...",
  "branch": "fix/1234-login-crash",
  "last_green_commit": "<sha>",
  "dirty": false,
  "done": ["auth middleware corrigé", "test régression ajouté"],
  "in_progress": "migration du handler legacy — 60%, reste le cas OAuth",
  "next_steps": ["finir cas OAuth dans auth/oauth.py:handle_callback",
                 "relancer tests/test_auth.py::test_oauth_flow"],
  "known_failures": [{"test": "test_oauth_flow", "cause_probable": "mock token expiré"}],
  "decisions": ["choisi PyJWT plutôt que python-jose car déjà en dépendance"],
  "traps": ["ne pas toucher config/session.py — utilisé aussi par le cron X"],
  "reasoning_mode_at_pause": "deep"
}
```

À la reprise, ce JSON est injecté dans le contexte du worker AVANT toute action. Le champ `traps` est le plus précieux : il évite de re-découvrir les pièges.

## 5. Migration / déplacement de code

Pour les extractions de modules (Phases 1-3 de l'audit) :

1. Commit 1 : test de caractérisation du comportement actuel (rouge interdit).
2. Commit 2 : déplacement mécanique pur (git détecte le rename → reviewable).
3. Commit 3+ : modifications éventuelles, séparées du déplacement.
4. **Jamais** déplacement + modification dans le même commit — ça rend le diff illisible et le rollback impossible à cibler.

## 6. Gates humaines git (rappel policy)

| Action | Gate |
|---|---|
| commit sur branche de travail | auto |
| push branche de travail | auto (backup) |
| merge vers main/master | **ask_human, toujours** |
| push --force-with-lease | ask_human |
| tag / release | ask_human |
| suppression de branche distante | ask_human |
| rebase de branche partagée | deny |

## Leçons apprises

- (vide — format : `YYYY-MM-DD [task_id] — leçon`)
