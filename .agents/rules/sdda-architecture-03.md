---
trigger: model_decision
description: "Architecture SDD_Agents, partie 3/4 (4. Les neuf gates) — lire avant toute action du pipeline SDD_Agents"
---
<!-- GÉNÉRÉ par sdda_admin/harness_build.py depuis .sdda/ARCHITECTURE.fr.md.
     NE PAS ÉDITER ICI : toute modification est écrasée au build suivant,
     et le test de parité la signale. Éditer la source. -->

## 4. Les neuf gates

Chaque gate est **déterministe** (0 token) sauf mention contraire. Chaque gate a
un enforcer sur disque déclaré dans `INVARIANTS.yml`.

| # | Gate | Vérifie | Bloquant sur |
|---|---|---|---|
| G0 | **MISSION** | objectif chiffré présent, budget déclaré (coût/latence/tokens), ground truth identifiée, aucun `<à préciser>` résiduel | `[MISSION_INCOMPLETE]` |
| G1 | **CAP** | chaque AC nomme métrique + seuil + dataset ; chaque élément de la MISSION couvert par >= 1 CAP | `[AC_NOT_EVALUABLE]`, `[TRACEABILITY_GAP]` |
| G2 | **TOPOLOGY** | architecture déclarée complète pour le pattern actif (l'alternative plus simple écartée reste consultative : P7, l'architecte décide) ; budget estimé <= budget déclaré ; toute boucle bornée ; tout agent a un contrat ; graphe atteignable sans cycle non borné ; **parts** `packaging`, `architecture`, `adr` | `[ARCH_SPEC_INCOMPLETE]`, `[BUDGET_EXCEEDED_ESTIMATE]`, `[UNBOUNDED_LOOP]`, `[TOPOLOGY_CONTRACT_MISSING]`, `[ADR_MISSING]` |
| G3 | **TOOL** | schéma valide ; tests de contrat verts (happy + chaque erreur déclarée + timeout + auth KO) ; connectivité live vérifiée ; classe d'effet de bord déclarée ; stratégie de sûreté présente si destructif | `[TOOL_CONTRACT_FAILED]`, `[SIDE_EFFECT_UNDECLARED]` |
| G4 | **RETRIEVAL** | golden set présent (>= n queries) ; recall@k, nDCG, groundedness, taux de citations résolues au-dessus des seuils | `[RETRIEVAL_BELOW_THRESHOLD]` |
| G5 | **AGENT** | chaque agent évalué **isolé** (outils mockés, retrieval figé) contre ses CAP ACs, sur k runs ; **parts** `calibration` (une calibration rouge bloque), `prompts` (hash épinglé de chaque prompt attendu par l'IR), `ownership` | `[AGENT_EVAL_FAILED]`, `[PROMPT_MISSING]`, `[PROMPT_HASH_MISMATCH]` |
| G6 | **ORCH** | evals bout-en-bout sur golden mission ; trajectoires conformes ; hops <= plafond ; coût et latence **mesurés** <= budget déclaré ; **part `api`** : le contrat exposé est dérivé de l'IR et lui correspond ; **part `framework`** : le code importe le framework déclaré, là où sa fiche le place, et aucun concurrent | `[TRAJECTORY_VIOLATION]`, `[BUDGET_EXCEEDED_MEASURED]`, `[API_CONTRACT_DRIFT]`, `[API_ROUTE_UNBACKED]`, `[FRAMEWORK_DRIFT]` |
| G7 | **SAFETY** | suite d'injection (directe + indirecte) ; jeu adversarial versionné joué en live ; audit de scope d'outils ; scan de secrets dans prompts/traces/datasets ; scan PII du vector store ; rapport de chaque reviewer obligatoire présent | `[INJECTION_SUCCEEDED]`, `[TOOL_SCOPE_EXCESS]`, `[SECRET_LEAK]`, `[PII_IN_INDEX]`, `[SAFETY_REVIEW_REPORT_MISSING]` |
| G8 | **ACCEPTANCE** | objectif chiffré de la MISSION atteint sur **holdout** (jamais sur le golden d'entraînement) ; non-régression vs baseline au-delà de la tolérance | `[GOAL_NOT_MET]`, `[REGRESSION]` |

**Règle du holdout** : les datasets d'ajustement (`golden/`) et de verdict
(`holdout/`) sont disjoints et le pipeline le vérifie par hash. Optimiser les
prompts contre le jeu qui rend le verdict est la façon agentic de se mentir.

**Parts obligatoires, parts contributives.** Une gate agrège des rapports
partiels (`sdda_lib/gate_reports.py`). Une part **obligatoire** absente laisse
la gate ouverte ; une part **contributive** absente ne bloque pas, mais son
ROUGE bloque toujours. G2 exige `topology`, `ir` et `budget`, et lit
`packaging`, `architecture` et `adr` ; G5 lit `calibration`, `ownership` et
`prompts` ; G6 lit `api` et `framework` ; G7 exige `suites`, `adversarial` et
`verdict`, et lit `secrets`, `pii` et `toolscope`. Contributives plutôt
qu'obligatoires parce qu'un projet sans décision qui exige un ADR, ou une
surface `cli` sans contrat HTTP, n'a rien à écrire — l'exiger ferait échouer
des livrables qui n'ont pas l'objet. Le rouge, lui, n'a jamais d'excuse.

**La part `adr` de G2 — une décision qui contredit un défaut se prend par
écrit.** `registry/adr-requirements.yml` déclare les décisions qui exigent un
ADR (base en écriture pour un agent, `ApiContractFirst: false`,
`TlsVerify: false`, PII en mémoire ou en trace brute, surface réseau sans
identité, `StackComboCheck: off`, pattern `network`) ; `validate-adr` écrit la
part. Un ADR ne couvre une exigence que s'il est `Status: Accepted` et la
**nomme** dans une ligne `Covers: Clé=valeur` : un ADR qui écrivait « false »
n'importe où couvrait auparavant toutes les décisions booléennes du projet.

**La calibration du juge est une part de G5, pas un avertissement.** Le
rapport `G5-{n}.calibration.json` est rangé sous le numéro de la MISSION, donc
lu par la gate : une calibration rouge bloque G5. Un juge non calibré rend son
grader `advisory` (score informatif, non bloquant), et une CAP dont tous les
graders sont advisory ne peut pas être verte. Un juge qui est l'un des modèles
que le produit fait tourner est refusé au preflight
(`[JUDGE_SAME_AS_EVALUATED]`) ; découvert au runtime, il rend un verdict
advisory (`[JUDGE_EQUALS_EVALUATED]`). Le juge est réel : `graders/judge_clients.py`
appelle Anthropic, OpenAI, Gemini ou Ollama en stdlib, URL et variable de clé
lues dans la fiche provider.

**G7 joue, il ne relit pas.** `/sdda-review` joue le jeu adversarial versionné
contre la surface livrée (`run-adversarial-suite --executor … --run-id`), et le
script enregistre lui-même chaque exécution dans
`.sys/reports/runs/{n}-adversarial.jsonl`, que `--replay` peut rejuger. Les
scans (`scan-secrets`, `scan-pii`) tournent une fois, avant l'étage B. Un
rapport de reviewer obligatoire absent est `[SAFETY_REVIEW_REPORT_MISSING]` et
non « zéro finding » : un reviewer qui n'a rien écrit n'a rien vérifié.

**L'API Gate est une part de G6, pas une dixième gate.** C'est la transposition
de l'`API Gate` de SDD_Pro, qui validait le contrat back↔front avant de générer
le front. Ici la couture est entre l'**IR** et le monde extérieur : l'OpenAPI
publié est **dérivé** des `inputSchema` / `outputSchema` de l'IR, jamais écrit à
la main, et un test déterministe (0 token) confronte les deux. La ranger dans G6
plutôt qu'en gate séparée est délibéré : `dev-api` travaille en PHASE 5 aux
côtés de `dev-orchestration`, et un dixième verrou pour une seule question
diluerait la lecture des neuf autres. Détail :
`stacks/serving/fastapi-sse.md §6`. Désactivable par `ApiContractFirst: false`,
qui exige un ADR. La part `framework` suit la même logique : une architecture
que la fiche relue en revue ne décrit plus est une dérive, pas un détail.

**`STACK.md` est validé avant toute dépense.** Ses valeurs sont confrontées à
`templates/project-config.schema.json`, section par section
(`[CONFIG_VALUE_INVALID]`, `[CONFIG_KEY_CONFLICT]`, `[CONFIG_KEY_MISPLACED]`),
bloquant au `smoke-check` et au preflight ; chaque clé du gabarit déclare le
script ou l'agent qui la lit (`x-readBy`), et les clés que personne ne lisait
ont été retirées. La combinaison active est reconnue par sa signature dans
`registry/compatibility.matrix.json` ; une combinaison non listée est gouvernée
par `StackComboCheck: strict|warn|off` (`[STACK_COMBO_UNLISTED]`).

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
en C#, `serving/cli-node.md` en TypeScript, `serving/cli-kotlin.md` en Kotlin) ;
un défaut qu'un langage ne peut pas honorer se voit au preflight
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
| **Build models** | quels modèles paient les tokens de *construction* (les 24 Developer Agents) | `capability-matrix.yml` > `harnesses.{Harness}.tier_models` |
| **Runtime models** | quels modèles fait tourner l'**application générée** | `STACK.md ## Runtime Models` |

Les trois sont indépendants. Construire avec Claude Code + Opus une application
qui tourne sur GPT-4-mini est un cas nominal, pas une exception. Confondre les
deux derniers est l'erreur la plus fréquente des frameworks concurrents : elle
rend le budget d'exécution incalculable.

**Un seul harnais est supporté : Claude Code.** C'est le seul où tous les hooks
refusent l'appel d'outil au moment où il a lieu. Codex CLI (`.codex/` et les
skills `.agents/skills/`), Gemini CLI (`.gemini/`) et Antigravity (`.agents/`)
sont **expérimentaux** : leurs façades se compilent dans les formats que leur
documentation officielle décrit, aucun run de conformance ne les a validées, et
leurs gates au runtime sont **partielles** (Codex, Gemini CLI :
`runtime_hooks: partial`) ou **absentes** (Antigravity). Leur payload de hook ne
nomme pas l'agent auteur d'un appel : seules les zones protégées y sont
refusées au runtime (plus les gates de spawn sous Gemini CLI) ; la matrice
d'ownership par agent est reportée au CI et aux scripts déterministes. Les
fichiers racine `AGENTS.md` et `GEMINI.md` sont des pointeurs générés, communs
aux trois. Détail : [docs/MULTI-HARNESS.fr.md](docs/MULTI-HARNESS.fr.md).

**Les agents déclarent un tier** (`fast` / `balanced` / `deep`), jamais un nom de
modèle. Deux résolutions, deux sources :

- **Construction** — le harnais la résout : `capability-matrix.yml` >
  `harnesses.{Harness}.tier_models`, que `harness_build` compile dans le
  `model:` de chaque façade d'agent. `## Build Models` ne porte plus aucune
  clé : `Provider`, `Endpoint`, `TierMap` et `Mode` n'étaient lus par personne,
  et changer `TierMap` ne changeait aucun appel. Une clé qu'on édite sans effet
  est pire qu'une clé absente — elle fait croire à un réglage.
- **Application** — `## Runtime Models` (`RuntimeProvider`, `RuntimeTierMap`),
  que `layered_config.read_runtime_tier_map` et le squelette généré consomment.

Les fiches `.sdda/providers/*.yaml` sont le **catalogue** par fournisseur
(identifiants de modèles, tarifs, URL par défaut, variable de clé), et elles
sont lues : `pricing.py` y prend ses tarifs (la table en dur n'est plus qu'un
repli testé), le juge LLM y prend `default_base_url`, `api_prefix` et
`auth_env`, et `gen-app-skeleton` épingle le SDK du fournisseur d'exécution
dans le projet généré. Ajouter un provider ne touche aucun agent. Les bornes
`tier_floor` / `tier_ceiling` de `agent-bounds.yaml` sont des invariants de
qualité : le Project Config ne peut pas les relâcher. Le budget de contexte
de chaque agent (`budget_bytes` de `loader.yml`) est lui-même plafonné par
tier — 60 % d'une fenêtre de 200 k tokens, soit ≈ 480 Ko — et un budget
déclaré au-delà refuse le spawn.

---

## 7. Ownership et parallélisme

La matrice d'ownership de SDD_Pro est reprise telle quelle et étendue aux
artefacts agentic. Extrait (la source est `loader.yml`, clés `writes:`) :

| Chemin | Owner exclusif | Mode |
|---|---|---|
| `workspace/pipeline/missions/{n}-*.md` | `po-elicitor` | Create puis append-only |
| `workspace/pipeline/caps/{n}-{m}-*.md` | `po-capabilities` | Create exclusif (1 fichier = 1 CAP) ; `architect-topology` n'y remplit que `## Allocated To` |
| `workspace/pipeline/topology/{n}-*.md` | `architect-topology` | Create exclusif |
| `workspace/pipeline/contracts/tools/{n}-*.tool.md` | `architect-tools` | Create exclusif |
| `workspace/pipeline/contracts/retrieval/{n}-*.retrieval.md` | `architect-rag` | Create exclusif |
| `workspace/src/*/prompts/{agent}.system.md` | `dev-prompt` | Create + Edit exclusif |
| `workspace/src/**/agents/{agent}/**` | `dev-agent` (1 instance par agent, liée à son répertoire) | Edit-augment exclusif |
| `workspace/src/**/tools/**` | `dev-tools` | Edit-augment exclusif |
| `workspace/src/**/retrieval/**` | `dev-retrieval` | Edit-augment exclusif |
| `workspace/src/**/orchestration/**` · `memory/**` · `shared/**` | `dev-orchestration` | Create + Edit exclusif ; `shared/` et l'interface mémoire en pré-passe |
| `workspace/src/**/serving/**` | `dev-api` | Edit-augment exclusif |
| `workspace/src/*/*` · `workspace/src/**/app/**` | `dev-backend` | la coquille : projet, composition, config, Domaine, packaging — rien du moteur |
| `workspace/src/*/{CLAUDE,AGENTS,GEMINI}.md` | script `gen-app-context` uniquement | Write atomique ; interdit à `dev-backend` |
| `workspace/src/**/tests/**` | `qa-tests` (zone partagée avec les `dev-*`, par couche) | Edit-augment |
| `workspace/pipeline/datasets/**` | `qa-evals` | Create exclusif ; **jamais** `dev-*` |
| `workspace/pipeline/baselines/**` | script déterministe uniquement | Write atomique |

> **Règle critique, propre à l'agentic** : `dev-agent` n'a **aucun** droit
> d'écriture sur `workspace/pipeline/datasets/` ni sur `workspace/src/{App}/prompts/`. L'agent qui
> écrit le code ne peut ni modifier le jeu qui le juge, ni réécrire le prompt qu'il
> est censé implémenter. Sans cette séparation, l'auto-confirmation est garantie —
> c'est le pendant agentic du `[QA_OWNERSHIP_VIOLATION]` de SDD_Pro.

**Les motifs se lisent par segment.** `*` couvre un segment de chemin, `**`
plusieurs : `workspace/src/*/*` est la racine du projet généré, pas tout
`src/`. Avant, une étoile traversait les `/`, et la zone de `dev-backend`
recouvrait silencieusement celle de tous les autres `dev-*`. La détection des
recouvrements compare désormais les zones RÉELLES, pas les chaînes : deux motifs
qui désignent les mêmes fichiers sont un recouvrement, qu'on résout (la couche la
plus externe gagne, `architect-tools` s'interdit les contrats `data-` que
`architect-data` écrit) ou qu'on **déclare** dans `shared_writes` avec son mode
(`serialized`, `append-only`, `disjoint-by-layer`…) et sa raison.

**Une instance, un répertoire.** `dev-agent` tourne en N instances parallèles,
et sa zone `agents/{agent}/**` n'a de sens que si l'on sait QUELLE instance
écrit. `/sdda-build` écrit `SDDA-INSTANCE: {agent}` dans le prompt ;
`preflight_instance_bind` l'enregistre au spawn, et la liaison à l'instance se
réserve à sa première écriture. Une instance qui écrit chez une autre est
refusée (`[OWNERSHIP_INSTANCE_ESCAPE]`).

**Les rapports de gate sont infalsifiables.** Les reviewers écrivent exactement
les `writes:` que `loader.yml` leur donne sous `.sys/.validation` ; un rapport de
GATE `.json` reste interdit à Write/Edit ET au shell. Un verdict qu'un agent
peut écrire n'est pas un verdict.

**Les hooks.** 15 hooks sont câblés dans
`.claude/settings.json`, chacun déclarant son `WIRING` : écriture
(`Write|Edit|MultiEdit|NotebookEdit`), lecture (`Read|Glob|Grep`), shell
(`Bash|PowerShell`), spawn (`Task|Agent`) et fin de sous-agent. Le hook shell
analyse ce qu'une commande écrit — répertoire courant, variables, jokers,
`bash -c`, `eval`, `$(…)`, `-EncodedCommand`, heredocs, casse Windows,
chemins `/g/…` — et refuse ce qu'il ne peut pas nommer sans l'exécuter
(`[OWNERSHIP_SHELL_OPAQUE]`) ; PowerShell a son propre dialecte. La commande
d'un hook est `${SDDA_PYTHON:-python}`, ancrée sur `$CLAUDE_PROJECT_DIR`. Par
défaut, un hook qui plante laisse passer en le disant ; avec
`SDDA_HOOKS_STRICT=1` (la CI), il REFUSE (`[HOOK_FAILED]`) — un hook qui ne
démarre pas rend un code que le harnais traite comme une autorisation. Et
`python .sdda/sdda.py hooks-selfcheck` **exécute** chaque hook câblé, payload
inoffensif et payload à refuser : la seule preuve qu'un hook tient est de le
lancer. `preflight_stack_combo` ne juge que les agents du pipeline : un
sous-agent hors pipeline n'est pas bloqué par un `STACK.md` rouge. Il ne
réécrit pas non plus ce qui applique la matrice — les hooks, `loader.yml`,
`agent-bounds.yaml`, `INVARIANTS.yml`, les réglages de hooks des quatre
harnais, `.git/hooks/` : c'était la porte par laquelle un `general-purpose` à
qui l'on dit « tu es dev-agent » neutralisait tout. Développer le framework par
sous-agents reste possible, déclaré : `SDDA_FRAMEWORK_DEV=1`.

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
franchissement de gate. Chaque span est écrit **entier, sous verrou exclusif** :
des evals parallèles perdaient des lignes.

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
plafond qu'il dépasse. `cost-report` et `trajectory-report` écrivent les mesures
que lisent `review-cost` et `review-orchestration`.

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
