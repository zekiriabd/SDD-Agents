# Rapport d'impact — codex

- Niveau de protection : **B**
- Statut : **planned**  ⚠️ *compilable, non validé par un run de conformance*

## Mécanismes

| Mécanisme | Support | Conséquence |
|---|---|---|
| `at_include` | unsupported | **absent** — repli documenté |
| `deterministic_python` | native | aucune |
| `mcp` | native | aucune |
| `runtime_hooks` | partial | **en partie au runtime** — le reste reporté au CI (cf. hooks ci-dessous) |
| `skills_autotrigger` | native | aucune |
| `slash_commands` | native | aucune |
| `structured_output` | native | aucune |
| `subagent_spawn` | native | aucune |

## Hooks

| Hook | Sort | Pourquoi |
|---|---|---|
| `postflight_no_inline_prompt` | absent | `SubagentStop` existe, mais ni l'agent qui s'arrête ni l'effet du code 2 ne sont documentés -> CI |
| `postflight_trace_present` | absent | `SubagentStop` existe, mais ni l'agent qui s'arrête ni l'effet du code 2 ne sont documentés -> CI |
| `preflight_agent_bounds` | absent | le champ de `spawn_agent` qui nomme l'agent lancé n'est pas documenté : une gate qui ne sait pas qui part jugerait tout spawn -> CI |
| `preflight_agent_budget` | absent | le champ de `spawn_agent` qui nomme l'agent lancé n'est pas documenté : une gate qui ne sait pas qui part jugerait tout spawn -> CI |
| `preflight_bash_ownership` | absent | ce hook ne juge qu'un sous-agent nommé ; le payload Codex ne nomme pas l'auteur -> CI |
| `preflight_cap_gate` | absent | le champ de `spawn_agent` qui nomme l'agent lancé n'est pas documenté : une gate qui ne sait pas qui part jugerait tout spawn -> CI |
| `preflight_cost_cap` | absent | le champ de `spawn_agent` qui nomme l'agent lancé n'est pas documenté : une gate qui ne sait pas qui part jugerait tout spawn -> CI |
| `preflight_db_envelope` | absent | le champ de `spawn_agent` qui nomme l'agent lancé n'est pas documenté : une gate qui ne sait pas qui part jugerait tout spawn -> CI |
| `preflight_forbidden_reads` | absent | ce hook ne juge qu'un sous-agent nommé ; le payload Codex ne nomme pas l'auteur -> CI |
| `preflight_instance_bind` | absent | le champ de `spawn_agent` qui nomme l'agent lancé n'est pas documenté : une gate qui ne sait pas qui part jugerait tout spawn -> CI |
| `preflight_judge_calibration` | absent | le champ de `spawn_agent` qui nomme l'agent lancé n'est pas documenté : une gate qui ne sait pas qui part jugerait tout spawn -> CI |
| `preflight_ownership` | degraded | `apply_patch` : zones protégées refusées au runtime ; la matrice par agent exige l'identité de l'auteur, absente du payload Codex -> CI |
| `preflight_retrieval_gate` | absent | le champ de `spawn_agent` qui nomme l'agent lancé n'est pas documenté : une gate qui ne sait pas qui part jugerait tout spawn -> CI |
| `preflight_stack_combo` | absent | le champ de `spawn_agent` qui nomme l'agent lancé n'est pas documenté : une gate qui ne sait pas qui part jugerait tout spawn -> CI |
| `preflight_tool_gate` | absent | le champ de `spawn_agent` qui nomme l'agent lancé n'est pas documenté : une gate qui ne sait pas qui part jugerait tout spawn -> CI |

## Invariants déplacés vers le CI

`runtime_hooks: partial` — ce qui suit n'est pas, ou pas entièrement,
appliqué au moment de l'action :

- `cap-ac-must-be-evaluable` — partiellement différé (un enforcer déterministe subsiste)
- `no-unbounded-loop` — partiellement différé (un enforcer déterministe subsiste)
- `build-budget-bounded` — partiellement différé (un enforcer déterministe subsiste)
- `activated-stack-is-loadable` — **appliqué en différé (CI)**
- `tool-side-effect-declared` — partiellement différé (un enforcer déterministe subsiste)
- `tool-gate-before-agent-wiring` — **appliqué en différé (CI)**
- `db-safety-envelope-present` — partiellement différé (un enforcer déterministe subsiste)
- `retrieval-gate-before-agent` — partiellement différé (un enforcer déterministe subsiste)
- `prompts-are-files` — partiellement différé (un enforcer déterministe subsiste)
- `llm-judge-calibrated` — partiellement différé (un enforcer déterministe subsiste)
- `trace-emitted-per-run` — partiellement différé (un enforcer déterministe subsiste)
- `ownership-matrix-enforced` — **en partie au runtime**, le reste appliqué en différé (CI)

> **À dire clairement** : ce qu'aucun hook ne juge ici n'est empêché par
> rien au moment de l'action. Le CI le rattrape — après que le travail a
> été fait sur une base fausse.

## Note de la matrice

EXPÉRIMENTAL : compilable, jamais validé par un run de conformance.
Codex CLI lit `AGENTS.md` à la racine (32 KiB au plus, project_doc_max_bytes) :
harness_build y écrit un pointeur vers `.codex/AGENTS.md`. Les custom prompts
`.codex/prompts/` n'étaient lus par personne (Codex ne lit que ~/.codex/prompts,
et les déprécie) : les commandes sont des skills `.agents/skills/`.
HOOKS : seul `preflight_ownership` est câblé, sur `apply_patch` — il refuse au
runtime les écritures dans les zones protégées (rapports de gate, baselines,
audit), quel que soit l'auteur. Le payload Codex ne nomme ni l'agent auteur
d'un appel, ni l'agent que lance `spawn_agent` : la matrice d'ownership par
agent, les lectures interdites, le shell et les gates de spawn restent au CI.
`SubagentStop` existe, mais ni l'agent qui s'arrête ni l'effet du code 2 n'y
sont documentés. Les hooks de projet ne se chargent que si `.codex/` est
approuvé (/hooks). Commande POSIX : non vérifié sous Windows.
`sandbox_mode` (read-only | workspace-write) est la seule borne d'écriture par
agent ; elle est plus grossière que la matrice. Caractères permis dans le
`name` d'un agent : non documentés (les exemples officiels sont en snake_case).
GARDE : tout le noyau déterministe, donc toute la validation réelle.
