<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis .sdda/commands/sdda-eval.md.
     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,
     et le test de parité la signale. Éditer la source. -->

# /sdda-eval

# /sdda-eval — PHASE 6 : évaluation et tests

<!-- @llm-only-flags-file : tous les flags CLI de cette commande slash sont interprétés par Claude. -->

> ⚠️ **Commande interne** — invoquée par `/sdda-full` STEP 4.5 (`--datasets-only`),
> STEP 6 (complet) et STEP 8 (`--acceptance`).
> Utilisateur final : préférer `/sdda-full {n}`.

Deux agents en parallèle, puis une exécution déterministe :

```
qa-evals  ∥  qa-tests                 (parallèle, chemins disjoints)
   datasets/**       src/**/tests/**
   suites/**         (L0→L2, déterministes)
   graders, calibration, baselines
        ↓ datasets FIGÉS (hash)
eval_runner.py  L0 → L7                          (script, k runs, variance)
        ↓
rapport trois couleurs  workspace/.sys/reports/{n}-{run-id}.md
```

**Un test assert, une eval score** (TESTING-AND-EVAL.md). Les confondre produit
soit des tests instables qu'on désactive, soit des evals qui ne détectent rien.

**Usage :**
- `/sdda-eval {n}` — PHASE 6 complète (agents + suites L0→L7)
- `/sdda-eval {n} --datasets-only` — `qa-evals` seul, produit golden /
  holdout / calibration / adversarial sans exécuter (pré-requis de G4 et G5)
- `/sdda-eval {n} --run-only` — rejoue les suites sans agent (après un
  changement de prompt ou de modèle)
- `/sdda-eval {n} --levels L0,L4` — sous-ensemble de niveaux
- `/sdda-eval {n} --acceptance` — PHASE 8 : holdout + non-régression → G8
- `/sdda-eval {n} --runs 5` — surcharge `EvalRuns` (jamais `1` en CI)

---

## STEP 1 — Valider les arguments

`{n}` entier ≥ 1 **obligatoire**. Flags exclusifs : `--datasets-only` ⊥
`--run-only` ⊥ `--acceptance`. `--levels` ⊆ `{L0..L7}` (L8 appartient à
`/sdda-review`, L9 à `--acceptance`). `--runs` entier ≥ 1.

`--runs 1` → autorisé en local uniquement ; si `CI=true` ou `SDDA_ENV=ci` →
ERROR :
```
ERROR: /sdda-eval {n} — k=1 refusé
CAUSE: [EVAL_SINGLE_RUN_FORBIDDEN] EvalRuns=1 en CI viole non-determinism-k-runs (P3)
FIX: relancer sans --runs (défaut EvalRuns={…}) — un run vert n'est pas une preuve
```

Invalide → ERROR `[INVALID_ARG]`.

---

## STEP 2 — Pré-conditions

1. `STACK.md` présent et rendu.
2. MISSION unique, **G1 franchie** (minimum pour `--datasets-only` : il faut
   des AC pour nommer les datasets) ; **G6 franchie** pour le mode complet et
   `--acceptance`.
   ```bash
   python .sdda/sdda.py compute-status --mission {n} --require-gate {G1|G6}
   ```
   KO → ERROR `[CAP_GATE_NOT_PASSED]` ou `[ORCH_GATE_NOT_PASSED]` (FIX :
   `/sdda-caps {n}` ou `/sdda-build {n}`).
3. Les clés de l'application sont là — c'est ici qu'elle appelle vraiment ses
   modèles (0 token, aucune valeur affichée) :
   ```bash
   python .sdda/sdda.py install-env --require      # sauf --datasets-only
   ```
   `workspace/assets/.env` absent → ERROR `[SECRET_FILE_MISSING]` ; une variable
   déclarée dans STACK.md mais absente → ERROR `[SECRET_VAR_UNDECLARED]`.
4. Lire `## Project Config` : `EvalRuns`, `EvalVarianceWarnPct`,
   `JudgeCalibrationMinKappa`, `JudgeCalibrationMinItems`,
   `HoldoutDisjointCheck`, `RegressionTolerancePct`, `AdversarialSetMinItems`,
   `MaxParallel`. Lire `## Runtime Models` (`JudgeModel`) et `## Active Eval`.

```bash
RUN_ID=${SDDA_RUN_ID:-$(python .sdda/sdda.py state new-run \
  --mission {n} --command "/sdda-eval" --tags "$TAGS")}
```

Si `--run-only` → STEP 5. Si `--acceptance` → STEP 7.

---

## STEP 3 — Agents en parallèle (borné `MaxParallel`)

| Agent | Tier | Écrit dans (Create exclusif) | Skippé si |
|---|:-:|---|---|
| `qa-evals` | **deep** | `workspace/pipeline/datasets/**`, `workspace/pipeline/{suites,calibration}/**` | jamais |
| `qa-tests` | balanced | `workspace/src/**/tests/**` (L0→L2) | `--datasets-only` |

Un seul message multi-`Agent` (2 ≤ `MaxParallel`). Chemins disjoints.

Prompt `qa-evals` :
```
MISSION {n}-{MissionName}. Pour chaque AC de chaque CAP (workspace/pipeline/caps/{n}-*.md), produire :
- golden/{dataset}.jsonl  (≥ 50 items, schéma .sdda/templates/golden-set.schema.json)
- holdout/mission-{n}-v{k}.jsonl  (≥ 30 items, DISJOINT du golden — vérifié par hash)
- calibration/{grader}.jsonl  (≥ {JudgeCalibrationMinItems} items labellisés HUMAINEMENT — signaler
  les items à faire labelliser, ne jamais les inventer)
- adversarial/{n}-*.jsonl  (≥ {AdversarialSetMinItems} : injection directe, indirecte via corpus
  empoisonné, via outil, abus d'outil, escalade, exfiltration, tenant, budget, persona)
- workspace/pipeline/suites/{n}-*.yaml  (grader, seuil, runs, dataset, niveau L3..L7 — depuis les AC)
Droit de veto : un AC non mesurable → [AC_NOT_EVALUABLE] renvoyé à po-capabilities, STOP.
Ground truth : ## Ground Truth de la MISSION (Source, Volume). Aucune écriture hors
workspace/pipeline/{datasets,suites,calibration}/.
```

`qa-tests` part **une fois par couche présente** dans `workspace/src/{App}/`
(`tools`, `retrieval`, `data`, `agents`, `orchestration`, `serving`, puis `tests`
pour les transverses), en vagues ≤ `MaxParallel` : son budget est plafonné par
tier (`preflight_agent_budget`), et lire tout `src/**` d'un coup remplissait la
fenêtre avant le premier tour. Les couches écrivent sous des `tests/` disjoints.

Prompt `qa-tests` — la première ligne désigne la couche (`{object}` de ses `reads:`) :
```
SDDA-LAYER: {couche}
MISSION {n}. Tests déterministes L0 (schémas, lint, IR, budget, disjonction, fraîcheur, ownership,
secrets), L1 (fonctions pures, LLM mocké), L2 (contrats d'outils : happy, chaque erreur, timeout,
auth KO, idempotence ; connectivité live marquée `network`). Stack : {lang}.md, {eval}.md.
Un test instable est un bug, pas une propriété. Aucune écriture hors src/**/tests/.
```

Attendre les deux. `qa-evals` en ERROR `[AC_NOT_EVALUABLE]` → STOP avec
FIX `/sdda-caps {n}` (le veto remonte d'un étage). Autre ERROR → STOP.

**State tracking** : `set-phase --phase eval_datasets --status {pass|fail}
--payload-json '{"golden":G,"holdout":H,"calibration":C,"adversarial":A,"suites":S}'`.

Si `--datasets-only` → STEP 4 puis récap court, STOP.

---

## STEP 4 — Figer les datasets (déterministe)

```bash
python .sdda/sdda.py validate-datasets --mission {n} --freeze --json
```

Le script écrit lui-même la part `datasets` de G8
(`workspace/.sys/.validation/G8-{n}-{MissionName}.datasets.json`) ; la sortie
`--json` ne sert qu'au récap.

| # | Contrôle | Classe si KO |
|---|---|---|
| 1 | Chaque `dataset` nommé par une AC existe et respecte `golden-set.schema.json` | `[EVAL_DATASET_MISSING]` |
| 2 | Tailles minimales : golden 50, holdout 30, calibration `JudgeCalibrationMinItems`, adversarial `AdversarialSetMinItems` | `[EVAL_DATASET_TOO_SMALL]` |
| 3 | `golden ∩ holdout = ∅` par hash d'item (`HoldoutDisjointCheck: strict`) | `[HOLDOUT_NOT_DISJOINT]` |
| 4 | Aucun secret, aucune PII non déclarée dans les items | `[SECRET_LEAK]` / `[PII_IN_DATASET]` |
| 5 | `dataset_hash` écrit pour chaque fichier (épinglage P10) | — |

`HoldoutDisjointCheck: warn` transforme 3 en WARN — audit-loggué (INVARIANTS
`holdout-disjoint-from-golden`). Les datasets sont **figés** : toute écriture
ultérieure change le hash et périme les résultats (R2).

### 4.bis — Calibration des juges (P9)

Pour chaque grader `llm-judge` des suites :

```bash
python .sdda/sdda.py calibrate-judge --mission {n} --grader {g} --json
```

Le script écrit lui-même la part `calibration` de G5
(`workspace/.sys/.validation/G5-{n}.calibration.json`, rattachée à chaque CAP de
la MISSION) : rouge, elle bloque G5. La sortie `--json` n'est **jamais**
redirigée vers `workspace/pipeline/calibration/{g}.json` — ce fichier est
l'ENTRÉE du script (le `judgeCalibrationRef` de la suite, qui pointe les labels
humains) ; l'écraser par le rapport effaçait la référence aux labels à la
première exécution.

| Résultat | Effet |
|---|---|
| `kappa ≥ JudgeCalibrationMinKappa` (0.6) | juge **bloquant** |
| `kappa < seuil` ou labels humains incomplets | juge **advisory** — score informatif, ne bloque plus ; WARN `[JUDGE_UNCALIBRATED]` |

Un juge advisory est écrit tel quel dans le rapport : on ne cache pas qu'on
mesure la complaisance d'un modèle envers un autre.

---

## STEP 5 — Exécution des suites L0 → L7 (script, k runs)

```bash
# L2 (G3 part suites) : les tests de contrat pytest de qa-tests, pas des items notés
python .sdda/sdda.py run-tool-suites --mission {n} --json
python .sdda/sdda.py eval-runner --mission {n} --run-id "$RUN_ID" \
  --levels ${LEVELS:-L0,L1,L3,L4,L5,L6,L7} $( [ -n "$RUNS" ] && echo --runs "$RUNS" ) \
  --executor {module}:{Executor} --json
```

`--run-id "$RUN_ID"` est obligatoire : sans lui le rapport est nommé par
horodatage, et `check-regression --run` / `promote-baseline --run` ne le
retrouvent pas. `--runs` n'est envoyé que sur surcharge explicite (`/sdda-eval
{n} --runs 5` → `RUNS=5`) : sans lui, `eval-runner` résout k **par suite** —
`runs` de l'AC, sinon `EvalRuns`, ou `EvalRunsCritical` pour une CAP
`critical` et toute suite L8. L'ancien `--runs ${RUNS:-EvalRuns}` envoyait le
texte `EvalRuns` à argparse (refus immédiat), et une valeur unique aurait
écrasé `EvalRunsCritical` sur les CAPs critiques.

Le rapport complet est écrit par le script lui-même, en JSON, sous
`workspace/.sys/reports/{n}-{RUN_ID}.json` (`-2`, `-3`… pour les appels suivants
du même run) — c'est ce que lisent les reviewers de l'étage B. La sortie
`--json` ne sert qu'au récap ; elle n'est jamais redirigée sous `.validation/`,
où seul un rapport de gate a sa place.

| Niveau | Contenu | Coût | Gate rejouée |
|---|---|---|---|
| L0 | statique : schémas, lint prompts, IR, budget estimé, disjonction, fraîcheur baselines, ownership, secrets, CVE | 0 token | — |
| L1 | unit, LLM mocké | 0 token | — |
| L2 | contrats d'outils + connectivité live | 0 token | G3 |
| L3 | retrieval sans agent : recall@k, nDCG, groundedness, citations | 0 token LLM (sauf groundedness si juge) | G4 |
| L4 | agents isolés (mocks) vs AC, k runs | tokens | G5 |
| L5 | trajectoires sur la trace : routage par classe, outils, hops, bornes | 0 token | G6 |
| L6 | intégration : vrais outils, vrai index, vraie base de test | ~0 token | — |
| L7 | bout-en-bout sur le golden de mission : qualité + coût + latence | tokens | G6 |

Chaque eval LLM porte `runs: k`, rapporte `score_mean`, `score_stddev`,
`pass_rate`, `min`, `max`, et son tuple d'épinglage
`(prompt_hash, model_id, retrieval_index_hash, tool_schema_hash, dataset_hash)`.

**Fraîcheur** : avant d'exécuter, `check_baseline_freshness.py` compare le tuple
courant à celui de la baseline. Si un hash a bougé, les résultats précédents
sont **marqués périmés** (`[EVAL_STALE]`, WARN) et la ré-exécution est
obligatoire — c'est précisément ce qui se passe ici. Aucun résultat périmé
n'est réutilisé.

Les rapports de gate `G3-{outil}.suites`, `G5-{cap}`, `G6-{n}-{MissionName}` sont **réécrits** par cette exécution : ils
sont la source de vérité de `compute_status.py` (R1). Une régression ici fait
redescendre la MISSION à `Implemented` (R2), silencieusement et sans arbitrage.

**State tracking** : `set-phase --phase eval --status {pass|warn|fail}
--payload-json '{"verdict":"green|yellow|red","levels":[…],"stale":bool}'`.

---

## STEP 6 — Rapport à trois couleurs

Verdict global = **minimum** des verdicts par CAP (R3) ; verdict CAP = minimum
de ses AC.

| | Condition |
|---|---|
| 🟢 **VERT** | `mean ≥ seuil` ∧ `pass_rate = 1.0` ∧ `stddev ≤ EvalVarianceWarnPct` — sur toutes les AC bloquantes |
| 🟡 **JAUNE** | seuil franchi en moyenne mais variance élevée ou `pass_rate < 1.0`, ou ≥ 1 juge advisory seul sur une AC |
| 🔴 **ROUGE** | `mean < seuil`, ou une CAP `critical` échoue, ou un budget (coût / latence / tokens) dépassé, ou L0-L2 rouge |

```
{🟢|🟡|🔴} /sdda-eval {n}-{MissionName} — PHASE 6 · k={k}

DÉTERMINISTE (L0-L2, L5, L6) : {pass}/{total} tests  {🟢|🔴}
RETRIEVAL   (L3)  {index}: recall@{k} {x} · nDCG {y} · groundedness {z} · citations {c}   {🟢|🔴}
AGENTS      (L4, isolés)
  CAP {n}-1 {Name}     {metric}  {mean} ±{std}  pass {p}  (k={k})   {🟢}
  CAP {n}-2 {Name}     {metric}  {mean} ±{std}  pass {p}  (k={k})   {🟡}  variance > {EvalVarianceWarnPct}%
  CAP {n}-3 {Name}     {metric}  {mean} ±{std}  pass {p}  (k={k})   {🔴}  critical
E2E         (L7)  coût p50 ${x} · max ${m} (cap ${cap}) · p95 {ms} ms · hops p95 {h}   {🟢|🔴}
Juges       : {J} calibrés (κ ≥ {min}) · {A} advisory ({liste})
Coût par CAP : {n}-1 ${…} · {n}-2 ${…} · …   (où part l'argent)
Abstention  : {rate} sur le golden  (un système qui ne dit jamais « je ne sais pas » ment mieux)
Top outils en échec : {tool} ({e} erreurs) · …

Rapport complet : workspace/.sys/reports/{n}-{RUN_ID}.md
Run trace       : {RUN_ID}

Prochaine étape :
  🟢 → /sdda-review {n}   (revue trois étages + SAFETY GATE)
  🟡 → lire les AC jaunes ; livrer maintenant est un pari sur le prochain tirage
  🔴 → /sdda-build {n} --agent {agent} sur les CAPs rouges, puis /sdda-eval {n} --run-only
```

Sur 🔴, émettre aussi le bloc ERROR :
```
ERROR: /sdda-eval {n} — verdict rouge
CAUSE: [EVAL_RED] CAP {n}-{m} {Name} {metric} mean {x} < {seuil} (critical, k={k}) ; [BUDGET_EXCEEDED_MEASURED] {détail}
FIX: corriger la couche fautive (G3→G6 indiquent laquelle) puis /sdda-eval {n} --run-only
```

---

## STEP 7 — Mode `--acceptance` : PHASE 8, ACCEPTANCE GATE (G8)

Uniquement sur **holdout**, jamais sur le golden. Pré-requis : G6 et G7
franchies (`--require-gate G7`) ; sinon ERROR `[SAFETY_GATE_NOT_PASSED]`.

```bash
python .sdda/sdda.py check-baseline-freshness --mission {n} --strict
python .sdda/sdda.py eval-runner --mission {n} --run-id "$RUN_ID" --level L9 --dataset holdout \
  --executor {module}:{Executor} $( [ -n "$RUNS" ] && echo --runs "$RUNS" ) \
  --baseline workspace/pipeline/baselines/{n}-system.json --json
python .sdda/sdda.py check-regression --mission {n} --run "$RUN_ID" --json \
  > workspace/.sys/.validation/regression-{n}.json
python .sdda/sdda.py compute-status --mission {n} --require-gate G8
# exit 0 → G8 franchie ; sinon → fail
python .sdda/sdda.py state set-phase --phase acceptance --status {pass|fail}
```

`check-regression --run "$RUN_ID"` lit **tous** les rapports du run
(`{n}-{RUN_ID}.json`, `-2`, `-3`…), chaque suite à sa dernière mesure : un run
de `/sdda-full` en porte un par appel d'`eval-runner` (G5, G6, PHASE 6, L8,
G8), et le premier seul était celui de la PHASE 4.

`set-phase --phase acceptance` ferme la lignée : sans lui, aucune commande
n'enregistrait la dernière phase canonique, `resume-target` ne rendait jamais
`done`, et un `--resume` après une acceptation verte rejouait l'acceptation —
c'est-à-dire relisait le holdout une fois de plus.

> **Un seul appel, niveau L9.** Il y en avait deux : un `--level L7 --dataset
> holdout`, puis un `--level L9`, concaténés par `>>` dans le même fichier. Les
> trois choses étaient fausses ensemble : `L7` est mappé sur **G6**, pas G8, donc
> le premier appel écrasait un verdict d'orchestration en croyant mesurer
> l'acceptation ; faire itérer une suite L7 sur le holdout est exactement ce que
> `[AC_DATASET_IS_HOLDOUT]` refuse ; et deux JSON concaténés ne se relisent pas.
> Le holdout n'est mesuré que par la suite `{n}-acceptance` (L9), compilée depuis
> `## Quantified Goal` de la MISSION.

`--executor` est **obligatoire** : `eval_runner` n'appelle aucun LLM lui-même.
Sans lui il sort `[EVAL_EXECUTOR_MISSING]` plutôt que de supposer une mesure.
Le squelette généré expose `{AppName}.evals.executor:CliExecutor` (bout-en-bout,
la surface console) et `:InProcessExecutor` (isolé, outils mockés).

`check_regression.py` rend le contrôle 3. Il lit la **tolérance** (`RegressionTolerancePct`)
**et l'écart-type de la baseline** (`RegressionNoiseSigma`) : une baisse au-delà de
la tolérance mais sous N σ de la baseline est `WARN [REGRESSION_WITHIN_NOISE]`,
pas `[REGRESSION]` — bloquer sur un tirage apprend à relever la tolérance. Exit 1
→ le contrôle 3 est KO. Sortie `data.regressions[]`, `data.withinNoise[]`,
`data.stale[]` (comparaison **refusée** si le tuple d'épinglage a bougé :
`[EVAL_BASELINE_STALE]`).

| # | Contrôle | Classe si KO |
|---|---|---|
| 1 | Tuple d'épinglage identique à celui des rapports G5/G6 (sinon les gates amont sont périmées) | `[EVAL_STALE]` |
| 2 | `## Quantified Goal` de la MISSION atteint sur holdout (`Metric ≥ Target`, k runs, pass_rate 1.0) | `[GOAL_NOT_MET]` |
| 3 | Aucune métrique en baisse > `RegressionTolerancePct` **et** hors de `RegressionNoiseSigma` σ de la baseline (si baseline existe) — `check_regression.py` | `[REGRESSION]` |
| 4 | `golden ∩ holdout = ∅` re-vérifié | `[HOLDOUT_NOT_DISJOINT]` |

| G8 | Effet |
|---|---|
| 🟢 | MISSION → `Approved`. La baseline **ne bouge pas automatiquement** (L9) : proposer `promote-baseline --mission {n} --run "$RUN_ID" --label "…"` (action tracée ; toutes les suites mesurées par le run) |
| 🟡 | objectif atteint en moyenne, variance élevée — MISSION reste `Evaluated` |
| 🔴 | STOP + ERROR `[ACCEPTANCE_GATE_FAILED]` |

```
ERROR: /sdda-eval {n} --acceptance — ACCEPTANCE GATE rouge
CAUSE: [ACCEPTANCE_GATE_FAILED] [GOAL_NOT_MET] {Metric} 0.88 < 0.90 sur holdout (k={k}) ; [REGRESSION] routing_accuracy -4.2% vs baseline (tolérance 3%)
FIX: NE PAS itérer contre le holdout ; corriger sur le golden (/sdda-build, /sdda-eval {n} --run-only) puis relancer --acceptance
```

**Aucun bypass pour G8.**

---

## Règles de cette commande

- **Deux agents au plus**, en parallèle, chemins disjoints ; aucun spawn imbriqué.
- **`qa-evals` est le seul** à écrire sous `workspace/pipeline/datasets/`. Jamais un `dev-*`.
- **Datasets figés avant exécution** — barrière AGENT-ROSTER.md §4.
- **k runs, jamais 1** hors dev local ; **variance rapportée**, jamais aplatie.
- **Juge non calibré = advisory**, dit explicitement.
- **Holdout intouchable** : on n'itère jamais contre lui ; `--acceptance` est
  la seule commande qui le lit.
- **Résultats épinglés** : un hash qui bouge périme, ne « reste probablement valable » pas.
- **Baseline déplacée explicitement**, jamais par écrasement.

---

## Chat Output Protocol

Applique ``.sdda/rules/output-protocol.md` (Read ce fichier avant de poursuivre)`. Label `[EVAL]`, plage `0-100%`
(agents 0-40 %, freeze + calibration 40-50 %, suites 50-95 %, rapport 95-100 %).
Erreurs : bloc ERROR/CAUSE/FIX 3 lignes.
