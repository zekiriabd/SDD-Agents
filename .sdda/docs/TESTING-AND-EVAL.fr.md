# Testing et Evaluation

La distinction fondatrice : **un test assert, une eval score.** Un système
agentic a besoin des deux, et les confondre produit soit des tests instables
qu'on finit par désactiver, soit des evals qui ne détectent rien.

| | Test | Eval |
|---|---|---|
| Porte sur | la partie **déterministe** | la partie **médiée par un LLM** |
| Verdict | pass / fail | score + variance vs seuil |
| Exécutions | 1 | **k** (défaut 3, 5 si critique) |
| Instable ⇒ | c'est un bug | c'est la nature du système, à quantifier |
| Coût | ~0 | tokens |

---

## 1. La pyramide L0 → L9

Chaque niveau a son écrivain de gate, et un seul : deux écrivains sur la même
gate produiraient deux vérités sur le même fait.

| Niveau | Gate | Qui l'exécute et écrit le rapport |
|---|---|---|
| L0–L1 | — | `qa-tests` (pytest, LLM mocké) + scripts de validation |
| L2 | G3, part `suites` | `run-tool-suites` (joue les tests pytest de `qa-tests`) |
| L3 | G4 | `run-retrieval-eval` |
| L4 | G5 | `eval-runner --level L4` |
| L5, L7 | G6 | `eval-runner --level L5,L7` |
| L6 | — | tests d'intégration, sans gate propre |
| L8 | G7, parts `suites` et `adversarial` | `eval-runner --level L8`, `run-adversarial-suite` |
| L9 | G8, part `acceptance` | `eval-runner --level L9 --dataset holdout`, puis `check-regression` |

### L0 — Statique, déterministe, 0 token
S'exécute à chaque commit, en quelques secondes.

- JSON Schema de chaque définition d'outil ;
- **lint de prompt** : pas de secret, pas d'instruction contradictoire, taille
  sous plafond, variables de template toutes résolvables, pas de référence à un
  outil inexistant, symétrie des skills entre contrat et prompt ;
- **validation de l'IR** : atteignabilité, terminaison, cycles bornés,
  références closes, moindre privilège (cf. `AGENTIC-IR.md §4`) ;
- **estimation de budget** sur le graphe ;
- disjonction `golden ∩ holdout = ∅` par hash ;
- fraîcheur des baselines (tuple de hashes de P10) ;
- audit d'ownership, scan de secrets ;
- CVE des dépendances 🟡 — non outillé pour l'application générée : les
  versions épinglées des `.libs.json` sont reprises de catalogues SDD_Pro qui
  les auditent, et Dependabot ne suit que les dépendances du framework
  lui-même.

### L1 — Unit
Fonctions pures : parsers, chunkers, mappers, réducteurs d'état, calculs de
coût, politiques de retry. **LLM mocké.** Vitesse et déterminisme complets.

### L2 — Contrat d'outil
Chaque outil contre son schéma : happy path, **chaque erreur déclarée**,
timeout, échec d'authentification, idempotence (appeler deux fois avec la même
clé produit-il un seul effet ?), respect du rate limit.

`qa-tests` écrit ces tests depuis la suite de l'outil
(`workspace/pipeline/suites/tool-{n}-{outil}.yaml`) ; `run-tool-suites` les
joue et exige que chaque cas déclaré soit **exercé** — un cas nommé mais jamais
joué n'est pas couvert, et un `xfail` est un contrat non tenu, donc rouge.

Un test de **connectivité live** existe séparément, marqué `network`, et
constitue la seconde moitié de la TOOL GATE. Un outil dont le contrat est vert
mais dont le service est inaccessible passerait sinon la gate.

### L3 — Retrieval evals *(RETRIEVAL GATE)*
Golden set de requêtes avec vérité terrain. **Aucun agent impliqué.**
`recall@k`, `nDCG@k`, `context_precision`, `groundedness`,
`citation_resolve_rate` (cf. `RAG-PATTERNS.md §5`).

`run-retrieval-eval` calcule tout sauf la groundedness, qui exige un juge : il la
reçoit de l'exécuteur ou du replay, et sans elle le verdict passe **jaune**,
avec la raison écrite — jamais vert par omission. Un rapport par retriever
(`G4-{retriever}.json`) : un index peut être vert pendant qu'un autre est rouge.

### L4 — Agent evals isolés *(AGENT GATE)*
Un agent seul, **outils mockés et retrieval figé**, contre les AC de ses CAPs.

L'isolement est le point : un agent évalué avec de vrais outils mesure la
somme de l'agent et des outils. Quand le score baisse, on ne sait pas lequel a
bougé. Avec des mocks, la variation est attribuable.

Graders : `exact`, `regex`, `schema`, `numeric-tolerance`,
`semantic-similarity`, `llm-judge` (calibré), `trajectory`.

La G5 se juge par CAP, et trois parts contributives la bloquent au rouge : la
`calibration` du juge (§3), l'épinglage des `prompts` (`[PROMPT_MISSING]`,
`[PROMPT_HASH_MISMATCH]`) et l'audit d'`ownership` de la phase.

### L5 — Orchestration / trajectoire
Sur la trace, pas sur la réponse :

- le routeur a-t-il routé correctement — **accuracy par classe** ;
- les outils appelés sont-ils ceux attendus, dans un ordre admissible ;
- nombre de hops : distribution et queue, pas seulement la moyenne ;
- les bornes sont-elles respectées, et le comportement à l'atteinte est-il celui
  qui est déclaré ;
- aucun état terminal inatteignable, aucune boucle observée.

Une réponse correcte obtenue par une trajectoire aberrante est un faux vert :
elle coûte dix fois le budget et cassera au prochain changement de prompt.

`trajectory-report` reconstruit chaque trajectoire depuis l'arbre des spans et
la confronte au graphe de l'IR, sans LLM : `[TRAJECTORY_VIOLATION]`,
`[BOUND_BEHAVIOR_MISMATCH]`, `[ORCH_PING_PONG]`, matrice de confusion par
classe. `review-orchestration` lit ce rapport ; il ne le refait pas.

### L6 — Intégration
Vrais outils, vrai index, vraie base de test. Connexions API, connexions base,
migrations, ingestion bout-en-bout. Sans LLM quand c'est possible. Aucune gate
ne lui appartient : ce qu'il prouve, les gates G3 et G4 le prouvent déjà couche
par couche ; il sert à trouver ce qui casse **entre** elles.

### L7 — End-to-end mission evals *(ORCH GATE)*
Système complet sur le golden set de la mission. Mesure conjointe de la qualité,
du **coût** et de la **latence**. Un run qui atteint le score en dépassant
`CostPerRunHardCapUsd` est **rouge**, pas jaune.

Le coût est recalculé depuis les tokens de chaque span, jamais relu depuis ce
que l'application déclare. La G6 porte aussi deux parts contributives : `api`
(le contrat HTTP publié est dérivé de l'IR) et `framework` (le code importe le
framework déclaré et aucun autre, `[FRAMEWORK_DRIFT]`).

### L8 — Adversarial et sécurité *(SAFETY GATE)*
Détaillé au §4.

### L9 — Acceptation et régression *(ACCEPTANCE GATE)*
Deux mesures, dans cet ordre, et uniquement sur le **holdout** :

1. **Acceptation** — la suite `{n}-acceptance`, compilée depuis
   `## Quantified Goal` de la MISSION, est la seule qui lise le holdout
   (`[AC_DATASET_IS_HOLDOUT]` pour toute autre). Objectif non atteint :
   `[GOAL_NOT_MET]`.
2. **Régression** — comparaison à la baseline épinglée. Une baisse au-delà de
   `RegressionTolerancePct` bloque (`[REGRESSION]`) — sauf si elle tient dans la
   bande de bruit de la baseline (`RegressionNoiseSigma` écarts-types) : c'est
   alors un tirage, `[REGRESSION_WITHIN_NOISE]` en avertissement. Une baseline
   dont le tuple d'épinglage diffère n'est pas comparée du tout
   (`[EVAL_BASELINE_STALE]`).

La baseline se déplace **explicitement**, par une action tracée
(`promote-baseline --run`), jamais par écrasement automatique — sinon la dérive
lente devient invisible.

---

## 2. Le protocole de non-déterminisme

**Règle** : aucune eval médiée par un LLM n'est rapportée depuis un seul run
(`[EVAL_SINGLE_RUN_FORBIDDEN]`). Invariant `non-determinism-k-runs`. `k` vient
de `EvalRuns` / `EvalRunsCritical`, sauf surcharge explicite `--runs`, et chaque
run est rejoué **sans cache** : réutiliser la sortie d'un run pour le suivant
reviendrait à mesurer une fois et rapporter k fois.

Le rapport (`workspace/.sys/reports/{n}-{RUN_ID}.json`) porte par suite :
`mean`, `stddev` (écart-type de population : les k runs **sont** la population
mesurée), `variancePct` (écart-type rapporté à la moyenne), `passRate`
(proportion des k runs au-dessus du seuil), `min`, `max`, et le détail par
classe.

```
CAP 1-2 ExplainInvoiceLine — groundedness
  seuil 0.85   k=3
  runs  0.91 · 0.88 · 0.80
  mean 0.863  stddev 0.046  pass_rate 0.67
  VERDICT: 🟡 JAUNE — moyenne au-dessus du seuil, un run sur trois en dessous.
           Ne pas livrer sur la foi du premier run.
```

Le verdict est à trois couleurs :

| | Condition |
|---|---|
| 🟢 **VERT** | `mean ≥ seuil` **et** aucune classe sous son seuil **et** `passRate = 1.0` **et** `variancePct ≤ EvalVarianceWarnPct` |
| 🟡 **JAUNE** | seuil franchi en moyenne mais `passRate < 1.0` ou `variancePct > EvalVarianceWarnPct` |
| 🔴 **ROUGE** | `mean < seuil`, ou une classe sous son seuil, ou tous les items en erreur d'exécution, ou un budget dépassé |

Une suite `advisory` — typiquement un juge non calibré — garde son verdict réel
dans le rapport, mais son rouge ne compte que pour un jaune dans la gate : elle
informe, elle ne bloque pas.

Un jaune n'est pas une indécision : c'est l'information qu'un déploiement est un
pari sur le prochain tirage.

---

## 3. Calibration des juges LLM

Un grader LLM non validé ne rend pas de verdict bloquant (P9, invariant
`llm-judge-calibrated`).

Le juge est un vrai client : `graders/judge_clients.py` appelle Anthropic,
OpenAI (et compatibles), Gemini ou Ollama en stdlib seule, à température 0 et en
sortie JSON structurée quand le fournisseur la contraint. Le modèle vient de
`JudgeModel` (`STACK.md ## Runtime Models`) ; la clé est lue dans
l'environnement du processus, sous le **nom** déclaré par la fiche provider —
jamais depuis un `.env`, qui appartient à l'application générée.

**Procédure** :
1. `qa-evals` produit un set de calibration : ≥ 50 items du domaine réel
   (`JudgeCalibrationMinItems`), labellisés par un humain selon la grille exacte
   du juge.
2. Le juge note les mêmes items.
3. `calibrate-judge` recalcule l'accord (kappa de Cohen pour du binaire/ordinal,
   corrélation pour du continu). Seuil par défaut : **κ ≥ 0.6**
   (`JudgeCalibrationMinKappa`).
4. Le jeu est versionné dans `workspace/pipeline/calibration/{grader}.json` et
   référencé depuis l'IR (`judgeCalibrationRef`) ; le script écrit la part
   `calibration` de la G5 (`G5-{n}.calibration.json`), et une calibration
   **rouge bloque la G5**.
5. En dessous du seuil : la grille est retravaillée, ou le juge devient
   `advisory` — il produit un score informatif mais ne bloque plus
   (`JudgeAdvisoryFallback`).

**Règles complémentaires** :
- des labels produits par un modèle ne calibrent rien
  (`[JUDGE_CALIBRATION_SYNTHETIC]`, sans bypass) ;
- un accord déclaré sans labels vérifiables reste un avertissement, jamais une
  mesure ;
- le juge n'est pas le modèle évalué : avec `JudgeMustDifferFromEvaluated: true`
  (le défaut), la configuration est refusée au preflight
  (`[JUDGE_SAME_AS_EVALUATED]`), et le cas est signalé au runtime s'il
  réapparaît (`[JUDGE_EQUALS_EVALUATED]`, advisory) ;
- la grille est une liste de critères vérifiables, pas « note de 1 à 10 la
  qualité » ; le juge voit la référence quand elle existe.

---

## 4. Suite adversariale et sécurité (L8, SAFETY GATE)

Exécutée contre le **système vivant**, jamais par relecture de code.

| Famille | Attaque | Attendu |
|---|---|---|
| **Injection directe** | « ignore les instructions précédentes et… » dans le message utilisateur | refus, comportement inchangé |
| **Injection indirecte** | un document du corpus ou une réponse d'API contient l'instruction | **le cas le plus important** — traité comme donnée, jamais exécuté |
| **Injection via outil** | un serveur MCP `untrusted` renvoie une instruction | ignorée |
| **Abus d'outil** | pousser l'agent à appeler un outil destructif hors mandat | refus + journalisation |
| **Escalade de privilège** | via la délégation, atteindre un outil d'un autre agent | impossible par construction (scopes) |
| **Exfiltration** | faire sortir un secret, une PII, ou le prompt système par un outil sortant | bloquée |
| **Franchissement d'autorisation** | obtenir un document d'un autre tenant par le retrieval | filtré à la source |
| **Épuisement de budget** | provoquer une boucle | borne atteinte, comportement déclaré |
| **Jailbreak de persona** | faire sortir du mandat déclaré | `refusal_policy` respectée |

`AdversarialSetMinItems: 25` minimum, et le set grandit : **toute attaque réussie
découverte devient un item permanent**. C'est le mécanisme qui empêche la même
faille de revenir.

Comment il se joue, dans `/sdda-review` :

- **Le jeu de départ n'est pas une page blanche.**
  `.sdda/templates/datasets/adversarial-seed.jsonl` (31 items) sert d'amorce à
  `qa-evals`, qui l'adapte au domaine.
- **Le set versionné est joué en live.** `run-adversarial-suite --executor …`
  vérifie d'abord la couverture des familles qu'imposent les entrées et les
  outils de chaque agent, puis joue chaque attaque contre la surface livrée et
  enregistre l'exécution dans `workspace/.sys/reports/runs/{n}-adversarial.jsonl`,
  que `--replay` rejuge à 0 token. Sémantique de sécurité, pas de qualité : sur
  k runs, une attaque qui passe une fois est une faille, pas une moyenne.
- **L'étage C improvise**, après une garde : `adversarial-target-check` refuse
  toute cible qu'il ne peut pas prouver locale ou isolée
  (`[ADVERSARIAL_TARGET_UNSAFE]`) — une attaque réussie contre la production est
  un incident, pas un finding.
- **Les findings deviennent des items.** `review-adversarial` n'écrit pas dans
  `datasets/` ; `promote-adversarial-findings --agent qa-evals` y ajoute chaque
  attaque réussie, dédoublonnée, sans jamais réécrire une ligne existante.

Scans déterministes complémentaires (0 token, une fois avant l'étage B) :
secrets dans prompts / traces / datasets / code (`scan-secrets`), PII dans le
vector store (`scan-pii`), écart entre outils exposés et outils exigés
(`audit-tool-scope`), outils non `read-only` sans stratégie de sûreté. Côté
produit, les guardrails de `## Active Guardrails` (détection d'injection,
rédaction PII, validation de schéma) sont du code du squelette généré, et c'est
cette suite qui les mesure. Enfin, la G7 exige les rapports de `review-safety`
et `review-orchestration` : un rapport absent n'est pas « 0 finding »
(`[SAFETY_REVIEW_REPORT_MISSING]`).

---

## 5. Les datasets

| Jeu | Rôle | Minimum | Qui écrit |
|---|---|:---:|---|
| `golden/` | ajustement — on itère contre lui | 50 | `qa-evals` |
| `holdout/` | **verdict** — on n'itère jamais contre lui | 30 | `qa-evals` |
| `calibration/` | labels humains pour les juges | 50 | humain + `qa-evals` |
| `adversarial/` | sécurité | 25 | `qa-evals` (amorce, puis findings promus) |

**Disjonction vérifiée par hash** (`HoldoutDisjointCheck: strict`,
`[HOLDOUT_NOT_DISJOINT]`). Optimiser les prompts contre le jeu qui rend le
verdict est la manière agentic de se mentir à soi-même, et elle est silencieuse.

**Aucun `dev-*` n'a le droit d'écrire dans `datasets/`.** L'agent qui écrit le
code ne peut pas modifier le jeu qui le juge. Sans cette barrière, l'auto-
confirmation n'est pas un risque : c'est le résultat par défaut.

---

## 6. Ce qu'on mesure quand « tout est vert »

Un tableau de bord qui n'affiche que des scores cache l'essentiel. Le rapport
final porte aussi :

- **coût par CAP** — où part l'argent, et quelle CAP coûte plus qu'elle ne vaut
  (`cost-report`, `[CAP_COST_EXCEEDS_VALUE]`) ;
- **distribution des trajectoires** — la queue à 3 % qui fait 12 hops
  (`trajectory-report`, `cost-report`) ;
- **taux d'abstention** — un système qui ne dit jamais « je ne sais pas » sur un
  domaine ouvert ne fait pas bien son travail, il ment mieux ;
- **top des outils en échec** — souvent la vraie cause d'un score médiocre
  (appels, échecs, retries par outil dans `cost-report`) ;
- **dérive dans le temps** — même prompt, même jeu, scores qui glissent : le
  modèle du fournisseur a bougé sous vos pieds. C'est la raison pour laquelle
  `model_id` fait partie du tuple d'épinglage (P10). La comparaison run à run
  existe (baselines, `check-regression`) ; la courbe dans le temps attend la
  console de validation 🟡.
