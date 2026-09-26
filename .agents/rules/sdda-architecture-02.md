---
trigger: model_decision
description: "Architecture SDD_Agents, partie 2/4 (3. Le pipeline forward) — lire avant toute action du pipeline SDD_Agents"
---
<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis .sdda/ARCHITECTURE.fr.md.
     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,
     et le test de parité la signale. Éditer la source. -->

### 2.ter Ce que l'humain fournit, ce que le framework produit

Jusqu'à la v5, `feats/` rangeait côte à côte le brief et le roster que l'humain
écrit, et la MISSION, les CAPs et les contrats que les agents génèrent ;
`proof/` rangeait sa vérité terrain à côté des jeux de `qa-evals`. En ouvrant
l'un ou l'autre, l'humain ne pouvait pas savoir ce qu'il devait remplir et ce
qu'il devait laisser au framework. La v6 le dit par l'arbre.

| Entrée | Nature | Écrite par | Régénérable |
|---|---|---|---|
| `stack/` | les choix techniques — `STACK.md`, seul, versionné | **l'humain** | non |
| `feats/` | ses spécifications — brief et roster, **Markdown seul**, à plat | **l'humain** | non |
| `assets/` | les données (racine des stores `kind: local`, l'`assets/` de SDD_Pro) et `.env` | **l'humain** | non |
| `seed/` | la vérité terrain — scénarios annotés, labels | **l'humain** | non |
| `pipeline/` | tout ce que le pipeline génère : MISSION, CAPs, topologie, contrats, ADR, jeux, suites, baselines, calibration | les agents `po-*`, `architect-*`, `qa-evals`, les scripts — **jamais un `dev-*`** sous datasets/suites/baselines/calibration | en partie |
| `src/` | l'application générée, prompts et schémas figés compris | les huit `dev-*`, `qa-tests`, les générateurs | oui |
| `.sys/` | état interne et sorties de run | les scripts | oui |

**L'entrée de l'utilisateur tient en quatre dépôts**, et c'est voulu : `STACK.md`
(les choix techniques — langage, framework, pattern, sources de données, URL
d'API, serveurs MCP), ses fichiers Markdown sous `feats/` (ce que le système
doit faire, puis le roster qui dit comment), ses données et son `.env` sous
`assets/`, et sa vérité terrain sous `seed/`. Le `.env` porte la clé des
*Runtime Models* (§6) : l'application générée le lit, le harnais de
construction jamais — il paie ses tokens avec son propre compte. Il est déposé
dans `assets/` et **copié** vers `src/{AppName}/.env` par l'étape qui crée le
projet — `gen-app-skeleton --write`, lancé par `dev-backend` en PHASE 3.0 —
sans LLM, parce que c'est de `src/{AppName}/` que l'application part en
exécutable ou en conteneur. L'humain n'a aucune commande à lancer ; la
commande `install-env` reste pour recopier une clé changée après le build. Aucun
agent ne lit l'un ou l'autre fichier : `preflight_forbidden_reads` et
`preflight_bash_ownership` refusent `[SECRET_READ_FORBIDDEN]`, y compris à
`architect-data` qui parcourt `assets/` pour inférer les schémas — quelle que
soit la graphie qui l'ouvre (`.ENV`, `.env.`, flux `.env::$DATA`, un Grep
filtré sur `.env`), et `.claude/settings.json` double ces hooks de `deny`
natifs de lecture. Trois règles tiennent l'entrée, vérifiées par `smoke-check`
et non racontées : `feats/` ne contient que du Markdown
(`[FEATS_NOT_MARKDOWN]`), `stack/` ne contient que `STACK.md`
(`[STACK_DIR_UNEXPECTED_FILE]`), aucune valeur de secret n'entre dans STACK.md
(`[STACK_SECRET_IN_CLEAR]`). Un workspace d'une version antérieure monte par
`python .sdda/sdda.py migrate-workspace`, qui range chaque fichier à sa place et
réécrit les références qui le citaient.

**La seule frontière qui ne souffre aucune exception est celle du jugement.**
L'agent qui écrit le code ne peut toucher ni au jeu qui le note, ni à la
référence contre laquelle sa régression est mesurée : `pipeline/datasets/`,
`suites/`, `baselines/`, `calibration/` et `fixtures/` sont interdits en écriture à tout
`dev-*`. La fusion de l'ancien `feats/` et de l'ancien `proof/` sous `pipeline/`
ne la desserre pas : elle tient aux zones de la matrice d'ownership, pas au nom
du répertoire parent. Ranger ces jeux sous `src/`, en revanche, les ferait
tomber dans la zone d'écriture des agents développeurs.

Deux conséquences se lisent directement dans l'arbre :

- **Les prompts sont DANS l'application**, `src/{App}/prompts/`, parce qu'un
  prompt système est un actif d'exécution. Rangé au même rang que les specs, il
  ne part pas avec le code ; rangé sous `src/` mais à côté de l'application, il
  n'en part pas davantage — l'exécutable ou le conteneur bâti depuis `src/{App}/`
  cherchait ses prompts dans un répertoire resté dans le dépôt. Même logique pour
  `skills/` (ce que l'agent sait faire, un fragment par compétence), `rules/` (ce
  qu'il doit ou ne doit jamais faire) et `memory/` (l'implémentation du contrat
  de mémoire) : une application agentic se lit dans son arbre — agents, prompts,
  skills, rules, tools, memory, orchestration — pas dans le framework qui l'a
  produite.
- **Les ADR ont UN emplacement**, `pipeline/decisions/`. Ils en avaient deux —
  `docs/adr/` que citait le gabarit, `.sys/.context/adrs/` que déclarait la
  matrice d'ownership — et le script des tâches humaines cherchait dans les
  deux. La question « cet ADR a-t-il été écrit ? » avait donc deux réponses
  possibles, et c'est celle que personne ne relisait qui gouvernait.

Un workspace d'une version antérieure monte par
`python .sdda/sdda.py migrate-workspace`, qui **déplace** le contenu plutôt que
de créer le nouvel arbre à côté de l'ancien.

**Cet arbre décrit le disque, pas l'intention.** 🟡 marque le seul écart assumé :
annoncé, pas encore écrit — `plugin.json` et `.sdda/skills/`, rien d'autre.
La règle vaut surtout pour `stacks/` —
67 fiches existent, quand le
catalogue visé en compte trois fois plus. Ce n'est pas un
manque à combler avant d'annoncer : c'est la séquence de
[docs/ROADMAP.fr.md](docs/ROADMAP.fr.md), qui livre **une combinaison validée de bout en
bout** (C1) avant d'en annoncer douze. Le catalogue cible et le niveau de
validation de chaque composant vivent dans `registry/compatibility.matrix.json`
(`componentLevels`, `catalogDiscrepancies`) et `registry/patterns.registry.json` —
registres machine, donc vérifiables, là où un arbre en prose ne l'est pas.

La conséquence opérationnelle, qui doit être dite : **une ligne activée dans
`STACK.md` pour un composant sans fiche sur disque ne charge rien.** Le catalogue
annoncé n'est pas un catalogue chargeable. Une valeur que les parseurs acceptent
mais que rien n'implémente — mémoire long terme, store distant, connecteur
`http-api` ou `mcp`, garde-fou sans fiche, human-in-the-loop hors langgraph — est
refusée au preflight (`[STACK_VALUE_UNIMPLEMENTED]`) au lieu d'être avalée.

### 2.ante Un seul point d'entrée pour l'outillage

Les 80 sous-commandes déterministes s'appellent par une forme unique :

```bash
python .sdda/sdda.py validate-mission --mission 1     # depuis un clone nu
sdda validate-mission --mission 1                     # après `pip install -e .sdda/python`
```

La première ne suppose **aucune installation**, et c'est elle qu'écrivent les
24 fiches d'agents et les 11 commandes. Un framework dont les prompts exigent un
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
Aujourd'hui l'inventaire est vide : aucun script cité n'est absent du disque
(`.sdda/docs/PLANNED-SCRIPTS.md`, régénéré par `planned-scripts --write`).

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
[docs/AGENTIC-IR.fr.md](docs/AGENTIC-IR.fr.md).

---

## 3. Le pipeline forward

```
 PHASE 0   ELICITATION        po-elicitor            -> pipeline/missions/{n}-{Name}.md
   |                                                         [MISSION GATE]
 PHASE 1   CAPABILITIES       po-capabilities        -> pipeline/caps/{n}-{m}-*.md
   |                                                         [CAP GATE]
   |       ROSTER             roster validate --if-present (script, 0 token, AVANT l'architecte)
 PHASE 2   TOPOLOGIE          architect-topology (seul)   -> pipeline/topology/{n}-topology.md
   |                          gen-source-tools --scope contracts (script, si declared-sources)
   |                          puis architect-tools || architect-rag ||
   |                               architect-data || architect-memory  (parallèle)
   |                                                     -> pipeline/contracts/**
   | PHASE 2.9 COMPILATION IR  validate-topology (passe complète), puis
   |                          ir-compiler (script, 0 token) -> .sys/.ir/{n}-system.ir.json
   |                                                         [TOPOLOGY GATE]  (s'exécute sur l'IR)
 PHASE 3   SOCLE              project-init (script, 0 token) : squelette, uv sync, contexte projet
   |                          dev-backend (coquille : composition, config, Domaine)
   |                            || PHASE 6a qa-evals --datasets-only : golden, calibration — AVANT le socle
   |                          puis dev-tools || dev-retrieval || dev-data     (parallèle)
   |                          gen-source-tools --scope code juste avant dev-data
   |                                                         [TOOL GATE] [RETRIEVAL GATE]
 PHASE 4   PROMPTS + AGENTS   4.0 dev-orchestration --prepass (shared/ + memory/interface, gelés)
   |                          4.1 dev-prompt (seul) -> 4.2 dev-agent  (parallèle, 1 instance par agent)
   |                                                         [AGENT GATE]
 PHASE 5   ORCHESTRATION      dev-orchestration -> dev-api -> dev-backend (packaging)   (séquentiel)
   |                                                         [ORCH GATE]
 PHASE 6   EVAL + TESTS       qa-evals || qa-tests
   |
 PHASE 7   REVUE              Etage A : spec-compliance seul
   |                          scans secrets + PII, une fois (0 token)
   |                          Etage B : agent-safety || cost-latency ||
   |                                    orchestration || rag-quality   (parallèle)
   |                          Etage C : review-adversarial (système vivant)
   |                          + le jeu adversarial versionné, joué EN LIVE
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

Ce que le schéma ne dit pas seul, et qui a été ajouté parce que son absence
coûtait :

- **Le roster est vérifié avant l'architecte.** `architect-topology` matérialise
  un roster DÉCLARÉ, il n'en invente pas (P7). Sans ce contrôle à 0 token,
  `/sdda-full` payait l'agent le plus cher du pipeline pour qu'il échoue au
  post-check — ou remplisse lui-même la déclaration que personne n'avait prise.
- **Les contrats des sources déclarées naissent en PHASE 2, avant l'IR**
  (`gen-source-tools --scope contracts`), et leur code en PHASE 3 juste avant
  `dev-data` (`--scope code`). Un contrat créé après l'IR décrirait un outil que
  G2 n'a jamais vu.
- **`validate-topology` joue sa passe complète avant `ir-compiler`**, et elle
  seule écrit la part `topology` de G2 : un IR ne se compile pas contre un
  contrat absent (`[TOPOLOGY_CONTRACT_MISSING]`, `[AGENT_CONTRACT_MISSING]`).
  La passe `--pre` n'écrit plus de rapport — elle rendait une part verte sans
  qu'aucun contrat ait été vérifié.
- **Les jeux avant le code (PHASE 6a).** G4 exige le golden de retrieval, G5 les
  goldens de CAP et les sets de calibration ; les produire avant le code est
  aussi ce qui empêche le code d'influencer le jeu qui le jugera. Ils partent
  **en même temps** que la coquille (`/sdda-build --with-datasets`) et se
  referment avant le socle : ni `qa-evals` ni `dev-backend` ne peut lire
  l'autre, et les enchaîner faisait attendre la coquille pour rien.
- **L'init projet est un script, pas un agent.** `project-init` ouvre la
  PHASE 3 : squelette, `uv sync`, puis `gen-app-context`, qui écrit
  `workspace/src/{App}/CLAUDE.md` (le `memory_file` du harnais actif) — le
  pendant du `CLAUDE.md` par projet de SDD_Pro, projeté depuis l'IR au lieu
  d'être rédigé. Chaque `dev-*` le lit à la place de STACK.md
  (`[PROJECT_NOT_INIT]` s'il manque, `[PROJECT_CONTEXT_STALE]` s'il a dérivé).
  Avant lui, `dev-backend` lançait le générateur lui-même — treize minutes
  mesurées pour trois appels de script — et huit agents relisaient 44 Ko de
  STACK.md pour reconstituer le même projet.
- **L'étape 4.0 — la pré-passe.** `dev-orchestration --prepass` pose les types
  partagés (`shared/`) et l'**interface** mémoire avant que les instances de
  `dev-agent` partent en parallèle. Sans elle, chaque instance inventait ses
  propres types de handoff et codait contre une mémoire écrite après elle. Ces
  zones sont ensuite **gelées** — `shared/**` et `memory/**` pendant la phase 4,
  `shared/**` et `memory/interface.*` pendant la phase 5 — et l'audit le vérifie
  sur le disque (`[OWNERSHIP_FROZEN_ZONE_CHANGED]`).
- **Chaque vague d'écriture est encadrée par un instantané.**
  `audit-ownership snapshot` avant les phases 3, 4.0, 4 et 5, puis
  `audit-ownership --since-snapshot` après, qui juge les fichiers que la phase
  a RÉELLEMENT écrits (`--instances` pour les instances de `dev-agent`,
  `--frozen` pour les zones gelées) et peut les révoquer (`--restore`). Ce que
  les hooks ne voient pas — un script qui écrit de l'intérieur — se voit sur le
  disque.
- **La phase 5 est séquentielle** : orchestration, puis surface, puis
  packaging. La surface s'attache au graphe, le packaging à la surface.
- **Le profil `poc` prend un autre chemin.** `Profile: poc` (`## Project Config`)
  est à la fois une couche de config — `.sdda/profiles/poc.yml`, entre la base
  et l'équipe : des jeux plus petits — et un aiguillage des commandes :
  `/sdda-build` remplace les PHASES 3 à 5 par `dev-prompt` ∥ `qa-evals`, puis un
  seul agent `dev-app` pour toute l'application ; les gates G3→G6 sont jouées et
  rapportées sans bloquer ni boucler ; `/sdda-full` saute la revue et
  l'acceptation. Sans G7, `compute-status` plafonne la MISSION à `Tested` : un
  poc n'est jamais `Approved`. Ce qui ne bouge pas : la frontière du jugement,
  les prompts hashés, les bornes en code. `dev-app` recouvre les zones des
  `dev-*` de couche par construction ; le partage est déclaré paire par paire
  (`exclusive-by-profile`), pour que la détection entre `dev-*` reste entière.
- **La reprise suit la lignée.** `/sdda-full {n} --resume` ouvre un run lié au
  précédent (`resumedFrom`) ; saut, reprise et compteur de tentatives se lisent
  sur toute la lignée, à la granularité de l'item (`--inputs-hash`), et
  `BuildLoopMaxCostUsd` borne la boucle de correction d'UN item, reprises
  comprises. `set-phase --phase acceptance` ferme la lignée.

---
