# CAP-{m}: {Name}

ID: {n}-{m}-{Name}
Parent MISSION: {n}-{MissionName}
Parent MISSION hash: sha256:{mission-hash-8}   # détecte une MISSION modifiée sous les pieds
Status: Draft
Criticality: normal            # normal | critical — pilote k runs et les seuils
Confidence: high

## Statement
Le système doit pouvoir <action observable et discrète>.

<Une CAP n'est PAS un agent. Une CAP dit CE QUE le système doit savoir faire ;
 le roster des agents est déclaré par l'ARCHITECTE dans
 `topology/{n}-topology.md ## 2. Roster déclaré` (P7). L'allocation CAP -> agent
 se fait en PHASE 2 contre ce roster, et se révise sans toucher cette CAP.>

## Acceptance Criteria
<LA section qui fait la valeur du framework. Chaque AC nomme :
 métrique + seuil + dataset + k runs. Un AC non mesurable est REJETÉ par G1
 avec [AC_NOT_EVALUABLE]. Voir PHILOSOPHY.md P2.>

- AC-1:
  - metric: <groundedness | routing_accuracy | exact_match | recall@k | schema_valid
             | trajectory_match | cost_usd | latency_ms | abstention_rate | …>
  - threshold: <ex. >= 0.85>
  - dataset: <workspace/datasets/golden/{nom}.jsonl>
  - grader: <exact | regex | schema | numeric-tolerance | semantic-similarity
             | llm-judge | trajectory | cost | latency>
  - runs: <3, ou 5 si criticality = critical>
  - notes: <ce que l'AC NE couvre pas — le trou assumé>

- AC-2:
  - metric:
  - threshold:
  - dataset:
  - grader:
  - runs:

## Covers
<Traçabilité montante. Chaque BR et AC de la MISSION doit apparaître dans le
 Covers d'au moins une CAP — vérifié par G1.>
- BR-<i>
- AC-<i>

## Inputs / Outputs
<Des schémas, pas de la prose. Ils deviendront les schémas de l'IR.>
- input: <JSON Schema inline ou $ref>
- output: <JSON Schema inline ou $ref>

## Failure Behavior
<Que fait le système quand CETTE capability échoue ? Hérite de la Failure
 Policy de la MISSION, ou la spécialise.>
- <ex. si la confiance de classification < 0.7 -> demander une clarification,
   ne jamais router par défaut>

## Allocated To
<Rempli en PHASE 2 par architect-topology, en rattachant cette CAP à un membre
 du ROSTER DÉCLARÉ par l'architecte. Laisser vide en PHASE 1 : une CAP qui nomme
 un agent mélange le besoin et l'architecture, et rend la CAP non réutilisable
 si le roster change.>
- agents: <à déterminer>
- tools: <à déterminer>
- retrievers: <à déterminer>

## Dependencies
<CAPs dont celle-ci dépend. Format `{n}-{m}`, ou NONE.>
- NONE

## Metadata
```json
{}
```
