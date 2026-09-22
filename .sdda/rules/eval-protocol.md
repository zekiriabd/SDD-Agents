# Règle — Protocole d'évaluation

> Règle **inconditionnelle** : chargée par tout agent qui produit, consomme ou
> commente un résultat d'évaluation. C'est le vocabulaire commun sans lequel
> « ça marche » ne veut rien dire.

---

## 1. Test ou eval — ne jamais confondre

| | **Test** | **Eval** |
|---|---|---|
| Porte sur | la partie déterministe | la partie médiée par un LLM |
| Verdict | pass / fail | score + variance vs seuil |
| Exécutions | 1 | **k** (3 par défaut, 5 si critique) |
| S'il est instable | c'est un bug à corriger | c'est la nature du système, à quantifier |
| Coût | ~0 | des tokens |

Confondre les deux produit soit des tests instables qu'on finit par désactiver,
soit des evals qui ne détectent rien.

---

## 2. k runs, toujours

**Aucune évaluation médiée par un LLM n'est rapportée depuis un seul run.**
Invariant `non-determinism-k-runs`.

Le rapport porte obligatoirement :

```
score_mean · score_stddev · pass_rate · min · max · runs
```

`pass_rate` = proportion des k runs au-dessus du seuil. C'est souvent
l'information la plus utile, et c'est celle qu'un booléen détruit.

`EvalSeedPolicy: vary` par défaut. Un seed fixe masque la variance : il ne sert
qu'à déboguer un cas précis, jamais à produire un verdict.

---

## 3. Le verdict à trois couleurs

| | Condition |
|---|---|
| 🟢 **VERT** | `mean >= seuil` **et** `pass_rate == 1.0` **et** `stddev <= EvalVarianceWarnPct` |
| 🟡 **JAUNE** | seuil franchi en moyenne, mais variance élevée **ou** `pass_rate < 1.0` |
| 🔴 **ROUGE** | `mean < seuil`, **ou** une classe critique échoue, **ou** un budget est dépassé |

**Le jaune n'est pas une indécision.** C'est l'information qu'un déploiement est
un pari sur le prochain tirage. Un score de 0.87 avec un écart-type de 0.01 et un
score de 0.87 avec un écart-type de 0.12 ne racontent pas la même histoire, et
les aplatir en « vert » est la façon la plus courante de livrer un agent qui
casse en production.

Format canonique d'un résultat :

```
CAP 1-2 ExplainInvoiceLine — groundedness
  seuil 0.85   k=3   grader llm-judge (calibré κ=0.71)
  runs  0.91 · 0.86 · 0.77
  mean 0.847  stddev 0.071  pass_rate 0.67
  🟡 JAUNE — moyenne sous le seuil, 1 run sur 3 échoue.
```

---

## 4. Les graders

| Grader | Déterministe | Usage |
|---|:---:|---|
| `exact` | ✅ | sortie canonique attendue |
| `regex` | ✅ | forme, présence d'un marqueur |
| `schema` | ✅ | conformité JSON Schema — le moins cher et le plus efficace |
| `numeric-tolerance` | ✅ | valeur numérique à ±ε |
| `trajectory` | ✅ | séquence d'appels sur la trace |
| `cost` / `latency` | ✅ | mesures du run |
| `semantic-similarity` | ◐ | embeddings — stable, mais mesure la ressemblance, pas la vérité |
| `llm-judge` | ❌ | jugement ouvert — **exige une calibration** |

**Préfère toujours le grader le plus déterministe qui répond à la question.** Un
`schema` qui vérifie la structure plus un `exact` sur le champ décisif vaut mieux
qu'un juge LLM sur la réponse entière : moins cher, reproductible, et il ne
flatte personne.

---

## 5. Calibration des juges LLM

Un grader LLM non calibré **ne rend pas de verdict bloquant**. Invariant
`llm-judge-calibrated`.

**Procédure**

1. Constituer ≥ `JudgeCalibrationMinItems` (50) items du domaine **réel**,
   labellisés par un humain selon la grille exacte du juge.
2. Faire noter les mêmes items par le juge.
3. Calculer l'accord : **kappa de Cohen** (binaire/ordinal) ou corrélation
   (continu). Seuil : `JudgeCalibrationMinKappa` (0.6).
4. Versionner le rapport dans `workspace/evals/calibration/{grader}.json`.
5. Sous le seuil : retravailler la grille, ou basculer le juge en
   **`advisory: true`** — il produit un score informatif et ne bloque plus.

**Règles complémentaires**

- Le juge **diffère** du modèle évalué quand c'est possible
  (`JudgeMustDifferFromEvaluated`). Un modèle qui se note lui-même mesure sa
  complaisance.
- La grille est une **liste de critères vérifiables**, jamais « note de 1 à 10 la
  qualité ». Un critère qu'un humain ne saurait pas appliquer de façon
  reproductible ne produira pas un juge reproductible.
- Le juge voit la **référence** quand elle existe. Juger sans référence, c'est
  juger la plausibilité.
- Les labels de calibration sont **humains**. Les faire produire par un LLM
  transforme la calibration en accord de deux modèles entre eux
  (`[JUDGE_CALIBRATION_SYNTHETIC]`).

---

## 6. Épinglage — un résultat sait ce qu'il a évalué

Tout résultat est indexé par :

```
(prompt_hash, model_id, retrieval_index_hash, tool_schema_hash, dataset_hash)
```

Si l'un bouge, le résultat est **périmé** — pas « probablement encore valable ».
Invariant `eval-baseline-hash-pinned` (P10).

C'est le seul garde-fou contre la dérive silencieuse : l'édition d'un prompt est
un changement de comportement qu'aucun compilateur n'attrape. `model_id` en fait
partie parce qu'un fournisseur peut déplacer un modèle sous vos pieds — mêmes
prompt et dataset, scores différents.

---

## 7. Golden, holdout, calibration, adversarial

| Jeu | Rôle | On itère dessus ? |
|---|---|:---:|
| `golden/` | ajustement des prompts et de la configuration | **oui** |
| `holdout/` | verdict d'acceptation (G8) | **jamais** |
| `calibration/` | validation des juges | non |
| `adversarial/` | sécurité (G7) | il **grandit** |

**Disjonction golden ∩ holdout vérifiée par hash** (`HoldoutDisjointCheck:
strict`). Optimiser contre le jeu qui rend le verdict est la manière agentic de
se mentir, et elle est silencieuse : les scores montent, la production ne suit
pas.

Le jeu adversarial est le seul qui grandit par conception : **toute attaque
réussie devient un item permanent**. C'est le mécanisme qui empêche une faille
de revenir.

---

## 8. Régression

Comparaison à la baseline épinglée. Une baisse au-delà de
`RegressionTolerancePct` (3 %) bloque.

La baseline se déplace **explicitement** (`BaselinePromotionPolicy: explicit`),
par une action tracée. En mode `auto`, chaque run repart de son propre résultat
et le système descend sans que rien ne l'annonce.

---

## 9. Ce qu'on rapporte en plus des scores

Un tableau de bord qui n'affiche que des scores cache l'essentiel :

- **coût par CAP** — quelle capability coûte plus cher qu'elle ne rapporte ;
- **distribution des trajectoires** — la queue à 3 % qui fait douze hops ;
- **taux d'abstention** — un système qui ne dit jamais « je ne sais pas » sur un
  domaine ouvert ne fait pas bien son travail, il ment mieux ;
- **top des outils en échec** — souvent la vraie cause d'un score médiocre ;
- **dérive temporelle** — mêmes prompt et jeu, scores qui glissent.

---

## 10. Règle de diagnostic

Avant d'accuser un prompt, situer l'étage :

| Observation | Conclusion |
|---|---|
| `recall@k` bas | le problème est le **retrieval**. Toucher au prompt ne servira à rien. |
| `recall@k` haut + `groundedness` bas | le problème est la **génération**. |
| `groundedness` haut + `answer_relevance` bas | le modèle répond à côté, avec des sources correctes. |
| `trajectory` aberrante + réponse correcte | **faux vert** : dix fois le budget, et ça cassera au prochain changement de prompt. |
| `tool_selection_accuracy` bas | la **description** de l'outil, pas l'agent. |

C'est pour rendre ce diagnostic possible que les gates sont étagées (P5). Un
système évalué seulement de bout en bout ne dit jamais *où* il a échoué.
