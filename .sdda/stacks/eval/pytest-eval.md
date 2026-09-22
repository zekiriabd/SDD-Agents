# Stack: pytest-eval (eval)

> §2.3 (Librairies) régénérée depuis `pytest-eval.libs.json` — ne pas éditer manuellement.

Stack ID: eval-pytest-eval
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: exécution des **évaluations** (L3 → L9) dans `pytest`, sans framework d'eval tiers — suites déclaratives YAML, **k runs par item**, rapport `score_mean / stddev / pass_rate`, verdict **vert / jaune / rouge**, épinglage au tuple P10, comparaison à la baseline, calibration des juges. Les tests L0–L2 (assert pass/fail) sont du `pytest` ordinaire couvert par `lang/python.md` ; cette fiche ne porte que ce qui **score**. Suppose `lang/python.md`.

---

## 1. Rôle et périmètre

**Un test assert, une eval score** (TESTING-AND-EVAL). `pytest` sait faire le
premier nativement ; il ne sait pas faire le second : il n'a pas de notion de
« k exécutions dont on rapporte la variance », ni de verdict à trois couleurs,
ni de dépendance à un tuple de hashes. Cette stack **ajoute** ces notions à
`pytest` par un plugin local (`workspace/proof/conftest.py` +
`sdda_eval` dans `.sdda/python/`) plutôt que d'adopter un framework d'eval
tiers, pour trois raisons :

1. **Un seul runner, une seule CI.** Les L1/L2 et les L3–L8 tournent dans la
   même commande, avec les mêmes marqueurs, le même `-x`, le même `xdist`.
2. **Les graders déterministes sont du code testable.** `recall@k`, `nDCG`,
   RRF, `exact`, `regex`, `schema`, `trajectory` sont des fonctions pures
   testées en L1 — pas des boîtes noires d'un framework.
3. **Le protocole SDD_Agents est non négociable** (k runs, verdict, tuple
   d'épinglage, holdout disjoint, juge calibré) et aucun framework tiers ne
   l'implémente tel quel. `ragas.md`, `deepeval.md`, `promptfoo.md` restent
   disponibles **comme graders** branchés dans ce runner, pas comme runners.

Périmètre : layout, suites YAML, marqueurs, mécanique k-runs, graders,
rapport, verdict, épinglage/baseline, calibration, options, ownership.
**Hors périmètre** : le contenu des datasets (`qa-evals`), les graders LLM
eux-mêmes (`guardrails/llm-judge.md` pour la grille ; ici on ne fait que les
appeler et vérifier leur calibration), l'observabilité (les evals **lisent**
les traces de `observability/*.md`).

---

## 2. Identité

### 2.1 Identité

- **Stack ID** : `eval-pytest-eval`
- **Langage** : Python 3.12 (`lang/python.md`)
- **Runner** : `pytest` 9.0.x + `pytest-asyncio` + `pytest-xdist` + `pytest-timeout`
- **Plugin local** : `workspace/proof/conftest.py` (options, fixtures, hooks de rapport) s'appuyant sur `sdda_eval` (`.sdda/python/sdda_lib/sdda_eval/` : stats, graders déterministes, verdict, épinglage, rapport)
- **Paramètres STACK.md** : `EvalRuns: 3`, `EvalVarianceWarnPct: 15`, `JudgeCalibrationMinKappa: 0.6`, `JudgeCalibrationMinItems: 50`, `HoldoutDisjointCheck: strict`, `RegressionTolerancePct: 3`, `GoldenSetMinItems: 50`, `HoldoutSetMinItems: 30`, `AdversarialSetMinItems: 25`, `BaselineStorage`
- **Ownership** : `workspace/proof/**` et `workspace/proof/datasets/**` → `qa-evals` ; `workspace/proof/baselines/**` → **script uniquement** ; `dev-*` exécutent, n'éditent pas

<!-- CORE_PACKAGES_START -->
```bash
# Auto-généré depuis pytest-eval.libs.json — ne pas éditer.
uv add --dev --project workspace/src/{AppName} \
  pytest==9.0.2 \
  pytest-asyncio==1.3.0 \
  pytest-xdist==3.8.0 \
  pytest-timeout==2.4.0 \
  jsonschema==4.25.1 \
  pyyaml==6.0.3 \
  pydantic==2.13.5 \
  structlog==26.1.0 \
  ruff==0.16.5 \
  mypy==2.3.1
```
<!-- CORE_PACKAGES_END -->

<!-- ONDEMAND_PACKAGES_START -->
```bash
# Auto-généré depuis pytest-eval.libs.json (on-demand).
# capability: property-tests
uv add --dev --project workspace/src/{AppName} hypothesis==6.140.3
# capability: http-mocks
uv add --dev --project workspace/src/{AppName} respx==0.22.0
# capability: semantic-similarity
uv add --dev --project workspace/src/{AppName} numpy==2.3.4
```
<!-- ONDEMAND_PACKAGES_END -->

<!-- LIBS_CATALOG_START -->
### 2.3 Librairies

> Source de vérité : `.sdda/stacks/eval/pytest-eval.libs.json`. Pins à
> re-résoudre contre PyPI au premier bootstrap (design-phase). Installées en
> groupe `dev` : l'application livrée n'embarque pas son runner d'eval.

| Lib | Version | Rôle |
|---|---|---|
| pytest | 9.0.2 | runner ; hooks `pytest_addoption`, `pytest_collection_modifyitems`, `pytest_sessionfinish` |
| pytest-asyncio | 1.3.0 | `asyncio_mode = "auto"` — les evals invoquent des coroutines |
| pytest-xdist | 3.8.0 | parallélisme **par item** (`-n`) ; les k runs d'un item restent séquentiels |
| pytest-timeout | 2.4.0 | `--timeout` par test = `AgentTimeoutSec × k + marge` ; un eval qui pend est un run qui facture |
| jsonschema | 4.25.1 | grader `schema` ; validation des suites YAML et des lignes de dataset |
| pyyaml | 6.0.3 | suites déclaratives |
| pydantic | 2.13.5 | `EvalReport`, `EvalItemResult`, `PinTuple` |
| structlog | 26.1.0 | logs du runner |
| ruff / mypy | 0.16.5 / 2.3.1 | L0 |

On-demand : `hypothesis` (L1 property-based sur graders et réducteurs),
`respx` (mocks `httpx` pour les L2 d'outils REST), `numpy` (grader
`semantic-similarity` — cosinus sur embeddings ; pas nécessaire pour
`recall@k`/`nDCG` qui sont en Python pur).

**Volontairement absent** : `pytest-rerunfailures` / `flaky` — relancer un
eval qui échoue **masque la variance** qu'on cherche à mesurer (P3). Interdit
sur le marqueur `eval` (§5.3). `scikit-learn` pour le kappa de Cohen : 30
lignes de Python pur dans `sdda_eval.stats`, testées en L1.
<!-- LIBS_CATALOG_END -->

---

## 3. Mapping des concepts SDD_Agents → idiomes pytest

| Concept (DOMAIN-MODEL) | Idiome | Notes |
|---|---|---|
| **EVAL SUITE** `{n}-{m}-{grader}` | un fichier `workspace/proof/suites/{n}-{m}-{grader}.yaml` | collecté par `test_suites.py` en un **item pytest par ligne de dataset** (id `suite::item_id`) |
| **EVALUATION** `(dataset, grader, seuil, k)` → `(score, variance, verdict)` | fixture `eval_runner(suite, item)` qui exécute k fois, agrège, écrit le résultat, et **fait échouer le test si verdict = rouge** | jaune = test **passe** avec `record_property("verdict", "yellow")` ; le verdict de session (§3.5) le remonte |
| **k runs** (P3) | `for k in range(runs)` **dans un même item**, séquentiel, sans cache | `runs = 5` si `criticality: critical` ou marqueur `critical`, sinon `EvalRuns` ; `--eval-runs` peut **augmenter**, jamais baisser |
| **GRADER** | `sdda_eval.graders.{exact,regex,schema,numeric_tolerance,semantic_similarity,llm_judge,trajectory,cost,latency}` — protocole `grade(expected, actual, ctx) -> float ∈ [0,1]` | déterministes en Python pur ; `llm_judge` appelle `JudgeModel` **≠** modèle évalué ; `trajectory`/`cost`/`latency` lisent la **trace JSONL** du run, pas la réponse |
| **DATASET** | `workspace/proof/datasets/{golden\|holdout\|calibration\|adversarial}/*.jsonl` ; une ligne = `{id, input, expected, tags[], criticality?}` validée par `golden-set.schema.json` | chargé en lecture seule ; **hash du fichier** dans le tuple |
| **BASELINE** (P10) | `workspace/proof/baselines/{suite}.json` : `{pin: PinTuple, score_mean, score_stddev, pass_rate, recorded_at, run_id}` | écrit **uniquement** par `sdda_scripts.baseline_promote` (action tracée) ; jamais par un test |
| **Tuple d'épinglage** | `PinTuple(prompt_hash, model_id, retrieval_index_hash, tool_schema_hash, dataset_hash)` calculé par la fixture `pin` au début de session | dans chaque rapport ; `pin != baseline.pin` → `[EVAL_BASELINE_STALE]` : la comparaison de régression est **refusée**, pas approximée |
| **Verdict** | `sdda_eval.verdict(mean, stddev, pass_rate, threshold, variance_warn_pct)` → `green \| yellow \| red` | table §3.4 ; rouge = `pytest.fail` ; jaune = pass + propriété ; le **verdict de session** est le pire des items |
| **Calibration** (P9) | fixture `calibrated_judge(grader_id)` : lit `workspace/proof/calibration/{grader}.json`, vérifie `n ≥ JudgeCalibrationMinItems` et `kappa ≥ JudgeCalibrationMinKappa` ; sinon le juge passe en **`advisory`** (score rapporté, **verdict non bloquant**) et l'item est marqué `xfail(strict=False)` avec `[JUDGE_NOT_CALIBRATED]` | un juge non calibré ne bloque pas et ne valide pas |
| **Holdout disjoint** | test L0 `test_datasets_disjoint.py` : `set(hash(item.input)) golden ∩ holdout == ∅` | `strict` → échec de collection ; `warn` → propriété |
| **Isolement L4** | fixtures `mocked_tools` (réponses depuis `workspace/proof/fixtures/tools/*.jsonl`) et `frozen_retrieval` (résultats figés par `query_hash`) | l'agent seul ; la variation est attribuable |
| **L7 bout-en-bout** | invocation de la surface CLI (`serving/cli.md`) : `uv run {AppName} run --json …` via `subprocess`, lecture des `RunEvent` et du code de sortie | ce qu'on mesure est ce qu'on livre |
| **L8 adversarial** | suite avec `expected.outcome ∈ {refused, unchanged_behavior, tool_not_called}` et grader `trajectory` ; **exit code 4 attendu** | toute attaque réussie devient un item permanent |
| **Coût de l'eval** | fixture `eval_budget` : somme `sdda.cost.usd` des traces ; `--eval-max-cost-usd` → arrêt de session `[EVAL_BUDGET_EXCEEDED]` | l'eval elle-même a un budget |
| **RUN / TRACE** | chaque exécution d'item produit un `run_id` ; le rapport référence les k `trace_path` | c'est là que la L5 lit hops, outils, bornes |

### 3.1 Suite déclarative

```yaml
# workspace/proof/suites/1-2-groundedness.yaml
id: 1-2-groundedness
level: L4
cap: 1-2-ExplainInvoiceLine
agent: 1-billing-specialist
dataset: workspace/proof/datasets/golden/billing-v1.jsonl
grader:
  id: llm-judge
  rubric: workspace/proof/rubrics/groundedness.md          # grille versionnée
  calibration: workspace/proof/calibration/groundedness.json
threshold: 0.85
runs: 3                        # 5 si critical
isolation:
  tools: mocked                # mocked | live
  retrieval: frozen            # frozen | live
  fixtures: workspace/proof/fixtures/1-billing-specialist/
timeout_s: 90
tags_filter: []                # exécuter un sous-ensemble par tag
```

Validée par `suite.schema.json` à la collection ; une suite invalide est une
erreur de collection (`[EVAL_SUITE_INVALID]`), pas un test qui échoue.

### 3.2 Le runner — un item, k runs

```python
# workspace/proof/test_suites.py — générique, ne change pas par mission
import pytest
from sdda_eval import load_suites, verdict, stats

SUITES = load_suites("workspace/proof/suites")

def pytest_generate_tests(metafunc):
    if "suite_item" in metafunc.fixturenames:
        params = [pytest.param((s, item), id=f"{s.id}::{item.id}", marks=marks_for(s, item)) for s in SUITES for item in s.items()]
        metafunc.parametrize("suite_item", params)

@pytest.mark.eval
async def test_suite_item(suite_item, eval_runner, pin, report, eval_budget):
    suite, item = suite_item
    runs = eval_runner.effective_runs(suite, item)               # 3 | 5 | --eval-runs (si plus grand)
    scores, traces = [], []
    for k in range(runs):                                         # séquentiel, sans cache : chaque run est un tirage
        outcome = await eval_runner.execute(suite, item, run_index=k)   # agent isolé | CLI --json | retrieval seul
        scores.append(await eval_runner.grade(suite, item, outcome))
        traces.append(outcome.trace_path)
        eval_budget.add(outcome.cost_usd)
    s = stats.summarize(scores)                                   # mean, stddev (échantillon), min, max, pass_rate vs threshold
    v = verdict.compute(s, threshold=suite.threshold, variance_warn_pct=eval_runner.variance_warn_pct)
    report.record(suite, item, s, v, traces, pin)
    if v == "red":
        pytest.fail(f"[AGENT_EVAL_FAILED] {suite.id}::{item.id} mean={s.mean:.3f} stddev={s.stddev:.3f} pass_rate={s.pass_rate:.2f} threshold={suite.threshold}")
    if v == "yellow":
        pytest.mark.usefixtures  # no-op ; la propriété suffit
        eval_runner.record_property("verdict", "yellow")
```

### 3.3 Statistiques

`stats.summarize(scores)` : `mean`, `stddev` (écart-type **échantillon**,
`statistics.stdev`, `0.0` si k = 1 — mais k = 1 est refusé sur `eval`), `min`,
`max`, `pass_rate = |{s ≥ threshold}| / k`, `stddev_pct = stddev / mean × 100`
(si `mean > 0`).

### 3.4 Verdict — table close

| Verdict | Condition | Effet pytest |
|---|---|---|
| 🟢 `green` | `mean ≥ threshold` **et** `pass_rate == 1.0` **et** `stddev_pct ≤ EvalVarianceWarnPct` | pass |
| 🟡 `yellow` | `mean ≥ threshold` **et** (`pass_rate < 1.0` **ou** `stddev_pct > EvalVarianceWarnPct`) | pass + `verdict=yellow` |
| 🔴 `red` | `mean < threshold` **ou** (`critical` et `pass_rate < 1.0`) **ou** budget de run dépassé sur un run **ou** grader `cost`/`latency` sous seuil | `pytest.fail` |

Le verdict de **session** (`pytest_sessionfinish`) = pire verdict des items ;
écrit dans le rapport et affiché en résumé ; `sdda_scripts.eval_verdict
--report …` le convertit en code de sortie `0 / 2 / 1` pour la CI (pytest ne
sait pas sortir « jaune »).

### 3.5 Rapport

`workspace/.sys/reports/{suite}-{session-run-id}.json` — un par suite et par
session :

```json
{
  "suite": "1-2-groundedness", "level": "L4", "cap": "1-2-ExplainInvoiceLine",
  "pin": {"prompt_hash": "sha256:…", "model_id": "…", "retrieval_index_hash": "sha256:…", "tool_schema_hash": "sha256:…", "dataset_hash": "sha256:…"},
  "runs": 3, "threshold": 0.85, "variance_warn_pct": 15,
  "items": [
    {"id": "b-017", "scores": [0.91, 0.86, 0.77], "mean": 0.847, "stddev": 0.071, "stddev_pct": 8.4, "pass_rate": 0.67,
     "verdict": "yellow", "traces": ["workspace/.sys/traces/runs/…jsonl", "…", "…"], "cost_usd": 0.041, "tags": ["factual"]}
  ],
  "aggregate": {"mean": 0.88, "stddev": 0.05, "pass_rate_items": 0.93, "verdict": "yellow", "cost_usd_total": 2.14, "duration_s": 412},
  "by_tag": {"factual": {"mean": 0.91, "n": 40}, "identifier": {"mean": 0.79, "n": 12}},
  "baseline": {"path": "workspace/proof/baselines/1-2-groundedness.json", "comparable": true, "delta_mean": -0.012, "regression": false},
  "judge": {"id": "llm-judge", "model_id": "…", "calibration_kappa": 0.71, "calibration_n": 62, "mode": "blocking"},
  "recorded_at": "2026-09-20T15:02:11Z", "session_run_id": "…", "semconv_version": "0.61b0"
}
```

`by_tag` est obligatoire : une moyenne globale à 0.88 cache 0.79 sur
`identifier` (RAG-PATTERNS §5, ORCHESTRATION-PATTERNS `router`).

---

## 4. Structure de fichiers générée

```
workspace/proof/
├── conftest.py                 # plugin local : options --eval-*, fixtures pin / eval_runner / report / eval_budget / calibrated_judge / mocked_tools / frozen_retrieval,
│                               #   hooks : collection (schema suites, disjonction golden/holdout, interdiction rerun sur eval), sessionfinish (rapport, verdict)
├── pytest.ini                  # markers, asyncio_mode=auto, timeout, -p no:randomly, addopts = -p no:cacheprovider pour eval
├── test_suites.py              # runner générique (§3.2)
├── test_datasets_disjoint.py   # L0 : golden ∩ holdout = ∅ par hash ; tailles minimales (GoldenSetMinItems…)
├── test_baselines_fresh.py     # L0 : chaque baseline a un pin ; pin == pin courant sinon [EVAL_BASELINE_STALE] (warn ou fail selon --eval-strict-baseline)
├── test_calibration.py         # L0 : chaque grader llm-judge référencé a un fichier de calibration valide ; kappa recalculé == stocké
├── suites/{n}-{m}-{grader}.yaml
├── rubrics/{grader}.md         # grilles des juges — versionnées, hashées (le hash entre dans judge.rubric_hash du rapport)
├── fixtures/{agent}/tools/*.jsonl        # réponses d'outils mockées (L4) : {tool, args_hash, response}
├── fixtures/{agent}/retrieval/*.jsonl    # résultats de retrieval figés (L4) : {query_hash, chunk_ids[], scores[]}
├── baselines/{suite}.json      # ÉCRIT PAR SCRIPT UNIQUEMENT (baseline_promote)
├── calibration/{grader}.json   # {items: [...], human_labels, judge_labels, kappa, n, judge_model_id, rubric_hash, recorded_at}
└── reports/{suite}-{session}.json

.sdda/python/sdda_lib/sdda_eval/         # partagé, testé dans .sdda/python/tests/
├── stats.py                    # summarize, cohen_kappa, ndcg, recall_at_k, rrf (référence)
├── verdict.py                  # compute(...) — table §3.4
├── graders/                    # exact, regex, schema, numeric_tolerance, semantic_similarity, llm_judge, trajectory, cost, latency
├── pin.py                      # PinTuple.compute(workspace) — mêmes fonctions de hash que le runtime (prompts.py, ToolSpec)
├── report.py                   # EvalReport pydantic + écriture atomique
├── runner.py                   # execute() : agent isolé | cli | retrieval ; grade() ; effective_runs()
└── datasets.py                 # chargement jsonl validé, hash, disjonction
```

---

## 5. Conventions imposées

### 5.1 Marqueurs

| Marqueur | Sens | Sélection typique |
|---|---|---|
| `eval` | item scoré à k runs (L3–L8) — **exige** `llm` ou `network` ou `retrieval` | `-m eval` |
| `l3_retrieval`, `l4_agent`, `l5_trajectory`, `l7_e2e`, `l8_adversarial`, `l9_regression` | niveau | `-m "eval and l4_agent"` |
| `critical` | `criticality: critical` → k = 5, `pass_rate` doit être 1.0 | |
| `llm` | coûte des tokens | exclu du smoke |
| `network` | exige une connectivité | exclu du smoke |
| `advisory` | posé automatiquement quand le juge n'est pas calibré | jamais posé à la main |

### 5.2 Options du plugin

| Option | Défaut | Règle |
|---|---|---|
| `--eval-runs N` | `EvalRuns` | `effective = max(N, suite.runs)` — on ne baisse jamais |
| `--eval-suite ID[,ID]` | toutes | filtre |
| `--eval-tags TAG[,TAG]` | tous | filtre par tag d'item |
| `--eval-dataset golden\|holdout` | `golden` | `holdout` **uniquement** via `sdda_scripts.acceptance_gate` (G8) — un humain qui itère sur holdout se ment |
| `--eval-max-cost-usd X` | `BuildLoopMaxCostUsd` | arrêt de session au dépassement |
| `--eval-judge-mode blocking\|advisory` | `blocking` | `advisory` force tous les juges en non-bloquant (exploration) ; le rapport le dit |
| `--eval-strict-baseline` | off | `[EVAL_BASELINE_STALE]` devient un échec au lieu d'un avertissement |
| `--eval-report-dir PATH` | `workspace/.sys/reports/` | |

### 5.3 Règles

1. **k ≥ 2 sur `eval`**, toujours ; `runs: 1` dans une suite est une erreur
   de collection. `EvalRuns: 3` par défaut, 5 si `critical`.
2. **Pas de cache entre les k runs** : `-p no:cacheprovider` sur les evals,
   et le `frozen_retrieval` fige les **entrées** (résultats de retrieval), pas
   les sorties du modèle.
3. **`pytest-rerunfailures`, `flaky` et `pytest.mark.flaky` sont refusés sur
   `eval`** (hook de collection → `[EVAL_RERUN_FORBIDDEN]`). La variance est
   la mesure, pas le bruit.
4. **`-p no:randomly`** : l'ordre des items n'a pas d'effet, mais le seed de
   `pytest-randomly` pollue `random` utilisé par certains graders.
5. **Le rouge fait échouer ; le jaune passe et se voit.** Un `pytest -m eval`
   vert avec 30 % de jaunes est un signal ; `eval_verdict` le convertit en
   code 2 pour la CI.
6. **Le juge n'est jamais le modèle évalué** quand `JudgeModel` le permet ;
   le rapport enregistre les deux `model_id` et le lint L0 signale l'égalité
   (`[JUDGE_SAME_AS_SUBJECT]`, avertissement).
7. **Le rapport porte le tuple d'épinglage complet**, la calibration du juge,
   les chemins de traces et `by_tag`. Un rapport sans `pin` est invalide.
8. **La baseline se déplace par une action tracée** :
   `uv run python -m sdda_scripts.baseline_promote --report … --reason "…"`.
   Elle écrit `baselines/{suite}.json` **et** une ligne dans
   `workspace/.sys/.audit/baselines.log`. Aucun test n'écrit dans `baselines/`.
9. **`holdout` est hors de portée du développeur** : le runner refuse
   `--eval-dataset holdout` sauf sous `SDDA_ACCEPTANCE_GATE=1` posé par le
   script de G8, et le journalise.
10. **Les graders `trajectory`, `cost`, `latency` lisent la trace**, jamais la
    réponse : `TraceSampleRate` doit être `1.0` (`[TRACE_SAMPLED_IN_EVAL]` sinon).
11. **`--timeout` par item** = `suite.timeout_s × runs + 30` ; un item qui
    pend est arrêté, marqué rouge, et le coût déjà engagé est compté.
12. **`xdist` par item, jamais par run** : `-n 4` distribue les items ; les k
    runs d'un item restent séquentiels dans un worker (ordre stable, budget
    additif par item).
13. **Toute attaque L8 réussie devient un item permanent** du dataset
    `adversarial/` — ajouté par `qa-evals` avec référence au run qui l'a
    découverte.

---

## 6. Commande de smoke

Déterministe, 0 token — vérifie le **runner**, pas les agents :

```bash
cd workspace/src/{AppName}
uv run pytest ../../evals -q -m "not llm and not network" \
  --co -p no:randomly                                   # collection : suites valides, marqueurs cohérents, k ≥ 2, disjonction golden/holdout
uv run pytest ../../evals/test_datasets_disjoint.py ../../evals/test_baselines_fresh.py ../../evals/test_calibration.py -q
uv run python -m sdda_eval.selftest
#   → graders déterministes sur cas connus (recall@k, nDCG, kappa, exact, regex, schema, trajectory sur une trace JSONL fixture)
#   → verdict.compute sur la table §3.4 (9 cas)
#   → summarize([0.91,0.86,0.77]) == mean 0.847 / stddev 0.071 / pass_rate 0.67 (à 1e-3)
#   → un rapport factice écrit, relu, valide EvalReport
#   → PinTuple.compute(workspace) identique au hash calculé par le runtime (prompts.py) sur un prompt fixture
```

Smoke Timeout : 60 s. Le premier `pytest -m "eval and l4_agent"` réel appartient
à la PHASE 6 et coûte des tokens — il n'est pas dans le smoke.

---

## 7. Pièges connus

1. **Asserter sur la moyenne seule.** `mean ≥ threshold` avec `pass_rate 0.67`
   passe en vert sans la table §3.4. Le verdict est une fonction **unique**
   (`verdict.compute`), testée ; aucun test n'écrit sa propre condition.
2. **`temperature=0` pris pour du déterminisme.** Les providers ne garantissent
   pas la reproductibilité à température nulle ; k runs restent nécessaires.
   Inversement, ne pas forcer `temperature=0` en eval si le produit tourne à
   `0.7` : on mesure ce qu'on livre.
3. **Cacher les sorties LLM entre runs** (pour « aller plus vite ») transforme
   k runs en 1 run copié k fois : variance nulle, verdict vert menteur. Le
   `frozen_retrieval` fige les entrées, jamais les sorties.
4. **`xdist` × k runs × coût.** `-n 8` avec 120 items × 3 runs = 360 appels en
   parallèle : quota provider et budget explosent. `--eval-max-cost-usd` et
   `-n` modéré ; le budget est additif dans une fixture de session
   (`xdist` : agrégé via fichiers par worker à `sessionfinish`).
5. **Le juge est le modèle évalué.** Kappa de 0.9 avec lui-même, verdicts
   complaisants. `JudgeModel ≠ RuntimeTierMap[tier]` quand possible ;
   avertissement L0 sinon.
6. **Calibration faite sur les mêmes items que le golden.** Le juge est
   calibré sur ce qu'il notera ensuite : la calibration ne mesure plus sa
   généralisation. `calibration/` est un jeu **distinct**, vérifié par hash
   comme la disjonction golden/holdout.
7. **Itérer sur le holdout.** Le plus insidieux ; d'où le refus du runner
   hors G8 et la journalisation. `HoldoutDisjointCheck: strict` ne protège
   pas d'un humain qui lit les deux fichiers — la règle §5.9 est
   organisationnelle autant que technique.
8. **Baseline écrasée automatiquement** à chaque run vert : la dérive lente
   devient invisible (L9). `baselines/` est en écriture script-only, avec
   raison et journal.
9. **Tuple d'épinglage partiel.** Oublier `model_id` (le fournisseur a changé
   le modèle sous le même alias) ou `retrieval_index_hash` (le corpus a été
   ré-ingéré) rend une baseline « comparable » alors qu'elle ne l'est pas.
   `PinTuple` a cinq champs obligatoires, tous non nuls.
10. **Hash de prompt différent entre runtime et eval.** Si `pin.py` normalise
    autrement que `prompts.py` (CRLF, trailing newline), toutes les baselines
    sont périmées en permanence. Une **seule** fonction de hash
    (`sdda_lib.hashing`), testée des deux côtés dans le selftest.
11. **`pytest-timeout` avec `method=signal`** ne fonctionne pas sous Windows ni
    dans des threads ; `timeout_method = thread` dans `pytest.ini`, et le
    runner pose son propre `asyncio.timeout` par run.
12. **Rapport écrasé.** Deux sessions le même jour → un fichier ; nommer par
    `session_run_id` (ULID) et écrire atomiquement (`tmp` + `rename`).
13. **Grader `schema` trop laxiste** (`additionalProperties` par défaut à
    `true`) : une sortie avec des champs inventés passe. Les schémas de sortie
    d'agent sont générés avec `additionalProperties: false`
    (`ConfigDict(extra="forbid")`).
14. **Le grader `trajectory` compare des listes exactes.** Deux ordres
    d'appels d'outils équivalents (`A, B` vs `B, A` indépendants) échouent
    à tort. La suite déclare `expected.trajectory` comme **contraintes**
    (`must_call`, `must_not_call`, `order: [[A,B]]` partiel, `max_hops`), pas
    comme séquence littérale.
15. **`asyncio_mode = "auto"` et fixtures de session async.** Le scope
    `session` d'une fixture async exige `loop_scope="session"` en
    `pytest-asyncio` 1.x ; sinon « attached to a different loop » au
    deuxième item.
