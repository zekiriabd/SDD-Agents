# Rapport d'impact — gemini-cli

- Niveau de protection : **B**
- Statut : **planned**  ⚠️ *compilable, non validé par un run de conformance*

## Mécanismes

| Mécanisme | Support | Conséquence |
|---|---|---|
| `at_include` | emulated | dégradation contrôlée (consigne écrite, pas de mécanisme) |
| `deterministic_python` | native | aucune |
| `mcp` | native | aucune |
| `runtime_hooks` | partial | **en partie au runtime** — le reste reporté au CI (cf. hooks ci-dessous) |
| `skills_autotrigger` | emulated | dégradation contrôlée (consigne écrite, pas de mécanisme) |
| `slash_commands` | native | aucune |
| `structured_output` | emulated | dégradation contrôlée (consigne écrite, pas de mécanisme) |
| `subagent_spawn` | native | aucune |

## Hooks

| Hook | Sort | Pourquoi |
|---|---|---|
| `postflight_no_inline_prompt` | absent | aucun événement de fin de sous-agent -> CI |
| `postflight_trace_present` | absent | aucun événement de fin de sous-agent -> CI |
| `preflight_agent_bounds` | degraded | sur l'outil du sous-agent (même nom que l'agent) ; le déclenchement de `BeforeTool` sur cet outil et ses paramètres ne sont pas documentés — non vérifié par un run |
| `preflight_agent_budget` | degraded | sur l'outil du sous-agent (même nom que l'agent) ; le déclenchement de `BeforeTool` sur cet outil et ses paramètres ne sont pas documentés — non vérifié par un run |
| `preflight_bash_ownership` | absent | ce hook ne juge qu'un sous-agent nommé ; le payload Gemini ne nomme pas l'auteur -> CI |
| `preflight_cap_gate` | degraded | sur l'outil du sous-agent (même nom que l'agent) ; le déclenchement de `BeforeTool` sur cet outil et ses paramètres ne sont pas documentés — non vérifié par un run |
| `preflight_cost_cap` | degraded | sur l'outil du sous-agent (même nom que l'agent) ; le déclenchement de `BeforeTool` sur cet outil et ses paramètres ne sont pas documentés — non vérifié par un run |
| `preflight_db_envelope` | degraded | sur l'outil du sous-agent (même nom que l'agent) ; le déclenchement de `BeforeTool` sur cet outil et ses paramètres ne sont pas documentés — non vérifié par un run |
| `preflight_forbidden_reads` | absent | ce hook ne juge qu'un sous-agent nommé ; le payload Gemini ne nomme pas l'auteur -> CI |
| `preflight_instance_bind` | degraded | sur l'outil du sous-agent (même nom que l'agent) ; le déclenchement de `BeforeTool` sur cet outil et ses paramètres ne sont pas documentés — non vérifié par un run |
| `preflight_judge_calibration` | degraded | sur l'outil du sous-agent (même nom que l'agent) ; le déclenchement de `BeforeTool` sur cet outil et ses paramètres ne sont pas documentés — non vérifié par un run |
| `preflight_ownership` | degraded | `write_file`/`replace` : zones protégées refusées au runtime ; la matrice par agent exige l'identité de l'auteur, absente du payload Gemini -> CI |
| `preflight_retrieval_gate` | degraded | sur l'outil du sous-agent (même nom que l'agent) ; le déclenchement de `BeforeTool` sur cet outil et ses paramètres ne sont pas documentés — non vérifié par un run |
| `preflight_stack_combo` | degraded | sur l'outil du sous-agent (même nom que l'agent) ; le déclenchement de `BeforeTool` sur cet outil et ses paramètres ne sont pas documentés — non vérifié par un run |
| `preflight_tool_gate` | degraded | sur l'outil du sous-agent (même nom que l'agent) ; le déclenchement de `BeforeTool` sur cet outil et ses paramètres ne sont pas documentés — non vérifié par un run |

## Invariants déplacés vers le CI

`runtime_hooks: partial` — ce qui suit n'est pas, ou pas entièrement,
appliqué au moment de l'action :

- `cap-ac-must-be-evaluable` — **en partie au runtime**, le reste appliqué en différé (CI)
- `no-unbounded-loop` — **en partie au runtime**, le reste appliqué en différé (CI)
- `build-budget-bounded` — **en partie au runtime**, le reste appliqué en différé (CI)
- `activated-stack-is-loadable` — **en partie au runtime**, le reste appliqué en différé (CI)
- `tool-side-effect-declared` — **en partie au runtime**, le reste appliqué en différé (CI)
- `tool-gate-before-agent-wiring` — **en partie au runtime**, le reste appliqué en différé (CI)
- `db-safety-envelope-present` — **en partie au runtime**, le reste appliqué en différé (CI)
- `retrieval-gate-before-agent` — **en partie au runtime**, le reste appliqué en différé (CI)
- `prompts-are-files` — partiellement différé (un enforcer déterministe subsiste)
- `llm-judge-calibrated` — **en partie au runtime**, le reste appliqué en différé (CI)
- `trace-emitted-per-run` — partiellement différé (un enforcer déterministe subsiste)
- `ownership-matrix-enforced` — **en partie au runtime**, le reste appliqué en différé (CI)

> **À dire clairement** : ce qu'aucun hook ne juge ici n'est empêché par
> rien au moment de l'action. Le CI le rattrape — après que le travail a
> été fait sur une base fausse.

## Sorties structurées

`structured_output: emulated` — les rapports
d'eval, les verdicts de gate et l'IR sont du JSON. Un JSON approximatif impose
un parsing défensif et crée une classe de faux verts : un rapport mal parsé
dont les champs manquants passent pour « aucun finding ». **À requalifier par
mesure au premier run de conformance, jamais par optimisme.**

## Note de la matrice

EXPÉRIMENTAL : compilable, jamais validé par un run de conformance.
Gemini CLI lit `GEMINI.md` à la racine : harness_build y écrit un pointeur
qui IMPORTE `.gemini/GEMINI.md` (@./.gemini/GEMINI.md).
HOOKS (BeforeTool, code 2 = blocage) : `preflight_ownership` sur
write_file|replace refuse au runtime les écritures dans les zones protégées,
quel que soit l'auteur ; les dix hooks de spawn sont câblés sur l'outil qui
porte le nom de chaque agent. Le déclenchement de BeforeTool sur l'outil d'un
sous-agent et les paramètres de cet outil ne sont PAS documentés : non
vérifié par un run. Le payload ne nomme pas l'agent auteur d'un appel : la
matrice par agent, les lectures interdites et le shell restent au CI. Aucun
événement de fin de sous-agent : `prompts-are-files` et
`trace-emitted-per-run` restent au CI. Les hooks de projet sont
empreintés : chaque changement de commande demande une confirmation.
Commande POSIX : non vérifié sous Windows.
`structured_output: emulated` est le point à surveiller : les rapports d'eval
et les verdicts de gate sont du JSON. À requalifier après mesure — pas avant.
