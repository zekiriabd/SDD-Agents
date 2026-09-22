# EVAL SUITE: {n}-{m}-{grader-or-metric}

MISSION: {n}-{MissionName}
Status: Draft
Level: <L0 | L1 | L2 | L3 | L4 | L5 | L6 | L7 | L8 | L9>
Gate: <G3 TOOL | G4 RETRIEVAL | G5 AGENT | G6 ORCH | G7 SAFETY | G8 ACCEPTANCE | —>
Owner: qa-evals                      # JAMAIS un dev-* (ARCHITECTURE §7)

> **Un test assert, une eval score** (TESTING-AND-EVAL.md). Cette suite est une
> **eval** : elle produit un score, une variance et un taux de réussite sur
> **k runs**, comparés à un seuil — jamais un booléen (P3). Si ce que vous
> décrivez est déterministe et rend pass/fail en un run, c'est un test : il vit
> dans `workspace/src/**/tests/`, pas ici.
>
> Fichier machine associé (lu par `eval_runner.py`) :
> `workspace/proof/suites/{n}-{m}-{grader}.yaml` — le bloc §1 en est la source.

---

## 1. Définition machine

```yaml
id: {n}-{m}-{grader}
level: L4
capRef: {n}-{m}-{Name}                  # CAP couverte — obligatoire sauf L0/L6/L8 système
agentRef: {n}-{agent-slug}              # L4/L5 : l'agent évalué ISOLÉ
metric: groundedness                    # groundedness | routing_accuracy | exact_match | recall@k
                                        # | ndcg@k | context_precision | schema_valid | trajectory_match
                                        # | cost_usd | latency_p95_ms | abstention_rate | injection_resisted | …
dataset: workspace/proof/datasets/golden/{name}-v1.jsonl
datasetHash: sha256:…                   # calculé — fait partie du tuple d'épinglage (P10)
datasetMinItems: 50                     # GoldenSetMinItems ; 30 holdout ; 25 adversarial
grader: llm-judge                       # exact | regex | schema | numeric-tolerance | semantic-similarity
                                        # | llm-judge | trajectory | cost | latency
graderConfig: {}                        # ex. numeric-tolerance: { abs: 0.01 } ; semantic-similarity: { model: …, min: 0.85 }
judgeCalibrationRef: workspace/proof/calibration/{grader}.json   # OBLIGATOIRE si grader = llm-judge et advisory = false
advisory: false                         # true : score informatif, ne bloque pas
threshold: 0.85                         # comparé à score_mean
perClassThreshold: {}                   # ex. routing : { refund: 1.0 } — 0 misroute sur la classe critique
runs: 3                                 # EvalRuns ; 5 si Criticality: critical ; 1 REFUSÉ en CI
seedPolicy: vary                        # vary | fixed — fixed masque la variance, débogage uniquement
varianceWarnPct: 15                     # EvalVarianceWarnPct — au-delà : JAUNE
baselineRef: workspace/proof/baselines/{n}-{m}-{grader}.json
regressionTolerancePct: 3               # RegressionTolerancePct (L9)
fixtures:                               # L4 : outils MOCKÉS et retrieval FIGÉ — l'isolement est le point
  tools: mocked
  retrieval: frozen:workspace/proof/fixtures/{index}-v1.json
  memory: frozen:workspace/proof/fixtures/memory-{n}-v1.json
pinned:                                 # tuple P10 — si l'un bouge, le résultat est PÉRIMÉ
  promptHash: sha256:…
  modelId: <résolu par le provider depuis le tier — ex. claude-sonnet-5>
  retrievalIndexHash: sha256:…
  toolSchemaHash: sha256:…
  datasetHash: sha256:…
```

---

## 2. Ce que la suite mesure — et ce qu'elle ne mesure pas

- **AC couvert** : `AC-{i}` de la CAP `{n}-{m}-…` — recopier l'AC tel quel :
  <ex. groundedness ≥ 0.85 sur datasets/golden/support-v1.jsonl (n=120, k=3)>
- **Question à laquelle elle répond** : <une phrase>
- **Ce qu'elle NE couvre PAS** (le trou assumé) : <ex. ne mesure pas la
  pertinence ; ne mesure pas le coût — couverts par `{n}-{m}-answer-relevance`
  et `{n}-cost`>
- **Pourquoi ce niveau** : <ex. L4 pour attribuer la variation à l'agent seul ;
  L7 pour mesurer le système entier>

> Le niveau détermine ce qui est réel et ce qui est figé (TESTING-AND-EVAL.md §1) :
>
> | Niveau | Réel | Figé / mocké | Gate |
> |---|---|---|---|
> | L3 | index | **aucun agent** | G4 |
> | L4 | un agent | outils mockés, retrieval figé | G5 |
> | L5 | trace d'orchestration | — (sur la trace, pas la réponse) | G6 |
> | L7 | système complet | — (qualité **+ coût + latence**) | G6 |
> | L8 | système **vivant** | — | G7 |
> | L9 | comparaison à la baseline épinglée | — | G8 |

---

## 3. Dataset

| | |
|---|---|
| Fichier | `{dataset}` — schéma `.sdda/templates/golden-set.schema.json` |
| Rôle | <golden (ajustement) \| holdout (**verdict**, jamais d'itération contre lui) \| calibration \| adversarial> |
| Taille | <n> items (minimum : 50 golden / 30 holdout / 50 calibration / 25 adversarial) |
| Origine | <ex. conversations réelles annotées ; synthétiques marqués `metadata.source: synthetic`> |
| Classes et distribution | <ex. billing 40 %, technical 35 %, refund 15 % (**critique**), autre 10 %> |
| Disjonction | `golden ∩ holdout = ∅` vérifiée par hash (`HoldoutDisjointCheck: strict`) |
| Owner | `qa-evals` — aucun `dev-*` n'écrit dans `datasets/` |

---

## 4. Grader

| | |
|---|---|
| Type | `{grader}` |
| Déterministe | <oui — 0 token \| non — tokens x items x k> |
| Configuration | <tolérance, regex, schéma, modèle d'embedding épinglé…> |
| Référence vue par le grader | <oui : `expected` du dataset \| non> |

### 4.1 Si `llm-judge` — calibration OBLIGATOIRE (P9, invariant `llm-judge-calibrated`)

| | |
|---|---|
| `JudgeModel` | <ex. claude-sonnet-5> — **≠ du modèle évalué** (`JudgeMustDifferFromEvaluated: true`) ; sinon on mesure la complaisance d'un modèle envers lui-même |
| Grille | liste de **critères vérifiables**, pas « note de 1 à 10 la qualité » : <C1 … ; C2 … ; C3 …> |
| Set de calibration | `workspace/proof/datasets/calibration/{grader}-v1.jsonl` — ≥ 50 items du domaine réel, labellisés par un humain **selon la grille exacte du juge** |
| Accord mesuré | κ de Cohen (binaire / ordinal) ou corrélation (continu) : <valeur> — seuil `JudgeCalibrationMinKappa` 0.6 |
| Rapport | `workspace/proof/calibration/{grader}.json` — versionné, référencé depuis l'IR (`judgeCalibrationRef`) |
| Sous le seuil | la grille est retravaillée, **ou** `advisory: true` : le juge informe, il ne bloque plus |

---

## 5. Seuil, k runs, verdict

| | |
|---|---|
| Seuil | `{threshold}` sur `score_mean` — origine : <l'AC de la CAP / défaut de config / justification métier> |
| Seuils par classe | <ex. classe critique `refund` : 1.0 — une accuracy globale de 0.95 peut cacher 0.40 sur la classe critique> |
| k runs | `{runs}` (`EvalRuns` 3 ; `EvalRunsCritical` 5) |
| Rapporté | `score_mean`, `score_stddev`, `pass_rate`, `min`, `max` — jamais un booléen |

| Verdict | Condition |
|---|---|
| 🟢 VERT | `mean ≥ seuil` **et** `pass_rate = 1.0` **et** `stddev ≤ EvalVarianceWarnPct` |
| 🟡 JAUNE | seuil franchi en moyenne mais variance élevée ou `pass_rate < 1.0` — « un déploiement est un pari sur le prochain tirage » |
| 🔴 ROUGE | `mean < seuil`, ou une classe critique échoue, ou un budget est dépassé (L7 : dépasser `CostPerRunHardCapUsd` est **rouge**, pas jaune) |

---

## 6. Baseline et régression (L9)

| | |
|---|---|
| Baseline | `{baselineRef}` — écrite **uniquement** par script déterministe (write atomique), jamais à la main |
| Tuple épinglé | `(prompt_hash, model_id, retrieval_index_hash, tool_schema_hash, dataset_hash)` — si l'un bouge, le résultat est **périmé** et la ré-exécution est exigée (`check_baseline_freshness.py`) |
| Tolérance | `RegressionTolerancePct` 3 % — au-delà : `[REGRESSION]`, bloquant |
| Promotion | `BaselinePromotionPolicy: explicit` — la baseline se déplace par une **action tracée**, jamais par écrasement automatique (sinon la dérive lente devient invisible) |
| Dérive fournisseur | même prompt, même jeu, scores qui glissent ⇒ le modèle a bougé sous vos pieds — c'est pour cela que `model_id` est dans le tuple |

---

## 7. Coût d'exécution de la suite

| | |
|---|---|
| Appels LLM par run complet | <items x k x (1 agent + 1 juge)> |
| Coût estimé | <USD> — compté dans le budget de **construction** (`MaxCostPerRun`) |
| Durée estimée | <min> |
| Fréquence | <à chaque changement de prompt / d'index / de schéma d'outil (périmé par le tuple) ; nightly ; à la promotion> |

---

## 8. Tableau de bord — ce qu'on lit au-delà du score (TESTING-AND-EVAL.md §6)

- [ ] coût par CAP
- [ ] distribution des trajectoires (la queue à 3 % qui fait 12 hops)
- [ ] taux d'abstention (un système qui ne dit jamais « je ne sais pas » ment mieux)
- [ ] top des outils en échec
- [ ] dérive dans le temps vs baseline
