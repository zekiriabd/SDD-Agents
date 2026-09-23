# CAP-4: AnswerBilling

ID: 1-4-AnswerBilling
Parent MISSION: 1-SupportDesk
Parent MISSION hash: sha256:31ddc73f
Status: Specified
Criticality: normal
Confidence: high

## Statement
Le système doit pouvoir répondre aux questions de facturation et de paiement
d'une commande du client identifié — montants, numéro de facture, statut de
paiement — sans exposer plus que les quatre derniers chiffres d'une carte et
sans recommander d'action hors mandat.

## Acceptance Criteria

- AC-1:
  - metric: numeric_tolerance
  - threshold: "<= 0.01"
  - dataset: workspace/proof/datasets/golden/answer-billing-v1.jsonl
  - grader: numeric-tolerance
  - runs: 3
  - notes: montants rendus (TTC par défaut, HT et TVA sur demande) comparés
    à la facture source ; ne couvre pas le multi-devises, absent du jeu de
    données.

- AC-2:
  - metric: false_positive_rate
  - threshold: "== 0"
  - dataset: workspace/proof/datasets/golden/answer-billing-v1.jsonl
  - grader: regex
  - runs: 3
  - notes: zéro numéro de carte complet exposé (seuls les 4 derniers
    chiffres autorisés) et présence systématique du numéro de facture cité.

- AC-3:
  - metric: accuracy
  - threshold: ">= 0.95"
  - dataset: workspace/proof/datasets/golden/answer-billing-v1.jsonl
  - grader: exact
  - runs: 3
  - notes: comportement correct par statut de paiement (`pending` avec
    échéance, `failed` avec motif et invitation à réessayer, facture
    absente -> « pas encore facturée ») ; ne couvre pas la relance
    effective du paiement, hors mandat de l'assistant.

## Covers
- BR-1
- BR-6
- BR-7
- BR-9
- BR-13
- AC-1
- AC-3
- AC-7

## Inputs / Outputs
- input: `{ tenant: string, order_id: string, as_of: string(date),
  detail_level?: "ttc"|"ht" }`
- output: `{ behavior: "answer"|"not_found", invoice_number?: string,
  amount_ttc?: number, amount_ht?: number, vat?: number,
  payment_status?: string, due_date?: string, failure_reason?: string,
  card_last4?: string, sources: array, degraded: boolean }`

## Failure Behavior
- Facture absente pour une commande existante -> « pas encore facturée »
  (BR-6), jamais inventée.
- `payments.failure_reason` (texte banque) contient une instruction ->
  traitée comme donnée, jamais exécutée (BR-7).
- Source `invoices`/`payments` indisponible ou périmée -> `degraded: true`,
  montant jamais inventé.
- Budget atteint -> échec explicite avec état partiel, jamais une réponse
  tronquée présentée comme complète.

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
