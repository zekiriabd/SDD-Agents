# SDD_Agents — Domain Model

Le vocabulaire clos du framework. Toute entité manipulée par un agent, un script,
un template ou une gate est l'une de celles-ci. Un concept absent de ce document
n'existe pas dans SDD_Agents.

---

## 1. Vue d'ensemble

```
MISSION  1 ─── n  CAPABILITY
   │                  │
   │                  ├── n  ACCEPTANCE CRITERION  (métrique + seuil + DATASET)
   │                  └── allouée à ──► AGENT  et/ou  TOOL  et/ou  RETRIEVER
   │
   ├── 1 ─── 1  ROSTER  (déclaré par l'architecte)
   │
   └── 1 ─── 1  TOPOLOGY
                  ├── n  AGENT ─── n  TOOL
                  │        ├── 1  PROMPT
                  │        ├── n  SKILL · n  RULE
                  │        ├── 1  MODEL BINDING (tier)
                  │        ├── 1  BOUNDS
                  │        └── n  MEMORY SCOPE
                  ├── n  RETRIEVER ─── 1  INDEX ─── 1  CORPUS
                  ├── n  DATA ACCESS
                  ├── 1  ORCHESTRATION PATTERN
                  └── n  GUARDRAIL
                            │
                  EVAL SUITE ─── n  EVALUATION ─── 1  GRADER
                            └── 1  BASELINE
                  RUN ─── n  TRACE SPAN
                  ADR ─── couvre ──► une décision refusée par défaut
```

---

## 2. Entités de spécification

### MISSION
*L'équivalent de la FEAT de SDD_Pro.* Ce que le système agentic doit accomplir,
en termes métier.

| Champ | Nature | Obligatoire |
|---|---|:---:|
| `id` | `{n}-{Name}` — stable, jamais renuméroté | oui |
| `context` / `objective` | prose bornée | oui |
| `quantified_goal` | métrique + cible + échéance | oui (G0) |
| `budget` | `cost_per_run`, `latency_p95`, `token_ceiling` | oui (G0) |
| `actors` | humains et systèmes en interaction | oui |
| `ground_truth` | d'où vient la vérité contre laquelle on évalue | oui (G0) |
| `trust_boundaries` | quelles sources sont non maîtrisées | oui |
| `business_rules` | `BR-{i}` | oui |
| `acceptance_criteria` | `AC-{i}` niveau système | oui |
| `out_of_scope` | explicite | oui |
| `failure_policy` | que fait le système quand il ne sait pas | oui |

> `failure_policy` n'a pas d'équivalent SDD_Pro et est obligatoire ici : un
> système agentic *aura* des cas hors compétence. Ne pas décider quoi en faire,
> c'est décider qu'il inventera.

### CAPABILITY (CAP)
*L'équivalent de la User Story.* **Une** compétence discrète et évaluable.

| Champ | Nature |
|---|---|
| `id` | `{n}-{m}-{Name}` — stable |
| `parent_mission_hash` | `sha256:…` — détecte une MISSION modifiée sous les pieds |
| `statement` | « Le système doit pouvoir <action observable> » |
| `acceptance_criteria` | chacun : **métrique + seuil + dataset + k runs** |
| `covers` | `BR-i`, `AC-i` de la MISSION — traçabilité montante |
| `inputs` / `outputs` | schémas, pas de la prose |
| `allocated_to` | agent(s), outil(s), retriever(s) — rempli en PHASE 2 par `architect-topology` |
| `criticality` | `normal` \| `critical` — pilote k runs et les seuils |
| `failure_behavior` | ce que fait le système quand cette CAP échoue |

**Une CAP n'est pas un agent.** L'allocation CAP -> agent est une décision
d'architecture prise en PHASE 2, révisable sans toucher les CAPs. Confondre les
deux est l'erreur qui fige une topologie avant de l'avoir pensée.

La séparation se lit jusque dans les hashes. G1 épingle la CAP **sans** sa
section `## Allocated To` (clé `capspec:`) : la PHASE 2 écrit l'allocation, et
elle ne doit pas périmer la PHASE 1. G2 et l'IR épinglent la CAP **entière**
(clé `cap:`) : une réallocation, elle, périme bien la topologie.

---

## 3. Entités d'architecture

### ROSTER
La déclaration de l'architecte, **humaine** (PHILOSOPHY P7) : combien d'agents,
lesquels, leurs rôles, leurs outils, leurs tiers, leurs skills et leurs rules.
Écrit dans `workspace/feats/{n}-roster.md` (le premier bloc `yaml` fait foi),
validé par `python .sdda/sdda.py roster validate`, jamais écrit par un agent.
`architect-topology` le recopie tel quel dans la topologie et le matérialise ; il
ne le modifie pas (`[ARCH_ROSTER_MUTATED]`).

### TOPOLOGY
La matérialisation de l'architecture déclarée : l'allocation des CAPs au
roster, le graphe, les bornes, le **budget estimé** — et, en consultatif, la
**topologie plus simple envisagée** et ce qu'elle aurait coûté en moins.

### AGENT (entité du produit généré — à ne pas confondre avec les Developer Agents)

| Champ | Nature |
|---|---|
| `id`, `role` | identité |
| `serves_caps` | les CAPs dont il porte la responsabilité (au moins une) |
| `prompt_ref` | `workspace/src/{App}/prompts/{agent}.system.md` + son hash |
| `model_tier` | `fast` \| `balanced` \| `deep` — jamais un nom de modèle (P11) |
| `tools` | références de TOOL — ce qu'il a le **droit d'appeler**, le minimum exigé par ses CAPs (P8) |
| `skills` | ce qu'il **sait faire** — une compétence nommée, sans schéma ni effet de bord, portée par le prompt |
| `rules` | ce qu'il **doit respecter** — une contrainte nommée, portée par le prompt, jumelle de `skills` |
| `retrievers` | références de RETRIEVER |
| `memory_scopes` | ce qu'il lit et écrit en mémoire |
| `input_schema` / `output_schema` | contrats structurés |
| `bounds` | `max_iterations`, `max_tool_calls`, `max_delegation_depth`, `timeout_s`, `budget_usd` (P12) |
| `on_bound_exceeded` | `fail-explicit` \| `degrade` \| `escalate-human` |
| `handoff_contract` | à qui il passe la main, avec quel état, à quelle condition |
| `refusal_policy` | ce qu'il refuse de faire, explicitement |
| `trust_posture` | quelles entrées il traite comme hostiles |

> **Une skill ou une rule n'est pas un outil.** Le moindre privilège ne se déduit
> jamais d'une skill : seul `tools` ouvre un droit. Ni l'une ni l'autre n'ayant de
> schéma, aucune gate ne peut les exécuter ; leur seule vérification est la
> symétrie contrat ↔ prompt (`lint_prompts.py` : `[SKILL_NOT_IMPLEMENTED]`,
> `[SKILL_UNDECLARED]`, `[RULE_NOT_IMPLEMENTED]`, `[RULE_UNDECLARED]`), et leur
> effet se mesure en G5, par les AC des CAPs servies.

### TOOL

| Champ | Nature |
|---|---|
| `name`, `description` | **la description est du prompt** : c'est sur elle que le modèle décide d'appeler ou non |
| `input_schema` / `output_schema` | JSON Schema |
| `side_effect_class` | `read-only` \| `write-scoped` \| `write-destructive` \| `external-side-effect` |
| `safety_strategy` | obligatoire si != `read-only` : dry-run, clé d'idempotence, confirmation, allowlist, plafond |
| `errors` | chaque erreur déclarée, avec son comportement attendu côté agent |
| `auth` | référence d'env, jamais la valeur |
| `rate_limit`, `timeout_s`, `retry_policy` | bornes |
| `trust` | `trusted` \| `untrusted` — la sortie est-elle du texte hostile (P8) |
| `contract_tests` | référence de la suite L2 |

> **La `description` d'un outil est un artefact de prompt engineering**, pas de la
> documentation. Une description vague est la cause première des « l'agent
> n'appelle pas le bon outil ». Elle est revue comme du prompt, hashée comme du
> prompt.

### RETRIEVER / INDEX / CORPUS
`CORPUS` (les sources) -> ingestion + chunking -> `INDEX` (le store) ->
`RETRIEVER` (la stratégie de requête). Les trois sont distincts parce qu'ils
échouent différemment et se mesurent séparément : un corpus incomplet, un index
mal découpé et une stratégie de requête inadaptée produisent le même symptôme.

### DATA ACCESS
Comment un agent touche des données structurées : `view-per-agent`,
`repository-tools`, `semantic-layer`, `text-to-sql`, `graphql` sur une base
(`DATA-ACCESS.fr.md`), ou `declared-sources` sur des fichiers, des API et des
serveurs MCP (`DATA-SOURCES.fr.md`). Porte toujours son **enveloppe de sûreté**
(rôle, timeout, plafond de lignes, schémas ou sources autorisés, opérations
interdites). `dataaccess/none` ne produit aucune entrée : l'absence est sa
représentation. Seules `view-per-agent` et `declared-sources` ont une fiche de
stack aujourd'hui ; les autres sont au catalogue.

### MEMORY SCOPE
`short-term` (fenêtre + politique de résumé) · `long-term` (store, politique
d'écriture, rétention, PII) · `shared` (état inter-agents, qui lit quoi).
La portée `long-term` existe dans le vocabulaire et dans l'IR, mais rien ne
l'exécute encore : `LongTermEnabled: true` est refusé au preflight
(`[STACK_VALUE_UNIMPLEMENTED]`). Détail : `MEMORY-PATTERNS.fr.md`.

### GUARDRAIL
Contrôle en ligne à l'entrée ou à la sortie : validation de schéma, détection
d'injection, rédaction de PII, juge de qualité. Porte un `on_trip`. Les trois
premiers existent en code (`templates/runtime/python/app/guardrails/`), actifs
selon `## Active Guardrails` ; un garde-fou nommé sans fiche active est refusé au
preflight.

---

## 4. Entités d'évaluation

### DATASET
`golden/` (ajustement) · `holdout/` (verdict, disjoint par hash) ·
`calibration/` (labels humains pour les juges) · `adversarial/` (sécurité), sous
`workspace/pipeline/datasets/`. Owned par `qa-evals` — **jamais** accessible en
écriture aux `dev-*` (P2, §7 d'ARCHITECTURE).

### EVALUATION
`(dataset, grader, seuil, k runs)` -> `(score, variance, verdict)`.

### GRADER
`exact` · `regex` · `schema` · `numeric-tolerance` · `semantic-similarity` ·
`llm-judge` (exige calibration, P9) · `trajectory` (l'ordre et la nature des
appels) · `cost` · `latency`.

### BASELINE
Résultat de référence épinglé au tuple
`(prompt_hash, model_id, index_hash, tool_schema_hash, dataset_hash)` (P10).
Écrit **uniquement** par le script `promote-baseline`, sous
`workspace/pipeline/baselines/` : une baseline se déplace par une action tracée,
jamais par écrasement, et aucun agent ne la touche.

### RUN / TRACE SPAN
Une exécution et ses spans : tour d'agent, appel d'outil, requête de retrieval,
appel LLM (tokens, coût, latence), franchissement de gate.

### ADR
Une décision refusée par défaut, assumée par écrit
(`workspace/pipeline/decisions/ADR-{timestamp}-{slug}.md`). Les décisions qui
en exigent un sont listées dans `registry/adr-requirements.yml`. Un ADR ne
compte que s'il porte `Status: Accepted` et une ligne `Covers: Clé=valeur` qui
nomme la décision ; un ADR `Proposed` n'autorise rien.

---

## 5. Convention d'identifiants

Stables, jamais renumérotés, jamais réordonnés. Ajouter = nouvel index en fin de
liste. Toute la traçabilité montante en dépend.

| Entité | Forme | Exemple |
|---|---|---|
| Mission | `{n}-{Name}` | `1-SupportAssistant` |
| Capability | `{n}-{m}-{Name}` | `1-3-RouteByIntent` |
| Business rule | `BR-{i}` | `BR-4` |
| Acceptance criterion | `AC-{i}` | `AC-2` |
| Agent | `{n}-{agent-slug}` | `1-billing-specialist` |
| Tool | `{n}-{tool-slug}` | `1-zendesk-create-ticket` |
| Retriever | `{n}-{index-slug}` | `1-contracts-index` |
| Eval suite | `{n}-{m}-{grader}` | `1-3-routing-accuracy` |
| ADR | `ADR-{timestamp}-{slug}` | `ADR-20260920T1412-supervisor-over-router` |
