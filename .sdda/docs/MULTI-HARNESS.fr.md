# Compilation multi-harnais

SDD_Agents s'écrit **une fois** dans `.sdda/`, et se compile vers les façades
que chaque harnais sait lire. Mécanisme repris de SDD_Pro, avec une contrainte
supplémentaire propre à l'agentic.

SSoT machine : [`.sdda/capability-matrix.yml`](../capability-matrix.yml).

## 0. Statut, à lire avant le reste

| Harnais | Statut | Gates bloquantes au runtime |
|---|---|---|
| **Claude Code** | **supporté** — harnais de référence, niveau A | oui : hooks `PreToolUse` / `SubagentStop` de `.claude/settings.json` |
| **Codex CLI** | **expérimental** — `status: planned`, niveau B | **non** : reportées au CI et aux scripts déterministes |
| **Gemini CLI** | **expérimental** — `status: planned`, niveau B | **non** : idem |

« Expérimental » veut dire exactement ceci : la façade se **compile** et reste à
jour (`harness-build --check` en CI), mais aucun run de conformance ne l'a
jamais exécutée de bout en bout, et **rien n'empêche au moment de l'action**
une écriture hors ownership, un agent câblé avant sa TOOL GATE ou un prompt
inline. Le wrapper de spawn que décrit ce document (§2, « wrapper
`codex exec` / `gemini -p` » ; `spawn_agent` dans la matrice) est **planifié,
pas encore écrit** : sous Codex ou Gemini, c'est à l'humain de lancer chaque
sous-agent et de passer `python .sdda/sdda.py framework-smoke` avant de croire
un résultat.

Codex CLI lit `AGENTS.md` et Gemini CLI `GEMINI.md` **à la racine du dépôt**,
pas dans `.codex/` ou `.gemini/`. `harness_build` écrit donc à la racine un
`AGENTS.md` et un `GEMINI.md` générés, qui renvoient à la façade et répètent ce
statut (`tests/test_harness_root_pointers.py`).

La matrice déclare aussi `antigravity` (`planned`, niveau B). Il partage
l'adaptateur et le répertoire `.gemini/` de Gemini CLI ; le build par défaut ne
construit qu'un harnais par répertoire, donc il ne se compile que sur demande
explicite (`--harness antigravity`).

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
  ┌─────────────┬─────────────┬───────────────┐
  │  .claude/   │   .codex/   │   .gemini/    │
  │  CLAUDE.md  │  AGENTS.md  │  GEMINI.md    │
  │  agents/    │  agents/    │  agents-inline│
  │  commands/  │  prompts/   │  commands/    │
  │  settings   │             │  *.toml       │
  └─────────────┴─────────────┴───────────────┘
   + harness-impact.md dans chaque façade
   + AGENTS.md / GEMINI.md racine (pointeurs)
```

**Les façades sont générées, jamais éditées.** Une modification directe dans
`.claude/` est écrasée au build suivant, et le contrôle de parité le détecte
(§6).

---

## 2. Ce qui se compile, et comment

| Source | Claude Code | Codex | Gemini CLI |
|---|---|---|---|
| `agents/{a}.md` | `.claude/agents/{a}.md` — sous-agent natif, `model:` résolu depuis le tier | `.codex/agents/{a}.md` — en-tête tier + corps, à inliner dans le prompt du wrapper `codex exec` | `.gemini/agents-inline/{a}.md` — idem, pour `gemini -p` |
| `commands/{c}.md` | `.claude/commands/{c}.md` | `.codex/prompts/{c}.md` | `.gemini/commands/{c}.toml` |
| `rules/*.md` | `@`-référencées, chargement paresseux | **inlinées** (pas de `@`) | inlinées |
| fichier mémoire | `.claude/CLAUDE.md` (lu nativement) | `.codex/AGENTS.md`, pointé par `AGENTS.md` racine | `.gemini/GEMINI.md`, pointé par `GEMINI.md` racine |
| hooks | `.claude/settings.json` — **bloquants au runtime**, plus des `deny` natifs de lecture des `.env` | absents → CI (wrapper planifié) | absents → CI (wrapper planifié) |
| `python/` | tel quel | tel quel | tel quel |

Le fichier mémoire de chaque harnais **est** l'architecture : il est compilé
depuis `.sdda/ARCHITECTURE.fr.md`, parce que les agents travaillent en français
(`.sdda/ARCHITECTURE.md` en est la référence anglaise, pour les lecteurs
humains).

Le corps métier est **identique** partout : seules l'enveloppe et la politique
de chargement changent. Sur un harnais sans lazy-load (`at_include` ≠ `native`),
chaque référence `@.sdda/{fichier}` est réécrite en « `` `.sdda/{fichier}` ``
(Read ce fichier avant de poursuivre) ». Le repli est verbeux à dessein : une référence
muette produirait un agent qui croit avoir lu une règle qu'il n'a jamais vue.
Les marqueurs de compteurs de `sync_counters` sont retirés à la compilation : un agent ne
paie aucun token pour l'entretien de la doc.

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

Le hook shell couvre **PowerShell** avec un dialecte dédié, pas seulement Git
Bash. Deux réglages décident de ce qui arrive quand un hook ne juge pas :

- **`SDDA_PYTHON`** choisit l'interpréteur (`python` en repli). Un hook qui ne
  démarre pas rend un code ∉ {0, 2}, que le harnais traite comme une
  **autorisation** : l'échec de lancement est donc dit sur stderr ;
- **`SDDA_HOOKS_STRICT=1`** (la CI) : un hook qui plante ou ne démarre pas
  **refuse** (`[HOOK_FAILED]`) au lieu de laisser passer.

`python .sdda/sdda.py hooks-selfcheck` exécute chaque commande câblée telle que
le harnais la lancera, avec un payload inoffensif (doit rendre 0) et un payload
à refuser (doit rendre 2). `framework-smoke` vérifie qu'un hook est câblé ;
seul `hooks-selfcheck` prouve qu'il s'exécute.

Sur un harnais sans hooks, **ils ne disparaissent pas — ils se déplacent** vers
un contrôle pre/post-exec du wrapper et une gate CI. Tant que le wrapper n'est
pas écrit, il ne reste que la gate CI et les enforcers déterministes des
invariants mixtes.

**La conséquence doit être dite, pas maquillée** : entre deux exécutions du
wrapper, rien n'empêche une écriture hors scope. Le CI la rattrape — plus tard,
après que le travail a été fait sur une base fausse.

C'est pourquoi le rapport d'impact est **obligatoire à chaque build** :

> `harness_build.py` refuse de produire une façade sans émettre son rapport
> (`{façade}/harness-impact.md`). Tout invariant dont **tous** les enforcers
> sont des hooks, sur un harnais où `runtime_hooks != native`, y apparaît sous
> la mention **« appliqué en différé (CI) »** ; un invariant qui garde un
> enforcer déterministe y apparaît « partiellement différé ». Le prétendre
> appliqué au runtime serait exactement le doc-theater que `INVARIANTS.yml`
> existe pour empêcher.

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
   n'existe plus dans la source (`--prune` les supprime).
4. **Rapport d'impact présent et à jour** pour chaque harnais construit : il
   fait partie du plan de build, donc de la comparaison. Les pointeurs racine
   `AGENTS.md` / `GEMINI.md` aussi.

Tout écart échoue avec `[HARNESS_PARITY_DRIFT]` et se corrige par
`python .sdda/sdda.py harness-build --prune`.

---

## 7. Ajouter un harnais

1. Déclarer ses mécanismes dans `capability-matrix.yml` (`status: planned`).
2. Écrire son adaptateur dans `harness_build.py` : une sous-classe d'`Adapter`
   (`out_dir`, `render_agent`, `render_command`, et au besoin `emit_agents`,
   `emit_settings`), enregistrée dans `ADAPTERS`. Un harnais déclaré sans
   adaptateur est annoncé `[ skip ]`, jamais construit en silence.
3. Construire, lire le rapport d'impact, **corriger ce qui se déplace en CI**.
4. Lancer un **run de conformance** : la même MISSION de bout en bout sur ce
   harnais et sur Claude Code, et comparer les verdicts.
5. Alors seulement, passer `status: validated` et annoncer un niveau de
   protection.

> `status: planned` signifie « compilable ». Jamais « validé ». La distinction
> est la même que pour les combos de stack, et pour la même raison : annoncer
> une compatibilité non mesurée est la forme de faux vert la plus facile à
> produire et la plus coûteuse à détromper.
