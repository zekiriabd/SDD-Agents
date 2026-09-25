# SDD_Agents — Spec Driven Development pour applications Agentic

> 🇬🇧 [English version](README.md) — la page anglaise est la page d'entrée par défaut ;
> cette version française en est la traduction. Le contenu technique (identifiants,
> classes `[CLASS]`, chemins, flags) est identique dans les deux. Les prompts, agents,
> commandes et règles sous `.sdda/` sont écrits en français.

**Le framework qui refuse de livrer un agent que personne n'a évalué.**

SDD_Agents transforme une **spécification** en **application agentic testée** :
agents, outils, RAG, orchestration, mémoire, code, évaluations — dans le langage
et le framework déclarés dans un seul fichier.

Frère de **SDD_Pro**, dont il hérite la méthodologie
(source-first, gates déterministes, ownership, invariants, catalogue de stacks,
abstraction harness/provider) — mais **pas** sa cible. SDD_Pro construit des
applications classiques (mobile, desktop, front/back). SDD_Agents construit des
systèmes d'agents LLM.

> **Statut : phase de conception.** `frameworkStatus: design-phase` dans
> [`registry/compatibility.matrix.json`](.sdda/registry/compatibility.matrix.json) :
> chaque gate, chaque script et chaque fiche d'agent existe, et **aucune
> combinaison de stack n'a encore été validée de bout en bout**, C1 comprise.
> Voir [Statut](#statut).

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
| **Python** | ✅ | LangChain · LangGraph | ✅ hybrid · pgvector · rerank | cli · fastapi-sse · batch | utilisable — seul runtime doté d'un générateur de squelette |
| **.NET** | ✅ | Microsoft Agent Framework | ❌ **aucune fiche** | cli-dotnet · aspnet-minimal | **sans RAG** |
| **TypeScript** | ✅ fiche | LangGraph.js | ❌ | cli-node · express · nestjs (backend) | **fiches seulement** — aucune combo bootstrap (eval/observability sont `[python]`), pas de générateur de squelette |
| **Kotlin** | ✅ fiche | Spring AI | ❌ | cli-kotlin · spring-boot (backend) | **fiches seulement** — même réserve ; pins Maven non vérifiés |
| **Java** | ❌ | — | ❌ | ❌ | **non testé, fiche absente** — `lang/java.md` et `serving/cli-java.md` sont annoncées dans `STACK.md.template` sans exister sur disque ; la matrice dit `java: untested` |

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
Lot 7 de la [ROADMAP](.sdda/docs/ROADMAP.fr.md).

Rien ici n'est *validé* : `frameworkStatus: design-phase`, et tous les
composants sont `untested` tant qu'aucun run mesuré n'a eu lieu (Lot 6).

### Quel harnais exécute la construction

| Harnais | Statut | Gates bloquantes au runtime |
|---|---|---|
| **Claude Code** | **supporté** — le harnais de référence | oui — les hooks de `.claude/settings.json` refusent l'appel d'outil |
| **Codex CLI** | **expérimental** — compilé vers `.codex/`, jamais validé par un run de conformance | **non** — reportées au CI et aux scripts déterministes |
| **Gemini CLI** | **expérimental** — compilé vers `.gemini/`, même réserve | **non** — idem |
| **Antigravity** | **planifié** — partage l'adaptateur et la façade `.gemini/` de Gemini CLI, compilé seulement sur demande explicite (`--harness antigravity`) | **non** — idem |

Sous Codex ou Gemini CLI, rien n'empêche au moment de l'action une écriture
hors ownership ou un agent câblé avant sa TOOL GATE ; le CI la rattrape plus
tard. Le wrapper de spawn dont ces harnais auraient besoin est planifié, pas
écrit. Les `AGENTS.md` et `GEMINI.md` de la racine sont des pointeurs générés
vers les façades — c'est eux que Codex et Gemini CLI lisent vraiment. Les
modèles de construction viennent du harnais (`capability-matrix.yml`,
`tier_models`), jamais de `STACK.md`. Détail :
[MULTI-HARNESS.fr.md](.sdda/docs/MULTI-HARNESS.fr.md).

---

## Démarrage rapide

> **Phase de conception.** `frameworkStatus: design-phase` dans
> [`registry/compatibility.matrix.json`](.sdda/registry/compatibility.matrix.json) :
> **aucune combinaison de stack n'est validée de bout en bout**, C1 comprise.
> Les étapes ci-dessous font tourner le pipeline ; elles ne promettent pas un
> verdict vert. Harnais : Claude Code (Codex et Gemini CLI sont expérimentaux,
> voir plus haut).

1. **Cloner** — Python 3.11+ seulement, rien à installer :
   `git clone https://github.com/zekiriabd/SDD-Agents.git && cd SDD-Agents`
2. **Amorcer** — `python bootstrap.py` (interactif), ou
   `python bootstrap.py --combo c1 --app-name SupportDesk --auto`. Il écrit
   `workspace/stack/STACK.md`, `workspace/assets/.env` et l'arborescence du
   workspace, puis lance un smoke. Aucun appel LLM.
3. **Remplir `workspace/stack/STACK.md`** — surtout `## Project Config` :
   `CostPerRunTargetUsd` et `LatencyP95TargetMs` n'ont pas de défaut côté
   framework, et la MISSION GATE les exige. Des noms de variables seulement,
   jamais une valeur de secret. Les valeurs sont validées contre le schéma au
   `smoke-check` et au preflight.
4. **Secrets** — les valeurs dans `workspace/assets/.env` (`LLM_API_KEY`,
   `DB_*` si base). Rien d'autre à lancer : l'étape qui crée l'application
   (`dev-backend`, PHASE 3.0) y copie le fichier. Aucun agent ne lit l'un ou
   l'autre fichier.
5. **Vos entrées** — le brief en `workspace/feats/1-{Name}.md` (Markdown
   seulement), vos données sous `workspace/assets/`, votre vérité terrain
   (scénarios annotés, labels) sous `workspace/seed/`.
6. **Ouvrir Claude Code à la racine du dépôt**, puis éliciter la MISSION 1 depuis
   le brief : `/sdda-mission {Name} --from-brief workspace/feats/1-{Name}.md`.
   Tout `<à préciser>` laissé ouvert bloque G0 — y répondre, ou éditer la MISSION.
7. **Lancer le pipeline** — `/sdda-full 1`. Il s'arrête proprement sur toute
   décision qui vous appartient, à commencer par le roster : `/sdda-roster 1`
   écrit un `workspace/feats/1-roster.md` pré-rempli, vous le complétez, puis
   `/sdda-full 1 --resume`.
8. **Lire l'état** — `/sdda-status 1` (`--gates` pour le détail contrôle par
   contrôle). L'état est dérivé des rapports de gate, jamais déclaré.

`/sdda-help` dit quoi faire ensuite depuis l'état dérivé.

---

## Où vit votre travail — le workspace

Ce que vous fournissez est à la racine ; ce que le framework produit est sous
`pipeline/`. Jusqu'à la v5, `feats/` mêlait votre brief et votre roster aux
MISSION, CAPs et contrats que les agents génèrent, et `proof/` mêlait votre
vérité terrain aux jeux d'évaluation : en ouvrant l'un ou l'autre, impossible
de savoir ce qu'il vous revenait de remplir.

```
workspace/
│ ── ce que VOUS fournissez ────────────────────────────────────────────────
├── stack/     STACK.md, seul, versionné — les choix techniques, des noms de variables seulement
├── feats/     vos specs, Markdown seul, à plat — {n}-{Name}.md (le brief) · {n}-roster.md (le roster)
├── assets/    vos données (racine des stores `kind: local`) et .env — les VALEURS des secrets de l'app
├── seed/      votre vérité terrain — scénarios annotés, labels
│ ── ce que le FRAMEWORK produit ───────────────────────────────────────────
├── pipeline/  missions · caps · topology · contracts · decisions (ADR) · datasets · suites · baselines · calibration
├── src/       l'application générée, prompts et schémas figés compris ; .env copié depuis assets/
└── .sys/      état interne et sorties de run — IR, validation, rapports, traces (régénérable)
```

Vous fournissez quatre choses : `stack/STACK.md` (langage, framework, pattern,
sources de données, URL d'API, serveurs MCP), des fichiers Markdown sous
`feats/` (ce que le système doit faire, puis le roster qui dit comment), vos
données et votre `.env` sous `assets/`, et votre vérité terrain sous `seed/`.
L'étape qui crée l'application copie `assets/.env` vers `src/{App}/.env`
sans LLM : l'application générée le lit, le harnais de construction jamais, et
aucun agent ne peut le lire — les hooks de lecture refusent
`[SECRET_READ_FORBIDDEN]`. **Aucun agent `dev-*` n'écrit jamais sous
`pipeline/datasets`, `suites`, `baselines` ni `calibration`** : l'agent qui écrit
le code ne peut toucher ni au jeu qui le note, ni à la référence contre laquelle
sa régression est mesurée. C'est la seule frontière du framework sans
exception, et elle est tenue au runtime par le hook d'ownership, pas par
convention.

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
             └─ AGENT GATE       chaque CAP AC évaluée sur son agent seul, juges calibrés, prompts épinglés
                  └─ ORCHESTRATION
                       └─ ORCH GATE   trajectoires, bornes de hops, coût/latence mesurés, dérive API et framework
                            └─ SAFETY GATE   injection, jeu adversarial joué en live, scope d'outils, secrets, PII
                                 └─ ACCEPTANCE GATE   objectif chiffré sur holdout
```

**Pourquoi** : sans ces gates, on débogue une « mauvaise orchestration » qui est en
fait un mauvais retriever, ou un « agent qui hallucine » qui est en fait un outil
dont le schéma ment. Chaque couche prouve qu'elle fonctionne avant que la suivante
s'appuie dessus.

En amont, la TOPOLOGY GATE (G2) tranche ce qui doit l'être avant une ligne de
code : l'IR, le budget estimé, le packaging, la complétude du roster déclaré,
et — par `registry/adr-requirements.yml` — toute décision qui contredit un
défaut sûr et exige donc un ADR accepté (`[ADR_MISSING]`). Les neuf gates et
leurs parts : [ARCHITECTURE.fr.md §4](.sdda/ARCHITECTURE.fr.md).

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

Le fichier est gouverné, pas seulement lu. Chaque valeur est validée contre
`templates/project-config.schema.json` (`[CONFIG_VALUE_INVALID]`,
`[CONFIG_KEY_CONFLICT]`, `[CONFIG_KEY_MISPLACED]`), chaque clé du gabarit nomme
le script ou l'agent qui la lit, et les clés que personne ne lisait ont été
retirées. Une valeur que les parseurs acceptent mais que rien n'implémente est
refusée (`[STACK_VALUE_UNIMPLEMENTED]`). La combinaison active est cherchée par
sa signature dans `registry/compatibility.matrix.json` ; une combinaison non
listée est gouvernée par `StackComboCheck: strict|warn|off`
(`[STACK_COMBO_UNLISTED]`).

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

## Modèle de sécurité

Deux systèmes sont à protéger, et le framework les traite séparément : la
**construction** (des Developer Agents qui écrivent dans votre dépôt) et le
**produit** (l'application agentic qu'ils génèrent).

**Pendant la construction** — appliqué au runtime sous Claude Code, reporté au
CI ailleurs :

- **Ownership.** Chaque écriture est confrontée à `loader.yml` par des motifs
  lus segment par segment ; chaque instance de `dev-agent` est liée à son propre
  répertoire `agents/{agent}/` ; chaque vague d'écriture est encadrée par un
  instantané `audit-ownership` qui peut révoquer ce qu'une phase a écrit hors de
  sa zone. Aucun agent ne peut écrire un rapport de gate, ni par l'éditeur ni par
  le shell.
- **Secrets.** Aucun agent ne lit `assets/.env` ni `src/{App}/.env`, quelle que
  soit la graphie qui l'ouvre (`[SECRET_READ_FORBIDDEN]`), et
  `.claude/settings.json` y ajoute des `deny` natifs de lecture. `STACK.md`
  porte des noms de variables, jamais des valeurs (`[STACK_SECRET_IN_CLEAR]`).
- **Shell.** Le hook shell résout ce qu'une commande Bash ou PowerShell écrit —
  `bash -c`, `eval`, `$(…)`, `-EncodedCommand`, heredocs, chemins Windows — et
  refuse ce qu'il ne peut pas nommer sans l'exécuter (`[OWNERSHIP_SHELL_OPAQUE]`).
- **Des hooks qui tombent.** Avec `SDDA_HOOKS_STRICT=1` (la CI), un hook qui
  plante refuse au lieu d'autoriser (`[HOOK_FAILED]`), et `hooks-selfcheck`
  exécute réellement chaque hook câblé : un hook qui ne démarre pas se lirait
  sinon comme vert.

**Dans le produit** — mesuré par les gates, pas promis :

- **Garde-fous en code.** Le squelette runtime Python livre la détection
  d'injection, la rédaction des PII et la validation du schéma de sortie
  (`.sdda/templates/runtime/python/app/guardrails/`), branchées là où le texte
  entre et sort, et actives selon `## Active Guardrails`.
- **Attaqué avant d'être livré.** `qa-evals` part d'un jeu adversarial d'amorce
  écrit à la main (`.sdda/templates/datasets/adversarial-seed.jsonl`) ; G7 joue
  le jeu versionné en live contre la surface livrée, et chaque attaque réussie
  trouvée en revue devient un item permanent. Un rapport de reviewer obligatoire
  absent est `[SAFETY_REVIEW_REPORT_MISSING]`, jamais « zéro finding ».
- **Des juges qui ne se notent pas eux-mêmes.** Un modèle juge que le produit
  fait aussi tourner est refusé au preflight (`[JUDGE_SAME_AS_EVALUATED]`) ; un
  juge non calibré n'est qu'advisory, et une calibration rouge bloque G5.
- **Un défaut dangereux exige une décision.** Désactiver la vérification TLS,
  donner à un agent l'écriture sur une base, garder des PII brutes en trace ou
  exposer une surface réseau sans identité d'appelant exige chaque fois un ADR
  accepté — G2 reste rouge sans lui.

---

## Documentation de conception

La documentation est en anglais par défaut ; les jumeaux français portent le
suffixe `.fr.md`. Le hub [.sdda/docs/README.fr.md](.sdda/docs/README.fr.md)
liste chaque document et ses langues disponibles.

| Document | Objet |
|---|---|
| [PHILOSOPHY.fr.md](.sdda/PHILOSOPHY.fr.md) | Les 12 principes fondateurs |
| [ARCHITECTURE.fr.md](.sdda/ARCHITECTURE.fr.md) | Arborescence, pipeline, 9 gates, abstraction harness/provider |
| [SDD-PRO-INHERITANCE.fr.md](.sdda/docs/SDD-PRO-INHERITANCE.fr.md) | Ce qu'on hérite, ce qu'on refuse, ce qu'on ajoute |
| [DOMAIN-MODEL.fr.md](.sdda/docs/DOMAIN-MODEL.fr.md) | Le vocabulaire clos : MISSION, CAP, AGENT, TOOL, RETRIEVER… |
| [AGENTIC-IR.fr.md](.sdda/docs/AGENTIC-IR.fr.md) | La représentation intermédiaire qui rend le multi-framework déterministe |
| [LIFECYCLE.fr.md](.sdda/docs/LIFECYCLE.fr.md) | Machine à états Draft → Approved, dérivée des gates |
| [AGENT-ROSTER.fr.md](.sdda/docs/AGENT-ROSTER.fr.md) | Les <!--sdda:count agents-->24<!--/sdda:count--> Developer Agents et leur orchestration interne |
| [ORCHESTRATION-PATTERNS.fr.md](.sdda/docs/ORCHESTRATION-PATTERNS.fr.md) | Catalogue + matrice de sélection |
| [RAG-PATTERNS.fr.md](.sdda/docs/RAG-PATTERNS.fr.md) | Catalogue + métriques de gate |
| [MEMORY-PATTERNS.fr.md](.sdda/docs/MEMORY-PATTERNS.fr.md) | Portées, coûts, et la mémoire comme surface d'attaque persistante |
| [DATA-ACCESS.fr.md](.sdda/docs/DATA-ACCESS.fr.md) | Stratégies d'accès base pour agents |
| [DATA-SOURCES.fr.md](.sdda/docs/DATA-SOURCES.fr.md) | Sources de données hors base — registre, connecteurs, secrets |
| [MULTI-HARNESS.fr.md](.sdda/docs/MULTI-HARNESS.fr.md) | Compilation vers Claude Code / Codex / Gemini CLI |
| [TESTING-AND-EVAL.fr.md](.sdda/docs/TESTING-AND-EVAL.fr.md) | La pyramide L0→L9 |
| [INVARIANTS.yml](.sdda/INVARIANTS.yml) | Les <!--sdda:count invariants-->22<!--/sdda:count--> contrats porteurs + leur enforcer |
| [ROADMAP.fr.md](.sdda/docs/ROADMAP.fr.md) | Ordre de construction + le MVP |
| [PLANNED-SCRIPTS.fr.md](.sdda/docs/PLANNED-SCRIPTS.fr.md) | Le backlog déterministe, généré — qui réclame quoi |
| [python/README.fr.md](.sdda/python/README.fr.md) | La couche Python déterministe |
| [CHANGELOG.md](CHANGELOG.md) | Ce qui a changé, version par version (en anglais) |

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
réciprocité des classes d'erreur, classes citées en prose que rien n'émet,
digests et compteurs à jour, références internes, options CLI citées par les
prompts, parité des jumeaux anglais/français (`docs.parity`), et
**l'honnêteté du catalogue** — aucune fiche de stack ne peut se déclarer validée
sans qu'un run l'ait mesurée.

```bash
python .sdda/sdda.py sync-error-registry --check   # taxonomie
python .sdda/sdda.py sync-digests --check          # digests par agent
python .sdda/sdda.py sync-counters --check         # chiffres cités par la prose
python .sdda/sdda.py harness-build --check         # façades vs source
python .sdda/sdda.py hooks-selfcheck               # chaque hook câblé, exécuté
python -m pytest .sdda/python/tests/ -q                         # couche déterministe
```

---

## Statut

**Phase de conception.** <!--sdda:count agents-->24<!--/sdda:count--> Developer Agents,
<!--sdda:count commands-->11<!--/sdda:count--> commandes,
<!--sdda:count invariants-->22<!--/sdda:count--> invariants,
<!--sdda:count stacks-->45<!--/sdda:count--> fiches de stack,
<!--sdda:count classes-->439<!--/sdda:count--> classes d'erreur,
<!--sdda:count hooks-->15<!--/sdda:count--> hooks et
<!--sdda:count subcommands-->80<!--/sdda:count--> sous-commandes déterministes existent sur
disque et sont testés (<!--sdda:count tests-->1623<!--/sdda:count--> fonctions de test).
Aucun script cité par un prompt ne manque
([PLANNED-SCRIPTS.fr.md](.sdda/docs/PLANNED-SCRIPTS.fr.md) est vide). Ce qui
n'existe **pas** encore, c'est la preuve : aucun pipeline n'a tourné de bout en
bout sur un vrai produit, donc aucune combinaison n'est validée. C'est le Lot 6
de la [ROADMAP](.sdda/docs/ROADMAP.fr.md), et rien de ce qui suit ne le remplace.

### Le dernier lot — fermer ce qui était annoncé sans être tenu

- **Workflow.** Les contrats des outils de source sont générés en PHASE 2 avant
  l'IR, leur code en PHASE 3 ; la passe complète de `validate-topology` tourne
  avant `ir-compiler` et écrit seule la part `topology` de G2 ; une nouvelle
  étape 4.0 de pré-passe pose les types `shared/` et l'interface mémoire, gelés
  pendant les phases 4 et 5 ; `--resume` suit la lignée des runs et
  `BuildLoopMaxCostUsd` borne la boucle d'un item ; G1 ne se périme plus quand la
  PHASE 2 remplit `## Allocated To`.
- **Gates.** G2 gagne une part `adr` (`registry/adr-requirements.yml`, couverte
  seulement par un ADR accepté qui nomme clé et valeur) ; G5 bloque sur une
  calibration de juge rouge et épingle chaque hash de prompt
  (`[PROMPT_MISSING]`, `[PROMPT_HASH_MISMATCH]`) ; G6 gagne une part `framework`
  (`[FRAMEWORK_DRIFT]`) ; G7 joue le jeu adversarial en live et traite un
  rapport de reviewer absent comme une erreur.
- **Ownership et hooks.** Les motifs se lisent par segment, les recouvrements
  réels de zones sont détectés et résolus, les instances de `dev-agent` sont
  liées à leur répertoire, les phases sont auditées contre un instantané et
  révocables ; le hook shell a été réécrit et couvre PowerShell ; mode strict et
  `hooks-selfcheck`.
- **Gouvernance de STACK.md.** Valeurs validées contre le schéma, clés mortes
  retirées (les tiers de construction viennent désormais de
  `capability-matrix.yml`), combos cherchées dans la matrice, valeurs non
  implémentées refusées, juge ≠ modèle évalué.
- **Runtime.** Un juge LLM réel en stdlib (Anthropic, OpenAI, Gemini, Ollama),
  des tarifs lus dans les fiches providers, des garde-fous en code, un jeu
  adversarial d'amorce de 31 items, des traces écrites sous verrou exclusif.
- **Industrialisation.** `LICENSE` MIT, `CHANGELOG.md`, workflow de release
  déclenché par tag, Dependabot, actions épinglées par SHA, job `lint` (ruff +
  mypy), plancher de couverture, `AGENTS.md` / `GEMINI.md` à la racine ; dix
  scripts neufs (`corpus-profile`, `chunking-bench`, `validate-envelope`,
  `cost-report`, `trajectory-report`, `adversarial-target-check`,
  `promote-adversarial-findings`, `validate-adr`, `validate-framework`,
  `hooks-selfcheck`) ; chaque document a désormais une référence anglaise et un
  jumeau français.

Détail : [CHANGELOG.md](CHANGELOG.md).

### Comment on en est arrivé là

**Lots 1 et 2 — le socle déterministe et le moteur d'évaluation :**

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

Les scripts de gate n'appellent eux-mêmes aucun LLM : ils reçoivent un
exécuteur injecté ou rejouent des runs enregistrés, et seul le grader
`llm-judge` appelle un modèle. C'est délibéré — une gate qui jugerait avec un
modèle non épinglé ne serait pas déterministe.

**Lot 3 — la jonction entre les fiches d'agent et le pipeline :**

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

**Lot 4 — les gates de la génération.** `validate_tool_contract.py` écrit la
part `contracts` de la TOOL GATE (G3) : méta-schéma des schémas d'outil,
`required` ⊆ `properties`, cohérence de la stratégie de sûreté, confrontation
contrat ↔ IR ↔ **code**, enveloppe DB. `check_ir_freshness.py` refuse de générer
depuis un IR qui ne décrit plus ses sources. Deux défauts de fond ont été
trouvés en les câblant :

- `compute_status` cherchait `G3-{outil}`, `G4-{retriever}`, `G5-{cap}`, alors
  qu'`eval_runner` écrivait tout sous `{mission}`. Les rapports étaient verts
  dans des fichiers que la machine à états n'ouvrait jamais : l'état `Tested`
  était **inatteignable**. La granularité est maintenant celle du LIFECYCLE ;
- `compiledFrom` ne hashait pas les contrats. Éditer un schéma d'outil laissait
  l'IR se déclarer frais tout en décrivant autre chose — exactement le cas que
  `/sdda-topology --recompile-only` existe pour traiter. `contractHashes` est
  entré dans l'IR et dans son schéma.

**L'API GATE existe.** `validate_api_contract.py` écrit la part `api` de G6 :
schémas publiés confrontés à l'`inputSchema`/`outputSchema` de l'IR dans les
deux sens, routes confrontées au contrat de la surface active, statuts HTTP
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
`STACK.md ## Active Retrieval Stack` : deux vérités sur le même fait, que rien
ne confrontait. L'IR porte désormais l'**intention** (`pattern`, `topK`,
`citationMode`, `identityFilter`, `gateThresholds`, `envelope`, `exposedTo`) et
le **`binding`** (`store`, `embeddingModel`, `chunk`, `hybridWeights`,
`rerank`, `strategy`), tenus par trois mécanismes :

- `[INFRA_LEAK_IN_INTENT]` — tout nom de composant hors de `binding` est refusé ;
- `[RETRIEVAL_BINDING_MISMATCH]` — le `binding` est **réconcilié** avec les
  stacks actives à la compilation, sur la *famille* (`voyage` couvre
  `voyage-3-large`) ; la version, elle, reste épinglée par `indexHash` ;
- un test qui simule le **second générateur** : il dérive un plan d'appel
  complet sans jamais lire `binding`, et ce plan est identique pour `pgvector`
  et pour n'importe quel autre store.

**Lot d'audit — le pipeline ne pouvait pas aboutir une seule fois, et cinq
contrôles étaient inertes.** `ir_compiler` exigeait un holdout qui n'existe que
trois phases plus loin ; aucune suite L9 n'était jamais compilée ; neuf options
CLI étaient citées par les prompts et n'existaient dans aucun script
(désormais attrapées par `refs.flags`) ; les hooks d'ownership lisaient
l'identité du sous-agent au mauvais endroit du payload, les hooks de spawn ne
matchaient que `Task`, les façades ne portaient aucun `model:`, `MaxCostPerRun`
n'avait aucun écrivain, et les commandes de hook étaient relatives au répertoire
courant. Tout est corrigé ; le lot a aussi livré le squelette runtime Python
(`gen-app-skeleton --write`), les items d'évaluation en parallèle, les paquets
de contexte tranchés sur les fiches actives, et la migration du workspace.

**Maturité honnête.** La couche déterministe est en bêta. La couche de
génération — huit agents `dev-*`, le squelette Python — et la couche de revue —
six reviewers — sont écrites et câblées à leurs gates, et **n'ont jamais été
exécutées de bout en bout**. Python est le seul runtime doté d'un générateur de
squelette, .NET n'a pas de chaîne de retrieval, TypeScript et Kotlin ont des
fiches mais aucun générateur, Java n'a rien.

**Aucune combinaison de stack n'est annoncée validée**, parce qu'aucune n'a
encore été mesurée par un run réel. C1 est la cible du MVP, en `design-phase`.
Annoncer autre chose serait précisément le faux vert que ce framework existe
pour empêcher.

---

## Licence

MIT — voir [LICENSE](LICENSE). Les mêmes termes couvrent les sources du
framework sous `.sdda/` et le paquet Python `sdda` construit depuis
`.sdda/python/`.
