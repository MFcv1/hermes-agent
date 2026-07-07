# docs/project — Documentation produit Hermes (Matthis)

Docs spécifiques au projet Hermes/Telegram/Repo Cockpit de Matthis,
séparés des docs d'ingénierie du repo (contrats, design, security — qui restent
directement dans `docs/`).

## Organisation

| Dossier | Contenu |
|---|---|
| `audits/` | Audits datés (accessibilité, features & learnings...) |
| `roadmaps/` | Roadmaps d'implémentation datées |
| `prompts/` | Prompts de cadrage envoyés aux agents (Claude Fable, Pilot Mode...) |
| `handoffs/` | Handoffs inter-agents (Codex, Repo Cockpit STATE...) |
| `notes/` | Notes de travail diverses |
| `PHASE0_RESTART_PROCEDURE.md` | Procédure préparée pour restart VPS Phase 0 (validation humaine requise). |
| `PHASE0_COMPLETION_REPORT.md` | Rapport Phase 0 : push, restart VPS, health, risques, rollback. |
| `PHASE1_FORMATTING_EXTRACTION_REPORT.md` | Rapport Phase 1 partielle : extraction formatting Telegram. |
| `PHASE1_REPO_COCKPIT_CLIENT_REPORT.md` | Rapport Phase 1 partielle : extraction client HTTP Repo Cockpit gateway. |
| `PHASE1_REPO_COCKPIT_FORMATTERS_REPORT.md` | Rapport Phase 1 partielle : extraction formatters panels/status/PR Repo Cockpit. |
| `PHASE1_REPO_COCKPIT_KEYBOARDS_REPORT.md` | Rapport Phase 1 partielle : extraction builders de keyboards Repo Cockpit. |
| `PHASE1_REPO_COCKPIT_TEXT_REPORT.md` | Rapport Phase 1 partielle : extraction textes `/new`, Pilote, sélection repo. |
| `PHASE1_COMPLETION_REPORT.md` | Rapport de fin locale Phase 1 : `telegram.py` sous 2k lignes, mixins extraits, tests verts. |
| `PHASE2_OBSERVATION_BUS_REPORT.md` | Rapport Phase 2 locale : reporter gateway, payload v2, compat v1, masquage. |
| `PHASE3_WORKER_RUNTIME_ENGINE_REPORT.md` | Rapport Phase 3 VPS : state machine, runs, CommandSpan, corrélation observations. |
| `PHASE4_SELF_REPAIR_V2_REPORT.md` | Rapport Phase 4 VPS : policy engine, self-repair v2, snapshots, rollback, budget. |
| `PHASE5_MEMORY_HANDOFF_STORE_REPORT.md` | Rapport Phase 5 VPS : handoffs Cockpit, cache SQLite gateway, reprise via parent task. |
| `PHASE6_EVAL_HARNESS_REPORT.md` | Rapport Phase 6 VPS : golden routing, scénarios repair, table evaluations Cockpit. |
| `PHASE7_AUTONOMY_STATUS_UX_REPORT.md` | Rapport Phase 7 VPS : `/status` Telegram enrichi, payload autonomy complet. |
| `RUNBOOK_REGISTRY_REPORT.md` | Rapport quick win 9 VPS : registry de runbooks YAML validés, table `runbooks`. |
| `TELEMETRY_STORE_REPORT.md` | Rapport quick win 11 VPS : store `events`, coûts journaliers, sink telemetry gateway. |
| `COST_DASHBOARD_REPORT.md` | Rapport quick win 12 VPS : dashboard coût jour/task/modèle depuis telemetry. |
| `SELFOPS_TELEMETRY_AUTONOMY_REPORT.md` | Rapport quick win 13 VPS : heartbeat self-ops, timeline telemetry, eval events, cost guard. |
| `SELFOPS_ACTIONS_TELEMETRY_REPORT.md` | Rapport quick win 14 VPS : actions self-ops sûres, analytics task, eval report, cost guard enforce. |
| `AUTONOMIE_V2_IMPLEMENTATION_STATUS.md` | Point de reprise consolidé : phases, preuves, prochaine cible. |
| `autonomie-v2-symbol-inventory.json` | Inventaire statique des symboles `gateway/run.py` et `gateway/platforms/telegram.py`. |

## Documents de référence (ailleurs)

- **`/AUDIT-AUTONOMIE-V2.md`** (racine) — plan directeur Autonomie V2.
- **`docs/brain/`** — bibliothèque d'implémentation normative (commencer par `00-INDEX.md`).

## Règles

- Fichiers datés : garder le format `SUJET_YYYY-MM-DD.md`.
- Un doc obsolète n'est pas supprimé : préfixer le titre de `[OBSOLETE]` et pointer vers son remplaçant.
- Ne PAS déplacer les contrats de `docs/` référencés par le code/tests
  (`relay-connector-contract.md`, `chronos-managed-cron-contract.md`, `session-lifecycle.md`).
