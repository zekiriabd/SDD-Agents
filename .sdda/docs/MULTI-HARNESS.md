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
`codex exec` / `gemini -p` ») est **planifié, pas encore écrit** : sous Codex ou
Gemini, c'est à l'humain de lancer chaque sous-agent et de passer
`python .sdda/sdda.py framework-smoke` avant de croire un résultat.

Codex CLI lit `AGENTS.md` et Gemini CLI `GEMINI.md` **à la racine du dépôt**,
pas dans `.codex/` ou `.gemini/`. `harness_build` écrit donc à la racine un
`AGENTS.md` et un `GEMINI.md` générés, qui renvoient à la façade et répètent ce
statut.

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
  ┌─────────────┬─────────────┬─────────────┐
  │  .claude/   │   .codex/   │  .gemini/   │
  │  CLAUDE.md  │  AGENTS.md  │  GEMINI.md  │
  │  agents/    │  prompts/   │  commands/  │
  │  commands/  │             │  *.toml     │
  │  settings   │  config.toml│  settings   │
  └─────────────┴─────────────┴─────────────┘
```

**Les façades sont générées, jamais éditées.** Une modification directe dans
`.claude/` est écrasée au build suivant, et un test de parité le détecte.

---

## 2. Ce qui se compile, et comment

| Source | Claude Code | Codex | Gemini CLI |
|---|---|---|---|
| `agents/{a}.md` | `.claude/agents/{a}.md` — sous-agent natif | inliné dans le prompt du wrapper `codex exec` | inliné dans le prompt `gemini -p` |
| `commands/{c}.md` | `.claude/commands/{c}.md` | `.codex/prompts/{c}.md` | `.gemini/commands/{c}.toml` |
| `rules/*.md` | `@`-référencées, chargement paresseux | **inlinées** (pas de `@`) | inlinées |
| fichier mémoire | `.claude/CLAUDE.md` (lu nativement) | `.codex/AGENTS.md`, pointé par `AGENTS.md` racine | `.gemini/GEMINI.md`, pointé par `GEMINI.md` racine |
| hooks | `.claude/settings.json` — **bloquants au runtime** | absents → CI (wrapper planifié) | absents → CI (wrapper planifié) |
| `python/` | tel quel | tel quel | tel quel |

Le corps métier est **identique** partout : seules l'enveloppe et la politique
de chargement changent. Les références `@.sdda/...` sont réécrites en consignes
`Read X avant STEP n` sur les harnais sans lazy-load.

---

## 3. La ligne rouge : les hooks

C'est là que la portabilité cesse d'être gratuite.

Sous Claude Code, ces invariants sont appliqués **au moment de l'action**, par
un hook qui refuse l'appel d'outil :

| Invariant | Hook |
|---|---|
| `tool-gate-before-agent-wiring` | `preflight_tool_gate` |
| `retrieval-gate-before-agent` | `preflight_retrieval_gate` |
| `prompts-are-files` | `postflight_no_inline_prompt` |
| `no-unbounded-loop` | `preflight_agent_bounds` |
| `db-safety-envelope-present` | `preflight_db_envelope` |
| ownership (datasets, prompts, baselines) | `audit_ownership` |

Sur un harnais sans hooks, **ils ne disparaissent pas — ils se déplacent** vers
un contrôle pre/post-exec du wrapper et une gate CI. Tant que le wrapper n'est
pas écrit, il ne reste que la gate CI.

**La conséquence doit être dite, pas maquillée** : entre deux exécutions du
wrapper, rien n'empêche une écriture hors scope. Le CI la rattrape — plus tard,
après que le travail a été fait sur une base fausse.

C'est pourquoi le rapport d'impact est **obligatoire à chaque build** :

> `harness_build.py` refuse de produire une façade sans émettre son rapport.
> Tout invariant dont **tous** les enforcers sont des hooks, sur un harnais où
> `runtime_hooks != native`, y apparaît sous la mention **« appliqué en différé
> (CI) »**. Le prétendre appliqué au runtime serait exactement le doc-theater
> que `INVARIANTS.yml` existe pour empêcher.

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

`tests/test_harness_parity.py`, exécuté en CI :

1. **Couverture** — chaque agent et chaque commande de `.sdda/` a sa
   contrepartie dans chaque façade déclarée.
2. **Identité du corps** — le contenu métier est identique après normalisation
   (CRLF, BOM, réécriture des `@`-refs). Une divergence signifie qu'une façade a
   été éditée à la main.
3. **Absence d'orphelins** — aucune façade ne porte un agent ou une commande qui
   n'existe plus dans la source.
4. **Rapport d'impact présent et à jour** pour chaque harnais construit.

---

## 7. Ajouter un harnais

1. Déclarer ses mécanismes dans `capability-matrix.yml` (`status: planned`).
2. Écrire son adaptateur dans `harness_build.py` : `emit_agents`,
   `emit_commands`, `emit_memory_file`, `emit_settings`.
3. Construire, lire le rapport d'impact, **corriger ce qui se déplace en CI**.
4. Lancer un **run de conformance** : le même MISSION de bout en bout sur ce
   harnais et sur Claude Code, et comparer les verdicts.
5. Alors seulement, passer `status: validated` et annoncer un niveau de
   protection.

> `status: planned` signifie « compilable ». Jamais « validé ». La distinction
> est la même que pour les combos de stack, et pour la même raison : annoncer
> une compatibilité non mesurée est la forme de faux vert la plus facile à
> produire et la plus coûteuse à détromper.
