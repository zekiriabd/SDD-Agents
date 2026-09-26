# Compilation multi-harnais

SDD_Agents s'écrit **une fois** dans `.sdda/`, et se compile vers les façades
que chaque harnais sait lire. Mécanisme repris de SDD_Pro, avec une contrainte
supplémentaire propre à l'agentic.

SSoT machine : [`.sdda/capability-matrix.yml`](../capability-matrix.yml).
Chaque emplacement, format et limite ci-dessous vient de la documentation
**officielle** du harnais (URL dans la matrice et à côté des constantes de
`harness_build.py`). Ce que cette documentation ne dit pas est marqué **non
vérifié** et n'est pas codé.

## 0. Statut, à lire avant le reste

| Harnais | Statut | Gates bloquantes au runtime |
|---|---|---|
| **Claude Code** | **supporté** — harnais de référence, niveau A | oui : hooks `PreToolUse` / `SubagentStop` de `.claude/settings.json` |
| **Codex CLI** | **expérimental** — `status: planned`, niveau B | **en partie** (`runtime_hooks: partial`) : `apply_patch` refusé dans les zones protégées ; le reste au CI |
| **Gemini CLI** | **expérimental** — `status: planned`, niveau B | **en partie** (`runtime_hooks: partial`) : `write_file`/`replace` dans les zones protégées, et les dix gates de spawn (non vérifié par un run) ; le reste au CI |
| **Antigravity** | **expérimental** — `status: planned`, niveau B | **non** (`ci_fallback`) : hooks existants mais payload non documenté |

« Expérimental » veut dire exactement ceci : la façade se **compile**, reste à
jour (`harness-build --check`) et se relit par le parseur de son harnais
(`framework-smoke`, contrôle `facades.harnesses`), mais aucun run de
conformance ne l'a exécutée de bout en bout. Ce qu'aucun hook ne juge sur un
harnais — une écriture hors ownership par un agent précis, un agent câblé avant
sa TOOL GATE sous Codex, un prompt inline — n'est empêché par **rien** au
moment de l'action : il faut passer `python .sdda/sdda.py framework-smoke`
avant de croire un résultat.

**La limite commune, à dire.** Ni Codex ni Gemini CLI ne transmettent au hook
l'**identité de l'agent** qui fait l'appel (champ équivalent à `agent_type` :
non documenté). Tout ce qui se juge « par agent » — la matrice d'ownership, les
`forbidden_reads`, les lectures de `.env`, le shell — n'est donc pas
transposable au runtime ; seul ce qui se juge quel que soit l'auteur l'est :
les **zones protégées** (rapports de gate, baselines, audit). Ce n'est pas un
réglage manquant, c'est une information que le harnais ne donne pas.

Fichiers racine : Codex CLI lit `AGENTS.md`, Gemini CLI `GEMINI.md`,
Antigravity **les deux**. `harness_build` y écrit un même renvoi neutre, qui
nomme la façade de chaque harnais et répète ce statut
(`tests/test_harness_root_pointers.py`) ; `GEMINI.md` **importe**
`.gemini/GEMINI.md` (`@./.gemini/GEMINI.md`), que Gemini CLI charge alors
nativement.

---

## 1. Le principe

```
.sdda/                          source neutre — la seule chose qu'on écrit
  ├─ agents/*.md
  ├─ commands/*.md
  ├─ rules/*.md
  └─ python/                    indépendant du harnais par construction
        │
        │   harness_build.py
        ▼
  ┌────────────┬──────────────┬──────────────┬───────────────────┐
  │  .claude/  │   .codex/    │   .gemini/   │   .agents/        │
  │  CLAUDE.md │  AGENTS.md   │  GEMINI.md   │  rules/*.md       │
  │  agents/   │  agents/     │  agents/     │  agents/          │
  │   *.md     │   *.toml     │   *.md       │   *.md            │
  │  commands/ │  hooks.json  │  commands/   │  skills/ ◄─ Codex │
  │  settings  │              │   *.toml     │   aussi           │
  │            │              │  settings    │                   │
  └────────────┴──────────────┴──────────────┴───────────────────┘
   + harness-impact.md dans chaque façade
   + AGENTS.md / GEMINI.md racine (pointeurs)
```

**Les façades sont générées, jamais éditées.** Une modification directe dans
`.claude/` est écrasée au build suivant, et le contrôle de parité le détecte
(§6).

---

## 2. Ce qui se compile, et comment

| Source | Claude Code | Codex CLI | Gemini CLI | Antigravity |
|---|---|---|---|---|
| `agents/{a}.md` | `.claude/agents/{a}.md` — `model:` résolu depuis le tier | `.codex/agents/{a}.toml` — `name`, `description`, `developer_instructions`, `model`, `sandbox_mode` | `.gemini/agents/{a}.md` — `tools` traduits (`read_file`, `write_file`, `replace`…), `model` | `.agents/agents/{a}.md` — `model` (`pro`/`flash`), `subagent: true`, **sans** `tools` |
| `commands/{c}.md` | `.claude/commands/{c}.md` | skill `.agents/skills/{c}/SKILL.md` (`$sdda-…`) | `.gemini/commands/{c}.toml` | skill `.agents/skills/{c}/SKILL.md` (`/sdda-…`) — la même que Codex |
| `rules/*.md` | `@`-référencées | inlinées (« Read ce fichier ») | inlinées | inlinées |
| fichier mémoire | `.claude/CLAUDE.md` | `.codex/AGENTS.md`, renvoyé par `AGENTS.md` | `.gemini/GEMINI.md`, **importé** par `GEMINI.md` | `.agents/rules/sdda-architecture-*.md`, ≤ 24 000 octets chacun, `trigger: model_decision` |
| hooks | `.claude/settings.json` — tous, bloquants, plus des `deny` natifs de lecture des `.env` | `.codex/hooks.json` — `preflight_ownership` sur `apply_patch` | `.gemini/settings.json` — `BeforeTool` : ownership sur `write_file`/`replace`, gates de spawn sur l'outil de chaque agent | aucun |
| modèles (`tier_models`) | `opus` / `sonnet` / `haiku` | `gpt-6-astra` / `gpt-6-sol` / `gpt-6-luna` | `gemini-3-pro-preview` (deep, balanced) / `gemini-3-flash-preview` | `pro` (deep, balanced) / `flash` |

Pourquoi ces choix, et ce qu'ils corrigent :

- **Codex** ne lit les custom prompts que sous `~/.codex/prompts` et les
  déprécie : l'ancienne façade `.codex/prompts/` n'était lue par personne. Les
  skills `.agents/skills/` sont cherchées du répertoire courant jusqu'à la
  racine du dépôt. `AGENTS.md` est tronqué en silence au-delà de 32 KiB
  (`project_doc_max_bytes`) : le pointeur reste petit, la façade (59 Ko) est lue
  sur consigne.
- **Gemini CLI** exécute `@{…}` (injection de fichier) et `!{…}` (shell) dans le
  prompt d'une commande : `recall@{k}` lisait le fichier `k` à chaque
  `/sdda-build`. La compilation insère une espace (`@ {k}`) — aucun échappement
  n'est documenté. Les sous-agents sont natifs (`.gemini/agents/`), exposés à
  l'agent principal comme un outil du même nom.
- **Antigravity** ne lisait rien de `.gemini/`. Les workflows sont retirés le
  1er novembre 2026 : les commandes sont des skills, qu'Antigravity partage
  avec Codex (même fichier, octet pour octet). La liste des noms d'outils n'est
  pas publiée et un nom erroné peut bloquer un sous-agent : `tools` n'est pas
  émis, les outils autorisés sont écrits dans le corps comme consigne.
- **Claude Code** : `$0`, `$1`… dans une commande sont des placeholders
  d'arguments ; les montants d'exemple (`$0.09`) sont échappés (`\$0.09`), sans
  quoi le premier argument les remplaçait et les suivants (`--resume`)
  n'arrivaient jamais au modèle. Une commande n'accepte pas de `name:`.

Le fichier mémoire de chaque harnais **est** l'architecture : il est compilé
depuis `.sdda/ARCHITECTURE.fr.md`, parce que les agents travaillent en français
(`.sdda/ARCHITECTURE.md` en est la référence anglaise, pour les lecteurs
humains).

Le corps métier est **identique** partout : seules l'enveloppe et la politique
de chargement changent. Sur un harnais sans lazy-load (`at_include` ≠ `native`),
chaque référence `@.sdda/{fichier}` est réécrite en « `` `.sdda/{fichier}` ``
(Read ce fichier avant de poursuivre) ». Le repli est verbeux à dessein : une référence
muette produirait un agent qui croit avoir lu une règle qu'il n'a jamais vue.
Les marqueurs de compteurs de `sync_counters` sont retirés à la compilation, pour
tous les harnais : un agent ne paie aucun token pour l'entretien de la doc.

---

## 3. La ligne rouge : les hooks

C'est là que la portabilité cesse d'être gratuite.

Sous Claude Code, ces invariants sont appliqués **au moment de l'action**, par
un hook qui refuse l'appel d'outil (code de sortie 2). Chaque hook de
`sdda_hooks/` déclare son `WIRING` ; `harness_build` les câble tous, sans table
en dur :

| Événement / outils | Hooks | Invariants |
|---|---|---|
| `PreToolUse` `Task\|Agent` | `preflight_tool_gate` | `tool-gate-before-agent-wiring`, `tool-side-effect-declared` |
| | `preflight_retrieval_gate` | `retrieval-gate-before-agent` |
| | `preflight_agent_bounds` | `no-unbounded-loop` |
| | `preflight_db_envelope` | `db-safety-envelope-present` |
| | `preflight_cap_gate` | `cap-ac-must-be-evaluable` |
| | `preflight_judge_calibration` | `llm-judge-calibrated` |
| | `preflight_stack_combo` | `activated-stack-is-loadable` |
| | `preflight_agent_budget`, `preflight_cost_cap`, `preflight_instance_bind` | budget de contexte, plafond de coût, liaison d'instance `dev-agent` |
| `PreToolUse` `Write\|Edit\|MultiEdit\|NotebookEdit` | `preflight_ownership` | `ownership-matrix-enforced` |
| `PreToolUse` `Bash\|PowerShell` | `preflight_bash_ownership` | `ownership-matrix-enforced` (écritures et lectures de secrets par le shell) |
| `PreToolUse` `Read\|Glob\|Grep` | `preflight_forbidden_reads` | `ownership-matrix-enforced` (`forbidden_reads`, `[SECRET_READ_FORBIDDEN]`) |
| `SubagentStop` | `postflight_no_inline_prompt` | `prompts-are-files` |
| | `postflight_trace_present` | `trace-emitted-per-run` |

**Sur les autres harnais, ce qui se transpose et ce qui ne se transpose pas.**
Les payloads de Gemini CLI (`write_file`, `grep_search`, `run_shell_command`,
outil d'un sous-agent) et de Codex (`apply_patch`, dont la grammaire
`*** Add/Update/Delete File:` donne les fichiers touchés) sont traduits en forme
Claude Code par `_hook.foreign_payloads`, activé par `SDDA_HARNESS` que pose la
commande générée. Puis :

| Hook | Codex CLI | Gemini CLI | Antigravity |
|---|---|---|---|
| `preflight_ownership` | **câblé** (`^apply_patch$`) — zones protégées seulement | **câblé** (`write_file\|replace`) — zones protégées seulement | non |
| les dix hooks de spawn | non — le champ de `spawn_agent` qui nomme l'agent lancé n'est pas documenté | **câblés** sur l'outil de chaque agent — déclenchement et paramètres non vérifiés | non |
| `preflight_bash_ownership`, `preflight_forbidden_reads` | non — identité de l'auteur absente | non — identité de l'auteur absente | non |
| `SubagentStop` | non — agent et effet du code 2 non documentés | non — aucun événement | non |
| raison commune | | | arguments de `toolCall.args` et codes de sortie non documentés ; refus par JSON `decision` sur stdout |

Autres points **non vérifiés** : le shell dans lequel Codex et Gemini CLI
lancent une commande de hook sous Windows (les commandes générées sont POSIX) ;
les caractères permis dans le `name` d'un agent Codex (les exemples officiels
sont en snake_case). Les hooks de projet ne se chargent sous Codex que si la
couche `.codex/` est approuvée ; Gemini CLI empreinte les siens et demande une
confirmation à chaque changement de commande.

Le hook shell couvre **PowerShell** avec un dialecte dédié, pas seulement Git
Bash. Deux réglages décident de ce qui arrive quand un hook ne juge pas :

- **`SDDA_PYTHON`** choisit l'interpréteur (`python` en repli). Un hook qui ne
  démarre pas rend un code ∉ {0, 2}, que le harnais traite comme une
  **autorisation** : l'échec de lancement est donc dit sur stderr ;
- **`SDDA_HOOKS_STRICT=1`** (la CI) : un hook qui plante ou ne démarre pas
  **refuse** (`[HOOK_FAILED]`) au lieu de laisser passer.

`python .sdda/sdda.py hooks-selfcheck` exécute chaque commande câblée — de
`.claude/settings.json`, `.gemini/settings.json` et `.codex/hooks.json` — telle
que le harnais la lancera, avec un payload inoffensif (doit rendre 0) et, pour
les hooks d'ownership, un payload à refuser (doit rendre 2) dans le dialecte du
harnais. `framework-smoke` vérifie qu'un hook est câblé ; seul
`hooks-selfcheck` prouve qu'il s'exécute.

Là où un hook n'est pas câblé, **il ne disparaît pas — il se déplace** vers la
gate CI et les enforcers déterministes des invariants mixtes.

**La conséquence doit être dite, pas maquillée** : ce qu'aucun hook ne juge
n'est empêché par rien au moment de l'action. Le CI le rattrape — plus tard,
après que le travail a été fait sur une base fausse.

C'est pourquoi le rapport d'impact est **obligatoire à chaque build** :

> `harness_build.py` refuse de produire une façade sans émettre son rapport
> (`{façade}/harness-impact.md`), qui liste le sort de chaque hook sur le
> harnais (câblé, dégradé, absent — et pourquoi). Tout invariant dont **tous**
> les enforcers sont des hooks non câblés y apparaît sous la mention
> **« appliqué en différé (CI) »** ; un invariant dont un hook est câblé
> dégradé, « en partie au runtime » ; un invariant qui garde un enforcer
> déterministe, « partiellement différé ». Le prétendre appliqué au runtime
> serait exactement le doc-theater que `INVARIANTS.yml` existe pour empêcher.

---

## 4. La contrainte propre à l'agentic : `structured_output`

Sans équivalent dans SDD_Pro, et elle compte.

Les rapports d'eval, les verdicts de gate et l'IR compilé sont du **JSON**. Un
harnais qui rend du JSON approximatif impose :

1. un parsing défensif partout ;
2. une passe de réparation — donc un appel de modèle en plus ;
3. une classe de faux verts : un rapport mal parsé dont les champs manquants
   sont interprétés comme « pas de finding ».

Le troisième point est le vrai danger. Un `structured_output: emulated` dans la
matrice n'est pas une note de bas de page : c'est un facteur de risque sur la
fiabilité des verdicts, et il doit être requalifié par **mesure** au premier run
de conformance — jamais par optimisme.

---

## 5. Ce qui ne bouge jamais

`.sdda/python/` — validateurs, `ir_compiler`, `estimate_budget`, `eval_runner`,
graders, scans de sécurité, `compute_status`.

C'est **80 % de la valeur réelle du framework**, et c'est du Python qui tourne
partout à l'identique. Un harnais dégradé perd du confort d'orchestration et
l'immédiateté des gates ; il ne perd pas la validation.

C'est ce qui rend la promesse multi-harnais tenable plutôt que marketing : le
cœur ne dépend d'aucun harnais parce qu'il ne dépend d'aucun LLM.

---

## 6. Test de parité

`python .sdda/sdda.py harness-build --check`, exécuté en CI, recalcule chaque
façade sans rien écrire et la compare octet à octet au disque :

1. **Couverture** — chaque agent et chaque commande de `.sdda/` a sa
   contrepartie dans chaque façade construite ; un fichier attendu absent est
   une dérive.
2. **Identité du corps** — le contenu généré est identique à ce que la source
   produit. Une divergence signifie qu'une façade a été éditée à la main, ou que
   la source a bougé sans rebuild.
3. **Absence d'orphelins** — aucune façade ne porte un agent ou une commande qui
   n'existe plus dans la source (`--prune` les supprime). Dans un répertoire que
   l'humain peut aussi garnir (`.agents/skills/`, `.agents/rules/`), seul un
   fichier qui porte la marque de génération est jugé : une skill écrite à la
   main n'est jamais supprimée.
4. **Rapport d'impact présent et à jour** pour chaque harnais construit : il
   fait partie du plan de build, donc de la comparaison. Les pointeurs racine
   `AGENTS.md` / `GEMINI.md` aussi.

`framework-smoke` ajoute ce que la parité ne prouve pas : que chaque façade se
**relit** par son harnais (contrôle `facades.harnesses`) — TOML de Codex et de
Gemini relu par `tomllib`, frontmatters en YAML strict, noms et outils Gemini,
`trigger` et taille des règles Antigravity, taille des pointeurs racine, aucun
`@{…}`/`!{…}` dans une commande Gemini, aucun import `@` parasite dans
`.gemini/GEMINI.md`.

Tout écart échoue avec `[HARNESS_PARITY_DRIFT]` et se corrige par
`python .sdda/sdda.py harness-build --prune`.

---

## 7. Ajouter un harnais

1. Déclarer ses mécanismes dans `capability-matrix.yml` (`status: planned`),
   chaque valeur sourcée dans la documentation officielle du harnais.
2. Écrire son adaptateur dans `harness_build.py` : une sous-classe d'`Adapter`
   (`out_dir`, `root_pointers`, `managed`, `render_agent_file`,
   `render_command`, et au besoin `emit_commands`, `emit_memory_file`,
   `emit_settings`), enregistrée dans `ADAPTERS`. Un harnais déclaré sans
   adaptateur est annoncé `[ skip ]`, jamais construit en silence.
3. Pour ses hooks : traduire son payload dans `_hook.foreign_payloads`, ne câbler
   que ce que ce payload permet de juger, et donner une raison à chaque hook
   absent (`HookPort`) — elle finit dans le rapport d'impact.
4. Construire, lire le rapport d'impact, **corriger ce qui se déplace en CI**.
5. Lancer un **run de conformance** : la même MISSION de bout en bout sur ce
   harnais et sur Claude Code, et comparer les verdicts.
6. Alors seulement, passer `status: validated` et annoncer un niveau de
   protection.

> `status: planned` signifie « compilable ». Jamais « validé ». La distinction
> est la même que pour les combos de stack, et pour la même raison : annoncer
> une compatibilité non mesurée est la forme de faux vert la plus facile à
> produire et la plus coûteuse à détromper.
