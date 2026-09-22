# Orchestration: sequential

Stack ID: orchestration-sequential
Status: Stable
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *

> Aussi appelé *pipeline* ou *SeqAgent*.

---

## 1. Rôle et périmètre

Des étapes **fixes et connues**, exécutées dans un ordre imposé, chacune
raffinant la sortie de la précédente. Extraire → normaliser → valider → rédiger.

Coût prévisible et linéaire : la somme des étapes. C'est son principal mérite.

---

## 2. Quand l'employer

- l'ordre est une **propriété du métier**, pas une commodité d'implémentation ;
- chaque étape a une sortie structurée vérifiable ;
- les étapes ont des besoins de tier différents (extraction `fast`, rédaction
  `balanced`) — l'économie est alors substantielle.

**Quand ne pas l'employer** : si l'ordre n'est pas imposé par le métier, un
`single-agent` fera la même chose avec moins de latence et sans rupture de
contexte entre étapes.

---

## 3. Le mode d'échec dominant : l'erreur compose

Une extraction à 0.90 suivie d'une normalisation à 0.90 donne **0.81** — et
personne ne l'a vu passer, parce que chaque étape prise isolément semblait
correcte.

Sur cinq étapes à 0.95, on tombe à 0.77. C'est le calcul que personne ne fait
avant de construire un pipeline à cinq étapes, et c'est pourquoi il figure ici.

### La conséquence architecturale

**Chaque étape porte sa propre validation** — schéma de sortie au minimum,
grader dédié quand la sortie est du langage. Et **chaque étape a sa propre
eval**, en plus de l'eval de bout en bout.

Sans eval par étape, on diagnostique un résultat final dégradé sans savoir
quelle étape l'a abîmé — et on finit par retoucher le prompt de la dernière,
qui n'y était pour rien.

---

## 4. Obligations

| Obligation | Classe si absente |
|---|---|
| Sortie structurée validée à chaque étape | `[SEQUENTIAL_STEP_UNVALIDATED]` |
| Une eval par étape (L4), pas seulement bout en bout | `[AC_NOT_EVALUABLE]` |
| Comportement défini si une étape échoue | `[SEQUENTIAL_NO_FAILURE_PATH]` |
| Budget cumulé sous le plafond | `[BUDGET_EXCEEDED_ESTIMATE]` |

Sur l'échec d'étape, trois stratégies — à déclarer, pas à improviser :
**arrêter** (le plus honnête), **réessayer une fois avec le message d'erreur**,
ou **passer en mode dégradé** avec une sortie partielle **annoncée comme telle**.

---

## 5. Bornes

`maxHops` = nombre d'étapes. Pas de cycle : un pipeline qui boucle est un
`graph`, et il doit être déclaré comme tel.

---

## 6. Mapping vers l'IR

```jsonc
"orchestration": {
  "rootPattern": "sequential",
  "entryNode": "extract",
  "terminalNodes": ["compose"],
  "maxHops": 4,
  "nodes": [
    { "id": "extract",   "kind": "agent",    "ref": "1-extractor" },
    { "id": "normalize", "kind": "function", "ref": "normalize_entities" },
    { "id": "validate",  "kind": "function", "ref": "validate_business_rules" },
    { "id": "compose",   "kind": "agent",    "ref": "1-composer" }
  ],
  "edges": [
    { "from": "extract",   "to": "normalize", "condition": "always" },
    { "from": "normalize", "to": "validate",  "condition": "always" },
    { "from": "validate",  "to": "compose",   "condition": "valid" }
  ]
}
```

> Remarquer `kind: "function"` sur deux étapes. **Toute étape qui n'exige pas de
> jugement sur du langage doit être une fonction déterministe, pas un agent.**
> C'est le premier gain d'un pipeline bien conçu, et le premier réflexe du
> `architect-topology`.

---

## 7. Pièges

- **Mettre un LLM à chaque étape par symétrie.** La normalisation et la
  validation de règles métier sont du code. Un LLM y ajoute du coût, de la
  latence et du non-déterminisme, pour un résultat inférieur.
- **Perdre le contexte d'origine.** Si l'étape 4 a besoin du texte initial, il
  doit voyager dans l'état — explicitement, dans le contrat de handoff. Le
  supposer est le meilleur moyen de découvrir en production qu'il a disparu.
- **Le pipeline qui grandit.** Chaque nouveau cas limite ajoute une étape ;
  l'erreur composée s'aggrave à chaque fois. Au-delà de 5-6 étapes, se demander
  si un `graph` avec branches conditionnelles ne serait pas plus honnête.
- **Ne mesurer que la fin.** C'est l'erreur qui rend le pipeline indébogable.
