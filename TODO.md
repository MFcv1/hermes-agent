# TODO — Work Sessions + Codex Supervisor Mode

Objectif : créer un système global de sessions de travail, comparable aux
clavardages Codex, pour structurer tous les workflows Hermes/Codex au lieu de
dépendre du fil Telegram unique.

`@supervisormode` doit être le premier consommateur de ce modèle, pas un silo
séparé.

## 1. Sessions de travail structurées

- [ ] Créer un store de sessions de travail côté Codex/Hermes :
  - `work_session_id` ;
  - titre lisible ;
  - statut ;
  - type de workflow (`supervisor`, `pilote`, `autopilot`, `ask_review`,
    `libre`, `debug`, `deploy`, etc.) ;
  - canal d'origine (`codex`, `telegram`, `cockpit`, `cli`) ;
  - repo cible ;
  - provider cible ;
  - `task_id` Cockpit courant ;
  - session Hermes/gateway liée si disponible ;
  - branche GitHub ;
  - PR éventuelle ;
  - URL preview/live ;
  - chemins des briefs, rapports et screenshots CUA.
- [ ] Ajouter une règle : nouvelle mission = nouvelle session de travail, sauf
  reprise explicite d'un `task_id`.
- [ ] Ajouter des commandes/actions de reprise :
  - lister les sessions de travail récentes ;
  - reprendre une session ;
  - clore une session ;
  - rattacher une session à un `task_id` Cockpit trouvé après coup.
- [ ] Permettre de filtrer par repo, provider, statut, workflow, date et canal
  d'origine.
- [ ] Stocker chaque brief envoyé à Hermes ou à un worker comme artefact propre,
  sans dépendre de l'ancien historique Telegram.
- [ ] Produire un rapport final par session, avec liens GitHub/Cockpit/hosting.
- [ ] Brancher `@supervisormode` sur ce store global au lieu de créer une logique
  de session dédiée uniquement au superviseur.

## 2. Automatisation des limites actuelles du superviseur

- [ ] Extraire automatiquement un nouveau `task_id` depuis les réponses Telegram,
  les endpoints Cockpit ou le dernier thread actif.
- [ ] Ajouter une boucle de relance intelligente supervisée :
  - Hermes pose une question ;
  - Hermes bloque sur approval ;
  - Hermes travaille sur le mauvais repo ;
  - Hermes produit des docs mais ne pousse rien sur GitHub ;
  - smoke deploy échoue ;
  - task stagne/timed out.
- [ ] Transformer chaque relance en message Telegram traçable et l'ajouter au
  rapport Markdown/JSON.
- [ ] Ajouter un flow "repo + deploy + URL" piloté par le superviseur :
  - création repo si autorisée ;
  - branche dédiée ;
  - tâche Hermes ;
  - vérification GitHub ;
  - deploy preview Cloudflare/Vercel/Supabase selon provider ;
  - smoke URL ;
  - rapport final.
- [ ] Garder les approvals humaines pour production, DNS, coûts, secrets,
  actions irréversibles et merge vers `main`.

