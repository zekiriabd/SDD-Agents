<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis .sdda/capability-matrix.yml.
     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,
     et le test de parité la signale. Éditer la source. -->

# SDD_Agents — harnais expérimentaux (Codex CLI, Gemini CLI, Antigravity)

Les instructions du framework sont compilées par harnais ; **les lire avant
toute action** :

- **Codex CLI** : `.codex/AGENTS.md` (le lire en entier) ; commandes en skills
  `.agents/skills/` (`$sdda-…`) ; agents `.codex/agents/*.toml` ;
  hooks `.codex/hooks.json`.
- **Gemini CLI** : `.gemini/GEMINI.md` (importé ci-dessous) ; commandes
  `.gemini/commands/*.toml` ; agents `.gemini/agents/` ; hooks `.gemini/settings.json`.
- **Antigravity** : règles `.agents/rules/` (l'architecture, en parties) ;
  skills `.agents/skills/` (`/sdda-…`) ; agents `.agents/agents/`.

**Statut : expérimental.** Ces façades se compilent et sont vérifiées, mais
aucun run de conformance ne les a validées. Codex CLI et Antigravity n'ont
**aucune gate bloquante au runtime** ; sous Codex et Gemini CLI, seuls les
hooks que leur payload permet de juger sont câblés (zones protégées, et les
gates de spawn sous Gemini CLI). Le reste est reporté au CI et aux scripts
déterministes — une écriture hors ownership n'est rattrapée qu'après coup.
Détail : `{.codex,.gemini,.agents}/harness-impact.md`, `.sdda/docs/MULTI-HARNESS.md`.

Avant de considérer un travail terminé : `python .sdda/sdda.py framework-smoke`.
