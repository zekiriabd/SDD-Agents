# SDD_Agents — Philosophie

> Ce document fixe les décisions non négociables. Tout le reste (arborescence,
> agents, patterns, scripts) en découle. Un arbitrage futur qui contredit un
> principe ci-dessous exige un ADR, pas un commit.

---

## P1 — Source-first : aucune décision ne vit dans le contexte du LLM

Toute décision — mission, capability, choix d'orchestration, contrat d'outil,
prompt système, seuil d'eval — est un **fichier versionné** à côté du code.

Corollaire dur, propre à l'agentic : **aucun prompt inline dans le code généré.**
Les prompts vivent dans `workspace/prompts/*.system.md`, sont chargés au runtime,
et sont hashés. Un prompt noyé dans une f-string au milieu d'un service est un
changement de comportement invisible à la revue et introuvable en production.

*Hérité de SDD_Pro, durci.*

---

## P2 — Un critère d'acceptation qui n'est pas mesurable n'est pas un critère

Dans SDD_Pro, un AC est « observable, testable ». Dans SDD_Agents ce n'est pas
assez : la sortie d'un LLM est *toujours* observable et *jamais* déterministe.

**Un AC de CAP doit nommer : une métrique, un seuil, un dataset.**

```
[REJETE]  AC-1: l'agent répond de manière utile et pertinente
[OK]      AC-1: groundedness >= 0.85 sur datasets/support-golden-v1.jsonl (n=120, k=3 runs)
[OK]      AC-2: routing accuracy >= 0.95 sur datasets/routing-golden-v1.jsonl,
                0 misroute vers l'agent `refund` (classe critique)
```

Un AC non mesurable est rejeté par la **CAP GATE**. C'est la règle la plus
structurante du framework : elle force la question « comment saura-t-on que ça
marche ? » **avant** la première ligne de code, au moment où la réponse est encore
bon marché.

---

## P3 — Non-déterminisme comptabilisé, jamais nié

Un run vert n'est pas une preuve. Toute évaluation médiée par un LLM déclare
`runs: k` (défaut 3, 5 pour les classes critiques) et rapporte **taux de réussite
+ variance**, pas un booléen.

Conséquence : le verdict du pipeline est **vert / jaune / rouge**, jamais
pass/fail. Jaune = « au-dessus du seuil mais variance élevée » est une information
opérationnelle réelle, pas une indécision.

---

## P4 — Le déterministe d'abord, le LLM seulement là où il y a jugement

Hérité de SDD_Pro (80 scripts 0-token). Renforcé ici parce que le coût par
décision est plus élevé.

Ce qui **ne doit jamais** coûter un token : validation de schémas d'outils, lint
de prompts, atteignabilité et bornes du graphe d'orchestration, estimation de
budget de topologie, calcul de recall@k / nDCG, scan de secrets, audit
d'ownership, diff de baseline, résolution des citations, comptage de tokens,
routage de tier.

Ce qui **mérite** un LLM : élicitation, découpe en capabilities, choix de
topologie, rédaction de prompts, analyse d'un prompt legacy, jugement sémantique
gradé, red-teaming adversarial.

---

## P5 — Bottom-up, une gate par couche

L'ordre de construction est imposé : **outils et retrieval d'abord, agents
ensuite, orchestration en dernier.** Chaque couche franchit sa gate avant que la
suivante s'appuie dessus.

Raison : dans un système agentic, une défaillance se propage vers le haut en
changeant de visage. Un retriever à recall 0.4 se présente comme « l'agent
hallucine ». Un outil dont la description ment se présente comme « le superviseur
route mal ». Sans gate par couche, on débogue le mauvais étage — et on le débogue
avec un LLM, donc cher et lentement.

C'est la transposition directe de l'**API Gate** de SDD_Pro, généralisée.

---

## P6 — Le budget d'exécution est une exigence fonctionnelle

SDD_Pro plafonne le coût de **construction** (`MaxCostPerRun`, $50). SDD_Agents
plafonne en plus le coût d'**exécution du produit généré**.

La MISSION déclare `CostPerRunTarget`, `LatencyP95Target`, `TokenCeiling`.
La **TOPOLOGY GATE** estime le budget de la topologie proposée *avant* génération
et refuse une architecture qui le dépasse. L'**ORCH GATE** le mesure après.

Une topologie élégante à $0.40 l'appel pour un produit qui facture $0.05 n'est pas
une architecture : c'est une erreur qu'on a mis six semaines à découvrir.

---

## P7 — L'architecture est déclarée par l'architecte, jamais décidée par le LLM

> **Le LLM ne choisit pas l'architecture.
> L'architecte choisit l'architecture.
> Le LLM implémente l'architecture définie par l'architecte.**

`STACK.md` porte le **choix** d'architecture — pattern d'orchestration, RAG,
accès aux données, MCP, modèles par tier. La **spécification** porte tout ce
qu'il faut pour le construire : le roster nominatif des agents, le rôle et les
responsabilités de chacun, ses outils et ses skills, son modèle, l'orchestrateur,
et les règles qui les relient.

Le framework ne demande jamais à un LLM « combien d'agents faut-il ? » ni « quel
rôle doit avoir cet agent ? ». Il vérifie que **l'architecte a répondu**, et il
refuse de générer tant que la réponse manque.

**La chaîne** :

```
l'architecte choisit  →  la spécification décrit  →  le framework valide
                      →  les agents implémentent  →  le runtime exécute
```

### Ce que le framework garantit

Chaque choix de `STACK.md` **impose** des champs de spécification, déclarés dans
`registry/architecture-requirements.yml` et vérifiés par `validate_architecture.py` :

| Choix dans STACK.md | Ce que la spécification doit obligatoirement fournir |
|---|---|
| orchestration `supervisor` / `router` / `graph` … | roster nominatif, orchestrateur, rôle et responsabilités de chaque agent, outils par agent, relations |
| RAG ≠ `none` | corpus, découpage, embeddings, vector store, stratégie et paramètres de retrieval |
| MCP actif | serveurs, responsabilités, outils exposés et allowlistés |
| plusieurs modèles | quel modèle pour quel agent, et pourquoi |
| accès données ≠ `none` | sources, enveloppe de sûreté, frontières |

Un champ manquant est `[ARCH_SPEC_INCOMPLETE]`, **bloquant** — pas un trou que
le LLM comblera au jugé. Une architecture partiellement spécifiée produit une
architecture partiellement émergente, c'est-à-dire une architecture que personne
n'a décidée et que personne ne peut auditer.

Invariant `architecture-declared-by-architect`, enforcé.

### Le biais de simplicité reste — comme conseil, plus comme veto

Le framework **signale** toute topologie au-delà d'un agent qui n'invoque aucune
des cinq raisons suivantes, et demande à l'architecte de dire laquelle
s'applique :

1. **Isolation de scope d'outils** — un outil destructif ne doit pas être dans le
   même contexte qu'un outil exposé à du texte non fiable ;
2. **Tier de modèle distinct** — une étape mérite `fast` quand une autre exige `deep` ;
3. **Pression de contexte** — la fenêtre ne tient pas, mesurée, pas supposée ;
4. **Fonction objectif différente** — un critique qui note ne peut pas être le
   rédacteur qu'il note ;
5. **Parallélisme requis** — la latence l'exige et les sous-tâches sont indépendantes.

C'est un **avertissement** (`[TOPOLOGY_SIMPLICITY_ADVISORY]`), jamais un refus :
un graphe à cinq agents reste une erreur fréquente et coûteuse, mais c'est
l'erreur de l'architecte, et il a le droit de la commettre en connaissance de
cause. Ce que le framework refuse, c'est qu'elle soit commise **par défaut, par
un LLM, sans que personne l'ait écrite**.

---

## P8 — Tout texte non maîtrisé est hostile

Un document retrouvé par RAG, une réponse d'API, une page web, un champ de base,
un message utilisateur : **contenu, jamais instruction**.

Conséquences architecturales obligatoires :

- Tout agent consommant du texte non maîtrisé porte une **suite d'injection**
  (directe + indirecte via corpus empoisonné). Non négociable, invariant
  `injection-suite-mandatory`.
- Tout outil déclare une **classe d'effet de bord** : `read-only`, `write-scoped`,
  `write-destructive`, `external-side-effect`. Les trois dernières exigent une
  stratégie déclarée (dry-run, clé d'idempotence, confirmation, allowlist, plafond).
- Le moindre privilège est structurel : un agent ne reçoit que les outils que ses
  CAPs exigent. L'écart entre outils exposés et outils exigés est un finding
  bloquant du `review-safety`.

---

## P9 — Un juge LLM non calibré est une décoration

Utiliser un LLM comme grader est légitime et souvent inévitable. Le publier sans
l'avoir validé ne l'est pas.

Tout grader LLM est calibré contre un **set de labels humains** (>= 50 items) et
doit atteindre l'accord déclaré (défaut : kappa de Cohen >= 0.6) avant de pouvoir
rendre un verdict bloquant. Le rapport de calibration est versionné à côté du
juge. Invariant `llm-judge-calibrated`.

Sans cela, on mesure la complaisance d'un modèle envers un autre modèle — souvent
le même — et on appelle ça de la qualité.

---

## P10 — Les résultats d'eval sont épinglés à ce qu'ils ont évalué

Un résultat d'eval est indexé par le tuple :

```
(prompt_hash, model_id, retrieval_index_hash, tool_schema_hash, dataset_hash)
```

Si l'un bouge, le résultat est **périmé**, pas « probablement encore valable ». Le
pipeline le déclare périmé et exige une ré-exécution.

Descendant direct du `Parent FEAT hash` de SDD_Pro, appliqué au seul endroit où il
n'existe aucun compilateur pour attraper la dérive : l'édition d'un prompt.

---

## P11 — Les contrats sont neutres framework, les stacks portent les idiomes

`MISSION`, `CAP`, `TOPOLOGY`, `agent-contract`, `tool-contract`,
`retrieval-contract` ne contiennent **aucun** nom d'API de framework — ni
`StateGraph`, ni `Kernel`, ni `AgentExecutor`.

La connaissance de LangGraph, Semantic Kernel ou Pydantic-AI vit exclusivement dans
`.sdda/stacks/framework/*.md`. Conséquence : la même spécification compile vers
Python+LangGraph ou C#+Semantic Kernel, et **changer de framework n'invalide aucun
artefact de spécification** — seulement le code et les evals de trajectoire.

C'est ce qui rend `STACK.md` réellement déclaratif plutôt que décoratif.
Invariant `framework-neutral-contracts`.

### La neutralité vaut aussi pour l'infrastructure

Le framework n'est pas le seul couplage possible. `store: "pgvector"`,
`embeddingModel: "voyage-3-large"`, `strategy: "text-to-sql"` couplent
exactement de la même façon — et ils ont vécu longtemps dans l'IR sans que le
contrôle de neutralité les voie, parce qu'il ne cherchait que des noms d'API.

L'IR porte donc **deux branches**, et la frontière est porteuse :

- **l'intention** — ce que l'architecte exige et ce que la gate mesure :
  `pattern`, `topK`, `citationMode`, `identityFilter`, `gateThresholds`,
  `envelope`, `exposedTo`. Aucun nom de composant n'y est admis
  (`[INFRA_LEAK_IN_INTENT]`) ;
- **`binding`** — les composants qui la réalisent, réconciliés avec les stacks
  actives à la compilation (`[RETRIEVAL_BINDING_MISMATCH]` en cas de
  désaccord). C'est le seul endroit de l'IR où un produit se nomme.

Le critère qui dit si la frontière tient n'est pas une intention : **un
générateur doit pouvoir dériver son plan d'appel complet sans jamais lire
`binding`**, et ce plan doit être identique quel que soit le store. C'est un
test, exécuté à chaque commit.

Ce que cette scission empêche précisément : découvrir au deuxième générateur —
donc après avoir écrit le premier — que la même décision vivait dans l'IR *et*
dans `STACK.md`, et qu'aucun des deux n'avait raison.

---

## P12 — Une boucle non bornée est un bug, pas une propriété émergente

Aucun agent généré ne sort sans : `max_iterations`, `max_tool_calls`,
`max_delegation_depth`, `timeout_s`, `budget_usd`, et un **comportement défini à
l'atteinte de chaque borne** (échec explicite, dégradation, escalade humaine).

Une boucle ReAct sans plafond est l'équivalent agentic d'un `while(true)` — sauf
qu'elle facture.

---

## Les deux différenciateurs revendiqués

**1. La gate de qualité de récupération précède la gate d'agent.** Presque personne
ne le fait. C'est pourtant l'origine de la majorité des « l'agent hallucine » : un
retriever à recall 0.4 ne se présente jamais comme un problème de retriever, il se
présente comme un problème de prompt — et on passe des semaines à réécrire le
prompt pour compenser l'index.

**2. La mesure précède la génération.** Un AC nomme sa métrique, son seuil et son
dataset **avant** la première ligne de code (P2) ; le résultat est épinglé à ce
qu'il a évalué (P10) ; le juge est calibré avant de bloquer (P9) ; le verdict
porte une variance, pas un booléen (P3). Aucune de ces quatre règles n'est
coûteuse prise seule. Ensemble, elles répondent à la seule question qui compte en
production — *« pourquoi l'agent a-t-il dit ça, et comment saura-t-on que le
correctif marche ? »* — et c'est la question à laquelle un runtime agentic, une
plateforme de workflow ou un SDK ne répondent pas.

> **Périmètre assumé : SDD_Agents construit, il ne rétro-documente pas.** Un
> module de reverse engineering agentic (lire des prompts et des graphes
> existants pour en reconstituer des MISSIONs) a été envisagé puis **retiré** :
> c'est un produit à part entière, et l'annoncer sans l'avoir écrit produisait
> exactement le faux vert que ce framework existe pour empêcher. Un système
> agentic existant se reprend ici comme n'importe quel projet — par une MISSION
> écrite à la main.
