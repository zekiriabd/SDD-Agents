# Les Developer Agents de SDD_Agents

<!--sdda:count agents-->24<!--/sdda:count--> agents spécialisés. **Ce sont les agents qui *construisent*** — à ne pas
confondre avec les agents du produit généré, décrits dans
`workspace/pipeline/contracts/agents/`.

Règle d'orchestration interne héritée de SDD_Pro : **aucun agent ne spawne un
autre agent.** La commande orchestre, l'agent exécute. C'est ce qui rend la
facture de construction prévisible et le parallélisme bornable (`MaxParallel`).

## 0. La convention de nommage — `{métier}-{domaine}`

Cinq préfixes, un par métier, dans l'ordre du pipeline :

| Préfixe | Métier | Phase | Agents |
|---|---|:---:|---|
| `po-` | product owner — le besoin et son découpage | 0-1 | `po-elicitor`, `po-capabilities` |
| `architect-` | architecture — matérialiser, contractualiser et chiffrer la structure déclarée | 2 | `architect-topology`, `architect-rag`, `architect-data`, `architect-memory`, `architect-tools` |
| `dev-` | implémentation | 3-5 | `dev-backend` (la coquille), `dev-tools`, `dev-retrieval`, `dev-data`, `dev-prompt`, `dev-agent`, `dev-orchestration`, `dev-api` ; `dev-app` (profil `poc` seulement) |
| `qa-` | ce qui prouve | 6a, 6 | `qa-tests`, `qa-evals` |
| `review-` | ce qui conteste | 7 | `review-spec`, `review-safety`, `review-cost`, `review-orchestration`, `review-rag`, `review-adversarial` |

Ce n'est pas de la cosmétique. La nomenclature d'origine appelait **cinq**
agents `*-architect` alors qu'aucun ne décide de l'architecture au sens où un
architecte l'entend : c'est l'**architecte humain** qui la déclare dans le
roster (`workspace/feats/{n}-roster.md`, PHILOSOPHY P7), `architect-topology` la
matérialise et la chiffre, les quatre autres contractualisent ce qu'il a
délimité. Et la phase fonctionnelle — celle où l'on demande ce que le système
doit faire et pour qui — n'avait aucun nom qui la désigne, alors qu'elle porte
deux agents.

Un `ls .sdda/agents/` trie désormais par métier puis par domaine : le roster se
lit comme une équipe, dans l'ordre où elle intervient, sans documentation.

**Pourquoi deux agents PO et non un seul.** `po-elicitor` recueille, puis
`po-capabilities` découpe en CAPs mesurables. Les fusionner supprimerait la
barrière qui rend le veto de `qa-evals` possible : une CAP dont l'AC n'est pas
mesurable lui revient avec `[AC_NOT_EVALUABLE]`, et elle doit revenir à
quelqu'un dont c'est le seul travail — pas à l'agent qui a aussi écrit la
MISSION dont elle dérive.

> **CAP = user story.** Le découpage de `po-capabilities` est celui que SDD_Pro
> fait en User Stories, avec quatre exigences de plus : métrique, seuil,
> dataset, k runs (cf. `SDD-PRO-INHERITANCE.fr.md §3`). Il n'y a pas de niveau
> intermédiaire entre MISSION et CAP — en ajouter un dupliquerait la traçabilité
> sans rien mesurer de plus.

---

## 1. Tableau de bord

La colonne « Écrit dans » est un résumé ; la source qui fait foi est la clé
`writes:` de chaque agent dans `.sdda/loader.yml`, que les hooks appliquent.

| Agent | Phase | Tier | Écrit dans | La question qu'il pose |
|---|:---:|:---:|---|---|
| **`po-elicitor`** | 0 | balanced | `pipeline/missions/` | Quelle est la vérité contre laquelle on jugera, et que fait le système quand il ne sait pas ? |
| **`po-capabilities`** | 1 | balanced | `pipeline/caps/` | Quelles compétences discrètes, et comment mesure-t-on chacune ? |
| **`architect-topology`** | 2 | **deep** | `pipeline/topology/`, `pipeline/contracts/agents/`, `## Allocated To` des CAPs, ADR | Le roster déclaré est-il complet pour le pattern actif, et combien coûte-t-il au pire cas ? |
| **`architect-tools`** | 2 | balanced | `pipeline/contracts/tools/` | Quel contrat, quels effets de bord, quelle sûreté ? |
| **`architect-rag`** | 2 | **deep** | `pipeline/contracts/retrieval/` | Quel corpus, quel découpage, quelle stratégie, quel golden set ? |
| **`architect-data`** | 2 | balanced | `pipeline/contracts/tools/{n}-data-*`, ADR | Comment l'agent touche la donnée sans pouvoir lui nuire ? |
| **`architect-memory`** | 2 | balanced | `pipeline/contracts/memory/` | Qu'est-ce qui persiste, pour combien de temps, avec quelles PII ? |
| **`dev-backend`** | 3.0, 5 | balanced | `src/{App}/*`, `src/**/app/` | — la coquille : projet, composition, config, règles calculables (3.0), puis packaging (5) — hérité de SDD_Pro, EN PLUS des six du moteur |
| **`dev-app`** | 3-5, `poc` seulement | balanced | `src/{App}/**` sauf `prompts/`, `skills/`, `rules/`, le fichier de contexte | — toute l'application en un agent sous `Profile: poc`, après `dev-prompt` ; remplace les sept agents ci-dessus et ne tourne jamais avec eux (`exclusive-by-profile`) |
| **`dev-tools`** | 3 | balanced | `src/**/tools/` | — implémente les outils |
| **`dev-retrieval`** | 3 | balanced | `src/**/retrieval/` | — implémente ingestion + retriever |
| **`dev-data`** | 3 | balanced | `src/**/data/` | — implémente vues, repositories, enveloppe |
| **`dev-prompt`** | 4 | **deep** | `src/{App}/prompts/`, `skills/`, `rules/` | Comment ce contrat devient-il un prompt qui tient sous adversité — outils, **skills et rules** compris ? |
| **`dev-agent`** | 4 | **deep** | `src/**/agents/{agent}/` | — implémente un agent (1 instance liée par agent) |
| **`dev-orchestration`** | 4.0, 5 | **deep** | `src/**/shared/`, `src/**/memory/`, `src/**/orchestration/` | — pré-passe (types partagés + interface mémoire), puis graphe/superviseur/routeur et mémoire |
| **`dev-api`** | 5 | balanced | `src/**/serving/` | — implémente la surface d'exposition |
| **`qa-evals`** | 6a, 6 | **deep** | `pipeline/datasets/`, `pipeline/suites/`, `pipeline/calibration/` | Quel jeu, quel grader, quel seuil, calibré comment ? (6a : les jeux, AVANT le code ; 6 : suites complétées) |
| **`qa-tests`** | 6 | balanced | `src/**/tests/` | — tests déterministes L0→L2, une invocation par couche |
| **`review-spec`** | 7A | balanced | `.sys/.validation/reports/spec-compliance-{n}.md` | Chaque AC de CAP a-t-elle une eval qui la couvre vraiment ? |
| **`review-safety`** | 7B | **deep** | `.sys/.validation/reports/agent-safety-{n}.md` | Où passe le texte hostile, et que peut-il déclencher ? |
| **`review-cost`** | 7B | fast | `.sys/.validation/reports/cost-latency-{n}.md`, `cost-{n}.json` | Combien ça coûte vraiment, et où part l'argent ? |
| **`review-orchestration`** | 7B | balanced | `.sys/.validation/reports/orchestration-{n}.md`, `trajectories-{n}.json` | Des hops inutiles, des boucles, des impasses, des handoffs sans contrat ? |
| **`review-rag`** | 7B | balanced | `.sys/.validation/reports/rag-quality-{n}.md` | Le retrieval tient-il, ou l'agent compense-t-il ? |
| **`review-adversarial`** | 7C | **deep** | `.sys/.validation/reports/adversarial-{n}.md`, `adversarial-findings/{n}.jsonl` | Comment je casse ce système maintenant qu'il tourne ? |

Bornes `tier_floor` / `tier_ceiling` par agent : `.sdda/agent-bounds.yaml`.
Elles sont des invariants de qualité, non surchargeables par le Project Config.

Les reviewers écrivent **exactement** les chemins de leur `writes:` sous
`workspace/.sys/.validation/` : c'est là que `validate-safety-gate` les relit, et
un nom de rapport inventé serait un rapport que la gate ne trouve pas. Un
rapport de **gate** (`G{k}-*.json`) leur reste interdit, à l'éditeur comme au
shell : seul le script de la gate l'écrit.

`dev-prompt` porte une seconde responsabilité que la colonne « Écrit dans »
ne montre qu'à moitié : il est l'**owner d'implémentation des skills et des
rules** des agents du produit. `architect-topology` les *déclare* dans le
contrat d'agent (recopiées du roster), `dev-prompt` les *implémente* dans
`prompts/{agent}.system.md` (`## Compétences`, `## Règles`) et dans un fragment
par slug (`skills/{slug}.md`, `rules/{slug}.md`), et aucun des deux ne peut
écrire chez l'autre — donc aucun ne peut résoudre seul un désaccord.
`lint_prompts.py` constate la correspondance dans les deux sens :
`[SKILL_NOT_IMPLEMENTED]`, `[SKILL_UNDECLARED]`, `[RULE_NOT_IMPLEMENTED]`,
`[RULE_UNDECLARED]`. Une skill ou une rule n'ayant ni schéma ni effet de bord,
c'est la seule vérification possible : aucune gate ne peut l'exécuter pour la
juger. Détail : `rules/ownership.md §2.2`.

---

## 2. Les agents qui portent la valeur du framework

### `architect-topology` — le chiffrage avant la construction

Il n'a pas d'équivalent dans SDD_Pro. Il ne **choisit pas** l'architecture — le
nombre d'agents, leurs rôles, leurs outils et le pattern sont déclarés par
l'architecte (roster et `STACK.md`, PHILOSOPHY P7). Ce qu'il apporte, personne
d'autre ne l'apporte : il est le seul point du pipeline où une topologie à
$0.40 l'appel, pour un produit qui en facture $0.05, se voit **avant** qu'on ait
tout construit dessus.

**Procédure imposée**, dans cet ordre :

1. Lire le roster déclaré (`workspace/feats/{n}-roster.md`, validé par
   `python .sdda/sdda.py roster validate` avant son spawn) et le recopier **tel
   quel** dans `## 2. Roster déclaré` de la topologie. Il le lit, il ne l'écrit
   jamais.
2. Lancer `python .sdda/sdda.py validate-architecture --mission {n}` : la
   déclaration est-elle complète pour le pattern actif
   (`registry/architecture-requirements.yml`) ? Rouge → STOP, il nomme les
   champs manquants et ne les devine pas.
3. Allouer chaque CAP au roster, en posant d'abord deux questions : un outil
   déterministe suffit-il ? (Souvent oui : un outil bat un agent à tous les
   critères — coût, latence, testabilité, débogabilité.) Une récupération
   suffit-elle ? Une CAP confiée à un agent alors qu'un outil suffisait se
   **signale** ; il ne retire pas l'agent.
4. Pour chaque agent au-delà du premier, chercher l'une des cinq raisons closes
   de P7 — isolation de scope d'outils, tier distinct, pression de contexte
   mesurée, fonction objectif différente, parallélisme requis. Aucune →
   `[TOPOLOGY_SIMPLICITY_ADVISORY]` avec le coût que cet agent ajoute : un
   chiffre, pas une opinion. La section **« Alternative plus simple
   considérée »** est consultative au même titre : ce qu'on aurait fait à N-1
   agents, et ce que ça aurait coûté en moins.
5. Produire le graphe (bloc ```mermaid de `## 4. Le graphe`, dans la topologie
   elle-même), les cinq bornes de chaque agent avec leur comportement à
   l'atteinte, et le **budget estimé** par chemin — nominal et pire cas. Pire cas
   au-dessus du plafond de la MISSION → `[BUDGET_EXCEEDED_ESTIMATE]` : il rend la
   main avec le chiffre et les trois leviers, sans en choisir aucun.

Son estimation est une **hypothèse** (ARCHITECTURE §5) : le fait vient après
lui, de `estimate-budget` sur l'IR compilé, qui écrit la part `budget` de G2.

Il **n'écrit pas** de prompt, **ne choisit pas** de modèle (il choisit un tier),
**ne nomme aucune API** de framework, et ne renomme, n'ajoute ni ne retire aucun
agent du roster (`[ARCH_ROSTER_MUTATED]`).

### `qa-evals` — celui sans qui rien n'est prouvé

Le seul autorisé à écrire dans `workspace/pipeline/datasets/`. Il part deux
fois : en **6a**, avant le code (`/sdda-eval {n} --datasets-only`), pour que les
jeux existent avant qu'un `dev-*` puisse s'y ajuster ; puis en **6**, pour
compléter les suites. Produit :

- **golden set** (ajustement) et **holdout** (verdict), disjoints, vérifiés par hash ;
- **set de calibration** : ≥ 50 items labellisés humainement
  (`JudgeCalibrationMinItems`), pour valider chaque juge LLM avant qu'il ne rende
  un verdict bloquant (P9) — un juge non calibré passe en advisory ;
- **set adversarial** : injections directes et indirectes, jailbreaks, tentatives
  d'abus d'outils, exfiltration ;
- les **suites** d'eval, leurs **graders** et leurs seuils.

Les **baselines** (P10), il ne les écrit pas : `workspace/pipeline/baselines/**`
est réservé au script `python .sdda/sdda.py promote-baseline`. Une baseline se
déplace par une action tracée, jamais par écrasement.

Il a un droit de veto : une CAP dont l'AC n'est pas mesurable lui revient, et il
la renvoie à `po-capabilities` avec `[AC_NOT_EVALUABLE]`.

### `review-safety`

Remplace et élargit le `security-reviewer` de SDD_Pro. Sa grille : injection
directe et **indirecte** (corpus empoisonné, réponse d'API, page web), excès de
scope d'outils par rapport aux CAPs, opérations destructives sans stratégie de
sûreté, fuite de secrets vers prompts/traces/datasets, PII dans le vector store,
escalade de privilège par délégation, exfiltration via un outil sortant.
Il **lit** les rapports des scans déterministes (`scan-secrets`, `scan-pii`,
`audit-tool-scope`), joués une seule fois avant l'étage B ; il ne les relance
pas. Ses classes `[SAFETY_*]` sont bloquantes. `AgentSafetyMode: off` est refusé
sur un run de production ou de CI.

---

## 3. Étage de revue — deux étages puis adversarial

Hérité de la revue en deux étages de SDD_Pro, adapté — l'étagement est le
déroulé de `/sdda-review`, pas une clé de configuration :

```
Étage A   review-spec  (SEUL)
          → chaque AC de CAP est-elle couverte par une eval qui la mesure
            vraiment, et non par une eval qui la contourne ?
          Verdict ROUGE → les étages B et C ne se lancent pas.

Scans     scan-secrets · scan-pii · audit-tool-scope   (0 token, une fois)

Étage B   agent-safety · cost-latency · orchestration · rag-quality   (PARALLÈLE)

Étage C   review-adversarial  (sur le système VIVANT, pas sur le code)
          → attaque réelle, trajectoires inattendues, entrées limites
          puis run-adversarial-suite : le set versionné, joué EN LIVE
```

Raison de l'étage A seul : agréger des findings de qualité, de coût et de
sécurité sur un système qui ne fait pas ce que la spec demande est du gaspillage.
On vérifie d'abord qu'on regarde le bon système.

Raison des scans avant l'étage B : une mesure, un producteur, un moment — avant
les lecteurs. Joués deux fois, la seconde exécution écrasait la part que la
première avait fait lire au reviewer.

Raison de l'étage C séparé : l'adversarial n'a de sens qu'exécuté. Relire du code
pour chercher une faille d'injection donne un avis ; lancer 40 injections contre
le système donne un fait. `review-adversarial` improvise et consigne chaque
attaque réussie comme **finding** ; le set versionné, lui, est joué par un
script (`python .sdda/sdda.py run-adversarial-suite`), qui enregistre lui-même
chaque exécution et écrit la part `adversarial` de G7. Un rapport de reviewer
obligatoire absent n'est pas « zéro finding » : c'est
`[SAFETY_REVIEW_REPORT_MISSING]`.

---

## 4. Orchestration interne — le parallélisme et ce qui le borne

| Phase | Parallélisable | Barrière |
|---|---|---|
| 2 — architecture | `architect-tools` ∥ `architect-rag` ∥ `architect-data` ∥ `architect-memory` | `architect-topology` d'abord (il fixe le périmètre), contrats des sources déclarées générés par script (`gen-source-tools --scope contracts`), puis compilation IR |
| 6a — datasets | `qa-evals` seul | G2 verte ; les jeux existent avant le code |
| 3 — socle | `dev-tools` ∥ `dev-retrieval` ∥ `dev-data` | `dev-backend` (squelette) seul d'abord ; code des sources généré (`gen-source-tools --scope code`) avant `dev-data` ; TOOL GATE + RETRIEVAL GATE avant la phase 4 |
| 4 — agents | 1 `dev-agent` par agent, en vagues parallèles | `dev-orchestration --prepass` (4.0) puis `dev-prompt` (4.1), seuls ; AGENT GATE après |
| 5 — orchestration | aucun : séquentielle | `dev-orchestration` → `dev-api` → `dev-backend` (packaging) ; ORCH GATE après |
| 6 — eval/tests | `qa-evals` ∥ `qa-tests` (une invocation par couche) | datasets figés avant l'exécution des evals |
| 7B — revue | 4 reviewers, en vagues ≤ `MaxParallel` | étage A vert, scans joués |

Le parallélisme est borné par `MaxParallel` et rendu sûr par la matrice
d'ownership, et cette sûreté se vérifie à trois moments plutôt que de se
supposer :

- **Au spawn et à chaque écriture.** Les globs d'ownership sont segmentés (`*`
  reste dans un segment, `**` en couvre plusieurs) et les recouvrements réels
  entre zones sont calculés (`audit-ownership --declared-only`). Chaque instance
  de `dev-agent` porte `SDDA-INSTANCE: {agent}` en première ligne de son prompt :
  le hook `preflight_instance_bind` la déclare, sa première écriture la lie à son
  agent, et une écriture sous `agents/{autre}/` est refusée
  (`[OWNERSHIP_INSTANCE_ESCAPE]`). Sans cette ligne, le spawn est refusé
  (`[OWNERSHIP_INSTANCE_UNDECLARED]`).
- **Sur le disque, après chaque vague.** `audit-ownership snapshot` avant la
  vague, `audit-ownership --since-snapshot` après (phases 3, 4.0, 4 et 5) : ce
  que les hooks ne voient pas — un script qui écrit de l'intérieur — se voit
  dans l'écart. Une écriture hors zone se révoque par `--restore`.
- **Sur les zones gelées.** Les types partagés et l'interface mémoire que la
  pré-passe 4.0 pose sont gelés pendant les phases 4 et 5 ; les toucher est
  `[OWNERSHIP_FROZEN_ZONE_CHANGED]`.

Deux `dev-agent` concurrents écrivent ainsi dans des `src/**/agents/{agent}/`
disjoints et ne partagent aucun fichier en écriture — non parce qu'on le leur a
demandé, mais parce que le hook et l'audit le constatent.
