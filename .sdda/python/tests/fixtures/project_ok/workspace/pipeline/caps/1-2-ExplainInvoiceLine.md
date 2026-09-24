# CAP-2: ExplainInvoiceLine

ID: 1-2-ExplainInvoiceLine
Parent MISSION: 1-SupportAssistant
Parent MISSION hash: sha256:5a983251
Status: Draft
Criticality: normal
Confidence: high

## Statement
Le système doit pouvoir expliquer une ligne de facture en citant le contrat source.

## Acceptance Criteria

- AC-1:
  - metric: groundedness
  - threshold: >= 0.85
  - dataset: workspace/pipeline/datasets/golden/billing-v1.jsonl
  - grader: llm-judge
  - calibration: workspace/pipeline/calibration/groundedness.json
  - runs: 3
  - notes: ne couvre pas les factures multi-devises

- AC-2:
  - metric: citation_resolve_rate
  - threshold: >= 0.98
  - dataset: workspace/pipeline/datasets/golden/billing-v1.jsonl
  - grader: exact
  - runs: 3

## Covers
- BR-2
- BR-3
- AC-2

## Inputs / Outputs
- input: {"$ref": "#/schemas/BillingRequest"}
- output: {"$ref": "#/schemas/BillingAnswer"}

## Failure Behavior
- si le retrieval ne remonte aucun document -> abstention explicite et proposition d'escalade

## Allocated To
- agents: `billing-specialist`
- tools: `invoice-lookup`, `zendesk-create-ticket`
- retrievers: `contracts-index`

## Dependencies
- 1-1

## Metadata
```json
{}
```
