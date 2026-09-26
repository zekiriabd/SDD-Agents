# Stack: hybrid-node (rag)

Stack ID: rag-hybrid-node
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: pattern de retrieval **hybride** côté **Node/TypeScript** — jambe lexicale (FTS PostgreSQL) + jambe vectorielle (pgvector), fusion par Reciprocal Rank Fusion en **fonction pure TypeScript** testée en L1, variante une-requête en SQL vérifiée contre elle. Implémente l'entité **RETRIEVER** du domain model. Pendant Node de `rag/hybrid.md` : **même SQL, mêmes paramètres, mêmes défauts, même protocole de mesure** ; seuls le client et le langage de la fusion changent. Embeddings Voyage par le SDK **officiel** `voyageai` (npm), `inputType` rendu obligatoire. Suppose `lang/typescript.md` et `vectorstore/pgvector-node.md`. Pas de `.libs.json` : `pg`/`pgvector` sont portés par `vectorstore/pgvector-node.libs.json`, `voyageai` par la capability `embedding-voyage` de ce même catalogue.

---

## 1. Rôle et périmètre

Tout ce que `rag/hybrid.md` §1 dit du pourquoi reste vrai : les deux jambes
échouent sur des cas **complémentaires** (identifiants exacts pour le lexical,
paraphrases pour le vectoriel), la fusion coûte une requête SQL et **aucun
token**, et c'est le pattern RAG par défaut du framework. Cette fiche ne le
redémontre pas ; elle dit comment l'écrire en Node **sans changer ce qu'on
mesure**.

La règle qui gouverne la fiche : **un rapport L3 produit sur une application
Node doit être comparable, ligne à ligne, à un rapport L3 produit sur
l'application Python de la même MISSION.** Mêmes deux CTE, mêmes
`candidates_per_leg`, même `rrf_k`, même règle d'égalité, même `indexHash`. Si
l'un des deux langages fusionnait autrement, une différence de recall@k ne dirait
plus rien de l'index.

**Hors périmètre**, comme en Python : le chunking (décidé dans le
`retrieval-contract` par mesure), le reranking (appliqué **après** la fusion),
la génération (`groundedness`, `answer_relevance` → `eval/vitest-eval.md` pour
les tests, `eval-runner` pour les evals).

### 1.1 Pourquoi pas `EnsembleRetriever`

LangChain.js propose une fusion toute faite : `EnsembleRetriever` (RRF pondéré).
Il a **quitté le paquet `langchain`** et vit désormais dans `@langchain/classic`
(`@langchain/classic/retrievers/ensemble`, vérifié dans les `exports` de la
1.0.48). Il n'est pas utilisé ici, pour trois raisons :

1. **Les rangs ne sont pas observables.** Il rend des `Document` fusionnés ; la
   `provenance` (`vector_rank`, `lexical_rank`) — la donnée qui règle les poids
   et diagnostique « quelle jambe a remonté le document » — est perdue.
2. **Il fusionne deux retrievers, pas deux jambes d'une même requête.** Côté
   LangChain.js, la jambe vectorielle serait `PGVectorStore` (sans tenant ni
   `tsvector`, cf. `vectorstore/pgvector-node.md` §1) et la jambe lexicale un
   `BM25Retriever` **en mémoire** (`@langchain/community/retrievers/bm25`),
   reconstruit à chaque démarrage et hors du filtre d'identité SQL.
3. **La fusion doit être une fonction pure testée en L1**, et la même en SQL et
   en TypeScript ; un retriever composite de framework n'est ni l'une ni
   l'autre.

`@langchain/classic` est donc **absent par conception**
(`vectorstore/pgvector-node.libs.json`).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `rag-hybrid-node` |
| **Famille** | RAG · pattern de requête (RETRIEVER) |
| **Dépend de** | `vectorstore/pgvector-node.md` (les deux jambes) · `embedding/voyage.md` · `lang/typescript.md` |
| **Embeddings** | `voyageai` 0.4.0 — SDK TypeScript **officiel** (mainteneur npm `voyage-ai`, dépôt voyage-ai/typescript-sdk) ; client `VoyageAIClient`, méthode `embed({ input, model, inputType, outputDimension })` |
| **Paramètres STACK.md** | `HybridEnabled: true`, `HybridWeights: { vector: 0.6, lexical: 0.4 }`, `RetrievalTopK: 8`, `RerankEnabled`, `RerankTopN` |
| **Paramètres du retrieval-contract** | `candidates_per_leg` (40), `rrf_k` (60), `LexicalLanguage` (`french`), `LexicalEngine` (`pg-fts` \| `pg-bm25`) |

`LexicalEngine: rank-bm25` (index en mémoire) n'a **pas** d'équivalent retenu
en Node : la seule voie in-process (`BM25Retriever` de `@langchain/community`)
contourne le filtre d'identité SQL. `external` (Elasticsearch/OpenSearch) : non
décrit ici, **non vérifié**.

La franchise sur « BM25 » de `rag/hybrid.md` §2.1 s'applique : `ts_rank_cd`
n'est pas BM25 ; un vrai BM25 exige une extension serveur (`pg_search`), et le
choix est une mesure consignée dans le `retrieval-contract`.

### 2.1 Voyage : `inputType` obligatoire

Dans le SDK, `inputType` est **optionnel** (`EmbedRequest.inputType?:
"query" | "document"`, lu dans les types publiés de la 0.4.0) : l'omettre ne
lève rien, et l'API calcule alors un embedding « sans type » qui dégrade le
recall sans erreur visible. La fiche le rend obligatoire **dans la signature**
du seul point d'entrée autorisé :

```ts
// workspace/src/{AppName}/retrieval/{index_slug}/embed.ts
import { VoyageAIClient } from "voyageai";
import { settings } from "#app/app/config.js";

export type InputType = "query" | "document";          // pas d'optionnel : le compilateur refuse l'oubli

const client = new VoyageAIClient({ apiKey: settings.voyageApiKey.reveal() });

export async function embed(texts: readonly string[], inputType: InputType, signal: AbortSignal): Promise<number[][]> {
  const res = await client.embed(
    { input: [...texts], model: settings.embeddingModel, inputType, outputDimension: settings.embeddingDims },
    { timeoutInSeconds: settings.embeddingTimeoutS, maxRetries: 2, abortSignal: signal },
  );
  const rows = [...(res.data ?? [])].sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
  if (rows.length !== texts.length || rows.some((r) => r.embedding?.length !== settings.embeddingDims)) {
    throw new RetrievalError("RETRIEVAL_DIMS_MISMATCH", `attendu ${texts.length}×${settings.embeddingDims}`);
  }
  return rows.map((r) => r.embedding!);   // usage.totalTokens → span `embeddings` (otel-genai-node)
}
```

Ingestion : `inputType: "document"` ; requête : `inputType: "query"`. Un
`client.embed` appelé hors de ce module est signalé en L0 par grep. Pourquoi pas
un client « compatible OpenAI » : la compatibilité n'est pas établie, et il
omettrait `input_type` en silence. Pourquoi pas
`@langchain/community/embeddings/voyage` : un paquet au très large arbre de pairs
optionnels pour 20 lignes, et un `inputType` que l'on ne voit plus à l'appel.

---

## 3. Mapping des concepts SDD_Agents → idiomes du pattern

La table de `rag/hybrid.md` §3 s'applique entièrement (poids, `candidates_per_leg`,
`topK`, citations, `Untrusted`, `provenance`, span `sdda.retrieve`, `indexHash`).
Seul l'idiome de concurrence change :

| Concept | Idiome Node |
|---|---|
| **RETRIEVER** `retrieve(query, { topK, tenant })` | `embed([query], "query")` puis **une** requête SQL (§3.2), **ou** `Promise.all([vectorLeg, lexicalLeg])` → `rrfFuse` → `[rerank]` → `topK` |
| Annulation | un `AbortSignal` traverse `embed`, les deux jambes et le rerank ; la surface l'abandonne à la déconnexion ou à `SIGINT` |

### 3.1 La fusion — fonction pure, testée en L1

```ts
// workspace/src/{AppName}/retrieval/{index_slug}/fusion.ts
export type Provenance = Readonly<Record<string, number | null>>;
export type Fused = readonly [chunkId: string, score: number, provenance: Provenance];

/** RRF pondérée. Déterministe : à égalité de score, ordre de chunkId (comparaison de code, pas de locale). */
export function rrfFuse(
  legs: Readonly<Record<string, readonly string[]>>,          // { vector: [id…] ordonnés, lexical: [id…] }
  opts: { weights: Readonly<Record<string, number>>; k?: number; topK: number },
): Fused[] {
  const k = opts.k ?? 60;
  const sum = Object.values(opts.weights).reduce((a, b) => a + b, 0);
  if (Math.abs(sum - 1) > 1e-9) throw new RetrievalError("RETRIEVAL_WEIGHTS_INVALID", "weights must sum to 1.0");
  const scores = new Map<string, number>();
  const prov = new Map<string, Record<string, number | null>>();
  const names = Object.keys(legs);
  for (const leg of names) {
    const w = opts.weights[leg];
    if (w === undefined) throw new RetrievalError("RETRIEVAL_WEIGHTS_INVALID", `no weight for leg ${leg}`);
    legs[leg]!.forEach((cid, i) => {
      scores.set(cid, (scores.get(cid) ?? 0) + w / (k + i + 1));
      const p = prov.get(cid) ?? Object.fromEntries(names.map((n) => [n, null]));
      p[leg] = i + 1;
      prov.set(cid, p);
    });
  }
  return [...scores.entries()]
    .sort(([a, sa], [b, sb]) => (sb - sa) || (a < b ? -1 : a > b ? 1 : 0))
    .slice(0, opts.topK)
    .map(([cid, s]) => [cid, s, prov.get(cid)!] as const);
}
```

Le comparateur d'égalité est `a < b` (unités de code UTF-16), **pas**
`localeCompare` : la locale du processus changerait l'ordre entre deux machines,
et l'ordre doit être celui de la fiche Python (`sorted` sur `str`) pour les
identifiants ASCII (UUID) du schéma. Pourquoi RRF et pas une combinaison de
scores : `rag/hybrid.md` §3.1, inchangé.

### 3.2 Variante pgvector en une requête

Le SQL de `rag/hybrid.md` §3.2, **à l'identique**, en paramètres positionnels
`pg` :

```sql
-- workspace/src/{AppName}/retrieval/{index_slug}/sql/hybrid-rrf.sql
-- $1 q_vec (pgvector.toSql)  $2 lang (regconfig)  $3 q_text  $4 n (candidates_per_leg)
-- $5 w_vec  $6 w_lex  $7 rrf_k  $8 top_k
WITH q AS (
  SELECT $1::vector AS v,
         websearch_to_tsquery($2::regconfig, rag.immutable_unaccent($3)) AS tsq
),
vec AS (
  SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> q.v) AS rnk
  FROM   rag.chunks c, q
  WHERE  c.tenant_id = current_setting('app.tenant_id')::uuid
    AND  c.pii_scan_status <> 'flagged'
  ORDER  BY c.embedding <=> q.v
  LIMIT  $4
),
lex AS (
  SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.content_tsv, q.tsq) DESC) AS rnk
  FROM   rag.chunks c, q
  WHERE  c.tenant_id = current_setting('app.tenant_id')::uuid
    AND  c.pii_scan_status <> 'flagged'
    AND  c.content_tsv @@ q.tsq
  ORDER  BY ts_rank_cd(c.content_tsv, q.tsq) DESC
  LIMIT  $4
),
fused AS (
  SELECT COALESCE(vec.id, lex.id) AS id,
         COALESCE($5::float8 / ($7::int + vec.rnk), 0)
       + COALESCE($6::float8 / ($7::int + lex.rnk), 0) AS score,
         vec.rnk AS vector_rank, lex.rnk AS lexical_rank
  FROM   vec FULL OUTER JOIN lex ON vec.id = lex.id
)
SELECT c.id, c.document_id, c.chunk_index, c.heading_path, c.char_start, c.char_end, c.content,
       f.score, f.vector_rank, f.lexical_rank
FROM   fused f JOIN rag.chunks c ON c.id = f.id
ORDER  BY f.score DESC, c.id
LIMIT  $8;
```

Les casts explicites (`::float8`, `::int`) ne sont pas décoratifs : `pg` envoie
les paramètres en texte, et `$5 / ($7 + rnk)` sans cast est résolu en division
**entière** par le planificateur si `$5` est inféré `integer` — tous les scores
valent 0 et l'ordre devient celui de `c.id`. `row_number()` rend un `bigint`,
que `pg` lit en **chaîne** : `vector_rank`/`lexical_rank` sont parsés en
`number` par le schéma Zod de la ligne.

La requête s'exécute dans `withTenantTx` (`vectorstore/pgvector-node.md` §3.2),
`hnsw.ef_search ≥ n`. `rrfFuse` reste la **référence** : la version SQL est
vérifiée contre elle en L6 sur un jeu fixe (`[RETRIEVAL_FUSION_MISMATCH]`).

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/retrieval/{index_slug}/     # zone dev-retrieval
├── index.ts                    # export retrieve(query, { topK, tenant, signal }) : Promise<RetrievedChunk[]>
├── retriever.ts                # embed(query) → SQL une-requête | jambes ∥ → rrfFuse → [rerank] → topK ; span sdda.retrieve
├── fusion.ts                   # rrfFuse — pure, L1
├── legs.ts                     # vectorLeg, lexicalLeg — délèguent à store.ts
├── embed.ts                    # SEUL appel à VoyageAIClient.embed ; inputType obligatoire
├── query-prep.ts               # unaccent cohérent avec l'ingestion ; identifiants préservés
├── sql/hybrid-rrf.sql          # §3.2 — copié dans dist/ par le build (lang/typescript.md §4)
├── models.ts                   # RetrievedChunk (Zod) : content Untrusted, score, citation, provenance
└── tests/
    ├── fusion.test.ts          # L1 : cas connus, égalités stables, poids invalides, doc dans 2 jambes remonte, parité Python (vecteur de référence)
    ├── query-prep.test.ts      # L1 : 'INV-2024-0093' survit à la préparation lexicale
    └── fusion-sql.test.ts      # describe("network …") : SQL == rrfFuse sur jeu fixe
```

La commande `retrieve` de la CLI (`serving/`, zone `dev-api`) appelle
`retrieve()` de `index.ts` ; elle ne réimplémente rien.

---

## 5. Conventions imposées — et comment mesurer

Les règles 1 à 7 de `rag/hybrid.md` §5.1 s'appliquent telles quelles (jambes
en parallèle ou une requête, `candidates_per_leg ≥ 3 × top_k`, RRF sur les
rangs, déduplication par `chunk_id`, `provenance` conservée, paramètres dans le
contrat et dans `indexHash`, requête lexicale préparée). En plus :

8. **Jamais `await` séquentiel des deux jambes** : `Promise.all`, et une seule
   transaction par jambe (chacune pose son tenant).
9. **Un seul module appelle Voyage** (`embed.ts`), `inputType` typé non
   optionnel.
10. **Parité inter-langages de la fusion** : `fusion.test.ts` rejoue un vecteur
    de référence (entrées + sortie attendue) identique à celui du test Python
    `test_fusion.py` ; une divergence est `[RETRIEVAL_FUSION_MISMATCH]`.

**Mesurer** : le protocole de `rag/hybrid.md` §5.2 (ablation, par tag, grille de
poids sur golden, profondeur, vérité document, citations) est joué par le runner
du framework — `python .sdda/sdda.py run-retrieval-eval` — qui **lance**
l'application par sa commande `retrieve` (§7) ; il ne l'importe pas. Le rapport
L3 est le même fichier, au même format, qu'en Python.

---

## 6. Commande de smoke

L1 sans provider ni base, puis smoke `network` avec un vecteur de requête fixé :

```bash
cd workspace/src/{AppName}
npx --no-install vitest run retrieval/{index_slug}/tests/fusion.test.ts retrieval/{index_slug}/tests/query-prep.test.ts
pnpm build
node dist/retrieval/{index_slug}/smoke.js --query "facture INV-2024-0093 montant"
#   → 3 listes (vector / lexical / fused) avec rangs et provenance
#   → vérifie : deux jambes > 0 candidat ; fused SQL == rrfFuse(vector, lexical) ; latence < LatencyP95Target/4
#   → exit 0 ; 3 si une jambe est vide ([RETRIEVAL_LEG_EMPTY]) ; 4 si SQL ≠ TS ([RETRIEVAL_FUSION_MISMATCH])
```

---

## 7. Contrat d'exécution

L'application Node implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** — mêmes commandes, même flux NDJSON `RunEvent`, mêmes codes de
sortie — et les trois points de `serving/cli.md` §3.5 :

1. `run --json --input-file -` lit l'entrée sur stdin ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'app sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil) et le retrieval
   figé depuis le même dossier, sans aucun appel réseau d'outil ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished`.

Pour ce pattern :

- `retrieve` renvoie les `result_ids` **dans l'ordre fusionné** — identifiants de
  DOCUMENTS (`docId` du chunk servi ; la gate mesure au niveau document,
  `serving/cli.md` §3.5), les chunks en `chunk_ids` — et les `scores`
  RRF correspondants ; `--k` absent → `topK` du `retrieval-contract`. Aucun
  appel au modèle de chat ; seul l'embedding de la requête est payé, et il
  apparaît dans la trace (span `embeddings`).
- En isolement, le retriever est remplacé par le retrieval figé de
  `SDDA_EVAL_FIXTURES/retrieval/{index}.jsonl` : une ligne `{"query": …}` ou
  `{"query_hash": "sha256:<hex de la requête UTF-8>"}` avec `results: [{id,
  score}]` ou `result_ids[]` + `scores[]` — le format que lit le squelette
  Python (`templates/runtime/python/app/isolation.py`), repris à l'identique.
  Ni Voyage ni PostgreSQL ne sont appelés.

Lancement : `node workspace/src/{AppName}/dist/cli.js` après `pnpm build`
(point d'entrée exact exigé par `lang/typescript.md` §4), ou le `bin` du
package. Les runners choisissent cette commande avec `--executor cli` ou
l'imposent avec `--executor cmd:<commande>`.

---

## 8. Pièges connus

Les pièges 1 à 12 de `rag/hybrid.md` §7 valent tous. Propres à Node :

1. **Division entière dans la CTE** (§3.2) : sans `::float8`, tous les scores
   fusionnés SQL valent 0 ; le test de parité L6 l'attrape, le smoke aussi.
2. **`bigint` lu en chaîne** : `row_number()` revient `"3"` ; comparer
   `"10" < "9"` inverse les rangs. Parsing Zod en `number` au bord.
3. **`localeCompare` pour départager** : dépend de `LANG`/ICU ; deux machines,
   deux ordres.
4. **`inputType` oublié** (§2.1) : aucune erreur, recall dégradé. Le type non
   optionnel et le grep L0 le rendent impossible.
5. **`embed` sans `abortSignal`** : un client déconnecté laisse l'appel Voyage
   facturer jusqu'au bout.
6. **`sql/*.sql` absent de `dist/`** : `tsc` ne copie pas les fichiers non
   TypeScript ; le build les copie (script `build` du `package.json`) et le
   smoke échoue sinon (`ENOENT`) au lieu d'un run réel.
