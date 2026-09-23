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
   └── 1 ─── 1  TOPOLOGY
                  ├── n  AGENT ─── n  TOOL
                  │        ├── 1  PROMPT
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
| `allocated_to` | agent(s), outil(s), retriever(s) — rempli en PHASE 2 |
| `criticality` | `normal` \| `critical` — pilote k runs et les seuils |
| `failure_behavior` | ce que fait le système quand cette CAP échoue |

**Une CAP n'est pas un agent.** L'allocation CAP -> agent est une décision
d'architecture prise en PHASE 2, révisable sans toucher les CAPs. Confondre les
deux est l'erreur qui fige une topologie avant de l'avoir pensée.

---

## 3. Entités d'architecture

### TOPOLOGY
La décision d'architecture agentic : quels agents, quels outils, quel pattern,
quel budget estimé, et **quelle alternative plus simple a été écartée et pourquoi**.

### AGENT (entité du produit généré — à ne pas confondre avec les Developer Agents)

| Champ | Nature |
|---|---|
| `id`, `role` | identité |
| `serves_caps` | les CAPs dont il porte la responsabilité |
| `prompt_ref` | `workspace/src/{App}/prompts/{agent}.system.md` + son hash |
| `model_tier` | `fast` \| `balanced` \| `deep` — jamais un nom de modèle (P11) |
| `tools` | références de TOOL — le minimum exigé par ses CAPs (P8) |
| `retrievers` | références de RETRIEVER |
| `memory_scopes` | ce qu'il lit et écrit en mémoire |
| `input_schema` / `output_schema` | contrats structurés |
| `bounds` | `max_iterations`, `max_tool_calls`, `max_delegation_depth`, `timeout_s`, `budget_usd` (P12) |
| `on_bound_exceeded` | `fail-explicit` \| `degrade` \| `escalate-human` |
| `handoff_contract` | à qui il passe la main, avec quel état, à quelle condition |
| `refusal_policy` | ce qu'il refuse de faire, explicitement |
| `trust_posture` | quelles entrées il traite comme hostiles |

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
Comment un agent touche une base : `view-per-agent`, `repository-tools`,
`semantic-layer`, `text-to-sql`, `graphql`. Porte toujours son **enveloppe de
sûreté** (rôle, timeout, plafond de lignes, schémas autorisés, statements
interdits).

### MEMORY SCOPE
`short-term` (fenêtre + politique de résumé) · `long-term` (store, politique
d'écriture, rétention, PII) · `shared` (état inter-agents, qui lit quoi).

### GUARDRAIL
Contrôle en ligne à l'entrée ou à la sortie : validation de schéma, détection
d'injection, redaction PII, juge de qualité. Porte un `on_trip`.

---

## 4. Entités d'évaluation

### DATASET
`golden/` (ajustement) · `holdout/` (verdict, disjoint par hash) ·
`calibration/` (labels humains pour les juges) · `adversarial/` (sécurité).
Owned par `qa-evals` — **jamais** accessible en écriture aux `dev-*` (P2, §7
d'ARCHITECTURE).

### EVALUATION
`(dataset, grader, seuil, k runs)` -> `(score, variance, verdict)`.

### GRADER
`exact` · `regex` · `schema` · `numeric-tolerance` · `semantic-similarity` ·
`llm-judge` (exige calibration, P9) · `trajectory` (l'ordre et la nature des
appels) · `cost` · `latency`.

### BASELINE
Résultat de référence épinglé au tuple
`(prompt_hash, model_id, index_hash, tool_schema_hash, dataset_hash)` (P10).

### RUN / TRACE SPAN
Une exécution et ses spans : tour d'agent, appel d'outil, requête de retrieval,
appel LLM (tokens, coût, latence), franchissement de gate.

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
