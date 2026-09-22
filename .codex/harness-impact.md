# Rapport d'impact — codex

- Niveau de protection : **B**
- Statut : **planned**  ⚠️ *compilable, non validé par un run de conformance*

## Mécanismes

| Mécanisme | Support | Conséquence |
|---|---|---|
| `at_include` | unsupported | **absent** — repli documenté |
| `deterministic_python` | native | aucune |
| `mcp` | native | aucune |
| `runtime_hooks` | ci_fallback | **reporté au CI** — appliqué plus tard, pas au moment de l'action |
| `skills_autotrigger` | emulated | dégradation contrôlée via wrapper |
| `slash_commands` | emulated | dégradation contrôlée via wrapper |
| `structured_output` | native | aucune |
| `subagent_spawn` | emulated | dégradation contrôlée via wrapper |

## Invariants déplacés vers le CI

`runtime_hooks: ci_fallback` — les invariants suivants ne sont plus
appliqués au moment de l'action mais **en différé (CI)** :

- `activated-stack-is-loadable` — **appliqué en différé (CI)**
- `tool-gate-before-agent-wiring` — **appliqué en différé (CI)**
- `cap-ac-must-be-evaluable` — partiellement différé (un enforcer déterministe subsiste)
- `no-unbounded-loop` — partiellement différé (un enforcer déterministe subsiste)
- `tool-side-effect-declared` — partiellement différé (un enforcer déterministe subsiste)
- `db-safety-envelope-present` — partiellement différé (un enforcer déterministe subsiste)
- `retrieval-gate-before-agent` — partiellement différé (un enforcer déterministe subsiste)
- `prompts-are-files` — partiellement différé (un enforcer déterministe subsiste)
- `llm-judge-calibrated` — partiellement différé (un enforcer déterministe subsiste)
- `trace-emitted-per-run` — partiellement différé (un enforcer déterministe subsiste)

> **À dire clairement** : entre deux exécutions du wrapper, rien n'empêche
> une écriture hors scope. Le CI la rattrape — après que le travail a été
> fait sur une base fausse.

## Note de la matrice

PERD : l'application des gates AU MOMENT de l'action. Les invariants
tool-gate-before-agent-wiring et retrieval-gate-before-agent deviennent des
contrôles pre-exec du wrapper + un gate CI. Conséquence assumée et à dire
clairement : entre deux exécutions du wrapper, rien n'empêche une écriture
hors scope. Le CI la rattrape, plus tard.
GARDE : tout le noyau déterministe, donc toute la validation réelle.
