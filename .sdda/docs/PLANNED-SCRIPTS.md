# Scripts planifiés

> **Généré** par `sdda_admin/planned_scripts.py`. Ne pas éditer à la main.

Inventaire des scripts déterministes que les agents, commandes et
invariants nomment sans qu'ils existent encore. C'est le backlog des
lots d'implémentation, pas une liste de bugs.

**Lecture** : le nombre d'appelants est un signal. Un script réclamé par
six endroits est un besoin établi ; un script réclamé une seule fois a pu
être inventé au fil de la plume et mérite une question avant d'être écrit.

- **46** écrits · **11** à écrire

## `sdda_scripts/` — 11 à écrire

| Script | Appelants | Réclamé par |
|---|---:|---|
| `adversarial_target_check.py` | 1 | `agents\review-adversarial.md` |
| `audit_bypass.py` | 1 | `commands\sdda-caps.md` |
| `chunking_bench.py` | 1 | `agents\architect-rag.md` |
| `corpus_profile.py` | 1 | `agents\architect-rag.md` |
| `cost_report.py` | 1 | `agents\review-cost.md` |
| `diff_code_vs_ir.py` | 1 | `commands\sdda-build.md` |
| `resolve_cap_hash_sentinel.py` | 1 | `commands\sdda-caps.md` |
| `smoke_check.py` | 1 | `commands\sdda-bootstrap.md` |
| `trajectory_report.py` | 1 | `agents\review-orchestration.md` |
| `validate_envelope.py` | 1 | `agents\dev-data.md` |
| `validate_tool_schemas.py` | 1 | `agents\dev-tools.md` |

## Noms cités sans chemin

Ces scripts sont nommés quelque part mais aucun endroit ne dit où ils
vivent. À rattacher à un paquet, ou à fusionner avec un script existant
qui fait déjà le travail.

| Nom | Cité par |
|---|---|
| `bootstrap.py` | `commands\sdda-bootstrap.md` |
| `framework_smoke.py` | `INVARIANTS.yml`, `rules\error-classification.md` |
| `promote_adversarial_findings.py` | `agents\qa-evals.md` |

