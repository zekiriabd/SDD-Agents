# `.sdda/python` — la couche déterministe de SDD_Agents

Tout ce qui est ici tourne **sans LLM et sans réseau** (PHILOSOPHY P4) :
stdlib uniquement, `pytest` seulement pour les tests. Python **3.11+**.

## Paquets

| Paquet | Rôle |
|---|---|
| `sdda_lib/` | bibliothèque partagée : `markdown_io` (sections, tables, listes), `yaml_mini` et `jsonschema_mini` (parseurs stdlib), `hashing` (sha256 normalisé CRLF/BOM), `graph` (SCC, cycles, atteignabilité, plus long chemin), `mermaid` (flowchart -> nœuds/arêtes), `layered_config` (base < team < projet, security-down), `pricing` (table de prix par modèle, latence par tier), `gate_reports` (rapports `.sys/.validation/`, audit des bypasses), `errors` (blocs ERROR 3 lignes avec `[CLASS]`), `paths` |
| `sdda_scripts/` | les gates et le compilateur — un script = une commande, `--root`, `--json`, `--no-report` partout |
| `sdda_admin/` | santé du framework lui-même : `framework_smoke.py`, `sync_error_registry.py`, `sync_digests.py` |
| `tests/` | pytest ; `fixtures/project_ok/` est un projet valide, les `fixtures/project_*` sont des overlays négatifs (voir `tests/fixtures/README.md`) |

## Lancer les tests

```bash
# depuis la racine du dépôt
python -m pytest .sdda/python/tests/ -q
```

Chaque test copie `project_ok/` dans un répertoire temporaire : les scripts
écrivent des rapports, un IR, et peuvent réécrire un `Status:` — jamais dans la
fixture source. La priorité des tests va aux cas **négatifs** : un validateur
qui ne sait que dire OK est inutile.

## Lancer les gates

Toutes acceptent `--root <projet>` (défaut : détection du dossier qui contient
`workspace/`), `--json` (sortie machine) et `--no-report` (ne pas écrire dans
`workspace/.sys/.validation/`). Exit `0` = OK, `1` = au moins une erreur bloquante.

| Gate | Commande | Rapport écrit |
|---|---|---|
| G0 MISSION | `python .sdda/sdda.py validate-mission` | `G0-{mission}.json` |
| G1 CAP | `python .sdda/sdda.py validate-cap` | `G1-{cap}.json`, `G1-{mission}.json` |
| G2 TOPOLOGY (Markdown) | `python .sdda/sdda.py validate-topology` | `G2-{mission}.topology.json` |
| 2.9 compilation IR | `python .sdda/sdda.py ir-compiler --mission 1` | `workspace/.sys/.ir/1-system.ir.json` |
| G2 TOPOLOGY (IR) | `python .sdda/sdda.py validate-ir --mission 1` | `G2-{mission}.ir.json` |
| G2 TOPOLOGY (budget) | `python .sdda/sdda.py estimate-budget --mission 1` | `G2-{mission}.budget.json` + `budget.estimated` dans l'IR |
| datasets (part de G8) | `python .sdda/sdda.py validate-datasets` | `G8-{mission}.datasets.json` |
| état dérivé | `python .sdda/sdda.py compute-status [--require-gate G1]` | — (peut réécrire un `Status:` non étayé) |

G2 n'est franchie que quand ses **trois** parts (`topology`, `ir`, `budget`)
sont vertes ; G8 exige `datasets` **et** `acceptance`.

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

`validate_ir.py` applique les 11 contrôles d'`AGENTIC-IR.md §4` : schéma,
références closes, atteignabilité, cycles bornés (`[UNBOUNDED_LOOP]`, sans
bypass), Σ budgets sur le plus long chemin, couverture des CAPs, moindre
privilège (`[TOOL_SCOPE_EXCESS]`), effets de bord (`[SIDE_EFFECT_UNDECLARED]`),
suites d'injection, neutralité framework (`[FRAMEWORK_LEAK_IN_CONTRACT]`),
évaluabilité et calibration des juges (`[JUDGE_UNCALIBRATED]`). Il refuse aussi
un IR périmé par rapport à ses sources (`[IR_STALE]`).

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
