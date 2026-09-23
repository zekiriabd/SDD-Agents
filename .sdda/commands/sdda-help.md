---
name: sdda-help
description: /sdda-help — Aide contextuelle « quoi faire ensuite » selon l'état dérivé
---
# /sdda-help — Aide contextuelle « quoi faire ensuite »

Guidance orientée action : pas un diagnostic brut (c'est `/sdda-status`), mais
**la prochaine commande logique** selon l'état dérivé du projet, avec son but
en une ligne.

**Lecture seule**, aucune écriture, aucun agent, ~0 token (délégation à
`compute_status.py`).

**Usage :**
- `/sdda-help` — guidance globale (où on en est + quoi faire)
- `/sdda-help {n}` — guidance focalisée sur la MISSION `{n}`
- `/sdda-help "comment X ?"` — FAQ (matching mots-clés)

**Différence avec `/sdda-status`** : même introspection, mais 1 à 5 lignes
actionnables au lieu d'un tree complet. Pour qui ne connaît pas les 10
commandes par cœur.

---

## STEP 1 — Détection du mode

| Argument | Mode |
|---|---|
| absent | **global** : toutes les MISSIONs, identifier le plus gros trou |
| entier ≥ 1 | **focused** : MISSION `{n}` |
| commence par `"` ou `'`, ou contient `?` | **FAQ** |

Si `{n}` sans MISSION correspondante :
```
Aucune MISSION {n} dans workspace/feats/missions/.
Pour la créer : /sdda-mission {Name}   (ou /sdda-full {Name} pour enchaîner tout le pipeline)
```

---

## STEP 2 — Collecte d'état (déterministe, 0 LLM)

```bash
python .sdda/sdda.py compute-status [--mission {n}] --json
python .sdda/sdda.py state status [--mission {n}] --json
python .sdda/sdda.py human-tasks [--mission {n}] --json     # ce qui attend un humain
```

Échec script → fallback : recommander `/sdda-status` et inviter à diagnostiquer.

---

## STEP 3 — Décision (arbre, premier match gagne)

### 3.A — Mode global

| Condition détectée | Recommandation |
|---|---|
| `STACK.md` absent ou avec `{{` | `python bootstrap.py` (terminal) — ou `/sdda-bootstrap` pour la marche à suivre |
| ≥ 1 tâche humaine **bloquante** (`human_tasks.py`) | son `how`, tel quel — un roster à compléter, un jaune G5/G6/G8 à assumer : aucune commande ne le fera à la place de l'humain |
| 0 MISSION | `/sdda-mission {Name}` — éliciter la 1re MISSION (objectif chiffré, budget, vérité terrain, ~10 questions) |
| ≥ 1 MISSION `Blocked` | `/sdda-status {n}` puis corriger `[{CLASS}]` — une gate rouge bloque tout ce qui est au-dessus (R3) |
| ≥ 1 MISSION avec résultats `stale` | `/sdda-eval {n} --run-only` — un hash épinglé a bougé, les verts précédents ne valent plus rien (P10) |
| ≥ 1 MISSION `Draft` (G0 🔴) | `/sdda-mission {n}` — compléter les `<à préciser>` |
| ≥ 1 MISSION G0 ✅ sans CAP | `/sdda-caps {n}` — découper en capabilities **mesurables** (métrique + seuil + dataset) |
| ≥ 1 MISSION `Specified` sans roster | `/sdda-roster {n}` — l'architecte déclare les agents (gabarit pré-rempli, 0 token) ; le framework ne les invente pas (P7) |
| ≥ 1 MISSION `Specified` | `/sdda-topology {n}` — matérialiser le roster déclaré : allocation, graphe, bornes, budget |
| ≥ 1 MISSION `Architected` | `/sdda-full {n}` — datasets → socle → agents → orchestration → eval → revue → acceptation |
| ≥ 1 MISSION `Implemented`/`Tested` | `/sdda-eval {n}` — suites L0→L7, verdict trois couleurs |
| ≥ 1 MISSION `Evaluated` 🟢 sans G8 | `/sdda-eval {n} --acceptance` — objectif chiffré sur **holdout** |
| ≥ 1 MISSION `Evaluated` 🟡 | lire les AC jaunes dans `/sdda-status {n}` — décider par écrit (ADR) ou corriger |
| tout `Approved` | `/sdda-status` pour le récap, ou `/sdda-mission {Name}` pour la suivante |

### 3.B — Mode focused (`{n}`)

| État dérivé de la MISSION `{n}` | Recommandation |
|---|---|
| `Draft`, G0 🔴 | `/sdda-mission {n}` (lire `G0-{n}-{Name}.json` : classes `[MISSION_*]`) |
| G0 ✅, 0 CAP ou G1 🔴 | `/sdda-caps {n}` (lire `{n}-G1-cap.json` : `[AC_NOT_EVALUABLE]` = l'AC ne nomme pas métrique/seuil/dataset) |
| G1 ✅, roster absent ou `[ARCH_ROSTER_*]` | `/sdda-roster {n}` puis remplir `workspace/feats/topology/{n}-roster.md` — chaque `<à préciser>` est une décision de l'architecte, pas du framework |
| G1 ✅, G2 absente ou 🔴 | `/sdda-topology {n}` — `[TOPOLOGY_UNJUSTIFIED]` : nommer une des 5 raisons de P7 ou supprimer l'agent ; `[UNBOUNDED_LOOP]` : borner le cycle |
| G2 ✅, IR stale | `/sdda-topology {n} --recompile-only` |
| G2 ✅, golden absent | `/sdda-eval {n} --datasets-only` — les datasets précèdent le code |
| G3 🔴 | `/sdda-build {n} --layer socle` — lire `{n}-G3-tool.json` : outil et test en échec |
| G4 🔴 | revoir le contrat de retrieval (chunking, hybridWeights, topK) puis `/sdda-build {n} --layer socle` — **ne pas compenser dans le prompt** |
| G5 🔴/🟡 | `/sdda-build {n} --agent {agent}` sur la CAP fautive, puis `/sdda-eval {n} --run-only` |
| G6 🔴 | lire la distribution des trajectoires dans `{n}-G6-orch.json` ; graphe → `/sdda-topology {n}` ; agent → `--agent` |
| G6 ✅, eval absente | `/sdda-eval {n}` |
| eval 🟢, G7 absente | `/sdda-review {n}` |
| étage A 🔴 | lire `{n}-review-A-spec.md` : `not_verified` → `/sdda-eval {n}` ; `circumvented` → corriger la suite |
| G7 🔴 `[INJECTION_SUCCEEDED]` | ajouter l'item au set adversarial (qa-evals), durcir le prompt, `/sdda-build {n} --agent {agent}` → `/sdda-eval --run-only` → `/sdda-review {n}` |
| G7 🔴 `[TOOL_SCOPE_EXCESS]` | retirer l'outil de `agents[].tools` dans la topologie → `/sdda-topology {n} --recompile-only` → `/sdda-build {n} --agent {agent}` |
| G7 ✅, G8 absente | `/sdda-eval {n} --acceptance` |
| G8 🔴 `[GOAL_NOT_MET]` | **ne pas itérer contre le holdout** — améliorer sur le golden, puis relancer `--acceptance` |
| `Approved` | rien — `/sdda-status {n}` ; `promote_baseline.py` si la baseline n'a pas été promue |

### 3.C — Mode FAQ

Matcher l'argument (minuscules) contre les mots-clés. Premier match → réponse
2-4 lignes. Aucun match → suggérer `/sdda-help` sans argument.

| Mots-clés | Réponse |
|---|---|
| `bootstrap`, `démarrer`, `commencer`, `nouveau projet` | `python bootstrap.py` (interactif) — rend `workspace/stack/STACK.md` + arborescence + smoke. Vérifier ensuite les 3 blocs `Active Harness` / `Build Models` / `Runtime Models` : ils sont indépendants. |
| `harness`, `build models`, `runtime models`, `quel modèle` | Trois notions distinctes (ARCHITECTURE §6) : où tourne la construction, qui paie les Developer Agents, qui fait tourner le produit. Les agents déclarent un **tier** (`fast/balanced/deep`), jamais un modèle ; la résolution est dans `.sdda/providers/*.yaml`. |
| `mission`, `feat`, `spec`, `élicitation` | `/sdda-mission {Name}`. Une MISSION exige un objectif chiffré, un budget d'exécution (coût/latence/tokens), une vérité terrain et une failure policy. G0 refuse tout `<à préciser>` résiduel. Aucun bypass. |
| `ac`, `critère`, `mesurable`, `evaluable`, `AC_NOT_EVALUABLE` | Un AC de CAP nomme **métrique + seuil + dataset + grader + k runs** (P2). « Répond de manière utile » est rejeté par G1. Graders : <!--sdda:graders-->`exact`, `regex`, `schema`, `numeric-tolerance`, `semantic-similarity`, `trajectory`, `cost`, `latency`, `llm-judge`<!--/sdda:graders--> (`llm-judge` calibré). |
| `roster`, `déclarer les agents`, `manifeste`, `ARCH_ROSTER`, `qui décide` | Le roster est déclaré par l'**architecte** dans `workspace/feats/topology/{n}-roster.md` — un Markdown dont le premier bloc `yaml` est la déclaration (`/sdda-roster {n}` écrit le gabarit pré-rempli, `--validate` le vérifie, 0 token). Le framework dérive ce qui se dérive et laisse `<à préciser>` ce qui se décide ; `architect-topology` matérialise **ce** roster sans en changer une ligne (P7). |
| `tâche humaine`, `human`, `bloqué sur quoi`, `qui doit faire` | `human_tasks.py [--mission n]` (repris par `/sdda-status`) liste ce qu'aucun agent n'a le droit de faire : roster à compléter, labels humains d'un juge, ADR exigé par `STACK.md`, jaune G5/G6/G8 à assumer. Listing, exit 0, aucun rapport. |
| `topologie`, `agent en plus`, `multi-agent`, `TOPOLOGY_UNJUSTIFIED`, `séparation des responsabilités` | Défaut : **un agent, des outils** (P7). Un agent supplémentaire exige une des 5 raisons closes : isolation de scope d'outils, tier distinct, pression de contexte mesurée, fonction objectif différente, parallélisme requis. « Séparation des responsabilités » n'est pas recevable. |
| `budget`, `coût`, `cost`, `BUDGET_EXCEEDED` | Deux budgets : **construction** (`MaxCostPerRun` $<!--sdda:config MaxCostPerRun-->50<!--/sdda:config-->, hook cost cap) et **exécution du produit** (`CostPerRunTargetUsd` / `HardCapUsd`, estimé en G2, mesuré en G6, bloquant aux deux — P6). Bypass estimation : `SDDA_BYPASS_BUDGET_ESTIMATE=1` + `SDDA_BYPASS_REASON`, audit-loggué. |
| `boucle`, `loop`, `max_iterations`, `UNBOUNDED_LOOP` | Tout agent porte `max_iterations`, `max_tool_calls`, `max_delegation_depth`, `timeout_s`, `budget_usd` + comportement à l'atteinte (P12). Tout cycle du graphe est coupé par une borne ; sinon G2 rouge, sans bypass. |
| `ir`, `intermediate`, `IR_STALE`, `recompile` | `workspace/.sys/.ir/{n}-system.ir.json` = projection compilée des contrats Markdown (jamais éditée à la main). Périmé → `/sdda-topology {n} --recompile-only`. La TOPOLOGY GATE s'exécute sur l'IR, pas sur la prose. |
| `retrieval`, `rag`, `recall`, `hallucine`, `RETRIEVAL_BELOW_THRESHOLD` | La RETRIEVAL GATE (G4) mesure le retrieval **sans agent** (L3) avant l'AGENT GATE. Un recall à 0.4 se présente comme « l'agent hallucine ». Corriger le contrat (chunking, hybride, rerank), jamais le prompt. Bypass `SDDA_BYPASS_RETRIEVAL_GATE=1`, porté jusqu'en G7. |
| `outil`, `tool`, `effet de bord`, `side effect`, `SIDE_EFFECT_UNDECLARED` | Tout outil déclare `sideEffectClass` ∈ `read-only / write-scoped / write-destructive / external-side-effect` ; les trois derniers exigent une `safetyStrategy` (dry-run, clé d'idempotence, confirmation, allowlist, plafond). G3 vérifie contrat + tests + connectivité live. |
| `prompt`, `inline`, `PROMPT_INLINE_DETECTED`, `f-string` | Les prompts vivent dans `workspace/src/prompts/{agent}.system.md`, hashés, chargés au runtime (P1). Un prompt dans une f-string est un changement de comportement invisible. Owner : `dev-prompt` ; `dev-agent` n'a aucun droit d'écriture dessus. |
| `dataset`, `golden`, `holdout`, `HOLDOUT_NOT_DISJOINT` | `golden/` = ajustement (≥ <!--sdda:config GoldenSetMinItems-->50<!--/sdda:config-->), `holdout/` = verdict (≥ <!--sdda:config HoldoutSetMinItems-->30<!--/sdda:config-->), disjoints par hash. On n'itère **jamais** contre le holdout : seule `/sdda-eval --acceptance` le lit. Owner exclusif : `qa-evals`, jamais un `dev-*`. |
| `juge`, `judge`, `llm-judge`, `kappa`, `calibr`, `advisory` | Un juge LLM est calibré contre ≥ <!--sdda:config JudgeCalibrationMinItems-->50<!--/sdda:config--> labels humains (κ ≥ <!--sdda:config JudgeCalibrationMinKappa-->0.6<!--/sdda:config-->) avant de bloquer (P9). Sous le seuil il devient `advisory` : score informatif, non bloquant, et la CAP ne peut pas être 🟢. Rapport : `workspace/proof/calibration/{grader}.json`. |
| `jaune`, `yellow`, `variance`, `k runs`, `pass_rate` | Toute eval LLM tourne k fois (défaut <!--sdda:config EvalRuns-->3<!--/sdda:config-->, <!--sdda:config EvalRunsCritical-->5<!--/sdda:config--> si critical) et rapporte moyenne + écart-type + pass_rate (P3). 🟢 = seuil ∧ pass_rate 1.0 ∧ variance ≤ `EvalVarianceWarnPct`. 🟡 = seuil en moyenne mais instable : livrer est un pari. |
| `stale`, `périmé`, `EVAL_STALE`, `hash`, `épinglage`, `pinning` | Un résultat est indexé par `(prompt_hash, model_id, index_hash, tool_schema_hash, dataset_hash)` (P10). Un hash bouge → périmé, la MISSION redescend à `Implemented` (R2), `/sdda-eval {n} --run-only` ré-exécute. |
| `injection`, `sécurité`, `safety`, `INJECTION_SUCCEEDED`, `adversarial` | Tout texte non maîtrisé est contenu, jamais instruction (P8). Tout agent avec une entrée `untrusted` porte une suite d'injection (directe + indirecte), exécutée contre le système **vivant** à l'étage C. `[INJECTION_SUCCEEDED]` et `[SECRET_LEAK]` n'ont **aucun** bypass. |
| `scope`, `moindre privilège`, `TOOL_SCOPE_EXCESS` | Un agent ne reçoit que les outils que ses CAPs exigent. L'écart est un finding bloquant de `review-safety` et du contrôle 7 de `validate_ir.py`. Retirer l'outil de la topologie, recompiler. |
| `status`, `état`, `STATUS_UNBACKED`, `Tested` | L'état est **dérivé** des rapports `workspace/.sys/.validation/{n}-G*.json` (R1), jamais de la ligne `Status:`. `/sdda-status {n}`. Un état sans rapport est écrasé. MISSION = min de ses CAPs (R3). |
| `bypass`, `force`, `FORCE_CUMUL_REJECTED`, `SDDA_BYPASS` | `--force` assume les gates **jaunes** seulement. Les bypasses de gate sont des env vars `SDDA_BYPASS_{GATE}=1` + `SDDA_BYPASS_REASON`, écrites dans `.sys/.audit/bypasses.jsonl`. ≥ 2 sur un run → refusé sauf `SDDA_ALLOW_FORCE=1`. G0, G1, G5, G6, G8 : aucun bypass. |
| `resume`, `reprendre`, `interrompu`, `crash` | `/sdda-full {n} --resume` reprend à la phase suivant la dernière `pass` du dernier run, en rejouant les gates (0 token). Dans la couche reprise, `/sdda-build` ne repaie pas un `dev-agent` déjà vert **sur le même prompt et la même entrée IR** (`sdda_state.py should-skip-item`). `--from-phase` force un départ mais ne peut pas sauter une gate non franchie (`[STATE_SKIP_FORBIDDEN]`). |
| `parallèle`, `MaxParallel`, `vague` | Le parallélisme est borné par `MaxParallel` (Project Config, défaut <!--sdda:config MaxParallel-->3<!--/sdda:config-->) et rendu sûr par la matrice d'ownership (chemins disjoints). Aucun agent ne spawne un autre agent : la commande orchestre en vagues. |
| `ownership`, `OWNERSHIP_VIOLATION`, `qui écrit` | `.sdda/rules/ownership.md`. Règle propre à l'agentic : `dev-agent` n'écrit **jamais** sous `datasets/` ni `prompts/` — l'agent qui écrit le code ne modifie ni le jeu qui le juge ni le prompt qu'il implémente. |
| `framework`, `langgraph`, `semantic kernel`, `changer de framework` | Les contrats et l'IR sont neutres framework (P11) ; les idiomes vivent dans `.sdda/stacks/framework/*.md`. Changer de framework = changer `STACK.md` et regénérer `src/` ; aucune spécification n'est invalidée. MVP : Python + LangChain/LangGraph (combo C1). |
| `combo`, `stack validée`, `sla` | Une seule combo sous SLA au MVP (C1, cf. `ROADMAP.md`). Tout le reste est `experimental` et le dit. On annonce ce qu'on a mesuré. |

---

## STEP 4 — Format de sortie

**Cible : 1-5 lignes**, format direct :

```
SDD_Agents — {résumé d'état en 1L}

→ {commande suggérée}          # {but en 1L}
   (puis : {commande suivante optionnelle})
```

Exemples :

```
SDD_Agents — MISSION 1 SupportAssistant : Specified (G1 🟢), aucune topologie.

→ /sdda-topology 1             # décider la topologie la plus simple qui tienne, écrire pourquoi pas plus simple
   (puis : /sdda-full 1 --from-phase build)
```

```
SDD_Agents — MISSION 1 : Blocked · G4 [RETRIEVAL_BELOW_THRESHOLD] recall@8 0.61 < 0.80.

→ éditer workspace/feats/contracts/retrieval/1-contracts-index.retrieval.md (chunking, hybridWeights, rerank)
→ /sdda-topology 1 --recompile-only puis /sdda-build 1 --layer socle
   Ne pas compenser côté prompt : un retriever faible se déguise en agent qui hallucine.
```

```
SDD_Agents — MISSION 2 : Evaluated 🟡 · CAP 2-3 groundedness 0.86 ±0.09 (k=3), juge advisory (κ 0.52).

→ faire labelliser 50 items dans workspace/proof/datasets/calibration/groundedness.jsonl, puis /sdda-eval 2 --run-only
   (sans juge calibré, la CAP ne peut pas être 🟢 — on mesure la complaisance d'un modèle envers un autre)
```

---

## Règles de cette commande

- **Lecture seule.** Aucun Write/Edit, aucun agent, aucune Q/R.
- **Délégation pure** vers `compute_status.py` et `sdda_state.py`. Coût ~0.
- **1 commande recommandée** à la fois — la prochaine étape **utile**, pas
  l'arbre complet.
- **Jamais « itérer contre le holdout »** comme suggestion, quelle que soit la
  situation.
- **FAQ minimale** — pas un substitut à `@.sdda/PHILOSOPHY.md`,
  `@.sdda/ARCHITECTURE.md`, `@.sdda/docs/`.

---

## Chat Output Protocol

Applique `@.sdda/rules/output-protocol.md`. Label `[ANALYSIS]`. Sortie 1 passe,
1-5 lignes. Erreurs : 1 ligne `🔴 [ANALYSIS/FAIL] {résumé}`.
