# CAP-6: AssessRefund

ID: 1-6-AssessRefund
Parent MISSION: 1-SupportDesk
Parent MISSION hash: sha256:31ddc73f
Status: Specified
Criticality: critical
Confidence: high

## Statement
Le système doit pouvoir qualifier l'éligibilité au remboursement d'une
commande du client identifié selon les règles métier déclarées, et produire
une demande structurée pour un conseiller, sans jamais confirmer le
remboursement lui-même.

## Acceptance Criteria

- AC-1:
  - metric: accuracy_per_class
  - threshold: ">= 0.90"
  - dataset: workspace/proof/datasets/golden/assess-refund-v1.jsonl
  - grader: exact
  - runs: 5
  - notes: mesurée séparément sur les trois classes (`eligible`,
    `not_eligible`, `needs_review`) — `needs_review` est la classe la plus
    critique (article manquant/endommagé/erroné), une moyenne globale la
    masquerait.

- AC-2:
  - metric: false_positive_rate
  - threshold: "== 0"
  - dataset: workspace/proof/datasets/golden/assess-refund-v1.jsonl
  - grader: regex
  - runs: 5
  - notes: zéro formulation confirmant un remboursement — le zéro absolu
    nommé explicitement par la MISSION (AC-4).

- AC-3:
  - metric: schema_valid
  - threshold: "== 1.0"
  - dataset: workspace/proof/datasets/golden/assess-refund-v1.jsonl
  - grader: schema
  - runs: 5
  - notes: structure du `claim_request` de type remboursement (eligibility,
    rule citée) ; ne couvre pas le montant, toujours défini par un
    conseiller (BR-8).

- AC-4:
  - metric: trajectory_match
  - threshold: ">= 0.95"
  - dataset: workspace/proof/datasets/golden/assess-refund-v1.jsonl
  - grader: trajectory
  - runs: 5
  - notes: une question de politique générale (« délai de rétractation ? »)
    est répondue directement sans appel d'outil (BR-8/BR-12) ; distingue la
    question générale de la demande concrète sur une commande.

## Covers
- BR-1
- BR-5
- BR-8
- BR-9
- BR-10
- BR-12
- BR-13
- AC-1
- AC-4
- AC-5
- AC-7

## Inputs / Outputs
- input: `{ tenant: string, order_id?: string,
  question_type: "policy"|"order-specific", as_of: string(date) }`
- output: `{ behavior: "answer"|"clarify"|"refuse", eligibility?:
  "eligible"|"not_eligible"|"needs_review", claim_request?: { type: string,
  order_id: string, customer_id: string, reason: string, facts: array,
  eligibility: string, rule: string }, policy_answer?: string,
  sources: array, degraded: boolean }`

## Failure Behavior
- Article manquant, endommagé ou erroné -> `needs_review`, aucun montant
  proposé par l'assistant, réservé au conseiller (BR-8).
- Remboursement déjà `approved`/`processed`, ou réclamation ouverte pour la
  même commande (historique partagé avec CAP 1-5, BR-5) -> on donne son
  état, aucune nouvelle qualification.
- Source `orders`/`shipments`/`refunds` indisponible ou périmée ->
  `degraded: true`, éligibilité jamais inventée.
- Budget atteint -> échec explicite avec état partiel, jamais une
  éligibilité tronquée présentée comme définitive.

## Allocated To
- agents: <à déterminer>
- tools: <à déterminer>
- retrievers: <à déterminer>

## Dependencies
- 1-1

## Metadata
```json
{}
```
