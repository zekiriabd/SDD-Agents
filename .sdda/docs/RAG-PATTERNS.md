# RAG, search and retrieval patterns

Consumed by `architect-rag`. Machine SSoT:
`.sdda/registry/patterns.registry.json`.

> **`none` is a legitimate and frequent choice.** Adding RAG by reflex to a
> problem that three deterministic tools solve better is the most expensive
> mistake in this domain: you add an ingestion pipeline, an index to maintain, a
> freshness drift and a source of hallucination, for zero gain.

**What loads today.** Only `none` and `hybrid` have a card under
`.sdda/stacks/rag/` (`hybrid` assumes Python), with `pgvector` for the vector
store, `voyage` and `bge-local` for embeddings, and `none`, `cohere-rerank` and
`bge-reranker-local` for reranking. The other patterns in this catalogue are
**documented intentions**: activated in `STACK.md`, they load nothing and are
refused at preflight (`[STACK_COMBO_UNLOADABLE]`). Chunking (§4) is not a card:
it is a set of `STACK.md` keys (`ChunkStrategy`, `ChunkSize`, `ChunkOverlap`)
that the retrieval contract fixes after measurement.

---

## 1. The three subsystems, which fail separately

`CORPUS` → ingestion/chunking → `INDEX` → query strategy → `RETRIEVER`

They are distinct in the domain model because they produce **the same symptom**
when they fail: "the agent is making things up". Telling them apart is what lets
you answer "where is the problem?" with a measurement rather than a hunch.

| Subsystem | Fails as | Measured by |
|---|---|---|
| Corpus | the document is not in the index | corpus coverage vs the golden set questions |
| Chunking | the document is there, cut in the wrong place | recall@k with **document-level ground truth** |
| Index / embedding | the chunk is right, similarity does not surface it | recall@k, nDCG |
| Query strategy | the question does not look like the text of the answer | recall delta with and without query transformation |
| Generation | everything was retrieved correctly, the answer invents | **groundedness** (the only metric that isolates this case) |

---

## 2. Catalogue

### `none`
The model knows, or deterministic tools know. **Consider it first.**

### `classic` — chunk → embed → top-k → stuff
- **When**: homogeneous corpus, factual questions, one answer per document.
- **Limit**: multi-hop, comparative or aggregative questions fail.

### `hybrid` — BM25 + vector, RRF fusion
- **When**: **almost always better than `classic`**, for a small extra cost.
- **Why**: vectors miss exact matches (references, identifiers, error codes,
  rare proper nouns); lexical search finds them. Lexical search misses
  paraphrases; vectors find them.
- **Default recommendation** as soon as there is RAG.

### `contextual` — chunk prefixed with its document's context
Each chunk is stored with 1-2 sentences locating it within its document.
- **When**: long, structured documents (contracts, standards, manuals), where
  an isolated chunk loses its subject.
- **Cost**: one LLM pass at ingestion, **amortised** over every query.
- **Typical gain**: the largest improvement per euro spent on structured
  document corpora.

### `hyde` — hypothetical document embeddings
Generate a hypothetical answer, embed it, search with it.
- **When**: questions do not look lexically like the answers (short question,
  verbose corpus).
- **Cost**: +1 LLM call per query, on the latency path.

### `sequential-multihop` — decomposition + chained retrieval
- **When**: "Which clause of this customer's contract covers the March
  incident?" — you must first find the customer, then their contract, then the
  clause.
- **Obligation**: a hop cap, otherwise the chain drifts.

### `agentic` — the retriever is a tool
The agent decides **when** to search, **what** to search for, and whether to
search again.
- **When**: unpredictable information needs, multiple sources.
- **Cost**: variable by nature — that is its main budget flaw.
- **Obligation**: `max_retrieval_calls` per run, traced and capped.

### `self-rag` — grade relevance and decide
The agent evaluates the retrieved documents and decides: answer, search again,
or declare that it does not know.
- **When**: the cost of a wrong answer exceeds the cost of an "I don't know".
- **Real gain**: it is the pattern that produces abstention — rare and
  valuable.

### `corrective-rag` (CRAG) — evaluate, then fall back
If retrieval is judged insufficient, fall back to another source (web, another
corpus, human escalation).
- **When**: incomplete corpus coverage, knowingly accepted.
- **Danger**: the web fallback introduces an **untrusted** source → the
  injection suite becomes mandatory (P8).

### `graph-rag` — entity graph + community summaries
- **When**: global questions ("what are the recurring themes in the
  complaints?") that no top-k can satisfy.
- **Cost**: expensive graph construction, heavy maintenance.
- **Candidly**: rarely justified below ~10,000 documents or for factual
  questions.

### `raptor` — hierarchical tree of summaries
Retrieval at several levels of abstraction.
- **When**: you need both the detail and the synthesis, depending on the
  question.

---

## 3. Selection matrix

| Need | Recommended pattern |
|---|---|
| Factual, homogeneous corpus | `hybrid` |
| Long, structured documents | `contextual` + `hybrid` |
| Identifiers, codes, exact references | `hybrid` (lexical weight ≥ 0.5) |
| Question ≠ vocabulary of the answer | `hyde` or query decomposition |
| Multi-hop / relational | `sequential-multihop` |
| Unpredictable information needs | `agentic` |
| Cost of an error > cost of admitting ignorance | `self-rag` |
| Incomplete coverage, knowingly accepted | `corrective-rag` |
| Global / thematic questions | `graph-rag` |
| Detail **and** synthesis | `raptor` |
| The data lives in a database, not in documents | **no RAG** → [DATA-ACCESS.md](DATA-ACCESS.md) |

---

## 4. Chunking — the decision that decides everything

More decisive for final quality than the choice of embedding model, and
routinely delegated to a default value.

| Strategy | When |
|---|---|
| `fixed` | unstructured corpus, baseline only |
| `recursive-structural` | respects headings, paragraphs, lists — **a reasonable default** |
| `semantic` | cuts at breaks in meaning; expensive at ingestion |
| `document-aware` | by article/section/clause — the best for legal texts and standards |
| `parent-child` | search on the small chunk, serve the parent — **the best precision/context trade-off** |

`ChunkSize` and `ChunkOverlap` are not universal constants: `architect-rag` must
produce a **comparative measurement** of at least two configurations on the
golden set, and the result goes into the `retrieval-contract`. Picking 512/50
because it is a tutorial's default is not an architecture decision.

Two deterministic scripts (0 tokens) make this rule enforceable rather than
declarative:

- **`python .sdda/sdda.py corpus-profile --mission {n}`** measures the corpus
  **before** any choice: number of documents per type, length distribution
  (which bounds the chunk size), language, structure (which makes
  `document-aware` possible or not), duplicates, PII (type and file, never the
  value), multi-valued access keys (which force the filter into the query, §6).
  The `.env` file is never opened; a PDF or a DOCX is counted, not read, and the
  report says so.
- **`python .sdda/sdda.py chunking-bench --mission {n} --config … --config …`**
  compares configurations on the same corpus and the same queries, with a stdlib
  BM25: recall@k, nDCG@k, MRR, context precision, chunk size and count, tokens
  served and indexed. It recommends the best recall@k but, among the
  configurations within 2 points of the best, the cheapest to ingest.
  Strategies: `fixed`, `recursive-structural`, `document-aware`, `paragraph`,
  `sentence`, `parent-child` — `semantic` is not included.

The bench compares chunkings **against each other**; it does not predict the
absolute recall of the production retriever, which only G4 measures on the real
index.

---

## 5. RETRIEVAL GATE (G4) metrics

Measured **without any agent** — that is the whole point.

| Metric | Measures | Default threshold |
|---|---|:---:|
| `recall@k` | is the relevant document in the top-k | ≥ 0.80 |
| `nDCG@k` | is it ranked well | ≥ 0.70 |
| `context_precision` | share of noise in the context served | ≥ 0.60 |
| `groundedness` | is every claim in the answer supported by the context | ≥ 0.85 |
| `answer_relevance` | does the answer address the question asked | ≥ 0.80 |
| `citation_resolve_rate` | does every citation point to a real passage | ≥ 0.98 |

`groundedness` and `answer_relevance` require an LLM judge → **calibration is
mandatory** (P9). `recall@k`, `nDCG`, `context_precision` and
`citation_resolve_rate` are deterministic and cost no tokens:
`python .sdda/sdda.py run-retrieval-eval --mission {n}` computes them from an
injected executor (`--executor`, the generated retriever) or from a replay
(`--replay`), and writes one report per retriever
(`.sys/.validation/G4-{retriever}.json`). Thresholds come from the retrieval
contract, with the Project Config only as the default. The script calls no
judge: a `groundedness` the executor does not provide stays **absent**, and the
verdict turns yellow with the reason written down — never green by omission.

**Diagnostic rule**: high `recall@k` + low `groundedness` ⇒ the problem is
generation, not retrieval. Low `recall@k` ⇒ there is no point touching the
prompt. This distinction is what the gate makes possible, and it is what the
gate exists for.

---

## 6. Corpus security

A corpus is an **attack surface** (P8):

- **Poisoning**: a document containing "ignore the previous instructions" will
  end up in a context. The G7 adversarial suite carries an
  `indirect-injection` family that plays such documents against the live
  system (`run-adversarial-suite`).
- **PII**: what goes into the vector store is hard to remove selectively.
  `MemoryPIIPolicy` and the G7 PII scan (`scan-pii`, `[PII_IN_INDEX]`) apply to
  the corpus bound for the index.
- **Authorisation leak**: if the corpus mixes documents with different access
  levels, retrieval **must** be filtered by the caller's identity — not after
  the fact by the model. Post-generation filtering is a leak with one more step.
