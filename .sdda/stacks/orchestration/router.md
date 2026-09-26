# Orchestration: router

Stack ID: orchestration-router
Status: Stable
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *

---

## 1. Rôle et périmètre

Un nœud de classification bon marché détermine l'intention, puis délègue à un
spécialiste. Chaque spécialiste a un prompt court et un scope d'outils étroit.

**Souvent moins cher que `single-agent`**, contrairement à l'intuition : une
classification en `fast` plus un spécialiste à prompt court coûte moins qu'un
agent unique portant tous les outils et toutes les instructions de tous les cas.

---

## 2. Quand l'employer

- les intentions sont **disjointes** et traitées très différemment ;
- chaque famille a ses propres outils, et les mélanger dégrade la sélection ;
- un cas exige un tier élevé quand les autres non ;
- un scope d'outils doit être isolé (outil destructif) — raison n°1 de P7.

## 3. Le mode d'échec dominant

**Le misroute est silencieux et irrécupérable.** Une fois parti chez le mauvais
spécialiste, rien ne le rattrape : le spécialiste répond avec assurance dans son
domaine, à une question qui n'en relevait pas.

C'est la différence fondamentale avec `single-agent`, où une mauvaise sélection
d'outil se rattrape au tour suivant.

## 4. Obligations — vérifiées par la TOPOLOGY GATE

| Obligation | Classe si absente |
|---|---|
| **Chemin de repli** — le cas « aucune classe ne correspond » | `[ROUTER_NO_FALLBACK]` |
| **Seuil de confiance** — sous le seuil, clarifier plutôt que router | `[ROUTER_NO_CONFIDENCE_GATE]` |
| **Golden set de routing** avec `accuracy_per_class` | `[AC_NOT_EVALUABLE]` |

> **`accuracy_per_class`, jamais `accuracy` globale.** Une accuracy de 0.95 peut
> cacher 0.40 sur la classe critique — et c'est exactement la classe qui compte.
> Un routeur qui envoie 6 % des demandes de remboursement au support technique
> est un incident produit, pas une statistique acceptable.

Pour toute classe dont un misroute est coûteux (remboursement, suppression,
escalade), déclarer un **misroute interdit** : un AC à 0 occurrence, pas un
pourcentage.

---

## 5. Bornes

| Borne | Valeur de départ |
|---|---:|
| `maxHops` | 2 (classifier → spécialiste) |
| `max_delegation_depth` | 1 |

Un routeur qui reroute est un `supervisor` déguisé : soit on assume le
superviseur, soit `maxHops` reste à 2.

---

## 6. Mapping vers l'IR

```jsonc
"orchestration": {
  "rootPattern": "router",
  "entryNode": "classifier",
  "terminalNodes": ["billing", "technical", "clarify"],
  "maxHops": 2,
  "nodes": [
    { "id": "classifier", "kind": "router", "ref": "1-intent-classifier" },
    { "id": "billing",    "kind": "agent",  "ref": "1-billing-specialist" },
    { "id": "technical",  "kind": "agent",  "ref": "1-tech-specialist" },
    { "id": "clarify",    "kind": "agent",  "ref": "1-clarifier" }
  ],
  "edges": [
    { "from": "classifier", "to": "billing",   "condition": "intent=='billing' && confidence>=0.7" },
    { "from": "classifier", "to": "technical", "condition": "intent=='technical' && confidence>=0.7" },
    { "from": "classifier", "to": "clarify",   "condition": "confidence<0.7", "isFallback": true }
  ]
}
```

Le classifieur est `kind: "router"` et non `kind: "agent"` : `validate_ir.py`
exige alors au moins une arête `isFallback: true`.

L'arête `isFallback` est la branche **par défaut** : elle reçoit tout ce
qu'aucune route ne prend — confiance sous le seuil **et** intention hors des
classes déclarées. Sa condition est prise **telle que l'IR l'écrit**
(`fallback_condition` du routeur généré ; `confidence<{seuil}` à défaut) :
réécrire la condition dans le code, même équivalente, fait diverger le
manifeste du graphe et `diff-code-vs-ir` le refuse.

---

## 7. Le classifieur

- **Tier `fast`** dans la quasi-totalité des cas. La classification d'intention
  est la tâche la mieux servie par un petit modèle : elle est bornée, son espace
  de sortie est fini, et elle est vérifiable par un schéma.
- **Sortie structurée obligatoire** : `{intent: enum, confidence: number}`. Un
  classifieur en texte libre est un classifieur qu'on devra parser, donc réparer.
- **Pas d'outils.** Un classifieur qui appelle des outils fait le travail du
  spécialiste et détruit l'économie du pattern.

---

## 8. Évaluation

| Niveau | Quoi |
|---|---|
| **L4** | le classifieur seul, sur le golden de routing — `accuracy_per_class`, matrice de confusion |
| **L4** | chaque spécialiste seul, sur ses CAP ACs, en supposant le routage correct |
| **L5** | trajectoire : la bonne branche a-t-elle été prise, avec quelle confiance |
| **L7** | bout en bout — le misroute apparaît ici, mais il est trop tard pour le diagnostiquer |

Évaluer le classifieur **séparément** est ce qui permet de distinguer « le
spécialiste répond mal » de « le spécialiste n'aurait jamais dû être appelé ».

---

## 9. Pièges

- **Classes qui se chevauchent.** Si deux humains classent différemment le même
  exemple, le modèle n'y arrivera pas non plus. Le problème est la taxonomie,
  pas le modèle — retravailler les classes avant de retravailler le prompt.
- **Trop de classes.** Au-delà de 6-8, la confusion croît vite. Regrouper, puis
  sous-router à l'intérieur d'une branche si nécessaire (imbrication de niveau 2).
- **Le repli qui ne fait rien.** Un `fallback` qui renvoie « je n'ai pas compris »
  sans rien proposer transforme une incertitude en échec pour l'utilisateur.
- **Router sur le premier message d'une conversation multi-tours.** L'intention
  se précise au deuxième tour. Prévoir la re-classification, ou assumer la
  contrainte et l'écrire.
