# Rapport d'impact — antigravity

- Niveau de protection : **B**
- Statut : **planned**  ⚠️ *compilable, non validé par un run de conformance*

## Mécanismes

| Mécanisme | Support | Conséquence |
|---|---|---|
| `at_include` | emulated | dégradation contrôlée (consigne écrite, pas de mécanisme) |
| `deterministic_python` | native | aucune |
| `mcp` | native | aucune |
| `runtime_hooks` | ci_fallback | **reporté au CI** — appliqué plus tard, pas au moment de l'action |
| `skills_autotrigger` | native | aucune |
| `slash_commands` | native | aucune |
| `structured_output` | native | aucune |
| `subagent_spawn` | native | aucune |

## Hooks

| Hook | Sort | Pourquoi |
|---|---|---|
| `postflight_no_inline_prompt` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `postflight_trace_present` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_agent_bounds` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_agent_budget` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_bash_ownership` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_cap_gate` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_cost_cap` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_db_envelope` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_forbidden_reads` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_instance_bind` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_judge_calibration` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_ownership` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_retrieval_gate` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_stack_combo` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |
| `preflight_tool_gate` | absent | arguments de `toolCall.args` et sémantique des codes de sortie non documentés -> CI |

## Invariants déplacés vers le CI

`runtime_hooks: ci_fallback` — ce qui suit n'est pas, ou pas entièrement,
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
- `ownership-matrix-enforced` — partiellement différé (un enforcer déterministe subsiste)

> **À dire clairement** : ce qu'aucun hook ne juge ici n'est empêché par
> rien au moment de l'action. Le CI le rattrape — après que le travail a
> été fait sur une base fausse.

## Note de la matrice

EXPÉRIMENTAL : compilable, jamais validé par un run de conformance.
Façade propre `.agents/` (il partageait `.gemini/`, qu'il ne lit pas).
Antigravity lit `AGENTS.md` ET `GEMINI.md` à la racine (toujours actifs) et
`.agents/rules/` : l'architecture y est découpée en règles de 24 000 octets au
plus, `trigger: model_decision` (59 Ko en `always_on` dépasseraient la limite
par fichier et le budget de 20 000 tokens des règles actives). Commandes en
skills `.agents/skills/` — les workflows sont retirés le 1er novembre 2026.
Sous-agents sans `tools` : la doc ne publie pas les noms d'outils et signale
qu'un nom erroné peut bloquer l'agent ; les outils autorisés sont écrits dans
le corps, comme consigne.
HOOKS : aucun câblé. `.agents/hooks.json` et PreToolUse existent, mais la doc
ne publie ni les arguments de `toolCall.args` ni l'effet d'un code de sortie
(le refus passe par un JSON `decision` sur stdout) : un hook qui ne sait pas
lire le chemin visé serait un enforcer muet. Tout reste au CI.
