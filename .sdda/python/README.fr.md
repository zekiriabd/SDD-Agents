# `.sdda/python` — la couche déterministe de SDD_Agents

Tout ce qui est ici tourne **sans LLM et sans réseau** (PHILOSOPHY P4) :
stdlib uniquement (`dependencies = []`), `pytest`, `ruff` et `mypy` seulement
pour le développement (`pip install -e ".sdda/python[dev]"`). Python **3.11+**.

La seule exception est volontaire et bornée : `sdda_lib/graders/judge_clients.py`
appelle un juge LLM réel (Anthropic, OpenAI, Gemini, Ollama), en stdlib, pour
les seules suites d'eval qui déclarent un grader `llm-judge` ; l'URL et le nom
de la variable de clé viennent de `providers/*.yaml`. Les validateurs, le
compilateur d'IR et les hooks n'appellent jamais de modèle.

## Paquets

| Paquet | Rôle |
|---|---|
| `sdda_cli.py` | dispatcher unique : `python .sdda/sdda.py {cmd}` (ou `sdda {cmd}` après installation). Le registre est **dérivé du disque** : tout module de `sdda_scripts/`, `sdda_admin/` ou `sdda_hooks/` est une sous-commande nommée par son fichier (`validate_mission.py` -> `validate-mission`) |
| `sdda_lib/` | bibliothèque partagée : `markdown_io` (sections, tables, listes), `yaml_mini` et `jsonschema_mini` (parseurs stdlib, testés contre PyYAML et jsonschema), `hashing` (sha256 normalisé CRLF/BOM), `graph` (SCC, cycles, atteignabilité, plus long chemin), `mermaid` (flowchart -> nœuds/arêtes), `layered_config` (base < team < projet, security-down, validation contre `project-config.schema.json`), `pricing` (tarifs lus dans `providers/*.yaml`, table en dur en repli), `gate_reports` (rapports `.sys/.validation/`, parts de gate, audit des bypasses), `errors` (blocs ERROR 3 lignes avec `[CLASS]`), `tracing` (spans OTel-GenAI, écrits sous verrou), `retrieval_metrics`, `eval_stats`, `eval_pinning`, `calibration`, `graders/`, `paths`, `workspace` |
| `sdda_scripts/` | les gates, le compilateur d'IR, les générateurs et les rapports — un script = une sous-commande, `--root` et `--json` partout, `--no-report` sur les gates |
| `sdda_admin/` | santé du framework lui-même : `framework_smoke`, `sync_error_registry`, `sync_digests`, `sync_counters`, `harness_build`, `hooks_selfcheck`, `planned_scripts`, `command_flags` |
| `sdda_hooks/` | les hooks bloquants `PreToolUse` / `SubagentStop` de Claude Code ; chacun déclare son `WIRING`, que `harness_build` câble dans `.claude/settings.json` (cf. [MULTI-HARNESS.fr.md](../docs/MULTI-HARNESS.fr.md) §3) |
| `tests/` | pytest ; `fixtures/project_ok/` est un projet valide, les autres `fixtures/project_*` sont des overlays (voir [tests/fixtures/README.fr.md](tests/fixtures/README.fr.md)) |

## Lancer les tests

```bash
# depuis la racine du dépôt
python -m pytest .sdda/python/tests/ -q
```

Chaque test copie `project_ok/` dans un répertoire temporaire : les scripts
écrivent des rapports, un IR, et peuvent réécrire un `Status:` — jamais dans la
fixture source. Une garde (`tests/_workspace_guard.py`) fait échouer tout test
qui écrit sous le `workspace/` **réel** du dépôt, y compris depuis un
sous-processus. La priorité des tests va aux cas **négatifs** : un validateur
qui ne sait que dire OK est inutile.

La CI joue en plus `ruff` et `mypy` (configuration dans `pyproject.toml`) et une
couverture avec plancher (`fail_under`).

## Lancer les gates

Toutes acceptent `--root <projet>` (défaut : détection du dossier qui contient
`workspace/`), `--json` (sortie machine) et `--no-report` (ne pas écrire dans
`workspace/.sys/.validation/`). Exit `0` = OK, `1` = au moins une erreur bloquante.
`python .sdda/sdda.py --help` liste toutes les sous-commandes.

| Gate | Commande | Rapport écrit |
|---|---|---|
| G0 MISSION | `python .sdda/sdda.py validate-mission` | `G0-{mission}.json` |
| G1 CAP | `python .sdda/sdda.py validate-cap` | `G1-{cap}.json`, `G1-{mission}.json` |
| G2 TOPOLOGY (Markdown) | `python .sdda/sdda.py validate-topology` | `G2-{mission}.topology.json` (pas avec `--pre`) |
| 2.9 compilation IR | `python .sdda/sdda.py ir-compiler --mission 1` | `workspace/.sys/.ir/1-system.ir.json` |
| G2 TOPOLOGY (IR) | `python .sdda/sdda.py validate-ir --mission 1` | `G2-{mission}.ir.json` |
| G2 TOPOLOGY (budget) | `python .sdda/sdda.py estimate-budget --mission 1` | `G2-{mission}.budget.json` + `budget.estimated` dans l'IR |
| G2 (contributives) | `validate-architecture`, `validate-packaging`, `validate-adr` | `G2-{mission}.{architecture\|packaging\|adr}.json` |
| datasets (part de G8) | `python .sdda/sdda.py validate-datasets` | `G8-{mission}.datasets.json` |
| état dérivé | `python .sdda/sdda.py compute-status [--require-gate G1]` | — (peut réécrire un `Status:` non étayé) |

G2 n'est franchie que quand ses **trois** parts obligatoires (`topology`, `ir`,
`budget`) sont vertes ; ses parts contributives (`architecture`, `packaging`,
`adr`) ne bloquent pas par leur absence, mais leur **rouge** bloque. G8 exige
`datasets` **et** `acceptance`. La table complète des parts vit dans
`sdda_lib/gate_reports.py` (`GATE_PARTS`, `GATE_PARTS_ADVISORY`).

`validate-topology` joue sa passe **complète** avant `ir-compiler` : c'est la
seule qui écrit la part `topology` de G2 ; `--pre` ne fait qu'un contrôle rapide
sans rapport.

### L'IR

`ir_compiler.py` projette MISSION + CAPs + TOPOLOGY (graphe Mermaid compris) +
`contracts/**` + `STACK.md` vers un JSON conforme à `registry/ir.schema.json`.
Il est **déterministe** (clés triées, LF, `compiledAt` conservé tant que le
contenu ne change pas) et **n'invente rien** : un champ manquant produit
`[IR_COMPILE_FAILED]` avec `fichier:section`. Les seules dérivations sont des
conventions listées dans l'en-tête du script (id de suite `{n}-{m}-{metric}`,
calibration par défaut `workspace/pipeline/calibration/{metric}.json`, suite
d'injection `{agent}-injection`, arête Mermaid pointillée `-.->` = arête qui ne
compte pas comme hop).

`validate_ir.py` applique les 11 contrôles d'[AGENTIC-IR.fr.md](../docs/AGENTIC-IR.fr.md)
§4 : schéma, références closes, atteignabilité, cycles bornés
(`[UNBOUNDED_LOOP]`, sans bypass), Σ budgets sur le plus long chemin, couverture
des CAPs, moindre privilège (`[TOOL_SCOPE_EXCESS]`), effets de bord
(`[SIDE_EFFECT_UNDECLARED]`), suites d'injection, neutralité framework
(`[FRAMEWORK_LEAK_IN_CONTRACT]`), évaluabilité et calibration des juges
(`[JUDGE_UNCALIBRATED]`). Il refuse aussi un IR périmé par rapport à ses sources
(`[IR_STALE]`).

`estimate_budget.py` calcule un chemin **nominal** (chaque nœud une fois) et
un **pire cas** (`maxHops` atteint, agents à `maxIterations`, outils à leur
timeout) avec des hypothèses de planification visibles en tête de script — la
G6 mesure, ici on estime. Pire cas > `CostPerRunHardCapUsd` =>
`[BUDGET_EXCEEDED_ESTIMATE]`. Bypass audité : `SDDA_BYPASS_BUDGET_ESTIMATE=1`
+ `SDDA_BYPASS_REASON=…`.

### La machine à états

`compute_status.py` ne lit jamais la ligne `Status:` pour décider : il dérive
l'état des rapports de gate présents et frais (LIFECYCLE R1), périme un rapport
dont un hash épinglé a bougé (R2), prend le minimum des CAPs pour la MISSION
(R3), signale une confiance qui monte (R4) et liste les bypasses (R5). Un
`Status:` déclaré plus haut que l'état calculé est `[STATUS_UNBACKED]` et
**écrasé** (`--no-write` pour ne faire que rapporter).

## Santé du framework

```bash
python .sdda/sdda.py framework-smoke            # cohérence de .sdda/
python .sdda/sdda.py sync-error-registry --check # toute [CLASS] émise est enregistrée
```

Avant un commit, la chaîne complète régénère ce qui est dérivé puis vérifie :
`sync-error-registry` → `sync-digests` → `sync-counters --write` →
`harness-build --prune` → `framework-smoke` → pytest. La CI rejoue chaque étape
en `--check`, plus `hooks-selfcheck` (chaque hook câblé démarre et juge, en
mode strict `SDDA_HOOKS_STRICT=1`).
