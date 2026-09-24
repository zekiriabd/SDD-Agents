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

### L0 — Statique, déterministe, 0 token
S'exécute à chaque commit, en quelques secondes.

- JSON Schema de chaque définition d'outil ;
- **lint de prompt** : pas de secret, pas d'instruction contradictoire, taille
  sous plafond, variables de template toutes résolvables, pas de référence à un
  outil inexistant ;
- **validation de l'IR** : atteignabilité, terminaison, cycles bornés,
  références closes, moindre privilège (cf. `AGENTIC-IR.md §4`) ;
- **estimation de budget** sur le graphe ;
- disjonction `golden ∩ holdout = ∅` par hash ;
- fraîcheur des baselines (tuple de hashes de P10) ;
- audit d'ownership, scan de secrets, CVE des dépendances.

### L1 — Unit
Fonctions pures : parsers, chunkers, mappers, réducteurs d'état, calculs de
coût, politiques de retry. **LLM mocké.** Vitesse et déterminisme complets.

### L2 — Contrat d'outil
Chaque outil contre son schéma : happy path, **chaque erreur déclarée**,
timeout, échec d'authentification, idempotence (appeler deux fois avec la même
clé produit-il un seul effet ?), respect du rate limit.

Un test de **connectivité live** existe séparément, marqué `network`, et
constitue la seconde moitié de la TOOL GATE. Un outil dont le contrat est vert
mais dont le service est inaccessible passerait sinon la gate.

### L3 — Retrieval evals *(RETRIEVAL GATE)*
Golden set de requêtes avec vérité terrain. **Aucun agent impliqué.**
`recall@k`, `nDCG@k`, `context_precision`, `groundedness`,
`citation_resolve_rate` (cf. `RAG-PATTERNS.md §5`).

### L4 — Agent evals isolés *(AGENT GATE)*
Un agent seul, **outils mockés et retrieval figé**, contre les AC de ses CAPs.

L'isolement est le point : un agent évalué avec de vrais outils mesure la
somme de l'agent et des outils. Quand le score baisse, on ne sait pas lequel a
bougé. Avec des mocks, la variation est attribuable.

Graders : `exact`, `regex`, `schema`, `numeric-tolerance`,
`semantic-similarity`, `llm-judge` (calibré), `trajectory`.

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

### L6 — Intégration
Vrais outils, vrai index, vraie base de test. Connexions API, connexions base,
migrations, ingestion bout-en-bout. Sans LLM quand c'est possible.

### L7 — End-to-end mission evals *(ORCH GATE)*
Système complet sur le golden set de la mission. Mesure conjointe de la qualité,
du **coût** et de la **latence**. Un run qui atteint le score en dépassant
`CostPerRunHardCapUsd` est **rouge**, pas jaune.

### L8 — Adversarial et sécurité *(SAFETY GATE)*
Détaillé au §4.

### L9 — Régression
Comparaison à la baseline épinglée. Une baisse au-delà de
`RegressionTolerancePct` bloque. La baseline se déplace **explicitement**, par
une action tracée, jamais par écrasement automatique — sinon la dérive lente
devient invisible.

---

## 2. Le protocole de non-déterminisme

**Règle** : aucune eval médiée par un LLM n'est rapportée depuis un seul run.
Invariant `non-determinism-k-runs`.

Le rapport porte : `score_mean`, `score_stddev`, `pass_rate` (proportion des k
runs au-dessus du seuil), `min`, `max`.

```
CAP 1-2 ExplainInvoiceLine — groundedness
  seuil 0.85   k=3
  runs  0.91 · 0.86 · 0.77
  mean 0.847  stddev 0.071  pass_rate 0.67
  VERDICT: 🟡 JAUNE — moyenne sous le seuil, un run échoue.
           Ne pas livrer sur la foi du premier run.
```

Le verdict est à trois couleurs :

| | Condition |
|---|---|
| 🟢 **VERT** | `mean ≥ seuil` **et** `pass_rate = 1.0` **et** `stddev ≤ EvalVarianceWarnPct` |
| 🟡 **JAUNE** | seuil franchi en moyenne mais variance élevée ou `pass_rate < 1.0` |
| 🔴 **ROUGE** | `mean < seuil`, ou une classe critique échoue, ou un budget est dépassé |

Un jaune n'est pas une indécision : c'est l'information qu'un déploiement est un
pari sur le prochain tirage.

---

## 3. Calibration des juges LLM

Un grader LLM non validé ne rend pas de verdict bloquant (P9, invariant
`llm-judge-calibrated`).

**Procédure** :
1. `qa-evals` produit un set de calibration : ≥ 50 items du domaine réel,
   labellisés par un humain selon la grille exacte du juge.
2. Le juge note les mêmes items.
3. On calcule l'accord (kappa de Cohen pour du binaire/ordinal, corrélation pour
   du continu). Seuil par défaut : **κ ≥ 0.6**.
4. Le rapport est versionné dans `workspace/pipeline/calibration/{grader}.json` et
   référencé depuis l'IR.
5. En dessous du seuil : la grille est retravaillée, ou le juge devient
   `advisory` — il produit un score informatif mais ne bloque plus.

**Règles complémentaires** : le juge n'est pas le modèle évalué quand c'est
évitable ; la grille est une liste de critères vérifiables, pas « note de 1 à 10
la qualité » ; le juge voit la référence quand elle existe.

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

Scans déterministes complémentaires (0 token) : secrets dans prompts / traces /
datasets, PII dans le vector store, écart entre outils exposés et outils exigés,
outils non `read-only` sans stratégie de sûreté.

---

## 5. Les datasets

| Jeu | Rôle | Minimum | Qui écrit |
|---|---|:---:|---|
| `golden/` | ajustement — on itère contre lui | 50 | `qa-evals` |
| `holdout/` | **verdict** — on n'itère jamais contre lui | 30 | `qa-evals` |
| `calibration/` | labels humains pour les juges | 50 | humain + `qa-evals` |
| `adversarial/` | sécurité | 25 | `qa-evals` + findings |

**Disjonction vérifiée par hash** (`HoldoutDisjointCheck: strict`). Optimiser les
prompts contre le jeu qui rend le verdict est la manière agentic de se mentir à
soi-même, et elle est silencieuse.

**Aucun `dev-*` n'a le droit d'écrire dans `datasets/`.** L'agent qui écrit le
code ne peut pas modifier le jeu qui le juge. Sans cette barrière, l'auto-
confirmation n'est pas un risque : c'est le résultat par défaut.

---

## 6. Ce qu'on mesure quand « tout est vert »

Un tableau de bord qui n'affiche que des scores cache l'essentiel. Le rapport
final porte aussi :

- **coût par CAP** — où part l'argent, et quelle CAP coûte plus qu'elle ne vaut ;
- **distribution des trajectoires** — la queue à 3 % qui fait 12 hops ;
- **taux d'abstention** — un système qui ne dit jamais « je ne sais pas » sur un
  domaine ouvert ne fait pas bien son travail, il ment mieux ;
- **top des outils en échec** — souvent la vraie cause d'un score médiocre ;
- **dérive dans le temps** — même prompt, même jeu, scores qui glissent : le
  modèle du fournisseur a bougé sous vos pieds. C'est la raison pour laquelle
  `model_id` fait partie du tuple d'épinglage (P10).
