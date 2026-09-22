# MISSION: SupportAssistant

MISSION ID: 1-SupportAssistant
Status: Draft
Confidence: high

## Context
Le support facturation traite 1 200 tickets par mois, dont 60 % portent sur la
lecture d'une ligne de facture. Les réponses sont manuelles et lentes.

## Objective
Répondre aux questions de facturation de premier niveau avec des citations
vérifiables, et escalader le reste.

## Quantified Goal
- Metric: taux de résolution sans escalade humaine
- Target: >= 0.75 sur le holdout
- Deadline: 2026-12-01

## Execution Budget
- CostPerRunTargetUsd: 0.08
- CostPerRunHardCapUsd: 0.30
- LatencyP95TargetMs: 9000
- TokenCeilingPerRun: 60000
- Justification: marge brute de 0.40 EUR par conversation, 1 200 conversations/mois

## Ground Truth
- Source: 400 conversations support résolues et annotées (export Zendesk 2026-06)
- Owner: responsable support facturation
- Volume available: 400 items, dont 50 labellisés pour calibration
- Gaps: aucune vérité terrain sur les litiges > 500 EUR — hors périmètre, escalade systématique

## Trust Boundaries
- Untrusted: `user_message`, `retrieved_documents`
- Trusted: base facturation en lecture seule via vues

## Actors
- client: pose une question sur sa facture, ne voit que ses propres données
- agent support: reçoit les escalades avec l'état partiel

## Business Rules
- BR-1: toute question est classée dans une intention de la liste close (billing, technical, other)
- BR-2: toute affirmation factuelle sur une facture cite la ligne de contrat source
- BR-3: aucun remboursement n'est émis par le système, seulement un ticket

## Acceptance Criteria
- AC-1: une question de facturation est routée vers le spécialiste facturation
- AC-2: une explication de ligne de facture est fondée sur les documents contractuels

## Failure Policy
- Hors compétence: s'abstenir et proposer l'escalade humaine, jamais deviner
- Confiance faible: répondre en signalant l'incertitude et en citant la source
- Outil indisponible: dégrader vers une réponse partielle explicite
- Budget atteint: échec explicite avec l'état partiel, jamais une réponse tronquée silencieuse

## Required Stack
- language: python
- framework: langgraph
- orchestration: router
- rag: hybrid
- dataaccess: none
- serving: cli

## Out of Scope
- les litiges > 500 EUR (escalade directe)

## Dependencies
- NONE
