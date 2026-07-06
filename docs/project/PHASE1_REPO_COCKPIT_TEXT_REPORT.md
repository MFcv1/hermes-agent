# Phase 1 — extraction textes Repo Cockpit gateway

Date : 2026-07-06
Branche : `codex/ops-update-readiness`

## Objectif

Continuer Phase 1 selon `AUDIT-AUTONOMIE-V2.md` : sortir du monolithe Telegram les textes purs du flow `/new`, Pilote et sélection repo, sans déplacer les handlers ni modifier l'UX.

## Changement fait

Création du module :

```text
gateway/repo_cockpit_text.py
```

Il contient maintenant :

```text
mode_title()
mode_note()
pilot_intent_title()
pilot_waiting_prompt_text()
repo_selected_text()
new_chat_text()
project_created_text()
tasks_list_text()
audit_task_text()
format_audit_started()
format_audit_completed()
format_audit_blocked()
```

`TelegramAdapter` garde les méthodes historiques :

```text
_mode_title()
_mode_note()
_pilot_intent_title()
_pilot_waiting_prompt_text()
_repo_selected_text()
_new_chat_text()
```

mais elles délèguent maintenant au module dédié.

## Tests ajoutés

```text
tests/gateway/test_repo_cockpit_text.py
```

Couvre :

- titres/notes de modes `ask_review`, `pilote`, `autopilot` ;
- titres de routes Pilote ;
- panneau "Pilote prêt" avec échappement HTML ;
- texte "Repo sélectionné" ;
- texte `/new` avec repo sélectionné.
- texte projet créé et liste `/tasks` ;
- prompt/rapports d'audit dry-run.

## Vérifications

Tests ciblés :

```text
45 passed in 0.40s
```

Tests Phase 1 larges :

```text
239 passed in 1.86s
```

Compile :

```bash
venv/bin/python -m py_compile \
  gateway/platforms/telegram.py \
  gateway/repo_cockpit_text.py \
  gateway/repo_cockpit_keyboards.py \
  gateway/repo_cockpit_formatting.py \
  gateway/repo_cockpit_client.py
```

OK.

## Impact

Avant les extractions texte :

```text
gateway/platforms/telegram.py 9420 lignes
```

Après :

```text
gateway/platforms/telegram.py 9327 lignes
gateway/repo_cockpit_text.py  180 lignes
```

## Risques

Faible : extraction de textes purs uniquement.

Points surveillés :

- Les handlers async restent dans `TelegramAdapter`.
- Le calcul du reasoning Pilote reste côté adapter, puis le texte reçoit une valeur prête.
- Pas de sync/restart VPS effectué pour cette Phase 1 partielle.

## Rollback

Avant push :

```bash
git checkout -- gateway/platforms/telegram.py docs/project/AUTONOMIE_V2_IMPLEMENTATION_STATUS.md docs/project/README.md
rm gateway/repo_cockpit_text.py tests/gateway/test_repo_cockpit_text.py docs/project/PHASE1_REPO_COCKPIT_TEXT_REPORT.md
```

Après commit :

```bash
git revert <commit-phase1-text>
```

## Prochaine étape Phase 1

Prochaine cible recommandée :

```text
inventaire des helpers purs restants dans la zone Repo Cockpit de TelegramAdapter
```

Chercher d'abord les petits helpers sans I/O réseau/bot/persistence. Ne pas déplacer `_handle_callback_query()` ni les flows async tant que les fonctions pures ne sont pas isolées.
