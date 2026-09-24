# Agentic IR — représentation intermédiaire

La charnière qui rend `STACK.md` réellement déclaratif : changer de framework
change le générateur, pas la spécification.

---

## 1. Position dans la chaîne

```
 MISSION / CAPS / TOPOLOGY / CONTRACTS        (Markdown — source autoritaire, éditée)
              │
              │  ir-compiler   (script déterministe, 0 token)
              ▼
 workspace/.sys/.ir/{n}-system.ir.json        (IR — projection compilée, jamais éditée)
              │
              ├── validate_ir.py        schéma, atteignabilité, bornes, refs, scopes
              ├── estimate_budget.py    coût et latence estimés sur le graphe
              ├── TOPOLOGY GATE         s'exécute ICI, pas sur de la prose
              │
              ├──► générateur Python / LangGraph
              ├──► générateur Python / LangChain
              ├──► générateur C# / Semantic Kernel
              ├──► générateur TypeScript / LangChain.js
              └──► générateur Java / Spring AI
```

**Règle d'or** : l'IR est **régénérable et jetable**. Il n'est jamais édité à la
main, jamais commité comme source de vérité, et toute divergence entre l'IR et les
contrats Markdown se résout en faveur du Markdown — puis par une recompilation.

---

## 2. Pourquoi une IR, et pas seulement « des contrats neutres »

| Sans IR | Avec IR |
|---|---|
| Chaque générateur ré-interprète le Markdown **avec un LLM** — 3 frameworks = 3 interprétations divergentes de la même spec | L'interprétation a lieu **une fois**, en amont. Les générateurs consomment une structure close |
| La TOPOLOGY GATE doit juger de la prose | La gate valide un graphe : atteignabilité, cycles, bornes, références — déterministe, 0 token |
| Le budget estimé est une opinion | Le budget se **calcule** sur les nœuds et les arêtes |
| Un changement d'architecture est un diff de paragraphes | Un diff structuré, lisible, reviewable |
| « Est-ce que le code généré correspond à la spec ? » est une question de jugement | C'est une comparaison code ↔ IR, partiellement automatisable |

---

## 3. Forme de l'IR

Schéma canonique : `.sdda/registry/ir.schema.json`. Structure :

```jsonc
{
  "irVersion": "1",
  "missionId": "1-SupportAssistant",
  "compiledFrom": {
    "missionHash":  "sha256:…",
    "capHashes":    { "1-1-…": "sha256:…", "1-2-…": "sha256:…" },
    "topologyHash": "sha256:…",
    "stackHash":    "sha256:…"
  },

  "budget": {
    "costPerRunTargetUsd": 0.05,
    "costPerRunHardCapUsd": 0.25,
    "latencyP95TargetMs": 8000,
    "tokenCeilingPerRun": 60000
  },

  "orchestration": {
    "rootPattern": "supervisor",
    "entryNode": "supervisor",
    "maxHops": 8,
    "checkpointing": true,
    "humanInTheLoop": false,
    "nodes": [
      { "id": "supervisor",  "kind": "agent",  "ref": "1-supervisor" },
      { "id": "billing",     "kind": "agent",  "ref": "1-billing-specialist" },
      { "id": "kb_lookup",   "kind": "retriever", "ref": "1-contracts-index" },
      { "id": "finalize",    "kind": "function", "ref": "compose_answer" }
    ],
    "edges": [
      { "from": "supervisor", "to": "billing",  "condition": "intent == 'billing'" },
      { "from": "billing",    "to": "supervisor", "condition": "always", "countsAsHop": true },
      { "from": "supervisor", "to": "finalize", "condition": "resolved || hops >= maxHops" }
    ],
    "terminalNodes": ["finalize"]
  },

  "agents": [
    {
      "id": "1-billing-specialist",
      "servesCaps": ["1-2-ExplainInvoiceLine", "1-4-IssueRefundTicket"],
      "promptRef": "workspace/src/{App}/prompts/billing-specialist.system.md",
      "promptHash": "sha256:…",
      "modelTier": "balanced",
      "tools": ["1-invoice-lookup", "1-zendesk-create-ticket"],
      "retrievers": ["1-contracts-index"],
      "memoryScopes": { "read": ["conversation"], "write": [] },
      "inputSchema":  { "$ref": "#/schemas/BillingRequest" },
      "outputSchema": { "$ref": "#/schemas/BillingAnswer" },
      "bounds": {
        "maxIterations": 8, "maxToolCalls": 15,
        "maxDelegationDepth": 1, "timeoutSec": 60, "budgetUsd": 0.08
      },
      "onBoundExceeded": "escalate-human",
      "trustPosture": { "untrustedInputs": ["retrieved_documents", "user_message"] },
      "refusalPolicy": ["ne jamais émettre de remboursement > 500 EUR sans escalade"]
    }
  ],

  "tools": [
    {
      "id": "1-zendesk-create-ticket",
      "sideEffectClass": "external-side-effect",
      "safetyStrategy": { "idempotency": "natural-key:conversation_id", "dryRunSupported": true,
                          "confirmation": "required-above:0", "cap": { "perRun": 1 } },
      "inputSchema": { "…": "…" },
      "outputSchema": { "…": "…" },
      "errors": [ { "code": "RATE_LIMITED", "agentBehavior": "backoff-then-escalate" } ],
      "authEnv": "ZENDESK_TOKEN",
      "timeoutSec": 10,
      "trust": "trusted",
      "contractTestsRef": "workspace/pipeline/suites/tool-1-zendesk-create-ticket.yaml"
    }
  ],

  "retrievers": [
    {
      // ── INTENTION : ce qu'on EXIGE, et ce que la RETRIEVAL GATE mesure ──
      "id": "1-contracts-index",
      "pattern": "hybrid",
      "topK": 8,
      "citationMode": "required",
      "identityFilter": "customer_id",
      "indexHash": "sha256:…",
      "gateThresholds": { "recallAtK": 0.80, "ndcg": 0.70,
                          "groundedness": 0.85, "citationResolveRate": 0.98 },

      // ── RÉALISATION : les composants qui l'atteignent, et eux seuls ──
      "binding": {
        "store": "pgvector",
        "embeddingModel": "voyage-3-large",
        "chunk": { "strategy": "recursive-structural", "size": 800, "overlap": 120 },
        "hybridWeights": { "vector": 0.6, "lexical": 0.4 },
        "rerank": null
      }
    }
  ],

  "dataAccess": [
    {
      "id": "1-billing-view",
      "binding": { "strategy": "view-per-agent" },
      "exposedTo": ["1-billing-specialist"],
      "envelope": { "role": "readonly", "statementTimeoutMs": 5000,
                    "maxRows": 500, "schemas": ["billing"],
                    "forbidden": ["DROP","TRUNCATE","ALTER","DELETE","UPDATE","INSERT"] }
    }
  ],

  "guardrails": {
    "input":  [{ "id": "injection-detection", "onTrip": "block-and-log" }],
    "output": [{ "id": "schema-validation",   "onTrip": "block-and-log" }]
  },

  "evaluation": {
    "suites": [
      { "id": "1-2-groundedness", "level": "L4", "capRef": "1-2-ExplainInvoiceLine",
        "dataset": "workspace/pipeline/datasets/golden/billing-v1.jsonl",
        "grader": "llm-judge", "judgeCalibrationRef": "workspace/pipeline/calibration/groundedness.json",
        "threshold": 0.85, "runs": 3 }
    ],
    "holdout": "workspace/pipeline/datasets/holdout/mission-1-v1.jsonl",
    "baselineRef": "workspace/pipeline/baselines/1-system.json"
  },

  "traceability": {
    "1-2-ExplainInvoiceLine": {
      "coversMissionItems": ["BR-3", "AC-1"],
      "implementedBy": { "agents": ["1-billing-specialist"], "tools": ["1-invoice-lookup"] },
      "evaluatedBy": ["1-2-groundedness"]
    }
  }
}
```

---

## 4. Ce que `validate_ir.py` vérifie (0 token, bloquant)

1. **Schéma** — conformité à `ir.schema.json`.
2. **Références closes** — tout `tools[]`, `retrievers[]`, `ref` de nœud pointe
   vers une entité déclarée. Aucune référence fantôme.
3. **Atteignabilité** — tout nœud est atteignable depuis `entryNode` ; tout
   chemin atteint un `terminalNode`.
4. **Bornes** — tout cycle du graphe est coupé par une borne (`maxHops`, une
   condition décrémentante, ou `maxIterations`). Un cycle non borné est une
   erreur bloquante, pas un avertissement (P12).
5. **Cohérence des bornes** — la somme des `budgetUsd` par agent sur le plus long
   chemin ne dépasse pas `costPerRunHardCapUsd`.
6. **Couverture des CAPs** — toute CAP est dans le `servesCaps` d'au moins un
   agent ou implémentée par un outil/retriever, et apparaît dans `traceability`.
7. **Moindre privilège** — tout outil câblé à un agent est exigé par au moins une
   de ses CAPs. L'excédent est un finding `[TOOL_SCOPE_EXCESS]`.
8. **Effets de bord** — tout outil non `read-only` porte une `safetyStrategy`, et
   un outil non idempotent ne porte pas de `retryPolicy` (un retry crée trois
   tickets).
8bis. **Cohabitation** — un agent exposé à une entrée non maîtrisée ne porte
   aucun outil `write-destructive` (irréversible, aucun bypass), et ne porte un
   `external-side-effect` que si sa stratégie **borne le dégât** (`idempotency`
   et `cap` ou `confirmation`). Le gradient et sa raison :
   `rules/agent-safety.md` §7.
9. **Posture de confiance** — tout agent dont une entrée est `untrusted` a une
   suite d'injection déclarée dans `evaluation.suites`.
10. **Neutralité framework** — aucun identifiant de framework dans l'IR
    (`StateGraph`, `Kernel`, `AgentExecutor`…). L'IR décrit *quoi*, jamais *avec
    quelle API* (P11).
10bis. **Neutralité d'infrastructure** — aucun identifiant de composant
    (`pgvector`, `voyage`, `postgres`, `psycopg`…) **hors de `binding`**
    (`[INFRA_LEAK_IN_INTENT]`). La neutralité framework ne suffisait pas : elle
    ne cherche que des noms d'API, et `store: "pgvector"` passait — alors qu'il
    couple exactement de la même façon. Le contrôle porte sur `retrievers[]` et
    `dataAccess[]`, les deux branches scindées ; `memory.longTermStore` reste
    hors périmètre tant que la mémoire n'est pas scindée à son tour.
11. **Évaluabilité** — toute suite déclare `dataset`, `grader`, `threshold`,
    `runs`. Tout `llm-judge` a un `judgeCalibrationRef` résolvable (P9).

---

## 4.bis La frontière `intent` / `binding`

Deux branches, et c'est ce qui rend l'IR réellement multi-langage.

| | **Intention** (hors `binding`) | **Réalisation** (`binding`) |
|---|---|---|
| Répond à | ce que l'architecte **exige** | comment on l'**atteint** |
| Retrieval | `pattern`, `topK`, `citationMode`, `identityFilter`, `maxRetrievalCalls`, `gateThresholds`, `indexHash` | `store`, `embeddingModel`, `chunk`, `hybridWeights`, `rerank` |
| Accès données | `exposedTo`, `envelope`, `connectors` | `strategy` |
| Lu par | les **gates** — c'est ce qui se mesure | les **générateurs**, au moment d'émettre du code |
| Nom de composant | **interdit** (`[INFRA_LEAK_IN_INTENT]`) | seul endroit où il est permis |

**Pourquoi cette frontière n'est pas cosmétique.** Avant elle, `store: "pgvector"`
et `embeddingModel` étaient des champs **obligatoires** de l'IR — et la même
décision vivait déjà dans `STACK.md ## Active Retrieval Stack`. Deux vérités sur
le même fait, que rien ne confrontait. Un générateur C# lisant `pgvector`
cherchait une fiche qui n'existe pas dans son runtime ; la ROADMAP prévoyait de
l'apprendre au Lot 7, sur le second générateur, c'est-à-dire après avoir écrit
le premier contre un contrat qu'on savait faux.

**La réconciliation remplace la duplication.** À la compilation, `binding` est
confronté aux stacks actives : un désaccord est `[RETRIEVAL_BINDING_MISMATCH]`,
jamais un arbitrage silencieux. La comparaison porte sur la **famille**
(`voyage` couvre `voyage-3-large`), parce qu'une fiche décrit une famille et un
contrat nomme un modèle ; le numéro de version, lui, est épinglé par
`indexHash` (P10).

**Le critère qui dit si la frontière tient** : un générateur doit pouvoir
dériver son plan d'appel complet **sans jamais lire `binding`**, et ce plan doit
être identique pour `pgvector` et pour n'importe quel autre store. C'est un test
(`test_ir_compiler.py`), pas une intention.

---

## 5. Ce que l'IR **ne** contient **pas**

- Aucun nom de classe, méthode ou API de framework.
- Aucun texte de prompt — seulement une **référence** et un **hash**. Le prompt
  reste un fichier lisible et reviewable (P1).
- Aucun secret — seulement des noms de variables d'environnement.
- Aucun résultat de mesure — l'IR décrit l'intention ; les résultats vivent dans
  `workspace/.sys/reports/`.
