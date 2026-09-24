# RETRIEVAL CONTRACT: 1-contracts-index

MISSION: 1-SupportAssistant
Status: Draft
Pattern: hybrid
Store: pgvector
Embedding Model: voyage-3-large
Citation Mode: required

---

## Chunking

- strategy: recursive-structural
- size: 800
- overlap: 120

## Retrieval

- topK: 8
- hybridWeights: {vector: 0.6, lexical: 0.4}
- rerank: none
- identityFilter: customer_id

## Gate Thresholds

- recallAtK: 0.80
- ndcg: 0.70
- groundedness: 0.85
- citationResolveRate: 0.98

## Index

- indexHash: sha256:0123456789abcdef
