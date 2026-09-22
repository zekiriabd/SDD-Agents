# Patterns RAG, recherche et retrieval

Consommé par `architect-rag`. SSoT machine :
`.sdda/registry/patterns.registry.json`.

> **`none` est un choix légitime et fréquent.** Mettre du RAG par réflexe sur un
> problème que trois outils déterministes résolvent mieux est l'erreur la plus
> coûteuse de ce domaine : on ajoute une chaîne d'ingestion, un index à maintenir,
> une dérive de fraîcheur et une source d'hallucination, pour un gain nul.

---

## 1. Les trois sous-systèmes, qui échouent séparément

`CORPUS` → ingestion/chunking → `INDEX` → stratégie de requête → `RETRIEVER`

Ils sont distincts dans le domain model parce qu'ils produisent **le même
symptôme** en échouant : « l'agent invente ». Les distinguer, c'est pouvoir
répondre à « où est le problème ? » avec une mesure plutôt qu'une intuition.

| Sous-système | Échoue comme | Se mesure par |
|---|---|---|
| Corpus | le document n'existe pas dans l'index | couverture du corpus vs questions du golden set |
| Chunking | le document est là, coupé au mauvais endroit | recall@k avec la **vérité au niveau document** |
| Index / embedding | le chunk est bon, la similarité ne le remonte pas | recall@k, nDCG |
| Stratégie de requête | la question ne ressemble pas au texte de la réponse | delta de recall avec et sans transformation de requête |
| Génération | tout est remonté correctement, la réponse invente | **groundedness** (la seule qui isole ce cas) |

---

## 2. Catalogue

### `none`
Le modèle sait, ou des outils déterministes savent. **Envisager d'abord.**

### `classic` — chunk → embed → top-k → stuff
- **Quand** : corpus homogène, questions factuelles, une réponse par document.
- **Limite** : les questions multi-sauts, comparatives ou agrégatives échouent.

### `hybrid` — BM25 + vecteur, fusion RRF
- **Quand** : **quasi toujours mieux que `classic`**, pour un surcoût faible.
- **Pourquoi** : le vecteur rate les correspondances exactes (références,
  identifiants, codes d'erreur, noms propres rares) ; le lexical les trouve. Le
  lexical rate les paraphrases ; le vecteur les trouve.
- **Recommandation par défaut** dès qu'il y a du RAG.

### `contextual` — chunk préfixé du contexte de son document
Chaque chunk est stocké avec 1-2 phrases situant sa place dans le document.
- **Quand** : documents longs et structurés (contrats, normes, manuels), où un
  chunk isolé perd son sujet.
- **Coût** : une passe LLM à l'ingestion, **amortie** sur toutes les requêtes.
- **Gain typique** : la plus forte amélioration par euro dépensé sur des corpus
  documentaires structurés.

### `hyde` — hypothetical document embeddings
Générer une réponse hypothétique, l'embedder, chercher avec elle.
- **Quand** : les questions ne ressemblent pas lexicalement aux réponses
  (question courte, corpus verbeux).
- **Coût** : +1 appel LLM par requête, sur le chemin de latence.

### `sequential-multihop` — décomposition + récupération en chaîne
- **Quand** : « Quelle clause du contrat de ce client couvre l'incident de
  mars ? » — il faut d'abord trouver le client, puis son contrat, puis la clause.
- **Obligation** : plafond de sauts, sinon la chaîne dérive.

### `agentic` — le retriever est un outil
L'agent décide **quand** chercher, **quoi** chercher, et s'il cherche encore.
- **Quand** : besoin d'information imprévisible, sources multiples.
- **Coût** : variable par nature — c'est son principal défaut budgétaire.
- **Obligation** : `max_retrieval_calls` par run, tracé et plafonné.

### `self-rag` — noter la pertinence et décider
L'agent évalue les documents remontés et décide : répondre, re-chercher,
ou déclarer qu'il ne sait pas.
- **Quand** : le coût d'une réponse fausse dépasse le coût d'un « je ne sais pas ».
- **Gain réel** : c'est le pattern qui fabrique l'abstention — rare et précieux.

### `corrective-rag` (CRAG) — évaluer puis se rabattre
Si le retrieval est jugé insuffisant, repli sur une autre source (web, autre
corpus, escalade humaine).
- **Quand** : couverture du corpus incomplète et assumée.
- **Danger** : le repli web introduit une source **non maîtrisée** → la suite
  d'injection devient obligatoire (P8).

### `graph-rag` — graphe d'entités + résumés de communautés
- **Quand** : questions globales (« quels sont les thèmes récurrents des
  réclamations ? ») qu'aucun top-k ne peut satisfaire.
- **Coût** : construction du graphe chère, maintenance lourde.
- **Franchise** : rarement justifié sous ~10 000 documents ou pour des questions
  factuelles.

### `raptor` — arbre hiérarchique de résumés
Récupération à plusieurs niveaux d'abstraction.
- **Quand** : il faut à la fois le détail et la synthèse selon la question.

---

## 3. Matrice de sélection

| Besoin | Pattern recommandé |
|---|---|
| Factuel, corpus homogène | `hybrid` |
| Documents longs et structurés | `contextual` + `hybrid` |
| Identifiants, codes, références exactes | `hybrid` (poids lexical ≥ 0.5) |
| Question ≠ lexique de la réponse | `hyde` ou décomposition de requête |
| Multi-sauts / relationnel | `sequential-multihop` |
| Besoin d'info imprévisible | `agentic` |
| Coût d'une erreur > coût d'un aveu d'ignorance | `self-rag` |
| Couverture incomplète assumée | `corrective-rag` |
| Questions globales / thématiques | `graph-rag` |
| Détail **et** synthèse | `raptor` |
| Les données sont dans une base, pas dans des documents | **aucun RAG** → `DATA-ACCESS.md` |

---

## 4. Le découpage — la décision qui décide de tout

Plus déterminant pour la qualité finale que le choix du modèle d'embedding, et
systématiquement sous-traité à une valeur par défaut.

| Stratégie | Quand |
|---|---|
| `fixed` | corpus non structuré, baseline uniquement |
| `recursive-structural` | respecte titres, paragraphes, listes — **défaut raisonnable** |
| `semantic` | coupe aux ruptures de sens ; coûteuse à l'ingestion |
| `document-aware` | par article/section/clause — le meilleur pour le juridique et les normes |
| `parent-child` | chercher sur le petit chunk, servir le parent — **le meilleur compromis précision/contexte** |

`ChunkSize` et `ChunkOverlap` ne sont pas des constantes universelles : le
`architect-rag` doit produire une **mesure comparative** d'au moins deux
configurations sur le golden set, et le résultat va dans le
`retrieval-contract`. Choisir 512/50 parce que c'est le défaut d'un tutoriel
n'est pas une décision d'architecture.

---

## 5. Métriques de la RETRIEVAL GATE (G4)

Mesurées **sans aucun agent** — c'est tout l'intérêt.

| Métrique | Mesure | Seuil par défaut |
|---|---|:---:|
| `recall@k` | le document pertinent est-il dans le top-k | ≥ 0.80 |
| `nDCG@k` | est-il bien classé | ≥ 0.70 |
| `context_precision` | proportion de bruit dans le contexte servi | ≥ 0.60 |
| `groundedness` | chaque affirmation de la réponse est-elle soutenue par le contexte | ≥ 0.85 |
| `answer_relevance` | la réponse répond-elle à la question posée | ≥ 0.80 |
| `citation_resolve_rate` | chaque citation pointe-t-elle vers un passage réel | ≥ 0.98 |

`groundedness` et `answer_relevance` exigent un juge LLM → **calibration
obligatoire** (P9). `recall@k`, `nDCG` et `citation_resolve_rate` sont
déterministes et ne coûtent aucun token.

**Règle de diagnostic** : `recall@k` haut + `groundedness` bas ⇒ le problème est
la génération, pas le retrieval. `recall@k` bas ⇒ inutile de toucher au prompt.
C'est cette distinction que la gate rend possible, et c'est pour elle qu'elle
existe.

---

## 6. Sécurité du corpus

Un corpus est une **surface d'attaque** (P8) :

- **Empoisonnement** : un document contenant « ignore les instructions
  précédentes » finira dans un contexte. La suite adversariale de G7 injecte
  volontairement de tels documents dans un index de test.
- **PII** : ce qui entre dans le vector store est difficile à retirer
  sélectivement. `MemoryPIIPolicy` et le scan PII de G7 s'appliquent à l'index.
- **Fuite d'autorisation** : si le corpus mélange des documents de niveaux
  d'accès différents, le retrieval **doit** être filtré par identité de
  l'appelant — pas après coup par le modèle. Un filtrage post-génération est une
  fuite avec une étape de plus.
