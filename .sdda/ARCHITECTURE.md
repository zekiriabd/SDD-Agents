# SDD_Agents — Architecture

Document de référence : arborescence, couches, pipeline, gates, abstraction
harness/provider. Découle de [PHILOSOPHY.md](PHILOSOPHY.md).

---

## 1. Les trois couches

Identique en esprit à SDD_Pro, adapté à l'agentic.

| Couche | Emplacement | Nature | Qui la lit |
|---|---|---|---|
| **Framework** | `.sdda/` | Source neutre : agents, commandes, règles, stacks, templates, invariants | Compilée vers les façades harness |
| **Façades harness** | `.claude/`, `.codex/`, `.gemini/` | Générées depuis `.sdda/` par `harness_build.py` | Le harnais actif |
| **Workspace** | `workspace/` | Le projet de l'utilisateur : spécifications, contrats, prompts, datasets, code généré | Les agents, au runtime |

> `.sdda/` (et non `.sdd/`) : nom court volontaire — il est référencé des centaines
> de fois dans 22 prompts d'agents ; deux caractères de moins sont des tokens
> économisés à chaque invocation. Distinct de `.sdd/` pour permettre de vendorer
> SDD_Pro et SDD_Agents dans un même dépôt.

---

## 2. Arborescence

```
SDD-Agents/
├── README.md                          # anglais par défaut ; jumeau README.fr.md
├── README.fr.md                       #   (convention : .sdda/docs/README.md)
├── bootstrap.py                       # interactif : STACK.md + workspace + smoke
├── plugin.json                        # 🟡 planifié — découverte marketplace (comme SDD_Pro)
│
├── .sdda/                             # ── FRAMEWORK (source neutre) ──────────
│   ├── PHILOSOPHY.md
│   ├── ARCHITECTURE.md
│   ├── INVARIANTS.yml                 # contrats porteurs + enforcer sur disque (compte : sync_counters)
│   ├── config.base.yml                # couche 1/3 du Project Config
│   ├── loader.yml                     # reads/writes/forbidden_reads + budget + cache par agent
│   ├── agent-bounds.yaml              # tier_default / floor / ceiling par agent
│   ├── capability-matrix.yml          # harnais x mécanismes
│   ├── agents/                        # 22 Developer Agents (cf. docs/AGENT-ROSTER.md)
│   ├── commands/                      # commandes slash
│   ├── rules/                         # règles opérationnelles
│   │   ├── ownership.md               # matrice d'écriture (hérité SDD_Pro)
│   │   ├── output-protocol.md
│   │   ├── error-classification.md    # taxonomie [CLASS] agentic
│   │   ├── eval-protocol.md           # k-runs, variance, calibration, baselines
│   │   ├── prompt-authoring.md        # comment un contrat devient un prompt
│   │   ├── agent-safety.md            # injection, scopes, effets de bord
│   │   └── budget-and-loop.md         # bornes, coût, escalade
│   ├── skills/                        # 🟡 planifié — skills auto-déclenchées des
│   │                                  #   Developer Agents. À ne pas confondre avec
│   │                                  #   les skills des agents du PRODUIT, qui
│   │                                  #   vivent au §5 des contrats et dans les
│   │                                  #   prompts (cf. §7, rules/ownership.md §2.2)
│   ├── providers/                     # anthropic / openai / google / azure / local
│   ├── stacks/                        # ── LE CATALOGUE — <!--sdda:count stacks-->31<!--/sdda:count--> fiches sur disque ─
│   │   │                    # Chaque fiche déclare `Languages:` (un langage,
│   │   │                    # plusieurs, ou `*` si elle n'en suppose aucun).
│   │   │                    # C'est la SSoT du couplage : preflight_stack_combo
│   │   │                    # refuse une fiche d'un autre runtime que le
│   │   │                    # langage actif -> [STACK_LANGUAGE_MISMATCH].
│   │   ├── lang/            python.md · csharp.md
│   │   ├── framework/       langchain.md · langgraph.md · ms-agent-framework.md
│   │   │                      (+ .libs.json chacun)
│   │   ├── orchestration/   single-agent.md · router.md · sequential.md
│   │   ├── rag/             none.md · hybrid.md            [python sauf none]
│   │   ├── vectorstore/     pgvector.md (+ .libs.json)     [python]
│   │   ├── embedding/       voyage.md · bge-local.md
│   │   ├── rerank/          none.md · cohere-rerank.md
│   │   │                    bge-reranker-local.md (+ .libs.json)
│   │   │                    # `RerankEnabled` existait dans STACK.md sans
│   │   │                    # aucune fiche derrière : la clé était lue, rien
│   │   │                    # ne l'implémentait.
│   │   ├── dataaccess/      view-per-agent.md · declared-sources.md · none.md
│   │   ├── memory/          buffer.md
│   │   ├── tools/           mcp.md
│   │   ├── eval/            pytest-eval.md (+ .libs.json)
│   │   ├── observability/   otel-genai.md (+ .libs.json)
│   │   ├── guardrails/      schema-validation.md · pii-redaction.md
│   │   │                    injection-detection.md
│   │   └── serving/         cli.md · fastapi-sse.md · aspnet-minimal.md · batch.md
│   │                        # surface = PAR OÙ L'ON ENTRE ; le LIVRABLE
│   │                        # (DeliverableType) vit dans ## Project Config
│   ├── registry/                      # ── REGISTRES MACHINE ─────────────────
│   │   ├── patterns.registry.json     # tout pattern : id, famille, critères, coût, risques
│   │   ├── compatibility.matrix.json  # lang x framework x pattern x provider x store
│   │   ├── architecture-requirements.yml  # P7 : ce que chaque choix de stack impose
│   │   └── ir.schema.json             # schéma de l'Agentic IR (cf. docs/AGENTIC-IR.md)
│   ├── templates/
│   │   ├── STACK.md.template
│   │   ├── mission.template.md
│   │   ├── capability.template.md
│   │   ├── topology.template.md
│   │   ├── agent-contract.template.md
│   │   ├── tool-contract.template.md
│   │   ├── retrieval-contract.template.md
│   │   ├── memory-contract.template.md
│   │   ├── eval-suite.template.md
│   │   ├── roster.manifest.template.yml   # roster déclaré par l'architecte (P7)
│   │   ├── sources.manifest.template.yml  # sources de données déclarées
│   │   ├── golden-set.schema.json
│   │   ├── tool-schema.schema.json
│   │   ├── project-config.schema.json
│   │   ├── libs-catalog.schema.json   # schéma des .libs.json de stacks/
│   │   ├── prompt.template.md
│   │   ├── adr.template.md
│   │   └── runtime/                   # squelettes de code par langage
│   │       # (les combinaisons de stack vivent dans registry/compatibility.matrix.json)
│   ├── digests/                       # tranches de taxonomie par agent
│   ├── sdda.py                        # lanceur : `python .sdda/sdda.py {cmd}` —
│   │                                  #   marche depuis un clone nu, sans pip install
│   └── python/                        # outillage déterministe 0-token
│       ├── sdda_cli.py                # dispatcher des 60 sous-commandes ; registre
│       │                              #   DÉRIVÉ du disque, lu aussi par les scanners
│       ├── sdda_lib/                  # config, markdown_io, hashing, pricing, traces
│       ├── sdda_scripts/              # validate_*, estimate_budget, eval_runner, …
│       ├── sdda_admin/                # harness_build, sync_digests, sync_error_registry
│       ├── sdda_hooks/                # gates bloquantes PreToolUse / SubagentStop
│       │                              #   chacune déclare son WIRING ; harness_build
│       │                              #   les câble TOUS, aucune table en dur
│       └── tests/
│
└── workspace/                         # ── LE PROJET — quatre entrées, cf. §2.ter
    │
    ├── stack/                         # ── CE QU'ON CONFIGURE ─────────────────
    │   ├── STACK.md                   # gitignored (secrets en clair)
    │   └── sources/ · topology/       # manifestes VERSIONNÉS (sources, roster)
    │
    ├── feats/                         # ── CE QU'ON SPÉCIFIE ──────────────────
    │   ├── missions/    {n}-{Name}.md
    │   ├── caps/        {n}-{m}-{Name}.md
    │   ├── topology/    {n}-topology.md   + {n}-topology.mmd (graphe Mermaid)
    │   ├── contracts/
    │   │   ├── agents/      {n}-{agent}.agent.md
    │   │   ├── tools/       {n}-{tool}.tool.md
    │   │   ├── retrieval/   {n}-{index}.retrieval.md
    │   │   ├── memory/      {n}-memory.md
    │   │   └── dataaccess/schemas/     # schémas figés des sources déclarées
    │   ├── decisions/  ADR-{ts}-{slug}.md   # UN seul endroit (cf. §2.ter)
    │   └── briefs/                     # matière première d'une MISSION
    │
    ├── src/                           # ── CE QU'ON PRODUIT ───────────────────
    │   ├── prompts/     {agent}.system.md   # hashés — actif d'EXÉCUTION
    │   └── {AppName}/                       # l'application agentic générée
    │
    ├── proof/                         # ── CE QUI JUGE ────────────────────────
    │   ├── datasets/    golden/ · holdout/ · calibration/ · adversarial/
    │   ├── suites/      les suites d'évaluation
    │   ├── baselines/   la référence de non-régression
    │   └── calibration/ κ de chaque juge LLM
    │                    # AUCUN `dev-*` n'écrit ici. Jamais.
    │
    └── .sys/                          # ── ÉTAT INTERNE ET SORTIES DE RUN ─────
        ├── .ir/         {n}-system.ir.json  # Agentic IR compilé depuis les contrats
        ├── .context/ · .state/ · .validation/ · .audit/
        ├── reports/     {n}-{run-id}.json   # rapports d'eval
        ├── traces/runs/ {run-id}.jsonl
        └── workspace.json                   # workspaceVersion — écrit par bootstrap,
                                             #   monté par sdda_scripts/migrate_workspace.py
```

### 2.ter Quatre entrées, quatre natures

L'arbre a porté douze répertoires au même niveau. Rien n'y disait qu'ils ne sont
pas de même nature : un lecteur voyait `caps/` et `traces/` côte à côte sans
pouvoir deviner que l'un se relit en revue et que l'autre se supprime sans
perte.

| Entrée | Nature | Écrite par | Régénérable |
|---|---|---|---|
| `stack/` | configuration | l'humain | non |
| `feats/` | **spécification** — l'ENTRÉE de la génération | humain, PO, architectes | non |
| `src/` | code généré, prompts compris | les six `dev-*`, `qa-tests` | oui |
| `proof/` | **ce qui juge** | `qa-evals` et les scripts, **jamais** un `dev-*` | non |
| `.sys/` | état interne et sorties de run | les scripts | oui |

**La seule frontière qui ne souffre aucune exception est celle de `proof/`.**
L'agent qui écrit le code ne peut toucher ni au jeu qui le note, ni à la
référence contre laquelle sa régression est mesurée. Ranger ces jeux sous
`src/` les ferait tomber dans la zone d'écriture des six agents développeurs, et
il faudrait creuser une exception à l'intérieur de leur propre périmètre. Une
exception dans un glob d'ownership est une exception qu'on oublie.

Deux conséquences se lisent directement dans l'arbre :

- **Les prompts sont sous `src/`** parce qu'un prompt système est un actif
  d'exécution. Rangé au même rang que les specs, il ne part pas avec le code :
  l'application livrée en exécutable ou en conteneur cherchait ses prompts dans
  un répertoire resté dans le dépôt.
- **Les ADR ont UN emplacement**, `feats/decisions/`. Ils en avaient deux —
  `docs/adr/` que citait le gabarit, `.sys/.context/adrs/` que déclarait la
  matrice d'ownership — et le script des tâches humaines cherchait dans les
  deux. La question « cet ADR a-t-il été écrit ? » avait donc deux réponses
  possibles, et c'est celle que personne ne relisait qui gouvernait.

Un workspace d'une version antérieure monte par
`python .sdda/sdda.py migrate-workspace`, qui **déplace** le contenu plutôt que
de créer le nouvel arbre à côté de l'ancien.

**Cet arbre décrit le disque, pas l'intention.** 🟡 marque le seul écart assumé :
annoncé, pas encore écrit. La règle vaut surtout pour `stacks/` —
<!--sdda:count stacks-->31<!--/sdda:count--> fiches existent, quand le
catalogue visé en compte trois fois plus. Ce n'est pas un
manque à combler avant d'annoncer : c'est la séquence de
[docs/ROADMAP.md](docs/ROADMAP.md), qui livre **une combinaison validée de bout en
bout** (C1) avant d'en annoncer douze. Le catalogue cible et le niveau de
validation de chaque composant vivent dans `registry/compatibility.matrix.json`
(`componentLevels`, `catalogDiscrepancies`) et `registry/patterns.registry.json` —
registres machine, donc vérifiables, là où un arbre en prose ne l'est pas.

La conséquence opérationnelle, qui doit être dite : **une ligne activée dans
`STACK.md` pour un composant sans fiche sur disque ne charge rien.** Le catalogue
annoncé n'est pas un catalogue chargeable.

### 2.ante Un seul point d'entrée pour l'outillage

Les 60 scripts déterministes s'appellent par une forme unique :

```bash
python .sdda/sdda.py validate-mission --mission 1     # depuis un clone nu
sdda validate-mission --mission 1                     # après `pip install -e .sdda/python`
```

La première ne suppose **aucune installation**, et c'est elle qu'écrivent les
22 fiches d'agents et les 11 commandes. Un framework dont les prompts exigent un
`pip install` préalable échoue au premier clone — et l'agent qui reçoit
`command not found` invente la sortie du script au lieu de s'arrêter.

Le registre des sous-commandes est **dérivé du disque** (`sdda_cli.discover()`) :
tout module de `sdda_scripts/`, `sdda_admin/` ou `sdda_hooks/` est une
sous-commande nommée par son fichier (`validate_mission.py` -> `validate-mission`).
Aucune table à tenir, donc aucune table à laisser dériver — même raison que pour
le registre d'erreurs (§9).

Ce n'est pas qu'une commodité d'écriture. `sdda_cli.resolve()` est la **SSoT que
lisent les scanners** : `planned_scripts.py` et `framework_smoke.py` résolvent
`python .sdda/sdda.py {cmd}` vers son module pour continuer de détecter un script
qu'un prompt annonce sans que personne l'ait écrit. Migrer la prose vers la forme
courte sans leur apprendre à la lire aurait rendu ce contrôle muet — c'est-à-dire
aurait rouvert exactement la porte que `refs.planned.undeclared` a fermée.

### 2.bis L'Agentic IR — la charnière du multi-framework

Entre les contrats Markdown (lisibles, discutables, versionnés) et le code
généré (LangGraph, Semantic Kernel, Pydantic-AI…) s'intercale une
**représentation intermédiaire machine** : `workspace/.sys/.ir/{n}-system.ir.json`.

```
contrats Markdown  --(ir-compiler, déterministe, 0 token)-->  system.ir.json
                                                                    |
                        +-------------------------+-----------------+
                        v                         v                 v
                 générateur Python         générateur C#      générateur TS
                 (LangGraph)               (Semantic Kernel)  (LangChain.js)
```

Pourquoi c'est structurant, et pas une couche de plus :

- **Le multi-framework devient déterministe.** Sans IR, chaque générateur
  ré-interprète le Markdown avec un LLM — donc trois interprétations divergentes
  de la même spécification. Avec IR, l'interprétation a lieu **une fois**, en
  amont, et les générateurs consomment une structure close.
- **L'IR est validable sans LLM** : schéma JSON, atteignabilité du graphe, bornes
  présentes, outils référencés existants, cohérence des scopes. La TOPOLOGY GATE
  s'exécute sur l'IR, pas sur de la prose.
- **L'IR est diffable.** Un changement d'architecture devient un diff structuré
  et lisible, pas un diff de paragraphes.
- **L'IR porte le budget estimé.** Coût et latence se calculent sur le graphe, pas
  sur une intention.

L'IR ne remplace pas les contrats Markdown : les contrats restent la source
autoritaire éditée par l'humain et les agents. L'IR en est la projection
compilée, régénérable, jamais éditée à la main. Détail et schéma :
[docs/AGENTIC-IR.md](docs/AGENTIC-IR.md).

---

## 3. Le pipeline forward

```
 PHASE 0   ELICITATION        po-elicitor            -> feats/missions/{n}-{Name}.md
   |                                                         [MISSION GATE]
 PHASE 1   CAPABILITIES       po-capabilities        -> feats/caps/{n}-{m}-*.md
   |                                                         [CAP GATE]
 PHASE 2   TOPOLOGIE          architect-topology          -> feats/topology/{n}-topology.md
   |         + architect-rag, architect-data,          + feats/contracts/**
   |           architect-memory, architect-tools  (parallèle)
   | PHASE 2.9 COMPILATION IR  ir-compiler (script, 0 token) -> .sys/.ir/{n}-system.ir.json
   |                                                         [TOPOLOGY GATE]  (s'exécute sur l'IR)
 PHASE 3   SOCLE              dev-tools || dev-retrieval || dev-data     (parallèle)
   |                                                         [TOOL GATE] [RETRIEVAL GATE]
 PHASE 4   PROMPTS + AGENTS   dev-prompt -> dev-agent  (parallèle par agent)
   |                                                         [AGENT GATE]
 PHASE 5   ORCHESTRATION      dev-orchestration             -> graphe / superviseur / routeur
   |       + dev-api (surface d'exposition)
   |                                                         [ORCH GATE]
 PHASE 6   EVAL + TESTS       qa-evals || qa-tests
   |
 PHASE 7   REVUE              Etage A : spec-compliance seul
   |                          Etage B : agent-safety || cost-latency ||
   |                                    orchestration || rag-quality   (parallèle)
   |                          Etage C : review-adversarial (système vivant)
   |                                                         [SAFETY GATE]
 PHASE 8   ACCEPTATION        mesure de l'objectif chiffré sur holdout
                              + non-régression vs baseline
                                                             [ACCEPTANCE GATE]
                                                          -> VERDICT vert/jaune/rouge
```

**Délégation pure** (hérité SDD_Pro) : la commande orchestratrice `/sdda-full`
n'invoque aucun agent directement — elle chaîne des commandes. **Aucun agent ne
spawne un autre agent** : l'orchestration appartient à la commande, donc la
facture reste prévisible.

---

## 4. Les neuf gates

Chaque gate est **déterministe** (0 token) sauf mention contraire. Chaque gate a
un enforcer sur disque déclaré dans `INVARIANTS.yml`.

| # | Gate | Vérifie | Bloquant sur |
|---|---|---|---|
| G0 | **MISSION** | objectif chiffré présent, budget déclaré (coût/latence/tokens), ground truth identifiée, aucun `<à préciser>` résiduel | `[MISSION_INCOMPLETE]` |
| G1 | **CAP** | chaque AC nomme métrique + seuil + dataset ; chaque élément de la MISSION couvert par >= 1 CAP | `[AC_NOT_EVALUABLE]`, `[TRACEABILITY_GAP]` |
| G2 | **TOPOLOGY** | pattern justifié + alternative plus simple explicitement écartée ; budget estimé <= budget déclaré ; toute boucle bornée ; tout agent a un contrat ; graphe atteignable sans cycle non borné | `[TOPOLOGY_UNJUSTIFIED]`, `[BUDGET_EXCEEDED_ESTIMATE]`, `[UNBOUNDED_LOOP]` |
| G3 | **TOOL** | schéma valide ; tests de contrat verts (happy + chaque erreur déclarée + timeout + auth KO) ; connectivité live vérifiée ; classe d'effet de bord déclarée ; stratégie de sûreté présente si destructif | `[TOOL_CONTRACT_FAILED]`, `[SIDE_EFFECT_UNDECLARED]` |
| G4 | **RETRIEVAL** | golden set présent (>= n queries) ; recall@k, nDCG, groundedness, taux de citations résolues au-dessus des seuils | `[RETRIEVAL_BELOW_THRESHOLD]` |
| G5 | **AGENT** | chaque agent évalué **isolé** (outils mockés, retrieval figé) contre ses CAP ACs, sur k runs | `[AGENT_EVAL_FAILED]` |
| G6 | **ORCH** | evals bout-en-bout sur golden mission ; trajectoires conformes ; hops <= plafond ; coût et latence **mesurés** <= budget déclaré ; **part `api`** : le contrat exposé est dérivé de l'IR et lui correspond | `[TRAJECTORY_VIOLATION]`, `[BUDGET_EXCEEDED_MEASURED]`, `[API_CONTRACT_DRIFT]`, `[API_ROUTE_UNBACKED]` |
| G7 | **SAFETY** | suite d'injection (directe + indirecte) ; audit de scope d'outils ; scan de secrets dans prompts/traces/datasets ; scan PII du vector store | `[INJECTION_SUCCEEDED]`, `[TOOL_SCOPE_EXCESS]`, `[SECRET_LEAK]`, `[PII_IN_INDEX]` |
| G8 | **ACCEPTANCE** | objectif chiffré de la MISSION atteint sur **holdout** (jamais sur le golden d'entraînement) ; non-régression vs baseline au-delà de la tolérance | `[GOAL_NOT_MET]`, `[REGRESSION]` |

**Règle du holdout** : les datasets d'ajustement (`golden/`) et de verdict
(`holdout/`) sont disjoints et le pipeline le vérifie par hash. Optimiser les
prompts contre le jeu qui rend le verdict est la façon agentic de se mentir.

**L'API Gate est une part de G6, pas une dixième gate.** C'est la transposition
de l'`API Gate` de SDD_Pro, qui validait le contrat back↔front avant de générer
le front. Ici la couture est entre l'**IR** et le monde extérieur : l'OpenAPI
publié est **dérivé** des `inputSchema` / `outputSchema` de l'IR, jamais écrit à
la main, et un test déterministe (0 token) confronte les deux. La ranger dans G6
plutôt qu'en gate séparée est délibéré : `dev-api` travaille en PHASE 5 aux
côtés de `dev-orchestration`, et un dixième verrou pour une seule question
diluerait la lecture des neuf autres. Détail :
`stacks/serving/fastapi-sse.md §6`. Désactivable par `ApiContractFirst: false`,
qui exige un ADR.

**Le livrable est déclaré, pas déduit.** `DeliverableType` (`## Project Config`)
dit ce qu'on **installe** — `cli-exe`, `backend-api`, `library`, `batch-job`,
`container`, `mcp-server` — là où `## Active Serving Surface` dit par où l'on
**entre**. Les deux sont indépendants : un même `RunService` s'expose en HTTP ou
en lot, et se livre en conteneur ou en exécutable. Leur cohérence (livrable x
langage x surface x identité d'appelant) est vérifiée par
`validate_packaging.py`, en **part `packaging` de G2** : c'est une décision
d'architecture, et elle doit être tranchée avant qu'une ligne de code en dépende.

**Le défaut est `cli-exe`, dans les quatre langages.** Un système agentic se
livre d'abord comme un programme qu'on lance : une entrée, une sortie, un code
de retour. Rien à déployer, rien à authentifier, et c'est la surface que le
runner d'eval invoque en L4-L7 — donc ce qu'on mesure est ce qu'on livre. Chaque
langage a sa fiche console (`serving/cli.md` en Python, `serving/cli-dotnet.md`
en C#) ; un défaut qu'un langage ne peut pas honorer se voit au preflight
(`[STACK_LANGUAGE_MISMATCH]`) et non en silence.

**`backend-api` n'est pas une variante de présentation, c'est un changement de
nature.** Le moteur agentic cesse d'être un programme que quelqu'un lance et
devient un **service qu'une autre application appelle** : elle lui envoie une
requête, il exécute la MISSION, il rend la réponse et les événements. On le
choisit quand l'appelant est un logiciel — un front, un back métier, un
ordonnanceur — jamais pour faire plus propre. Il rend alors obligatoires trois
choses qui n'existent pas en `cli-exe` : un `ApiFramework` cohérent avec le
langage (`fastapi`, `aspnet-minimal`, `spring-boot`, `express`…), une identité
d'appelant établie au transport (`ApiAuthMode`, sans quoi tout le filtrage à la
source est contournable), et un contrat public dérivé de l'IR
(`ApiContractFirst`) — un appelant qu'on ne contrôle pas ne se corrige pas après
coup. La CLI reste générée : elle porte le smoke et les evals.

---

## 5. Contrat faits vs hypothèses

Hérité de SDD_Pro, généralisé à tout le pipeline.

- **FAITS** — produits par des scripts déterministes : schémas d'outils, graphe
  d'appels, métriques de retrieval, coûts mesurés, tailles de corpus, résultats de
  tests. **Peuvent** devenir des critères d'acceptation.
- **HYPOTHÈSES** — produites par des agents LLM : découpe en capabilities, choix de
  topologie, glossaire métier, zones de risque. **Ne peuvent jamais** devenir des
  critères d'acceptation sans validation humaine ou mesure.

La séparation est **structurelle** : l'agent écrit dans un fichier distinct qu'un
script fusionne dans la branche `hypotheses` uniquement. Il ne peut pas écraser un
fait, même s'il essaie.

---

## 6. Abstraction harness / provider / tier

Reprise intégrale du mécanisme SDD_Pro, avec une distinction supplémentaire
obligatoire en agentic :

| Notion | Qui exécute | Déclaré dans |
|---|---|---|
| **Harness** | où tourne l'orchestration de *construction* (Claude Code, Codex, Gemini CLI…) | `STACK.md ## Active Harness` |
| **Build models** | quels modèles paient les tokens de *construction* (les <!--sdda:count agents-->22<!--/sdda:count--> Developer Agents) | `STACK.md ## Build Models` |
| **Runtime models** | quels modèles fait tourner l'**application générée** | `STACK.md ## Runtime Models` |

Les trois sont indépendants. Construire avec Claude Code + Opus une application
qui tourne sur GPT-4-mini est un cas nominal, pas une exception. Confondre les
deux derniers est l'erreur la plus fréquente des frameworks concurrents : elle
rend le budget d'exécution incalculable.

**Les agents déclarent un tier** (`fast` / `balanced` / `deep`), jamais un nom de
modèle. La résolution tier -> modèle appartient au provider actif
(`.sdda/providers/*.yaml`, clé `tier_map`). Ajouter un provider ne touche aucun
agent. Les bornes `tier_floor` / `tier_ceiling` de `agent-bounds.yaml` sont des
invariants de qualité : le Project Config ne peut pas les relâcher.

---

## 7. Ownership et parallélisme

La matrice d'ownership de SDD_Pro est reprise telle quelle et étendue aux
artefacts agentic. Extrait :

| Chemin | Owner exclusif | Mode |
|---|---|---|
| `workspace/feats/missions/{n}-*.md` | `po-elicitor` | Create puis append-only |
| `workspace/feats/caps/{n}-{m}-*.md` | `po-capabilities` | Create exclusif (1 fichier = 1 CAP) |
| `workspace/feats/topology/{n}-*.md` | `architect-topology` | Create exclusif |
| `workspace/feats/contracts/tools/*` | `architect-tools` | Create exclusif |
| `workspace/feats/contracts/retrieval/*` | `architect-rag` | Create exclusif |
| `workspace/src/prompts/{agent}.system.md` | `dev-prompt` | Create + Edit exclusif |
| `workspace/src/**/agents/{agent}/**` | `dev-agent` (1 instance par agent) | Edit-augment exclusif |
| `workspace/src/**/tools/**` | `dev-tools` | Edit-augment exclusif |
| `workspace/src/**/retrieval/**` | `dev-retrieval` | Edit-augment exclusif |
| `workspace/src/**/orchestration/**` | `dev-orchestration` | Create + Edit exclusif |
| `workspace/proof/datasets/**` | `qa-evals` | Create exclusif ; **jamais** `dev-*` |
| `workspace/proof/baselines/**` | script déterministe uniquement | Write atomique |

> **Règle critique, propre à l'agentic** : `dev-agent` n'a **aucun** droit
> d'écriture sur `workspace/proof/datasets/` ni sur `workspace/src/prompts/`. L'agent qui
> écrit le code ne peut ni modifier le jeu qui le juge, ni réécrire le prompt qu'il
> est censé implémenter. Sans cette séparation, l'auto-confirmation est garantie —
> c'est le pendant agentic du `[QA_OWNERSHIP_VIOLATION]` de SDD_Pro.

**Le cas des skills — deux owners, aucune autorité unique.** Une skill d'agent du
produit traverse deux fichiers déjà possédés : `architect-topology` la **déclare**
au §5 du contrat d'agent, `dev-prompt` l'**implémente** dans
`prompts/{agent}.system.md` (`## Compétences`). Aucun ne peut écrire chez l'autre,
donc aucun ne peut résoudre seul un désaccord entre déclaration et
implémentation — `lint_prompts.py` le constate dans les deux sens
(`[SKILL_NOT_IMPLEMENTED]`, `[SKILL_UNDECLARED]`). C'est la seule vérification
possible : un outil a un schéma qu'une gate peut exécuter, une skill n'a ni schéma
ni effet de bord. Un outil est ce que l'agent a le **droit d'appeler** ; une skill
est ce qu'il **sait faire**. Détail : `rules/ownership.md §2.2`.

---

## 8. Observabilité : artefact de première classe

Tout run — de construction comme d'exécution du produit — émet une trace de spans
**OTel-GenAI** dans `workspace/.sys/traces/runs/{run-id}.jsonl`, une ligne par span :
tour d'agent, appel d'outil (args redigés), requête de retrieval (+ documents
retournés + scores), appel LLM (modèle, tokens in/out/cache, coût, latence),
franchissement de gate.

**Un seul format, et c'est le span.** Chaque ligne porte `run_id`, `trace_id`,
`span_id` et `parent_span_id` : c'est ce dernier qui fait la valeur du format.
La profondeur de délégation et l'agent responsable d'un appel d'outil se
**lisent** dans l'arbre, là où une suite d'événements à plat obligeait à deviner
« le dernier agent vu » — faux dès que deux agents travaillent en parallèle, et
c'est précisément le moment où le périmètre d'outils compte.

**Le coût est recalculé depuis les tokens**, jamais relu depuis l'attribut
`sdda.cost.usd` que l'application déclare. Un chiffre qu'on relit sans le
recalculer n'est pas une mesure, c'est une déclaration ; l'écart entre les deux
est signalé, parce que c'est ce genre d'écart qui fait passer un run sous un
plafond qu'il dépasse.

**La construction laisse sa propre trace**, dans le même fichier et au même
format : un span `sdda.build.agent {agent}` par invocation de Developer Agent
(coût facturé, latence, tours de `build_loop`, contexte chargé vs le
`budget_bytes` de `loader.yml`), un span `sdda.gate {gate}` par franchissement,
et un span racine écrit par `sdda_state end-run`, seul à connaître le début, la
fin et le cumul du run. C'est la facture que l'utilisateur voit en premier, et
la seule que `MaxCostPerRun` prétend plafonner.

Ce coût de construction est **déclaré par le harnais**, pas recalculé : nous ne
voyons pas les tokens d'un sous-agent. Il reste donc dans un champ distinct de
celui du produit. Les additionner ferait passer un chiffre invérifiable pour une
mesure — et c'est exactement la confusion que §6 impose déjà d'éviter entre
*build models* et *runtime models*.

Sans trace, un système non déterministe n'est pas débogable : il n'y a pas de
stack trace à lire. Invariant `trace-emitted-per-run`.

Les traces alimentent la console de validation (même principe que la console
SQLite de SDD_Pro) : coût par CAP, dérive de scores dans le temps, distribution de
trajectoires, top des outils en échec.

---

## 9. Taxonomie d'erreurs `[CLASS]`

Hérité de SDD_Pro (193 classes) : tout bloc ERROR porte un code `[CLASS]` dans son
`CAUSE:`, pour que hooks, boucles de reprise et tableaux de bord classent sans
interpréter du texte. SDD_Agents en porte **<!--sdda:count classes-->379<!--/sdda:count-->**, liste close régénérée depuis
les émetteurs réels par `sdda_admin/sync_error_registry.py` — écrire la liste à la
main la ferait dériver dans les deux sens (`rules/error-classification.md §6`).
Familles propres à SDD_Agents :

`[MISSION_*]` · `[CAP_*]` · `[TOPOLOGY_*]` · `[AGENT_*]` · `[TOOL_*]` ·
`[RETRIEVAL_*]` · `[MEMORY_*]` · `[PROMPT_*]` · `[EVAL_*]` · `[JUDGE_*]` ·
`[BUDGET_*]` · `[SAFETY_*]` · `[TRACE_*]` · `[API_*]`

**Une classe citée ici doit avoir un émetteur.** `sync_error_registry.py`
régénère le registre depuis les émetteurs **réels** — donc une classe qui ne
vit que dans ce document n'y entre jamais, et le registre se déclare « à jour »
sans elle. C'est ainsi que `[API_CONTRACT_DRIFT]` et `[API_ROUTE_UNBACKED]` ont
pu être annoncés bloquants au §4 pendant tout un lot sans qu'aucun script ne
les émette. Le contrôle `errors.documented` de `framework_smoke.py` ferme cette
porte : toute classe citée dans la prose normative (`.sdda/*.md`,
`.sdda/docs/*.md`) et émise par rien est un **échec**, pas un avertissement.

---

## 10. Ce que SDD_Agents ne fera pas

Déclaré d'entrée, pour que la promesse reste tenable :

- **Pas d'entraînement ni de fine-tuning.** Le framework compose des modèles
  existants ; il ne produit pas de poids.
- **Pas de garantie de correction du produit généré.** Il garantit que le produit
  a été *mesuré* contre des seuils déclarés, sur des jeux déclarés. Un seuil trop
  bas reste un seuil trop bas.
- **Pas d'hébergement ni d'exploitation.** Il produit du code, des evals et de la
  CI ; il ne fait pas tourner la production.
- **Pas de choix de modèle à votre place sur des critères qu'il ne mesure pas.**
  Les tiers sont déclarés, la résolution appartient au provider.
