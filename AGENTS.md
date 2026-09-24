<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis .sdda/capability-matrix.yml.
     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,
     et le test de parité la signale. Éditer la source. -->

# SDD_Agents — Codex CLI (expérimental)

Les instructions du framework sont dans `.codex/AGENTS.md` :
**le lire en entier avant toute action.** Commandes :
`.codex/prompts/*.md`. Agents : `.codex/agents/`.

**Statut : expérimental.** La façade Codex CLI est compilable, jamais
validée par un run de conformance, et n'a
**aucune gate bloquante au runtime** : les hooks d'ownership et de gates
n'existent que sous Claude Code. Ce qu'ils appliquent est reporté au CI
et aux scripts déterministes — une écriture hors scope n'est rattrapée
qu'après coup. Détail :
`.codex/harness-impact.md`, `.sdda/docs/MULTI-HARNESS.md`.

Avant de considérer un travail terminé : `python .sdda/sdda.py framework-smoke`.
