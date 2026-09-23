# SDD_Agents — Spec Driven Development pour applications Agentic

> 🇬🇧 [English version](README.md) — la page anglaise est la page d'entrée par défaut ;
> cette version française en est la traduction. Le contenu technique (identifiants,
> classes `[CLASS]`, chemins, flags) est identique dans les deux.

**Le framework qui refuse de livrer un agent que personne n'a évalué.**

SDD_Agents transforme une **spécification** en **application agentic testée** :
agents, outils, RAG, orchestration, mémoire, code, évaluations — dans le langage
et le framework déclarés dans un seul fichier.

Frère de **SDD_Pro**, dont il hérite la méthodologie
(source-first, gates déterministes, ownership, invariants, catalogue de stacks,
abstraction harness/provider) — mais **pas** sa cible. SDD_Pro construit des
applications classiques (mobile, desktop, front/back). SDD_Agents construit des
systèmes d'agents LLM.

---

## L'échelle, en une image

```
MISSION            spécification métier versionnée (objectif chiffré, budget, ground truth)
  └─ CAP           capabilities — 1 compétence discrète, AC *évaluable* (métrique + seuil + dataset)
       └─ TOPOLOGY agents, outils, pattern d'orchestration, budget estimé
            └─ CONTRACTS   agent / tool / retrieval / memory — neutres framework
                 └─ CODE   généré dans la stack déclarée
                      └─ EVAL + TESTS   9 niveaux, du schéma de tool à l'adversarial
                           └─ VERDICT   vert / jaune / rouge, mesuré, jamais affirmé
```

### Ce que « la stack déclarée » couvre aujourd'hui

L'IR est conçue multi-langage et le vérifie : `validate_ir.py` refuse tout nom
d'API de framework dans un contrat, y compris `spring-ai`, `langchain4j` et
`vercel-ai-sdk`. Mais **un générateur n'existe que là où les fiches de stack
existent**, et le catalogue en couvre <!--sdda:count stacks-->45<!--/sdda:count--> sur les 99 lignes que `STACK.md`
propose. L'écart est annoncé ligne par ligne — `(fiche absente)` — plutôt que
sous-entendu.

| | Langage | Framework | RAG · vector · rerank | Serving | État |
|---|---|---|---|---|---|
| **Python** | ✅ | LangChain · LangGraph | ✅ hybrid · pgvector · rerank | cli · fastapi-sse · batch | utilisable |
| **.NET** | ✅ | Microsoft Agent Framework | ❌ **aucune fiche** | aspnet-minimal | **sans RAG** |
| **TypeScript** | ✅ fiche | LangGraph.js | ❌ | cli-node · express · nestjs (backend) | **fiches seulement** — aucune combo bootstrap (eval/observability sont `[python]`), pas de générateur de squelette |
| **Kotlin** | ✅ fiche | Spring AI | ❌ | cli-kotlin · spring-boot (backend) | **fiches seulement** — même réserve ; pins Maven non vérifiés |
| **Java** | ❌ | — | ❌ | ❌ | non planifié |

Deux familles du catalogue sont héritées de SDD_Pro depuis le 2026-09-23 :
`archi/` (mvc · ddd · microservice — l'architecture de la **coquille**
applicative, sélectionnée par `## Active Architecture Pattern`) et `backend/`
(python-fastapi · node-express · nestjs · kotlin-spring-boot · dotnet-minimalapi
— la maison HTTP autour de la surface, active seulement si
`DeliverableType: backend-api`). Réécrites pour l'agentic, pas copiées : pas
d'ORM, pas d'entité, le « Model » est dérivé de l'IR.

**.NET ne peut pas faire de RAG aujourd'hui.** Toute la chaîne de retrieval
(`rag/hybrid.md`, `vectorstore/pgvector.md`, `dataaccess/*`) est écrite en
Python et le déclare (`Languages: python`). Ce n'est pas une omission de
documentation : `preflight_stack_combo` **refuse** la combinaison
(`[STACK_LANGUAGE_MISMATCH]`) au lieu de laisser un générateur .NET recevoir du
`psycopg` comme référence et improviser une traduction. Le RAG .NET est au
Lot 7 de la [ROADMAP](.sdda/docs/ROADMAP.md).

Rien ici n'est *validé* : `frameworkStatus: design-phase`, et tous les
composants sont `untested` tant qu'aucun run mesuré n'a eu lieu (Lot 6).

---

## Où vit votre travail — le workspace

Quatre entrées, quatre natures. L'arbre a porté douze répertoires au même
niveau, sans que rien ne dise que `caps/` se relit en revue quand `traces/` se
supprime sans perte.

```
workspace/
├── stack/     ce qu'on CONFIGURE — STACK.md, seul, versionné (noms de variables ; valeurs dans src/{App}/.env)
├── feats/     ce qu'on SPÉCIFIE  — Markdown seul : briefs · missions · caps · roster + topology · contracts · decisions (ADR)
├── assets/    ce qu'on DÉPOSE    — fichiers statiques (exports JSON/CSV, corpus) : la racine des stores `kind: local` (l'assets/ de SDD_Pro)
├── src/       ce qu'on PRODUIT   — l'application générée, prompts et schémas figés compris
├── proof/     ce qui JUGE        — seed (votre vérité terrain) · datasets · suites · baselines · calibration
└── .sys/      état interne et sorties de run — IR, validation, rapports, traces (régénérable)
```

Vous écrivez trois choses : `stack/STACK.md` (les choix techniques — langage,
framework, pattern, sources de données, URL d'API, serveurs MCP ; les valeurs des
secrets vont dans un `src/{App}/.env` gitignoré, avec l'application qui les
consomme — le harnais de construction ne le lit jamais), des fichiers Markdown sous `feats/` (ce
que le système doit faire), et votre vérité terrain sous `proof/seed/`. Tout le
reste est produit. **Aucun agent `dev-*` n'écrit jamais sous
`proof/`** : l'agent qui écrit le code ne peut toucher ni au jeu qui le note, ni
à la référence contre laquelle sa régression est mesurée. C'est la seule
frontière du framework sans exception, et elle est tenue au runtime par le hook
d'ownership, pas par convention.

Un workspace plus ancien passe à cet arbre par
`python .sdda/sdda.py migrate-workspace`, qui déplace le contenu au lieu de
créer le nouvel arbre à côté de l'ancien.

---

## L'inversion structurelle

SDD_Pro construit **backend-first** : `dev-backend` (toutes les US) → **API Gate**
→ `dev-frontend`. La gate élimine la dérive de contrat entre les deux couches.

SDD_Agents construit **bottom-up, une gate par couche** :

```
TOOLS + RETRIEVAL + DATA ACCESS          ← les couches qui touchent le monde réel
   ├─ TOOL GATE       contrats verts + connectivité live vérifiée
   └─ RETRIEVAL GATE  recall@k / groundedness / citations sur golden set
        └─ AGENTS (isolés, outils mockés)
             └─ AGENT GATE       chaque CAP AC évaluée sur son agent seul
                  └─ ORCHESTRATION
                       └─ ORCH GATE   trajectoires, bornes de hops, coût/latence mesurés
                            └─ SAFETY GATE   injection, scope d'outils, secrets, PII
                                 └─ ACCEPTANCE GATE   objectif chiffré sur holdout
```

**Pourquoi** : sans ces gates, on débogue une « mauvaise orchestration » qui est en
fait un mauvais retriever, ou un « agent qui hallucine » qui est en fait un outil
dont le schéma ment. Chaque couche prouve qu'elle fonctionne avant que la suivante
s'appuie dessus.

---

## STACK.md — un fichier, toute l'architecture technique

Le fichier de configuration déclaratif lu par **tous** les agents à **chaque**
invocation. Changer de framework, de pattern d'orchestration, de type de RAG ou de
stratégie d'accès base = éditer une ligne, pas réécrire le code.

```markdown
## Active Language & Runtime
 - .sdda/stacks/lang/python.md

## Active Agent Framework
 - .sdda/stacks/framework/langgraph.md        # langchain seul ? +langgraph ? +langsmith ?

## Active Orchestration Pattern
 - .sdda/stacks/orchestration/supervisor.md   # single-agent | router | sequential | …

## Active RAG Pattern
 - .sdda/stacks/rag/hybrid.md                 # none | classic | agentic | self-rag | …

## Active Retrieval Stack
 - .sdda/stacks/vectorstore/pgvector.md
 - .sdda/stacks/embedding/voyage.md
VectorStoreConnection:                        # OÙ est l'index, et avec quelles clés.
  Mode: same-as-database                      # Distinct du bloc DB_* ci-dessous :
  Endpoint:                                   # celui-ci décrit la base MÉTIER,
  Collection:                                 # celui-là l'index VECTORIEL. Les
  ApiKeyEnv:                                  # confondre ne se voit pas tant que
  Dimensions: 1024                            # le store est pgvector — il vit alors
                                              # dans la même base, par coïncidence.

## Active Reranker
 - .sdda/stacks/rerank/none.md                # none | cohere-rerank | bge-reranker-local
                                              # Se décide sur une mesure : recall@25 bon
                                              # + nDCG@5 médiocre = c'est le cas du reranker.

## Active Data Access
 - .sdda/stacks/dataaccess/view-per-agent.md  # view-per-agent | text-to-sql | repository-tools | …
```

Chaque ligne activée doit désigner une fiche **qui existe** et **qui parle le
langage actif** — sinon `preflight_stack_combo` refuse le spawn
(`[STACK_COMBO_UNLOADABLE]`, `[STACK_LANGUAGE_MISMATCH]`). Une ligne activée
pour une fiche absente ne charge rien : l'agent travaille sans mapping de
couches, sans idiomes et sans `.libs.json`, donc il invente.

Spécification complète : [.sdda/templates/STACK.md.template](.sdda/templates/STACK.md.template).

---

## Ce qui change vraiment par rapport à une app classique

| Application classique | Application agentic |
|---|---|
| `assertEquals(f(x), y)` | distribution de sorties → **eval sur k runs**, taux de réussite + variance |
| La spec produit du code | **Une partie de la spec *est* le prompt** — elle doit survivre à la compilation |
| La perf est un sujet ops | **Coût et latence sont fonctionnels** — 7 hops à $0.40/appel tuent le produit |
| Stack trace | **Trace de spans** (tour d'agent, appel outil, retrieval, tokens, coût) |
| Une régression casse un test | Une régression **baisse un score** — il faut une baseline versionnée |
| La surface d'attaque, c'est l'API | La surface d'attaque, c'est **chaque document retrouvé** (injection indirecte) |
| Refactorer = le compilateur t'attrape | Éditer un prompt = **rien ne t'attrape** → hash pinning + re-eval obligatoire |

---

## Documentation de conception

| Document | Objet |
|---|---|
| [PHILOSOPHY.md](.sdda/PHILOSOPHY.md) | Les 12 principes fondateurs |
| [ARCHITECTURE.md](.sdda/ARCHITECTURE.md) | Arborescence, pipeline, 9 gates, abstraction harness/provider |
| [SDD-PRO-INHERITANCE.md](.sdda/docs/SDD-PRO-INHERITANCE.md) | Ce qu'on hérite, ce qu'on refuse, ce qu'on ajoute |
| [DOMAIN-MODEL.md](.sdda/docs/DOMAIN-MODEL.md) | Le vocabulaire clos : MISSION, CAP, AGENT, TOOL, RETRIEVER… |
| [AGENTIC-IR.md](.sdda/docs/AGENTIC-IR.md) | La représentation intermédiaire qui rend le multi-framework déterministe |
| [LIFECYCLE.md](.sdda/docs/LIFECYCLE.md) | Machine à états Draft → Approved, dérivée des gates |
| [AGENT-ROSTER.md](.sdda/docs/AGENT-ROSTER.md) | Les <!--sdda:count agents-->23<!--/sdda:count--> Developer Agents et leur orchestration interne |
| [ORCHESTRATION-PATTERNS.md](.sdda/docs/ORCHESTRATION-PATTERNS.md) | Catalogue + matrice de sélection |
| [RAG-PATTERNS.md](.sdda/docs/RAG-PATTERNS.md) | Catalogue + métriques de gate |
| [MEMORY-PATTERNS.md](.sdda/docs/MEMORY-PATTERNS.md) | Portées, coûts, et la mémoire comme surface d'attaque persistante |
| [DATA-ACCESS.md](.sdda/docs/DATA-ACCESS.md) | Stratégies d'accès base pour agents |
| [MULTI-HARNESS.md](.sdda/docs/MULTI-HARNESS.md) | Compilation vers Claude Code / Codex / Gemini CLI |
| [TESTING-AND-EVAL.md](.sdda/docs/TESTING-AND-EVAL.md) | La pyramide L0→L9 |
| [INVARIANTS.yml](.sdda/INVARIANTS.yml) | Les <!--sdda:count invariants-->21<!--/sdda:count--> contrats porteurs + leur enforcer |
| [ROADMAP.md](.sdda/docs/ROADMAP.md) | Ordre de construction + le MVP |
| [PLANNED-SCRIPTS.md](.sdda/docs/PLANNED-SCRIPTS.md) | Le backlog déterministe, généré — qui réclame quoi |

**Templates de spécification** : [mission](.sdda/templates/mission.template.md) ·
[capability](.sdda/templates/capability.template.md) ·
[topology](.sdda/templates/topology.template.md) ·
[agent-contract](.sdda/templates/agent-contract.template.md) ·
[tool-contract](.sdda/templates/tool-contract.template.md) ·
[STACK.md](.sdda/templates/STACK.md.template)

---

## Vérifier l'état réel

Le framework porte son propre dispositif anti-pourrissement — celui qui refuse
qu'une règle écrite ne soit plus appliquée par rien :

```bash
python .sdda/sdda.py framework-smoke
```

Il vérifie : parité agents ↔ bornes de tier, invariants ↔ enforcers sur disque,
réciprocité des classes d'erreur, digests à jour, références internes, et
**l'honnêteté du catalogue** — aucune fiche de stack ne peut se déclarer validée
sans qu'un run l'ait mesurée.

```bash
python .sdda/sdda.py sync-error-registry --check   # taxonomie
python .sdda/sdda.py sync-digests --check          # digests par agent
python -m pytest .sdda/python/tests/ -q                         # couche déterministe
```

---

## Statut

**Lots 1 et 2 écrits.** Le socle déterministe et le moteur d'évaluation
existent et sont testés (<!--sdda:count tests-->1147<!--/sdda:count--> fonctions de test) :

- `bootstrap.py` de bout en bout ; G0 (mission), G1 (capabilities) et G2
  (topologie, IR, budget) **refusent** effectivement une spécification
  défectueuse — un critère d'acceptation non mesurable sort
  `[AC_NOT_EVALUABLE]` en exit 1 ;
- `ir_compiler.py` + `validate_ir.py` et leurs contrôles, `estimate_budget.py`,
  `compute_status.py` ;
- `eval_runner.py` (k runs, variance, verdict trois couleurs), les graders,
  `calibrate_judge.py` (κ de Cohen), l'épinglage et la détection de régression ;
- `run_retrieval_eval.py` (G4 — recall@k, nDCG, context precision, citations,
  **sans agent**) et `run_adversarial_suite.py` (G7 — couverture des familles
  d'attaque et rejeu du set versionné).

**Aucun de ces scripts n'appelle un LLM** : ils reçoivent un exécuteur injecté
ou rejouent des runs enregistrés. C'est délibéré — le framework sait aujourd'hui
*juger* une spécification et une mesure, il ne sait pas encore en *produire*.

**Lot 3 en cours** — la jonction entre les fiches d'agent et le pipeline :

- `sdda_state.py` — journal des runs : identité, phases, reprise (`--resume`),
  bypasses. Il ne calcule aucun état d'artefact : ça reste `compute_status.py`,
  et deux vérités sur le même fait, c'est zéro vérité vérifiable ;
- `context_pack.py` — `loader.yml` rendu exécutable : ce que chaque agent va
  réellement lire, par couche de cache, en octets, confronté à son
  `budget_bytes`. Un dépassement ou un pack périmé **refuse le spawn**.
  `bootstrap.py` construit les packs à la création du workspace ;
- `spawn_brief.py` — le prompt d'invocation n'est plus recopié dans chaque
  commande : il est assemblé. Fiche + hash, tier résolu contre
  `agent-bounds.yaml` (`tier_floor` ne cède pas), contexte résolu, périmètre
  d'écriture placeholders résolus, et les **faits injectés** — ce qu'un agent
  doit connaître mais que l'ownership lui interdit de lire.

**La première fiche d'agent a été exécutée.** `po-elicitor` a produit une
MISSION à partir d'un brief client, en mode non interactif : elle a laissé 19
`<à préciser>`, refusé de convertir 0,05 EUR en USD faute de parité déclarée,
refusé d'inventer la Failure Policy que le brief disait indécise — et G0 a
bloqué, comme prévu. La fiche tient comme prompt.

Ce que la mesure et cette exécution ont trouvé, et qui est corrigé :

- six agents `dev-*` lisaient un pack que rien ne savait construire ;
- le budget de `architect-rag` était intenable (171 Ko mesurés contre 150 Ko
  déclarés — dont 75 Ko pour le seul `patterns.registry.json`) ;
- `validate_mission`, `validate_cap` et `validate_topology` n'acceptaient pas le
  `--mission {n}` que les commandes écrivent depuis le premier jour ;
- `po-elicitor` devait remplir `## Required Stack` sans avoir le droit de
  lire `STACK.md` : la fiche était intenable jusqu'à ce que le brief lui injecte
  les stacks actives ;
- `{m}` désignait deux choses dans `loader.yml` (l'index de CAP et « une autre
  MISSION »), ce qui interdisait à `po-capabilities` de lire la sienne.

Reste ouvert, et non résolu : le budget s'exprime en USD (`CostPerRunTargetUsd`)
alors que les briefs arrivent en euros, sans parité déclarée nulle part.

**Lot 4 commencé — les gates de la génération.** `validate_tool_contract.py`
écrit la part `contracts` de la TOOL GATE (G3) : méta-schéma des schémas
d'outil, `required` ⊆ `properties`, cohérence de la stratégie de sûreté,
confrontation contrat ↔ IR ↔ **code**, enveloppe DB. `check_ir_freshness.py`
refuse de générer depuis un IR qui ne décrit plus ses sources.

Deux défauts de fond trouvés en les câblant :

- `compute_status` cherche `G3-{outil}`, `G4-{retriever}`, `G5-{cap}`, alors
  qu'`eval_runner` écrivait tout sous `{mission}`. Les rapports étaient verts
  dans des fichiers que la machine à états n'ouvrait jamais : l'état `Tested`
  était **inatteignable**. La granularité est maintenant celle du LIFECYCLE ;
- `compiledFrom` ne hashait pas les contrats. Éditer un schéma d'outil laissait
  l'IR se déclarer frais tout en décrivant autre chose — exactement le cas que
  `/sdda-topology --recompile-only` existe pour traiter. `contractHashes` est
  entré dans l'IR et dans son schéma.

**L'API GATE existe enfin.** `validate_api_contract.py` écrit la part `api` de
G6 : schémas publiés confrontés à l'`inputSchema`/`outputSchema` de l'IR dans
les deux sens, routes confrontées au contrat de la surface active, statuts HTTP
confrontés à la table `[CLASS]` → code. Tant qu'aucun `openapi.json` n'est
publié, la part est **non applicable** : elle n'écrit aucun rapport, donc
n'accorde aucun vert.

Ce qu'un audit du dispositif anti-pourrissement a trouvé, et qui est corrigé :

- `[API_CONTRACT_DRIFT]` et `[API_ROUTE_UNBACKED]` étaient **annoncés bloquants
  dans `ARCHITECTURE.md §4` et émis par rien**. Le registre canonique se disait
  « à jour » parce qu'il est régénéré depuis les émetteurs *réels* : une classe
  qui ne vit que dans la prose n'y entre jamais, et son absence ne se voit pas.
  `framework_smoke.py` porte désormais `errors.documented`, qui **échoue** sur
  toute classe citée dans la prose normative et émise par personne. Même
  histoire pour `[REFLECTION_SELF_GRADING]`, anti-pattern « critique = rédacteur »
  refusé par la TOPOLOGY GATE — sur le papier — depuis le premier jour ;
- le template de contrat d'agent numérotait `## 7. Règles` puis `## 7. Retrievers`,
  sans `## 6` : cinq fiches lisaient `§12 handoffs` là où le template écrivait
  `§13`, et `lint_prompts.py` cherchait un `## 6. Règles` qui n'existait pas.
  Deux contrôles neufs ferment la porte — `templates.numbering` (suite contiguë,
  sans doublon) et `refs.sections` (tout `§n` cité à côté d'un artefact désigne
  une section qui existe) ;
- **le mécanisme skills/rules ne pouvait pas fonctionner de bout en bout** :
  `lint_prompts.py` cherche `## Compétences` et `## Règles` en H2, quand
  `prompt.template.md` écrivait ses titres en H1 et ne portait ni l'une ni
  l'autre. Les trois définitions divergentes de la structure d'un prompt
  (règle, template, fiche `dev-prompt`) sont alignées sur une seule, et
  `prompt-authoring.md §2` en est la SSoT ;
- le trim par rôle du contexte, annoncé en commentaire dans `loader.yml`,
  existe : `pack_sources` accepte `#families=…`. `architect-rag` reçoit les deux
  familles du registre qu'il décide au lieu des six du catalogue — pack de
  161 Ko à 107 Ko, budget de 92 % à 71 %, **sans relever le plafond**. Les
  retraits sont déclarés au manifeste du pack, pour qu'une famille absente ne se
  lise pas comme une famille inexistante.

**L'IR est scindé en `intent` / `binding`** — avant le Lot 4, délibérément.

`store: "pgvector"` et `embeddingModel: "voyage-3-large"` étaient des champs
**obligatoires** de l'IR, alors que la même décision vivait déjà dans
`STACK.md ## Active Retrieval Stack`. Deux vérités sur le même fait, que rien ne
confrontait — et le contrôle de neutralité ne les voyait pas, parce qu'il ne
cherchait que des noms d'**API** de framework. Un générateur C# lisant
`pgvector` serait allé chercher une fiche qui n'existe pas dans son runtime.

L'IR porte désormais deux branches : l'**intention** (`pattern`, `topK`,
`citationMode`, `identityFilter`, `gateThresholds`, `envelope`, `exposedTo`) et
le **`binding`** (`store`, `embeddingModel`, `chunk`, `hybridWeights`,
`rerank`, `strategy`). Trois mécanismes la tiennent :

- `[INFRA_LEAK_IN_INTENT]` — tout nom de composant hors de `binding` est refusé ;
- `[RETRIEVAL_BINDING_MISMATCH]` — le `binding` est **réconcilié** avec les
  stacks actives à la compilation, sur la *famille* (`voyage` couvre
  `voyage-3-large`) ; la version, elle, reste épinglée par `indexHash` ;
- un test qui simule le **second générateur** : il dérive un plan d'appel
  complet sans jamais lire `binding`, et ce plan est identique pour `pgvector`
  et pour n'importe quel autre store.

La ROADMAP prévoyait d'apprendre l'échec éventuel de l'IR au Lot 7, sur le
second générateur. Le faire maintenant coûte un schéma, un compilateur et un
validateur ; le faire après le Lot 4 aurait coûté six générateurs déjà écrits
contre un contrat qu'on savait faux. `memory.longTermStore` nomme lui aussi un
composant et reste hors périmètre : sa propre scission n'est pas faite, et le
contrôle le dit plutôt que de le taire.

**Lot d'audit — le pipeline ne pouvait pas aboutir une seule fois, et cinq
contrôles étaient inertes.** Un audit complet, source par source, puis chaque
constat corrigé. Ce qu'il a trouvé :

- `ir_compiler` exigeait le holdout que `qa-evals` ne produit que trois phases
  plus loin : aucune mission neuve ne franchissait G2. L'exigence est déplacée
  dans `validate_datasets` (G8), où elle est actionnable, et le holdout est
  devenu une source compilée pour que son arrivée périme l'IR ;
- **aucune suite L9 n'était jamais compilée** : la part `acceptance` de G8
  n'avait aucune exécution capable de la rendre verte. Elle naît désormais de
  la ligne `Grader:` du `## Quantified Goal` de la mission ;
- neuf options CLI étaient citées par les prompts et n'existaient dans aucun
  script (`--pre`, `--static`, `--isolated`, `--dataset`, `--require`,
  `--min-items`, …). argparse répondait par un `usage:` au lieu d'un bloc
  `ERROR/CAUSE/FIX`, et le modèle orchestrateur concluait que le contrôle
  « n'avait rien dit ». Un scanner neuf, `command-flags`, résout chaque option
  citée contre le parseur réel et tourne dans `framework-smoke` sous
  `refs.flags` ;
- cinq contrôles se déclaraient actifs et ne s'exécutaient pas : les hooks
  d'ownership lisaient l'identité du sous-agent au mauvais endroit du payload
  (toute écriture de sous-agent passait) ; les hooks de spawn ne matchaient que
  `Task`, jamais `Agent` ; les façades portaient `model_tier` mais aucune clé
  `model:` (les 22 agents héritaient du modèle parent) ; `MaxCostPerRun` n'avait
  aucun appelant qui alimente le cumul ; les bornes du `build_loop` ne vivaient
  que dans le prompt qu'elles devaient borner ;
- les commandes de hook étaient relatives au répertoire courant : un `cd`
  désarmait les quatorze. Elles sont ancrées sur `$CLAUDE_PROJECT_DIR`.

Ce qu'il a livré :

- **un squelette runtime Python** (`.sdda/templates/runtime/python/app/`,
  18 fichiers) : point d'entrée, client LLM par tier, bornes en code, traçage
  OTel-GenAI, boucle d'orchestration, surface console, et les deux exécuteurs
  d'évaluation dont G5/G6/G8 avaient besoin.
  `python .sdda/sdda.py gen-app-skeleton --write` le matérialise ; la console
  générée démarre, résout ses tiers, rend un code de sortie par classe d'erreur
  et émet une trace que le lecteur du framework parse ;
- les items d'évaluation mesurés en parallèle (`EvalMaxParallel`, ordre
  préservé) ; les paquets de contexte tranchés sur les fiches de stack
  *actives* — l'agent le plus saturé passe de 93 % à 61 % de son budget sans
  relever aucun plafond ;
- le workspace à quatre entrées (`feats/ · stack/ · src/ · proof/ · .sys/`) et
  une migration qui déplace le contenu existant.

Maturité honnête après ce lot : la couche déterministe est en bêta ; la couche
génération est un squelette plus six prompts d'agents qui **n'ont toujours
jamais été exécutés de bout en bout**. Rien de ce qui précède ne change le
tableau des langages : Python est le seul runtime doté d'un squelette, .NET n'a
pas de chaîne de retrieval, TypeScript et Java n'ont aucune fiche.

Reste à faire : les six agents générateurs et leurs stacks (Lot 4), puis la
revue (Lot 5). Ordre et raisons : [ROADMAP.md](.sdda/docs/ROADMAP.md).

**Aucune combinaison de stack n'est annoncée validée**, parce qu'aucune n'a
encore été mesurée par un run réel. C1 est la cible du MVP, en `design-phase`.
Annoncer autre chose serait précisément le faux vert que ce framework existe
pour empêcher.
