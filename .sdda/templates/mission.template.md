# MISSION: {Name}

MISSION ID: {n}-{Name}
Status: Draft
Confidence: high          # high | medium | low — ne monte jamais en montant l'échelle

## Context
<2-4 phrases. Ce qui existe aujourd'hui. Ce qui manque. Pas d'aspiration.>

## Objective
<Un seul résultat observable. Ce que le système agentic doit accomplir.>

## Quantified Goal
<NE PAS annoter ce titre : les scripts compilent `^##\s+{titre}\s*$`.
 Métrique + cible + échéance. Écrire `<à préciser>` rend le trou explicite —
 mais G0 refuse un `<à préciser>` résiduel.>
- Metric: <ex. taux de résolution sans escalade humaine>
- Target: <ex. >= 0.75 sur le holdout>
- Deadline: <ex. 2026-12-01>

## Execution Budget
<Exigence FONCTIONNELLE, pas un sujet d'exploitation (P6). Estimé en G2,
 mesuré en G6, bloquant aux deux.>
- CostPerRunTargetUsd: <ex. 0.05>
- CostPerRunHardCapUsd: <ex. 0.25>
- LatencyP95TargetMs: <ex. 8000>
- TokenCeilingPerRun: <ex. 60000>
- Justification: <pourquoi ces chiffres — modèle économique, SLA, volume attendu>

## Ground Truth
<Obligatoire (G0). Contre quoi évalue-t-on ? Sans réponse ici, aucune eval
 n'est possible et le projet ne doit pas démarrer.>
- Source: <ex. 400 conversations support résolues et annotées, export Zendesk 2026>
- Owner: <qui arbitre un désaccord sur la vérité>
- Volume available: <ex. 400 items, dont 50 labellisés pour calibration>
- Gaps: <ce dont on n'a PAS de vérité terrain — et ce qu'on en fait>

## Trust Boundaries
<Quelles sources sont NON maîtrisées (P8). Chacune impose une suite d'injection.>
- Untrusted: <ex. documents du corpus client, réponses de l'API partenaire, message utilisateur>
- Trusted: <ex. base interne en lecture seule via vues>

## Actors
- <acteur-1>: <rôle, ce qu'il attend, ce qu'il a le droit de voir>

## Business Rules
- BR-1: <règle métier sans ambiguïté — elle finira dans un prompt ou dans un outil>
- BR-N:

## Acceptance Criteria
<Niveau système. Chacun sera décliné en AC de CAP mesurables.>
- AC-1: <condition observable>
- AC-N:

## Failure Policy
<Obligatoire (G0). Sans équivalent dans une application classique : un système
 agentic RENCONTRERA des cas hors compétence. Ne pas décider quoi en faire,
 c'est décider qu'il inventera.>
- Hors compétence: <ex. abstenir + proposer l'escalade humaine, jamais deviner>
- Confiance faible: <ex. répondre en signalant l'incertitude et en citant la source>
- Outil indisponible: <ex. dégrader vers une réponse partielle explicite>
- Budget atteint: <ex. échec explicite avec l'état partiel, jamais une réponse tronquée silencieuse>

## Required Stack
<Anti-dérive : G0 vérifie que STACK.md active exactement ces stacks.>
- language: <ex. python>
- framework: <ex. langgraph, ou none>
- orchestration: <ex. router>
- rag: <ex. hybrid, ou none>
- dataaccess: <ex. view-per-agent, ou none>
- serving: <ex. fastapi-sse>

## Out of Scope
- <ce qui est explicitement exclu — et ce qui n'est PAS exclu mais reporté>

## Dependencies
- <MISSION id ou NONE>
