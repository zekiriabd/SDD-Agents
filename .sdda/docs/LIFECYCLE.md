# Cycle de vie et machine à états

Chaque artefact de spécification porte un `Status`. Les transitions sont
**gardées** : on ne passe à l'état suivant qu'en franchissant une gate. L'état est
un **fait** dérivé des gates franchies, jamais une déclaration d'intention d'un
agent.

---

## 1. La machine à états

```
   Draft ──G0 · G1──► Specified ──G2──► Architected ──(revue de plan)──► Planned
                                                                          │
                                                                          │ G3 · G4
                                                                          ▼
                                                                     Implemented
                                                                          │ G5
                                                                          ▼
                                                                       Tested
                                                                          │ G6 · G7
                                                                          ▼
                                                                      Evaluated
                                                                          │ G8
                                                                          ▼
                                                                       Approved

   Depuis tout état non terminal :  ──► Blocked   (gate rouge, cause [CLASS] portée)
                                    ──► Deferred
                                    ──► Cancelled
```

Une étiquette d'arête nomme la gate qui **donne** l'état d'arrivée — celle du
tableau ci-dessous et de `compute_status.py`. Le schéma les décalait d'un cran
(`Specified ──G1──► Architected`) : il plaçait `Architected` après G1, là où le
tableau et le code l'accordent à G2. `Planned` n'a pas de gate numérotée : la
revue de plan est conditionnelle, et son absence fait sauter le niveau
(`OPTIONAL_LEVELS`).

**Aucun saut d'état.** `Draft -> Implemented` est refusé même avec un flag : le
bypass existe au niveau d'une gate donnée (audit-loggué), pas au niveau de la
machine.

---

## 2. Les états

| État | Signifie | Gate qui y donne accès | Porté par |
|---|---|---|---|
| `Draft` | existe, incomplet | — | MISSION, CAP |
| `Specified` | objectif chiffré, budget, ground truth, trust boundaries présents ; AC évaluables | **G0** puis **G1** | MISSION, CAP |
| `Architected` | topologie décidée, alternative simple écartée par écrit, contrats écrits, IR compilé et valide, budget estimé sous plafond | **G2** | MISSION, TOPOLOGY |
| `Planned` | plans d'implémentation par couche écrits, ordre de construction figé | revue de plan (humaine, conditionnelle) | TOPOLOGY, contrats |
| `Implemented` | outils verts + connectivité live, retrieval au-dessus des seuils, agents et orchestration matérialisés | **G3** + **G4** | TOOL, RETRIEVER, AGENT |
| `Tested` | L0→L2 verts, agents évalués isolés sur leurs CAP ACs | **G5** | CAP, AGENT |
| `Evaluated` | evals bout-en-bout passées, coût et latence **mesurés** sous budget, suite adversariale et sécurité passées | **G6** + **G7** | MISSION |
| `Approved` | objectif chiffré atteint sur **holdout**, non-régression vs baseline | **G8** | MISSION |
| `Blocked` | une gate a rendu rouge ; porte la classe `[CLASS]` et le rapport | — | tout |
| `Deferred` / `Cancelled` | décision humaine tracée | — | tout |

---

## 3. Règles de transition

**R1 — L'état est dérivé, pas déclaré.**
`sdda_scripts/compute_status.py` calcule l'état depuis les rapports de gate sur
disque. Un agent qui écrit `Status: Tested` dans un fichier sans rapport
correspondant émet `[STATUS_UNBACKED]` et le script écrase. Un état auto-proclamé
est le mécanisme par lequel un pipeline agentic se déclare vert.

**R2 — La régression d'état est automatique et silencieuse.**
Si un hash épinglé bouge (prompt, modèle, index, schéma d'outil, dataset — P10),
tout artefact au-dessus de `Implemented` **redescend** à `Implemented` et les
résultats concernés sont marqués périmés. Aucune validation humaine n'est requise
pour redescendre : c'est un fait, pas un arbitrage.

**R3 — L'état d'un parent est le minimum de ses enfants.**
Une MISSION est `Evaluated` quand **toutes** ses CAPs le sont. Une seule CAP
`Blocked` rend la MISSION `Blocked`. Pas de moyenne, pas de pourcentage
d'avancement qui masque un trou.

**R4 — La confiance ne monte jamais en montant l'échelle.**
Hérité de SDD_Pro. Une CAP dérivée d'une
MISSION à confiance `medium` ne peut pas être `high`. Un agent qui sert une CAP à
confiance `medium` hérite du plafond.

**R5 — Un bypass est nominatif, borné et audité.**
Chaque gate a un bypass explicite (`SDDA_BYPASS_{GATE}=1` ou un flag de commande).
Il est écrit dans `workspace/.sys/.audit/bypasses.jsonl` avec l'horodatage,
l'opérateur et la raison. Le cumul de ≥ 2 bypasses sur un même run est lui-même
bloquant (`preflight_force_cumul`, hérité de SDD_Pro).

---

## 4. Ce que la console affiche

L'état n'a de valeur que s'il est lisible d'un coup d'œil :

```
MISSION 1-SupportAssistant                                        Evaluated  🟡
  budget      $0.041/run (cible $0.05)   p95 6.2s (cible 8s)      ✅
  holdout     objectif 0.90 → mesuré 0.88                          🟡  G8 non franchie
  CAP 1-1 ClassifyIntent          Approved   0.97 ±0.01  (k=5)     ✅
  CAP 1-2 ExplainInvoiceLine      Evaluated  0.86 ±0.09  (k=3)     🟡  variance > 15%
  CAP 1-3 RouteByIntent           Approved   0.96 ±0.02  (k=5)     ✅
  CAP 1-4 IssueRefundTicket       Blocked    [INJECTION_SUCCEEDED]         🔴
```

Le jaune de la CAP 1-2 est une information réelle : le score passe le seuil mais
la variance dit que le prochain run peut ne pas passer. Un pipeline qui n'affiche
qu'un booléen aurait montré vert.
