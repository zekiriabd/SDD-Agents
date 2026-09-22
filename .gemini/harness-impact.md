# Rapport d'impact — gemini-cli

- Niveau de protection : **B**
- Statut : **planned**  ⚠️ *compilable, non validé par un run de conformance*

## Mécanismes

| Mécanisme | Support | Conséquence |
|---|---|---|
| `at_include` | emulated | dégradation contrôlée via wrapper |
| `deterministic_python` | native | aucune |
| `mcp` | native | aucune |
| `runtime_hooks` | ci_fallback | **reporté au CI** — appliqué plus tard, pas au moment de l'action |
| `skills_autotrigger` | emulated | dégradation contrôlée via wrapper |
| `slash_commands` | native | aucune |
| `structured_output` | emulated | dégradation contrôlée via wrapper |
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

## Sorties structurées

`structured_output: emulated` — les rapports
d'eval, les verdicts de gate et l'IR sont du JSON. Un JSON approximatif impose
un parsing défensif et crée une classe de faux verts : un rapport mal parsé
dont les champs manquants passent pour « aucun finding ». **À requalifier par
mesure au premier run de conformance, jamais par optimisme.**

## Note de la matrice

Idem codex pour les hooks. `structured_output: emulated` est le point à
surveiller : les rapports d'eval et les verdicts de gate sont du JSON, et
un parsing défensif est un coût réel. À requalifier après mesure — pas
avant.
