# Roadmap de construction

Ordre d'implémentation de SDD_Agents lui-même. Le principe qui le gouverne est
celui que le framework impose à ses utilisateurs : **une combinaison validée de
bout en bout vaut mieux que douze annoncées.**

---

## Le MVP : une seule combinaison, mesurée

| Axe | Choix | Pourquoi celui-là |
|---|---|---|
| Langage | **Python 3.12** | l'écosystème agentic y est le plus mûr ; le tooling déterministe du framework y est déjà écrit (SDD_Pro) |
| Framework | **LangChain + LangGraph** | LangChain seul ne borne pas les boucles ni ne persiste l'état — or P12 et la reprise sont structurants. LangGraph apporte cycles bornés, checkpointing et human-in-the-loop. **LangSmith reste optionnel** : le tracing passe par la couche observabilité du framework, pas par une dépendance fournisseur |
| Orchestration | **`router` + `sequential`** | les deux patterns qui couvrent le plus de cas réels avec le coût le plus prévisible. `single-agent` est le défaut et ne demande presque rien à implémenter |
| RAG | **`hybrid`** (BM25 + vecteur, RRF) | quasi toujours meilleur que `classic` pour un surcoût faible ; sert de référence pour mesurer tous les autres patterns |
| Vector store | **pgvector** | une seule base à exploiter, transactions avec les données métier, et la RETRIEVAL GATE n'a pas besoin de plus |
| Reranking | **`none`** | c'est un levier qui se décide sur une mesure, pas par réflexe : `recall@25` bon + `nDCG@5` médiocre est le seul cas qui le justifie. La catégorie `rerank/` existe (`cohere-rerank`, `bge-reranker-local`) pour que le jour où la mesure le demande, la clé `RerankEnabled` charge enfin quelque chose — elle ne chargeait rien |
| Base | **PostgreSQL** | idem |
| Accès données | **`view-per-agent`** | le plus sûr, et celui qui donne les meilleurs résultats avec le moins de prompt |
| Outils | **MCP** + repository tools | MCP est le standard d'intégration ; les repository tools couvrent le reste |
| Serving | **CLI** puis **FastAPI + SSE** | le CLI suffit à tout valider et ne coûte presque rien |
| Eval | **pytest-eval** + graders maison | pas de dépendance à une plateforme tant que le protocole (k runs, variance, calibration) n'est pas éprouvé |
| Observabilité | **OTel-GenAI** | standard ouvert, pas de verrou fournisseur |

Cette combinaison devient la **combo C1 sous engagement SLA**. Tout le reste est
`experimental` jusqu'à mesure — et le dira.

---

## Lot 1 — Le squelette déterministe *(aucun LLM)*

Le pari : **tout ce qui peut être prouvé sans LLM doit exister avant le premier
appel LLM.** C'est ce qui rend le reste débogable.

1. `bootstrap.py` — interactif : `STACK.md` + arborescence `workspace/` + smoke.
2. `sdda_lib/` — lecture de config en 3 couches, `markdown_io` (sections,
   frontmatter), hashing, pricing, tracing.
3. Schémas JSON : `ir.schema.json`, `golden-set.schema.json`,
   `tool-schema.schema.json`, `project-config.schema.json`.
4. Validateurs : `validate_mission.py`, `validate_cap.py`,
   `validate_topology.py`, `validate_tool_contract.py`, `validate_datasets.py`.
5. `ir_compiler.py` + `validate_ir.py` — **le cœur** : les 11 contrôles de
   `AGENTIC-IR.md §4`.
6. `estimate_budget.py` — coût et latence sur le graphe.
7. `compute_status.py` — la machine à états dérivée des gates (LIFECYCLE R1).
8. Tests Python sur tout ce qui précède.

> **Critère de sortie du lot** : on peut écrire à la main une MISSION, des CAPs,
> une TOPOLOGY et des contrats, compiler l'IR, et voir les gates G0/G1/G2 rendre
> rouge sur des spécifications volontairement défectueuses. Sans un seul token.

## Lot 2 — Le moteur d'évaluation *(la valeur différenciante)*

Avant les générateurs de code. Délibérément : un framework qui génère avant de
savoir mesurer produit du volume invérifiable.

1. `eval_runner.py` — protocole k runs, moyenne, écart-type, taux de réussite,
   verdict à trois couleurs.
2. Graders : `exact`, `regex`, `schema`, `numeric-tolerance`,
   `semantic-similarity`, `trajectory`, `cost`, `latency`.
3. `llm-judge` + `calibrate_judge.py` (kappa de Cohen, seuil, bascule
   `advisory`).
4. `eval_pinning.py` + `check_baseline_freshness.py` — le tuple de P10.
5. `run_retrieval_eval.py` — recall@k, nDCG, context precision, groundedness,
   taux de citations résolues.
6. `run_adversarial_suite.py` — injection directe et indirecte.

## Lot 3 — Les Developer Agents du chemin critique

Six agents seulement, dans l'ordre du pipeline :

`po-elicitor` · `po-capabilities` · `architect-topology` ·
`architect-tools` · `architect-rag` · `dev-prompt`

Plus `loader.yml`, `agent-bounds.yaml`, `ownership.md`, `output-protocol.md`,
la taxonomie `[CLASS]`.

> **Critère de sortie** : une spécification en langage naturel produit une
> MISSION, des CAPs, une TOPOLOGY et des contrats qui franchissent G0→G2.
> Toujours zéro ligne de code applicatif généré.

## Lot 4 — Le générateur Python / LangGraph

`dev-tools` · `dev-retrieval` · `dev-data` · `dev-agent` · `dev-orchestration` ·
`dev-api`, et les stacks `python.md`, `langgraph.md`, `pgvector.md`,
`hybrid.md`, `view-per-agent.md`, `mcp.md`, `otel-genai.md` (+ `.libs.json`).

S'y ajoute `dev-backend`, le septième `dev-*`, hérité de SDD_Pro : la COQUILLE
de l'application (projet, composition, config, Domaine, packaging), jamais le
moteur. Il lit les fiches `archi/` (mvc, ddd, microservice — sélectionnées par
`## Active Architecture Pattern`) et, si `DeliverableType: backend-api`, les
fiches `backend/` (python-fastapi, node-express, nestjs, kotlin-spring-boot,
dotnet-minimalapi). Ces fiches sont réécrites pour l'agentic, pas copiées : pas
d'ORM, pas d'entité, le « Model » est dérivé de l'IR.

Gates G3 à G6 câblées.

## Lot 5 — Revue et acceptation

Les six reviewers, l'étage A / B / C, G7 et G8, la console de validation
(coût par CAP, dérive des scores, distribution des trajectoires).

## Lot 6 — Validation de bout en bout

Construire **un vrai produit** avec le framework, sur un vrai corpus, avec une
vraie vérité terrain. Mesurer. Publier les chiffres, y compris les mauvais.
C'est ce lot qui transforme C1 en combo sous SLA — pas une déclaration.

## Lot 7 — Élargissement

Un axe à la fois, chacun validé avant le suivant :

0. **Le RAG .NET** — préalable aux frameworks, et le plus urgent parce que
   c'est le seul manque aujourd'hui *opposable* : `preflight_stack_combo`
   refuse `csharp` + `rag/hybrid` + `vectorstore/pgvector`
   (`[STACK_LANGUAGE_MISMATCH]`), puisque toute la chaîne de retrieval déclare
   `Languages: python`. Il y manque exactement quatre choses :
   `vectorstore/pgvector` en variante Npgsql, la fusion RRF de `rag/hybrid` en
   C#, une fiche `eval/` .NET (xunit.v3 est déjà épinglé) et une fiche
   d'observabilité .NET — les paquets OTel .NET sont déjà épinglés dans
   `serving/aspnet-minimal.libs.json`. C'est ce manque qui a fait retirer la
   combo `dotnet-api` du bootstrap : elle activait des fiches Python sur un
   projet C#, et rien ne le disait.
1. **Frameworks** : .NET puis TypeScript — ce sont eux qui prouvent que l'IR
   tient sa promesse. Si un second générateur demande de modifier les contrats,
   l'IR a échoué et doit être corrigé.
   Note .NET : `framework/ms-agent-framework.md` existe déjà et couvre la
   convergence Semantic Kernel + AutoGen, qui est la cible .NET actuelle de
   Microsoft — l'axe porte donc sur le GÉNÉRATEUR, pas sur la fiche. Écrire
   `framework/semantic-kernel.md` serait construire sur la branche que
   l'éditeur n'avance plus.
   Note TypeScript : la cible est **LangGraph.js**, pas LangChain.js. L'argument
   qui a écarté LangChain seul en Python (tableau du MVP ci-dessus) vaut mot
   pour mot en TypeScript : il ne borne pas les boucles et ne persiste pas
   l'état, or P12 et la reprise sont structurants. Annoncer LangChain.js
   contredisait ce tableau dans le même document. Les fiches
   `lang/typescript.md`, `framework/langgraph-js.md` et `serving/cli-node.md`
   existent, en design-phase : elles décrivent, aucun générateur ne les lit
   encore, et le bootstrap ne propose aucune combo TypeScript.
   Note Java/Kotlin : aucun des deux candidats n'apporte l'équivalent de
   LangGraph. Entre Spring AI et Semantic Kernel Java, c'est Spring AI —
   Microsoft n'avance pas le portage Java. Mais l'absence de graphe borné avec
   checkpointing reporte la charge sur `dev-orchestration`, et c'est un coût
   que `framework/spring-ai.md` déclare, pas à découvrir au premier cycle non
   borné. Même statut que TypeScript : `lang/kotlin.md`, `spring-ai.md` et
   `serving/cli-kotlin.md` sont sur disque, aucune combo de bootstrap.
2. **Patterns d'orchestration** : `supervisor`, `graph`, `plan-execute`,
   `reflection`.
3. **Patterns RAG** : `contextual`, `agentic`, `self-rag`, `corrective-rag`.
4. **Multi-harnais** : compilation `.sdda/` → `.codex/`, `.gemini/`.

---

## Ce qu'on ne fera pas dans cet ordre, et pourquoi

**Ne pas commencer par les générateurs de code.** C'est la tentation naturelle et
c'est l'erreur : on obtient vite quelque chose qui tourne et qu'on ne sait pas
juger. Le moteur d'évaluation avant les générateurs est la décision de séquençage
la plus importante de cette roadmap.

**Ne pas supporter trois frameworks au lot 4.** Un seul générateur, complet et
mesuré. Le second sert de **test de l'IR** : s'il oblige à toucher aux contrats,
c'est que l'abstraction est fausse — et il vaut mieux l'apprendre au lot 7 sur
deux générateurs qu'au lot 4 sur trois.

**Ne pas annoncer de catalogue avant de l'avoir mesuré.** SDD_Pro affiche 36
stacks dont 8 explicitement 🟡. C'est cette honnêteté qui rend les 28 autres
crédibles. Annoncer douze frameworks agentic au lancement produirait exactement
le faux vert que ce framework existe pour empêcher.
