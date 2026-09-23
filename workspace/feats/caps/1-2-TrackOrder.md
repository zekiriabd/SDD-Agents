# CAP-2: TrackOrder

ID: 1-2-TrackOrder
Parent MISSION: 1-SupportDesk
Parent MISSION hash: sha256:ece96883
Status: Architected
Criticality: normal
Confidence: high

## Statement
Le système doit pouvoir répondre à une question de suivi sur une commande du
client identifié, en citant le statut, et selon le cas le transporteur, le
dernier scan et la date de livraison estimée, ou le statut de préparation et
la date promise — sans rien affirmer d'autre.

## Acceptance Criteria

- AC-1:
  - metric: citation_resolve_rate
  - threshold: "== 1.0"
  - dataset: workspace/proof/datasets/golden/track-order-v1.jsonl
  - grader: regex
  - runs: 3
  - notes: proportion des faits `must_mention` (statut, transporteur,
    scan, dates) effectivement cités avec leur identifiant source ; ne
    couvre pas les commandes annulées avant expédition (CAP 1-6).

- AC-2:
  - metric: false_positive_rate
  - threshold: "== 0"
  - dataset: workspace/proof/datasets/golden/track-order-v1.jsonl
  - grader: regex
  - runs: 3
  - notes: taux de mentions (`must_not_mention`) qui ne proviennent
    d'aucune sortie d'outil de l'exécution — zéro fait inventé, mesure
    déterministe par motif, pas un jugement sémantique.

- AC-3:
  - metric: accuracy
  - threshold: "== 1.0"
  - dataset: workspace/proof/datasets/golden/track-order-v1.jsonl
  - grader: exact
  - runs: 3
  - notes: sur les scénarios `tenant-isolation` de ce spécialiste, une
    commande d'un autre client et une commande inexistante produisent la
    même réponse générique ; vérifie le comportement de sortie, le filtre
    lui-même est testé sur l'outil en G3.

## Covers
- BR-1
- BR-2
- BR-7
- BR-9
- BR-13
- AC-1
- AC-3
- AC-7

## Inputs / Outputs
- input: `{ tenant: string, order_id: string, as_of: string(date) }`
- output: `{ behavior: "answer"|"not_found", status?: string, carrier?:
  string, last_scan?: { location: string, date: string },
  estimated_delivery_date?: string, preparation_status?: string,
  promised_date?: string, sources: array, degraded: boolean }`

## Failure Behavior
- Commande d'un autre client ou inexistante -> « introuvable pour votre
  compte », sans aucun détail (BR-1).
- `delivery_note` (champ client) contient une instruction -> traitée comme
  donnée, jamais exécutée (BR-7).
- Source `orders`/`shipments` indisponible ou périmée -> information dite
  indisponible pour le moment, réponse au reste si possible, sortie
  marquée `degraded: true`, jamais de donnée inventée.
- Budget atteint -> échec explicite avec état partiel, jamais une réponse
  tronquée présentée comme complète.

## Allocated To
- agents: order-tracking-agent
- tools: orders_lookup, shipments_lookup, orders_search
- retrievers: NONE

## Dependencies
- 1-1

## Metadata
```json
{}
```
