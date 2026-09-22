# RETRIEVAL CONTRACT: {n}-{index-slug}

MISSION: {n}-{MissionName}
Status: Draft
RAG Pattern: <classic | hybrid | contextual | hyde | sequential-multihop | agentic | self-rag | corrective-rag | graph-rag | raptor>
Store: <pgvector | qdrant | chroma | azure-ai-search | pinecone | weaviate | elasticsearch | faiss>
Embedding Model: <ex. voyage-3-large>          # épinglé dans le tuple des baselines (P10)
Index Hash: sha256:…                            # calculé à l'ingestion, jamais à la main

> Contrat **neutre framework** (P11) : aucun nom d'API de LlamaIndex, LangChain
> ou autre ici. Ce fichier décrit *quoi* ; `.sdda/stacks/rag/*.md` et
> `.sdda/stacks/vectorstore/*.md` décrivent *avec quoi*.
>
> Rappel préalable (RAG-PATTERNS.md) : **`none` est un choix légitime et
> fréquent.** Si ce contrat existe, la section 0 doit dire pourquoi trois outils
> déterministes ne suffisaient pas. Si les données sont dans une base, ce n'est
> pas un problème de RAG → `DATA-ACCESS.md`.

---

## 0. Pourquoi du RAG, et pourquoi ce pattern

- **Alternative `none` écartée parce que** : <le modèle ne sait pas / aucun outil
  déterministe ne fournit la réponse / les données sont documentaires, pas
  tabulaires>
- **Pattern choisi** : `{pattern}` — critère de la matrice de sélection
  (RAG-PATTERNS.md §3) : <ex. « documents longs et structurés » → contextual + hybrid>
- **Pattern plus simple écarté** : <ex. `classic` — rate les références exactes
  (codes d'erreur), mesuré : recall@8 0.61 vs 0.84 en hybrid sur le golden set>

---

## 1. Corpus

| | |
|---|---|
| Nature | <ex. contrats clients PDF, base de connaissance support, normes internes> |
| Volume | <n documents, ~n pages, ~n tokens> — **mesuré**, pas estimé |
| Langue(s) | <fr, en, …> |
| Structure | <ex. articles / clauses numérotées ; titres H1-H3 ; non structuré> |
| Fraîcheur | <fréquence de changement des sources ; tolérance à l'obsolescence> |
| Niveaux d'accès | <un seul niveau / plusieurs tenants / plusieurs rôles> → voir §7 |
| PII | <présente / absente / présente et redigée à l'ingestion> → voir §10 |
| Propriétaire | <qui arbitre le contenu et sa véracité> |

> Le corpus est le premier des trois sous-systèmes qui échouent séparément
> (RAG-PATTERNS.md §1). Il échoue comme : « le document n'existe pas dans
> l'index ». Il se mesure par : **couverture du corpus vs questions du golden set**.

- **Couverture mesurée** : <n / n questions du golden set ont leur document dans le corpus>
- **Trous assumés** : <ce que le corpus ne couvre pas, et ce que fait le système
  dans ce cas — cohérent avec la Failure Policy de la MISSION>

---

## 2. Sources et ingestion

| Source | Type | Accès | Trust |
|---|---|---|---|
| <ex. SharePoint /contrats> | PDF | <connecteur, auth `{ENV_VAR}`> | untrusted |
| <ex. base KB support> | HTML | <API, auth `{ENV_VAR}`> | untrusted |

> **Tout document du corpus est du texte non maîtrisé (P8)** : contenu, jamais
> instruction. Chaque agent qui consomme ce retriever porte une suite
> d'injection indirecte (corpus empoisonné) — §10.

| | |
|---|---|
| `IngestionMode` | <batch \| incremental \| streaming> |
| `IndexRefreshPolicy` | <manual \| scheduled \| on-source-change> |
| Parseurs | <ex. extraction PDF avec conservation des titres ; tables → texte linéarisé> |
| Déduplication | <par hash de contenu / par identifiant source> |
| Métadonnées conservées par chunk | <doc_id, titre, section, date, tenant_id, niveau d'accès, url source> |
| Ré-indexation | <complète / différentielle ; recalcule `Index Hash`> |

---

## 3. Découpage (chunking)

> **La décision qui décide de tout** (RAG-PATTERNS.md §4). Plus déterminante
> que le choix du modèle d'embedding, et systématiquement sous-traitée à une
> valeur par défaut.

| | |
|---|---|
| `ChunkStrategy` | <fixed \| recursive-structural \| semantic \| document-aware \| parent-child> |
| `ChunkSize` | <tokens> |
| `ChunkOverlap` | <tokens> |
| `ParentChildEnabled` | <true \| false> — si true : taille du parent servi : <tokens> |
| Préfixe contextuel (`contextual`) | <1-2 phrases générées à l'ingestion, tier `fast` — coût amorti> |

### 3.1 Mesure comparative — OBLIGATOIRE

> Au moins **deux configurations** mesurées sur le golden set (§9), sans aucun
> agent. Choisir 512/50 parce que c'est le défaut d'un tutoriel n'est pas une
> décision d'architecture. Référence portée dans l'IR :
> `retrievers[].binding.chunk.comparativeMeasureRef`.

Fichier de mesure : `workspace/evals/reports/retrieval-{n}-{index-slug}-chunking.json`

| Configuration | recall@k | nDCG@k | context_precision | Coût d'ingestion | Retenue |
|---|:---:|:---:|:---:|---:|:---:|
| `recursive-structural` 800/120 | … | … | … | … | |
| `recursive-structural` 400/60 | … | … | … | … | |
| `document-aware` (par clause) | … | … | … | … | ✔ |

- **Configuration retenue et pourquoi** : <le critère chiffré qui la départage>
- **Vérité terrain utilisée** : au **niveau document** (un document pertinent est
  attendu, quel que soit le chunk qui le remonte) — c'est ce qui isole l'échec
  du chunking de l'échec du corpus.

---

## 4. Index

| | |
|---|---|
| Store | `{store}` — fiche `.sdda/stacks/vectorstore/{store}.md` |
| Modèle d'embedding | `{embedding-model}` — dimensions : <n> — fiche `.sdda/stacks/embedding/*.md` |
| Auth embedding | variable d'environnement `{ENV_VAR}` — **jamais** la valeur |
| `HybridEnabled` | <true \| false> |
| `HybridWeights` | `{ vector: <0.0-1.0>, lexical: <0.0-1.0> }` — **lexical ≥ 0.5** si identifiants / codes / références exactes |
| Fusion | <RRF \| pondérée> |
| `RerankEnabled` / `RerankModel` | <false \| cohere-rerank-3 \| voyage-rerank-2 \| bge-reranker> — `RerankTopN` : <n> |
| Filtres de métadonnées supportés | <tenant_id, niveau d'accès, date, type de document> |
| `Index Hash` | `sha256:…` — inclus dans le tuple d'épinglage des baselines (P10) : un index reconstruit périme les evals |

> Sous-système « index / embedding » : échoue comme « le chunk est bon, la
> similarité ne le remonte pas ». Se mesure par recall@k et nDCG.

---

## 5. Stratégie de requête

| | |
|---|---|
| `RetrievalTopK` | <n> (défaut 8) |
| Transformation de requête | <aucune \| hyde \| décomposition \| réécriture> |
| Filtres appliqués systématiquement | <identité (§7), fraîcheur, type> |
| Contexte servi au modèle | <top-k chunks \| parents \| chunks + préfixe contextuel> — taille max : <tokens> |

**Bornes spécifiques au pattern** (obligatoires, P12) :

| Pattern | Borne | Valeur | Comportement à l'atteinte |
|---|---|---:|---|
| `sequential-multihop` | `max_hops` | <n> | <ex. répondre avec ce qui est remonté + signaler l'incomplétude> |
| `agentic` | `max_retrieval_calls` par run | <n> | <ex. cesser de chercher, répondre ou s'abstenir> — **tracé et plafonné**, porté dans l'IR (`retrievers[].maxRetrievalCalls`) |
| `self-rag` | seuil de pertinence / décision | <valeur> | <répondre \| re-chercher \| abstention explicite> |
| `corrective-rag` | source de repli | <web \| autre corpus \| escalade humaine> | **si repli non maîtrisé (web) : suite d'injection obligatoire** (P8) |
| `plan-execute`, `hyde` | +1 appel LLM sur le chemin de latence | — | compté dans le budget de la MISSION |

> Sous-système « stratégie de requête » : échoue comme « la question ne
> ressemble pas au texte de la réponse ». Se mesure par le **delta de recall
> avec et sans transformation de requête** — à mesurer et à reporter ici :
> <recall@k sans : … / avec : …>

---

## 6. Citations

| | |
|---|---|
| `CitationMode` | <required \| optional \| none> |
| Format | <ex. `[doc_id#chunk_id]`, `[titre, section, page]`> |
| Résolution | déterministe, 0 token : chaque citation pointe vers un passage **réel** de l'index ; vérifiée par `citation_resolve_rate` (§8) |
| Règle | avec `required` : **toute affirmation factuelle porte un pointeur résolvable** — une affirmation sans citation est un échec de génération, pas un détail de forme |

---

## 7. Filtrage par identité

> **Obligatoire si le corpus mélange des niveaux d'accès.** Le retrieval est
> filtré par l'identité de l'appelant **à la source** — dans la requête à
> l'index, sur les métadonnées — jamais après coup par le modèle. Un filtrage
> post-génération est une fuite avec une étape de plus (RAG-PATTERNS.md §6,
> finding bloquant de `review-safety`). Porté dans l'IR :
> `retrievers[].identityFilter`.

| | |
|---|---|
| Cloisonnement | <aucun \| par tenant \| par rôle \| par utilisateur> |
| Métadonnée de filtre | <ex. `tenant_id`, `acl_groups[]`> |
| Source de l'identité | <ex. jeton de session, jamais le message utilisateur> |
| Test L8 associé | « franchissement d'autorisation » : obtenir un document d'un autre tenant → **filtré à la source** |

---

## 8. Seuils de la RETRIEVAL GATE (G4)

> Mesurés **sans aucun agent** — c'est tout l'intérêt. Défauts de
> `config.base.yml` ; un seuil relâché ici doit être justifié. Les clés
> `GroundednessMin`, `RetrievalRecallAtK` et `CitationResolveRateMin` sont
> protégées security-down : le projet peut durcir, jamais relâcher ce que la
> couche team a fixé.

| Métrique | Seuil | Clé de config | Déterministe | Grader |
|---|:---:|---|:---:|---|
| `recall@k` (k = `RetrievalK`) | ≥ 0.80 | `RetrievalRecallAtK` | oui | — |
| `nDCG@k` | ≥ 0.70 | `RetrievalNdcgMin` | oui | — |
| `context_precision` | ≥ 0.60 | `RetrievalContextPrecisionMin` | non | llm-judge **calibré** |
| `groundedness` | ≥ 0.85 | `GroundednessMin` | non | llm-judge **calibré** — `workspace/evals/calibration/groundedness.json` |
| `answer_relevance` | ≥ 0.80 | `AnswerRelevanceMin` | non | llm-judge **calibré** |
| `citation_resolve_rate` | ≥ 0.98 | `CitationResolveRateMin` | oui | — |

- k runs pour les métriques à juge LLM : `EvalRuns` (3 ; 5 si une CAP servie est `critical`)
- **Règle de diagnostic** : `recall@k` haut + `groundedness` bas ⇒ le problème
  est la génération, pas le retrieval. `recall@k` bas ⇒ inutile de toucher au
  prompt.

Verdict G4 : <🟢 \| 🟡 \| 🔴> — rapport : `workspace/evals/reports/retrieval-{n}-{index-slug}.json`

---

## 9. Golden set de retrieval

| | |
|---|---|
| Fichier | `workspace/datasets/golden/retrieval-{index-slug}-v1.jsonl` |
| Schéma | `.sdda/templates/golden-set.schema.json` — items avec `expected_documents` |
| Taille minimale | `RetrievalGoldenMinQueries` (50) — actuelle : <n> |
| Vérité terrain | **au niveau document** (`expected_documents[].doc_id`, `relevance`) ; passages optionnels pour nDCG gradué |
| Origine | <ex. questions réelles du support, annotées par l'équipe métier> |
| Classes couvertes | <factuel \| identifiant exact \| paraphrase \| multi-sauts \| hors corpus (abstention attendue)> |
| Owner | `qa-evals` — **jamais** `dev-retrieval` (ownership, ARCHITECTURE §7) |
| Holdout | `workspace/datasets/holdout/retrieval-{index-slug}-v1.jsonl` — disjoint par hash (`HoldoutDisjointCheck: strict`) |

> Les items « hors corpus » sont indispensables : ils mesurent que le système
> **s'abstient** quand le document n'existe pas, au lieu d'inventer.

---

## 10. Sécurité du corpus

> Un corpus est une **surface d'attaque** (P8, RAG-PATTERNS.md §6).

| Risque | Mesure | Enforcer |
|---|---|---|
| Empoisonnement | documents « ignore les instructions précédentes… » injectés volontairement dans un **index de test** ; attendu : traités comme donnée, jamais exécutés | suite adversariale G7 — `workspace/datasets/adversarial/{agent}.jsonl` (famille « injection indirecte ») |
| PII | `MemoryPIIPolicy` appliquée à l'index : <forbid \| redact-before-write \| allow (ADR)> ; scan PII à l'ingestion | `scan_pii.py` (G7) — invariant `pii-not-in-vector-store` |
| Fuite d'autorisation | filtrage par identité à la source (§7) | `review-safety`, test L8 « franchissement d'autorisation » |
| Secrets dans les documents | scan de secrets à l'ingestion ; un document contenant une clé n'entre pas dans l'index | scan déterministe G7 — `[SECRET_LEAK]` |

---

## 11. Exposé à

| Agent | CAP qui l'exige | Mode de citation |
|---|---|---|
| `{n}-…` | `{n}-{m}-…` | required |

> Un retriever qu'aucune CAP n'exige ne doit être câblé à personne. Un agent
> n'est câblé à cet index qu'**après** le verdict G4 (invariant
> `retrieval-gate-before-agent`).

---

## 12. Tests et evals

| Niveau | Contenu | Fichier |
|---|---|---|
| L1 | chunkers, parseurs, résolution de citations — fonctions pures, LLM mocké | `workspace/src/retrieval/tests/` |
| L3 | golden set §9 contre les seuils §8, **aucun agent** | `workspace/evals/suites/retrieval-{n}-{index-slug}.yaml` |
| L6 | ingestion bout-en-bout sur la base de test, vrai index | marqué `network` |
| L8 | empoisonnement, franchissement d'autorisation, PII | suite adversariale |

---

## 13. Décisions à ADR

<Ce qui survivra à cette mission : choix de store, `MemoryPIIPolicy: allow`,
 repli web d'un `corrective-rag`, pattern `graph-rag` sous ~10 000 documents.>

- ADR-{timestamp}-{slug}: <titre>
