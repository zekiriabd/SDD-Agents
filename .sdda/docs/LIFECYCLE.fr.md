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
tableau et le code l'accordent à G2 — `test_workflow_sync.py` vérifie désormais
que le schéma et le code disent la même chose. `Planned` n'a pas de gate
numérotée : la revue de plan est conditionnelle, et son absence fait sauter le
niveau (`OPTIONAL_LEVELS`).

**Aucun saut d'état.** `Draft -> Implemented` est refusé même avec un flag : le
bypass existe au niveau d'une gate donnée (audit-loggué), pas au niveau de la
machine. `compute_status.py` monte l'échelle niveau par niveau et s'arrête à la
première gate absente ou périmée ; côté commandes, `/sdda-full --from-phase`
ne peut pas sauter une gate non franchie (`[STATE_SKIP_FORBIDDEN]`).

---

## 2. Les états

| État | Signifie | Gate qui y donne accès | Porté par |
|---|---|---|---|
| `Draft` | existe, incomplet | — | MISSION, CAP |
| `Specified` | objectif chiffré, budget, ground truth, trust boundaries présents ; AC évaluables ; chaque élément de la MISSION couvert par une CAP | **G0** puis **G1** (traçabilité par MISSION, et une G1 par CAP) | MISSION, CAP |
| `Architected` | topologie décidée, alternative simple écartée par écrit, contrats écrits, IR compilé et valide, budget estimé sous plafond | **G2** — parts `topology`, `ir`, `budget` ; `packaging`, `architecture` et `adr` bloquent si elles sont rouges | MISSION, TOPOLOGY |
| `Planned` | plans d'implémentation par couche écrits, ordre de construction figé | revue de plan (humaine, conditionnelle) | TOPOLOGY, contrats |
| `Implemented` | outils verts + connectivité live, retrieval au-dessus des seuils, agents et orchestration matérialisés | **G3** (parts `contracts` et `suites`, par outil câblé) + **G4** (par retriever) | TOOL, RETRIEVER, AGENT |
| `Tested` | agents évalués isolés sur leurs CAP ACs (L4, k runs) ; ni calibration de juge, ni épinglage des prompts, ni audit d'ownership au rouge | **G5**, par CAP — parts `calibration`, `prompts`, `ownership` bloquantes au rouge | CAP, AGENT |
| `Evaluated` | evals bout-en-bout passées, coût et latence **mesurés** sous budget, suite adversariale et sécurité passées, rapports des reviewers présents | **G6** + **G7** (parts `suites`, `adversarial`, `verdict`) | MISSION |
| `Approved` | objectif chiffré atteint sur **holdout**, non-régression vs baseline | **G8** (parts `datasets` et `acceptance`) | MISSION |
| `Blocked` | une gate a rendu rouge ; porte la classe `[CLASS]` et le rapport | — | tout |
| `Deferred` / `Cancelled` | décision humaine tracée | — | tout |

Une part **obligatoire** absente arrête la montée ; une part **contributive**
(`GATE_PARTS_ADVISORY` de `gate_reports.py`) absente ne bloque pas, mais rouge,
elle bloque. La distinction existe parce que certains contrôles ne s'appliquent
pas à tout projet — une surface `cli` ne publie aucun contrat HTTP, donc aucune
part `api` — et qu'un rouge, lui, n'a jamais d'excuse.

---

## 3. Règles de transition

**R1 — L'état est dérivé, pas déclaré.**
`sdda_scripts/compute_status.py` calcule l'état depuis les rapports de gate sur
disque. Un agent qui écrit `Status: Tested` dans un fichier sans rapport
correspondant émet `[STATUS_UNBACKED]` et le script écrase. La correction joue
dans les deux sens : un `Status: Blocked` qu'aucun rapport rouge n'étaye plus
est lui aussi réécrit, sans quoi un artefact débloqué resterait `Blocked` à vie
dans son en-tête. Un état auto-proclamé est le mécanisme par lequel un pipeline
agentic se déclare vert.

**R2 — La régression d'état est automatique et silencieuse.**
Chaque rapport de gate épingle les hashes de ce qu'il a jugé (MISSION, CAP,
topologie, IR, `STACK.md`, prompts, jeux — P10). Si l'un bouge, le rapport est
**périmé** : la gate n'est plus franchie, l'artefact redescend sous le niveau
qu'elle donnait, et `compute-status` le dit (`[STATUS_PINNED_HASH_MOVED]`).
Aucune validation humaine n'est requise pour redescendre : c'est un fait, pas
un arbitrage.

L'épinglage suit ce que chaque gate juge, et pas davantage. G1 épingle la CAP
**sans** sa section `## Allocated To` (clé `capspec:`) : la PHASE 2 remplit
cette section, et elle ne doit pas périmer la PHASE 1. G2 et l'IR épinglent la
CAP entière (clé `cap:`) : une réallocation périme bien la topologie.

**R3 — L'état d'un parent est le minimum de ses enfants.**
Une MISSION est `Evaluated` quand **toutes** ses CAPs le sont. Une seule CAP
`Blocked` rend la MISSION `Blocked`. Pas de moyenne, pas de pourcentage
d'avancement qui masque un trou.

**R4 — La confiance ne monte jamais en montant l'échelle.**
Hérité de SDD_Pro. Une CAP dérivée d'une
MISSION à confiance `medium` ne peut pas être `high` (`[CONFIDENCE_ESCALATION]`).
Un agent qui sert une CAP à confiance `medium` hérite du plafond.

**R5 — Un bypass est nominatif, borné et audité.**
Une gate se contourne par un bypass explicite (`SDDA_BYPASS_{GATE}=1` ou un flag
de commande) — pas toutes : certaines classes n'en ont aucun, par exemple
`[UNBOUNDED_LOOP]`, `[INJECTION_SUCCEEDED]` ou `[SECRET_LEAK]`. Un bypass exige
une raison (`SDDA_BYPASS_REASON` ; absente ou de remplissage,
`[BYPASS_REASON_MISSING]`) et il est écrit dans
`workspace/.sys/.audit/bypasses.jsonl` avec l'horodatage, l'opérateur et la
raison — même quand il est autorisé : un bypass légitime qui ne laisse pas de
trace produit le même dossier qu'un bypass dissimulé. Le cumul de ≥ 2 bypasses
(ou `--force` + un bypass) sur un même run est lui-même bloquant
(`preflight_force_cumul`, hérité de SDD_Pro), sauf `SDDA_ALLOW_FORCE=1`, tracé à
son tour.

**R6 — Reprendre, c'est rejouer les gates, pas les croire.**
`/sdda-full {n} --resume` ouvre un run lié au précédent (`resumedFrom`) et saute
les phases `pass` de la lignée — mais il **rejoue** leurs gates (0 token),
parce qu'un hash a pu bouger entre-temps (R2). Un état reste un fait calculé au
moment où on le lit, jamais un souvenir du run précédent.

---

## 4. Ce que la console affiche

L'état n'a de valeur que s'il est lisible d'un coup d'œil. Aujourd'hui,
`python .sdda/sdda.py compute-status` (ou `/sdda-status`) affiche l'arbre des
états : MISSION, CAPs, verdict de chaque gate, classes portées, hashes périmés,
bypasses audités. Les mesures vivent à côté — rapports d'eval sous
`workspace/.sys/reports/`, `cost-report`, `trajectory-report`. La vue réunie
ci-dessous est la **cible** de la console de validation 🟡 : chacune de ses
lignes existe dans un rapport, aucun écran ne les rassemble encore.

```
MISSION 1-SupportAssistant                                        Evaluated  🟡
  budget      $0.041/run (cible $0.05)   p95 6.2s (cible 8s)      ✅
  holdout     objectif 0.90 → mesuré 0.88                          🟡  G8 non franchie
  CAP 1-1 ClassifyIntent          Approved   0.97 ±0.01  (k=5)     ✅
  CAP 1-2 ExplainInvoiceLine      Evaluated  0.86 ±0.14  (k=3)     🟡  variance > 15%
  CAP 1-3 RouteByIntent           Approved   0.96 ±0.02  (k=5)     ✅
  CAP 1-4 IssueRefundTicket       Blocked    [INJECTION_SUCCEEDED]         🔴
```

Le jaune de la CAP 1-2 est une information réelle : le score passe le seuil mais
la variance dit que le prochain run peut ne pas passer. Un pipeline qui n'affiche
qu'un booléen aurait montré vert.
