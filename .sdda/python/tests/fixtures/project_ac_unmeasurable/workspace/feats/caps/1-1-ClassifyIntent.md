# CAP-1: ClassifyIntent

ID: 1-1-ClassifyIntent
Parent MISSION: 1-SupportAssistant
Parent MISSION hash: sha256:ea8cddec
Status: Draft
Criticality: critical
Confidence: high

## Statement
Le système doit pouvoir classer une question client dans une intention de la liste close.

## Acceptance Criteria

- AC-1: l'agent classe la question de manière utile et pertinente

## Covers
- BR-1
- AC-1

## Inputs / Outputs
- input: {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}
- output: {"type": "object", "properties": {"intent": {"enum": ["billing", "technical", "other"]}}, "required": ["intent"]}

## Failure Behavior
- si la confiance de classification < 0.7 -> demander une clarification, ne jamais router par défaut

## Allocated To
- agents: `intent-classifier`
- tools: aucun
- retrievers: aucun

## Dependencies
- NONE

## Metadata
```json
{}
```
