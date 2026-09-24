# Scripts planifiés

> **Généré** par `sdda_admin/planned_scripts.py`. Ne pas éditer à la main.

Inventaire des scripts déterministes que les agents, commandes et
invariants nomment sans qu'ils existent encore. C'est le backlog des
lots d'implémentation, pas une liste de bugs.

**Lecture** : le nombre d'appelants est un signal. Un script réclamé par
six endroits est un besoin établi ; un script réclamé une seule fois a pu
être inventé au fil de la plume et mérite une question avant d'être écrit.

- **67** écrits · **1** à écrire · **0** cité(s) sans déclaration

**Déclaré** : le prompt qui appelle le script dit « Planifié » à la ligne
suivante, avec la conduite à tenir tant qu'il manque. Un script absent cité
sans le dire fait croire à l'agent qu'il dispose d'un outil — il en invente
la sortie. `framework_smoke` échoue sur toute référence muette.

## `sdda_scripts/` — 1 à écrire

| Script | Appelants | Déclaré | Réclamé par |
|---|---:|:-:|---|
| `adversarial_target_check.py` | 1 | ✅ | `agents\review-adversarial.md` |

## Noms cités sans chemin

Ces scripts sont nommés quelque part mais aucun endroit ne dit où ils
vivent. À rattacher à un paquet, ou à fusionner avec un script existant
qui fait déjà le travail.

| Nom | Cité par |
|---|---|
| `bootstrap.py` | `commands\sdda-bootstrap.md` |
| `framework_smoke.py` | `INVARIANTS.yml`, `rules\error-classification.md` |
| `promote_adversarial_findings.py` | `agents\qa-evals.md` |

