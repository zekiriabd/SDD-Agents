# Les Developer Agents de SDD_Agents

<!--sdda:count agents-->22<!--/sdda:count--> agents spécialisés. **Ce sont les agents qui *construisent*** — à ne pas
confondre avec les agents du produit généré, décrits dans `contracts/agents/`.

Règle d'orchestration interne héritée de SDD_Pro : **aucun agent ne spawne un
autre agent.** La commande orchestre, l'agent exécute. C'est ce qui rend la
facture de construction prévisible et le parallélisme bornable (`MaxParallel`).

## 0. La convention de nommage — `{métier}-{domaine}`

Cinq préfixes, un par métier, dans l'ordre du pipeline :

| Préfixe | Métier | Phase | Agents |
|---|---|:---:|---|
| `po-` | product owner — le besoin et son découpage | 0-1 | `po-elicitor`, `po-capabilities` |
| `architect-` | architecture — les décisions de structure | 2 | `architect-topology`, `architect-rag`, `architect-data`, `architect-memory`, `architect-tools` |
| `dev-` | implémentation | 3-5 | `dev-tools`, `dev-retrieval`, `dev-data`, `dev-prompt`, `dev-agent`, `dev-orchestration`, `dev-api` |
| `qa-` | ce qui prouve | 6 | `qa-tests`, `qa-evals` |
| `review-` | ce qui conteste | 7 | `review-spec`, `review-safety`, `review-cost`, `review-orchestration`, `review-rag`, `review-adversarial` |

Ce n'est pas de la cosmétique. La nomenclature d'origine appelait **cinq**
agents `*-architect` alors qu'un seul décide de l'architecture au sens où un
architecte l'entend : `architect-topology` fixe le périmètre, les quatre autres
travaillent dedans. Et la phase fonctionnelle — celle où l'on demande ce que le
système doit faire et pour qui — n'avait aucun nom qui la désigne, alors qu'elle
porte deux agents.

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
> dataset, k runs (cf. `SDD-PRO-INHERITANCE.md §3`). Il n'y a pas de niveau
> intermédiaire entre MISSION et CAP — en ajouter un dupliquerait la traçabilité
> sans rien mesurer de plus.

---

## 1. Tableau de bord

| Agent | Phase | Tier | Écrit dans | La question qu'il pose |
|---|:---:|:---:|---|---|
| **`po-elicitor`** | 0 | balanced | `missions/` | Quelle est la vérité contre laquelle on jugera, et que fait le système quand il ne sait pas ? |
| **`po-capabilities`** | 1 | balanced | `caps/` | Quelles compétences discrètes, et comment mesure-t-on chacune ? |
| **`architect-topology`** | 2 | **deep** | `topology/` | Quelle est la topologie la plus simple qui tienne, et pourquoi pas plus simple encore ? |
| **`architect-tools`** | 2 | balanced | `contracts/tools/` | Quel contrat, quels effets de bord, quelle sûreté ? |
| **`architect-rag`** | 2 | **deep** | `contracts/retrieval/` | Quel corpus, quel découpage, quelle stratégie, quel golden set ? |
| **`architect-data`** | 2 | balanced | `contracts/tools/`, ADR | Comment l'agent touche la base sans pouvoir lui nuire ? |
| **`architect-memory`** | 2 | balanced | `contracts/memory/` | Qu'est-ce qui persiste, pour combien de temps, avec quelles PII ? |
| **`dev-prompt`** | 4 | **deep** | `prompts/` | Comment ce contrat devient-il un prompt qui tient sous adversité — outils **et skills** compris ? |
| **`dev-tools`** | 3 | balanced | `src/tools/` | — implémente |
| **`dev-retrieval`** | 3 | balanced | `src/retrieval/` | — implémente ingestion + retriever |
| **`dev-data`** | 3 | balanced | `src/data/` | — implémente vues, repositories, enveloppe |
| **`dev-agent`** | 4 | **deep** | `src/agents/{agent}/` | — implémente un agent (1 instance par agent) |
| **`dev-orchestration`** | 5 | **deep** | `src/orchestration/` | — implémente le graphe/superviseur/routeur |
| **`dev-api`** | 5 | balanced | `src/serving/` | — implémente la surface d'exposition |
| **`qa-evals`** | 6 | **deep** | `datasets/`, `evals/` | Quel jeu, quel grader, quel seuil, calibré comment ? |
| **`qa-tests`** | 6 | balanced | `src/**/tests/` | — tests déterministes L0→L2 |
| **`review-spec`** | 7A | balanced | rapports | Chaque AC de CAP a-t-elle une eval qui la couvre vraiment ? |
| **`review-safety`** | 7B | **deep** | rapports | Où passe le texte hostile, et que peut-il déclencher ? |
| **`review-cost`** | 7B | fast | rapports | Combien ça coûte vraiment, et où part l'argent ? |
| **`review-orchestration`** | 7B | balanced | rapports | Des hops inutiles, des boucles, des impasses, des handoffs sans contrat ? |
| **`review-rag`** | 7B | balanced | rapports | Le retrieval tient-il, ou l'agent compense-t-il ? |
| **`review-adversarial`** | 7C | **deep** | rapports | Comment je casse ce système maintenant qu'il tourne ? |

Bornes `tier_floor` / `tier_ceiling` par agent : `.sdda/agent-bounds.yaml`.
Elles sont des invariants de qualité, non surchargeables par le Project Config.

`dev-prompt` porte une seconde responsabilité que la colonne « Écrit dans »
ne montre pas : il est l'**owner d'implémentation des skills** des agents du
produit. `architect-topology` les *déclare* au §5 du contrat d'agent,
`dev-prompt` les *implémente* dans `prompts/{agent}.system.md`
(`## Compétences`), et aucun des deux ne peut écrire chez l'autre — donc aucun ne
peut résoudre seul un désaccord. `lint_prompts.py` constate la correspondance
dans les deux sens : `[SKILL_NOT_IMPLEMENTED]`, `[SKILL_UNDECLARED]`. Une skill
n'ayant ni schéma ni effet de bord, c'est la seule vérification possible : aucune
gate ne peut l'exécuter pour la juger. Détail : `rules/ownership.md §2.2`.

---

## 2. Les agents qui portent la valeur du framework

### `architect-topology` — l'agent signature

Il n'a pas d'équivalent dans SDD_Pro, et c'est lui qui décide si le produit sera
maintenable ou un plat de spaghettis à $0.40 l'appel.

**Procédure imposée**, dans cet ordre :

1. Partir de **un agent + des outils**. Toujours. Sans exception.
2. Pour chaque CAP, demander : un outil suffit-il ? (Souvent oui. Un outil
   déterministe bat un agent à tous les critères : coût, latence, testabilité,
   débogabilité.)
3. N'escalader vers un agent supplémentaire qu'en invoquant **explicitement** une
   des cinq raisons closes de P7 — isolation de scope d'outils, tier distinct,
   pression de contexte mesurée, fonction objectif différente, parallélisme requis.
4. Écrire dans `topology/{n}-topology.md` la section **« Alternative plus simple
   écartée »** : quelle topologie à N-1 agents a été envisagée, et quel critère
   précis la disqualifie. Section vide = `[TOPOLOGY_UNJUSTIFIED]`, bloquant.
5. Produire le graphe (`{n}-topology.mmd`, Mermaid) et le **budget estimé** par
   chemin.

Il **n'écrit pas** de prompt, **ne choisit pas** de modèle (il choisit un tier),
**ne nomme aucune API** de framework.

### `qa-evals` — celui sans qui rien n'est prouvé

Le seul autorisé à écrire dans `workspace/datasets/`. Produit :

- **golden set** (ajustement) et **holdout** (verdict), disjoints, vérifiés par hash ;
- **set de calibration** : ≥ 50 items labellisés humainement, pour valider chaque
  juge LLM avant qu'il ne rende un verdict bloquant (P9) ;
- **set adversarial** : injections directes et indirectes, jailbreaks, tentatives
  d'abus d'outils, exfiltration ;
- les **graders** et leurs seuils ;
- les **baselines** épinglées au tuple de hashes (P10).

Il a un droit de veto : une CAP dont l'AC n'est pas mesurable lui revient, et il
la renvoie à `po-capabilities` avec `[AC_NOT_EVALUABLE]`.

### `review-safety`

Remplace et élargit le `security-reviewer` de SDD_Pro. Sa grille : injection
directe et **indirecte** (corpus empoisonné, réponse d'API, page web), excès de
scope d'outils par rapport aux CAPs, opérations destructives sans stratégie de
sûreté, fuite de secrets vers prompts/traces/datasets, PII dans le vector store,
escalade de privilège par délégation, exfiltration via un outil sortant.
`AgentSafetyMode: off` est refusé sur un run de production.

---

## 3. Étage de revue — deux étages puis adversarial

Hérité de SDD_Pro (`AuditorBatchMode: two-stage`), adapté :

```
Étage A   review-spec  (SEUL)
          → chaque AC de CAP est-elle couverte par une eval qui la mesure
            vraiment, et non par une eval qui la contourne ?
          Verdict ROUGE → les étages B et C ne se lancent pas.

Étage B   agent-safety · cost-latency · orchestration · rag-quality   (PARALLÈLE)

Étage C   review-adversarial  (sur le système VIVANT, pas sur le code)
          → attaque réelle, trajectoires inattendues, entrées limites
```

Raison de l'étage A seul : agréger des findings de qualité, de coût et de
sécurité sur un système qui ne fait pas ce que la spec demande est du gaspillage.
On vérifie d'abord qu'on regarde le bon système.

Raison de l'étage C séparé : l'adversarial n'a de sens qu'exécuté. Relire du code
pour chercher une faille d'injection donne un avis ; lancer 40 injections contre
le système donne un fait.

---

## 4. Orchestration interne — le parallélisme et ce qui le borne

| Phase | Parallélisable | Barrière |
|---|---|---|
| 2 — architecture | `architect-tools` ∥ `architect-rag` ∥ `architect-data` ∥ `architect-memory` | `architect-topology` d'abord (il fixe le périmètre), puis compilation IR |
| 3 — socle | `dev-tools` ∥ `dev-retrieval` ∥ `dev-data` | TOOL GATE + RETRIEVAL GATE avant la phase 4 |
| 4 — agents | 1 `dev-agent` par agent, en parallèle | `dev-prompt` d'abord ; AGENT GATE après |
| 6 — eval/tests | `qa-evals` ∥ `qa-tests` | datasets figés avant l'exécution des evals |
| 7B — revue | 4 reviewers en parallèle | étage A vert |

Le parallélisme est borné par `MaxParallel` et rendu sûr par la matrice
d'ownership : deux `dev-agent` concurrents écrivent dans
`src/agents/{agent}/` disjoints et ne partagent aucun fichier en écriture.
