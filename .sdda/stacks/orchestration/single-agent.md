# Orchestration: single-agent

Stack ID: orchestration-single-agent
Status: Stable
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *

> **C'est le défaut du framework.** Tout autre pattern doit être défendu par
> écrit dans `topology/{n}-topology.md` (P7).

---

## 1. Rôle et périmètre

Un modèle, une boucle de raisonnement, N outils. L'agent décide seul quoi
appeler, dans quel ordre, et quand s'arrêter.

C'est le pattern le moins cher, le plus rapide, le plus facile à évaluer et le
plus facile à déboguer. **Il couvre plus de cas réels que ne le suggère sa
réputation** : une grande partie des systèmes construits en `supervisor`
fonctionneraient mieux ici.

---

## 2. Quand l'employer

- ≤ ~8 outils ;
- un seul domaine métier ;
- une seule posture de sortie (pas de personas contradictoires) ;
- pas de besoin de reprise après interruption ni d'interruption humaine.

## 3. Quand il dégrade

| Symptôme | Seuil observé | Ce qu'il faut faire |
|---|---|---|
| Sélection d'outil bruitée | au-delà de ~10-15 outils | regrouper des outils, ou passer à `router` |
| Instructions qui se neutralisent | ≥ 2 personas contradictoires dans le prompt | séparer en agents à fonction objectif distincte |
| Contexte saturé | mesuré, pas supposé | résumer, ou découper |
| Outil destructif exposé à du texte non maîtrisé | dès le premier | **isoler dans un second agent** — raison n°1 de P7 |

> **Le signal d'escalade doit être mesuré.** `tool_selection_accuracy` et la
> matrice de confusion des appels d'outils disent s'il y a un vrai problème.
> Escalader sur une impression produit une architecture plus chère sans gain.

---

## 4. Bornes

| Borne | Valeur de départ |
|---|---:|
| `max_iterations` | 8 |
| `max_tool_calls` | 15 |
| `max_delegation_depth` | 0 (il ne délègue pas) |
| `timeout_s` | 60 |

`maxHops` de l'orchestration = 1 : il n'y a pas de hop.

---

## 5. Mapping vers l'IR

```jsonc
"orchestration": {
  "rootPattern": "single-agent",
  "entryNode": "agent",
  "terminalNodes": ["agent"],
  "maxHops": 1,
  "nodes": [{ "id": "agent", "kind": "agent", "ref": "1-assistant" }],
  "edges": []
}
```

## 6. Évaluation

Le pattern le plus simple à évaluer, et c'est un avantage réel :

- **L4** — l'agent seul contre ses CAP ACs, outils mockés. C'est déjà presque
  l'évaluation de bout en bout.
- **L5** — `tool_selection_accuracy` sur un golden set d'appels attendus. **La
  métrique à surveiller** : c'est elle qui dira quand le pattern sature.
- **L7** — coût et latence, directement interprétables faute de hops.

## 7. Pièges

- **Empiler les outils « au cas où ».** Chaque outil ajouté dégrade la sélection
  des autres. Moins l'agent a d'outils, mieux il choisit. C'est aussi
  l'invariant du moindre privilège (`[TOOL_SCOPE_EXCESS]`).
- **Le prompt qui grossit à chaque cas limite découvert.** Au-delà de
  `PromptMaxTokens`, les instructions du bas cessent d'être suivies. Un cas
  limite récurrent appartient souvent à un outil, pas à une phrase de plus.
- **Confondre « un agent » et « simpliste ».** Un agent avec six outils bien
  décrits et une politique de refus claire est un système sérieux.
