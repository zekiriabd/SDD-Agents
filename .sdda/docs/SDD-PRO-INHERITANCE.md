# Héritage SDD_Pro — ce qu'on reprend, ce qu'on refuse, ce qu'on ajoute

Analyse du dépôt SDD-Pro (v7.0.3-dev, 1 387 fichiers, 29 agents,
41 commandes, 36 stacks, 31 invariants, ~2 542 tests Python).

---

## 1. Ce que SDD_Pro fait, en substance

SDD_Pro force la trajectoire inverse d'un assistant de code ordinaire. Un
assistant part du code et remonte vers l'intention ; SDD_Pro part de l'intention
et **verrouille chaque descente derrière une gate déterministe** :

```
FEAT (spec métier versionnée)
  └─ User Stories (IDs stables, AC traçables)
       └─ Plans techniques (fichiers, couches, contrats preserves/adds)
            └─ Code (backend d'abord → API Gate → frontend)
                 └─ QA + 5 reviewers (code, sécurité, spec, archi, adversarial)
```

Les mécanismes qui font que ça tient, et non un pipeline de prompts enchaînés :

| # | Mécanisme | Ce qu'il empêche concrètement |
|---|---|---|
| 1 | **Source-first** — chaque décision est un `.md` versionné, rien dans le contexte | La spec dérive dans le prompt et n'est jamais relue |
| 2 | **`stack.md` déclaratif**, 55 clés, lu par tous les agents à chaque invocation | Le choix technique se re-négocie à chaque agent |
| 3 | **Catalogue de stacks** : `.md` (mapping de couches, idiomes, smoke) + `.libs.json` (versions épinglées, CVE, LTS) | L'agent invente une lib ou une version |
| 4 | **Matrice d'ownership** — 1 fichier = 1 owner, ou mode d'écriture sérialisé | Les agents parallèles s'écrasent silencieusement |
| 5 | **`loader.yml`** — `reads` / `writes` / `forbidden_reads` + budget de tokens + couche de cache par agent | Un agent lit ce qui ne le regarde pas et explose son budget |
| 6 | **Taxonomie `[CLASS]`** — 193 classes, tout `ERROR` en porte une | Les hooks doivent interpréter du texte libre |
| 7 | **`INVARIANTS.yml`** — chaque contrat porteur pointe son **enforcer sur disque** ; un test échoue si l'enforcer disparaît | Le doc-theater : une règle écrite que plus rien n'applique |
| 8 | **Déterministe d'abord** — 80 scripts 0-token | On paie un LLM pour compter des lignes |
| 9 | **Abstraction tier** — les agents déclarent `fast/balanced/deep`, le provider résout | Changer de provider = toucher 29 agents |
| 10 | **Plafond de coût** — `MaxCostPerRun` $50, hard stop | La facture se découvre après |
| 11 | **API Gate** — contrat back↔front validé en mémoire avant le front | Le front appelle un endpoint qui n'existe pas |
| 12 | **Reviewers en deux étages** — spec-compliance seul, puis code/sécurité/archi en parallèle, puis adversarial | Agréger des findings qualité sur du code qui ne respecte pas la spec |
| 13 | **Compilation multi-harnais** — `.sdd/` → `.claude/`, `.codex/`, `.gemini/` | Le framework est prisonnier d'un outil |
| 14 | **Hash de FEAT dans l'US** (`Parent FEAT hash: sha256:…`) | La FEAT bouge sous les US sans que rien ne le dise |
| 15 | **Faits ≠ hypothèses** — un script écrit les faits, un agent écrit ses hypothèses dans un fichier séparé ; les deux ne se mélangent jamais | Une hypothèse LLM devient un critère d'acceptation |
| 17 | **Routeur déterministe de tier** — 70-80% des objets analysés à 0 token | Payer Opus pour un accesseur CRUD |
| 18 | **Cache par objet + idempotence** | Un run interrompu recoûte le prix plein |
| 19 | **Packs de contexte tranchés par rôle**, qui **déclarent ce qu'ils ont retiré** | L'agent invente ce qu'il n'a pas vu au lieu de baisser sa confiance |

---

## 2. Reprise intégrale (mécanisme identique, périmètre élargi)

1, 2, 4, 5, 6, 7, 8, 9, 10, 13, 15, 18, 19, 20 — repris **tels quels**. Ce sont
des mécanismes d'ingénierie de framework, indifférents à la nature du produit
généré.

Trois méritent une note sur l'élargissement :

- **`stack.md` → `STACK.md`** : 55 clés deviennent ~90, organisées en blocs
  agentic (framework, orchestration, RAG, retrieval, data access, mémoire, outils,
  guardrails, observabilité, eval, serving). Le mécanisme est identique : un
  fichier déclaratif, gitignored, lu par tous, qui rend l'architecture technique
  éditable sans toucher le code.

- **Catalogue de stacks** : le couple `{stack}.md` + `{stack}.libs.json` est
  repris à l'identique. `.md` porte le mapping de couches, les idiomes, la
  commande de smoke ; `.libs.json` porte les versions épinglées. Les catégories
  changent (framework agentic, pattern d'orchestration, pattern RAG, vector store,
  embedding, stratégie d'accès base, guardrails, observabilité, eval, serving).

- **Matrice d'ownership** : reprise, avec **une règle nouvelle et non
  négociable** — `dev-agent` n'a aucun droit d'écriture sur `workspace/pipeline/datasets/`
  ni `workspace/src/{App}/prompts/`. L'agent qui écrit le code ne peut ni modifier le jeu
  qui le juge, ni réécrire le prompt qu'il est censé implémenter. C'est le
  pendant agentic de `[QA_OWNERSHIP_VIOLATION]`, et c'est plus grave ici : sans
  cette barrière, l'auto-confirmation n'est pas un risque, c'est le résultat par
  défaut.

---

## 3. Reprise transposée (même idée, forme différente)

| SDD_Pro | SDD_Agents | Pourquoi la transposition |
|---|---|---|
| **FEAT** | **MISSION** | Ajoute `budget`, `ground_truth`, `trust_boundaries`, `failure_policy` — quatre champs sans équivalent classique, chacun obligatoire |
| **User Story** | **CAPABILITY** | Un AC « observable et testable » devient « métrique + seuil + dataset + k runs ». Une sortie de LLM est toujours observable et jamais déterministe |
| **Plans techniques** | **CONTRACTS** (agent / tool / retrieval / memory) **+ Agentic IR** | Le plan devient une structure machine, validable et diffable, consommée par N générateurs de framework |
| **API Gate** (back↔front) | **TOOL GATE + RETRIEVAL GATE + AGENT GATE + ORCH GATE** | Une seule couture chez SDD_Pro ; quatre ici, parce que chaque couche échoue en se déguisant en la couche du dessus |
| **`Parent FEAT hash`** | **Eval pinning** `(prompt, model, index, tool_schema, dataset)` | L'édition de prompt est le seul changement de comportement qu'aucun compilateur n'attrape |
| **QA (tests)** | **TESTS + EVALS** (L0→L9) | `assertEquals` ne s'applique qu'à la moitié déterministe du système |
| **5 reviewers** | **6 reviewers** : spec-compliance, agent-safety, cost-latency, orchestration, rag-quality, adversarial | `security-reviewer` devient `review-safety` (injection, scopes d'outils, exfiltration) ; `cost-latency` est nouveau et bloquant |
| **Vagues + tri topologique** sur le graphe d'appels SQL | **Vagues** sur le graphe d'agents et d'outils | Même propriété : un sous-agent est analysé avant son superviseur |
| **Routeur de tier** sur la complexité d'un corps SQL | **Routeur de tier** sur la complexité d'une CAP | Même principe, même économie |
| **`MaxCostPerRun`** (construction) | **+ `CostPerRunHardCapUsd`** (exécution du produit) | SDD_Pro plafonne ce que coûte la construction. En agentic, ce que coûte le **produit** est une exigence fonctionnelle |

---

## 4. Ce qu'on refuse de reprendre

**Le vocabulaire front/back.** `dev-frontend` et `UI mockups` ne se transposent
pas : un système agentic n'a pas d'interface à maquetter, et sa couture critique
n'est pas entre deux couches d'application.

> **Révision du 21/09/2026 — l'API Gate est reprise.** La position d'origine
> allait plus loin : elle rangeait aussi `dev-backend` et l'`API Gate` parmi les
> refus, et déclarait la surface d'exposition « un détail de `## Active Serving
> Surface`, pas un axe structurant ». C'était faux sur deux points, et l'un
> d'eux coûtait cher.
>
> D'abord, **un système agentic d'entreprise se livre presque toujours en
> back-end**. Le refuser comme axe laissait la question « et on livre quoi ? »
> sans réponse dans la configuration — donc arbitrée par un agent, donc
> différemment à chaque run. C'est ce qu'a corrigé `DeliverableType` (`##
> Project Config`), qui dit ce qu'on **installe**, là où `## Active Serving
> Surface` dit par où l'on **entre**.
>
> Ensuite, **l'API Gate a bien un équivalent, et il manquait**. Chez SDD_Pro
> elle valide le contrat back↔front avant de générer le front. Ici la couture
> est entre l'**IR** et le monde extérieur : l'OpenAPI publié doit être dérivé
> des `inputSchema` / `outputSchema` de l'IR et confronté à eux
> (`[API_CONTRACT_DRIFT]`, `[API_ROUTE_UNBACKED]`, `[API_STATUS_UNMAPPED]` —
> `stacks/serving/fastapi-sse.md §6`). Les quatre gates de la ligne ci-dessous
> couvrent les couches internes ; aucune ne regardait ce que le système expose.
>
> Ce qui reste refusé de la ligne d'origine : `dev-frontend`, les maquettes, et
> l'idée qu'une surface d'exposition dicte le pipeline. Le pipeline reste
> bottom-up par couche ; la surface en est la dernière, pas la première.

**Le flux backend-first.** Remplacé par bottom-up par couche (P5). L'ordre
n'est pas « la donnée d'abord » mais « ce qui touche le monde réel d'abord, et
qui le prouve avant qu'on s'appuie dessus ».

**Le pass/fail binaire.** Remplacé par vert/jaune/rouge avec variance. Un score
au-dessus du seuil avec un écart-type de 12 % n'est pas la même information qu'un
score au-dessus du seuil avec 1 %. Aplatir les deux en « vert » est la façon la
plus courante de livrer un agent qui casse en production.

**Le volume avant la validation.** SDD_Pro porte 36 stacks dont 8 seulement en
🟡, et le dit honnêtement. SDD_Agents démarre avec **une** combinaison validée de
bout en bout (cf. ROADMAP, MVP) et refuse d'en annoncer d'autres avant de les
avoir mesurées. Annoncer douze frameworks supportés au lancement produirait
exactement le « faux vert » que ce framework existe pour empêcher.

---

## 5. Ce qui est entièrement nouveau

Sans aucun précédent dans SDD_Pro :

1. **Agentic IR** — couche intermédiaire machine entre spec et code, qui rend la
   génération multi-framework déterministe (`docs/AGENTIC-IR.md`).
2. **Budget d'exécution comme exigence fonctionnelle** — estimé en G2, mesuré en
   G6, bloquant aux deux (P6).
3. **Comptabilité du non-déterminisme** — k runs, variance, verdict à trois
   couleurs (P3).
4. **Calibration des juges LLM** — un grader non validé contre des labels humains
   ne rend pas de verdict bloquant (P9).
5. **Disjonction golden / holdout vérifiée par hash** — on n'optimise pas contre
   le jeu qui rend le verdict.
6. **Suites d'injection obligatoires** — directe et indirecte, dès qu'une source
   non maîtrisée existe (P8).
7. **Classes d'effet de bord sur les outils** + stratégie de sûreté exigée.
8. **Bornes de boucle obligatoires** sur tout agent généré (P12).
9. **Budget de simplicité topologique** — chaque agent au-delà du premier doit
   être justifié par une raison d'une liste close (P7).
10. **Trace de spans comme artefact de première classe** — il n'y a pas de stack
    trace dans un système non déterministe.
11. **Machine à états dérivée des gates** — l'état est calculé depuis les
    rapports sur disque, jamais déclaré par un agent (`docs/LIFECYCLE.md`, R1).
