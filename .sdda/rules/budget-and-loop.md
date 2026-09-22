# Règle — Budget et bornes

> Deux budgets vivent dans ce framework et **ne doivent jamais être confondus** :
> ce que coûte la **construction**, et ce que coûte le **produit construit**.
> Les confondre rend le second incalculable — c'est l'erreur structurante des
> frameworks concurrents.

---

## 1. Les deux budgets

| | Budget de construction | Budget d'exécution |
|---|---|---|
| Qui paie | le pipeline SDD_Agents (22 Developer Agents) | l'application générée, à chaque appel utilisateur |
| Déclaré dans | `## Project Config` → `MaxCostPerRun` | `## Execution Budget` de la MISSION |
| Modèles | `## Build Models` | `## Runtime Models` |
| Plafonné par | hook `preflight_cost_cap` | bornes d'agent + `costPerRunHardCapUsd` |
| Nature | coût de projet, ponctuel | **exigence fonctionnelle**, récurrente |

Le second est le seul qui décide si le produit est viable. Une topologie élégante
à $0.40 l'appel pour un produit qui en facture $0.05 n'est pas une architecture :
c'est une erreur qu'on a mis six semaines à découvrir.

---

## 2. Budget d'exécution : estimé, puis mesuré

| Moment | Qui | Quoi |
|---|---|---|
| **G2** — avant toute génération | `estimate_budget.py` sur le graphe de l'IR | chemin nominal **et pire cas** |
| **G6** — après orchestration | `eval_runner.py` sur le golden set | coût et latence **réels**, p50 / p95 / max |

Dépassement à l'estimation → `[BUDGET_EXCEEDED_ESTIMATE]`, la topologie ne part
pas en génération.
Dépassement à la mesure → `[BUDGET_EXCEEDED_MEASURED]`, verdict **rouge** même
si la qualité est atteinte.

> **Le pire cas n'est pas une curiosité.** Sur un volume réel, il arrive tous les
> jours. Une topologie dont le pire cas triple le budget est une topologie dont
> la facture mensuelle sera imprévisible — et c'est toujours le pire cas qui
> arrive le jour de la démonstration.

---

## 3. Les cinq bornes obligatoires

Tout agent généré les porte, **toutes**, avec un comportement déclaré à
l'atteinte. Invariant `no-unbounded-loop` (P12).

| Borne | Protège de | Défaut |
|---|---|---|
| `max_iterations` | la boucle de raisonnement qui ne converge pas | 12 |
| `max_tool_calls` | l'agent qui appelle un outil en rafale | 25 |
| `max_delegation_depth` | la récursion d'agents | 3 |
| `timeout_s` | l'attente réseau qui bloque tout | 120 |
| `budget_usd` | tout le reste, en dernier recours | par agent |

Et au niveau de l'orchestration : `maxHops`.

**Comportement à l'atteinte** — `OnBoundExceeded` :

- `fail-explicit` — échec visible, avec l'état partiel. **Défaut.**
- `degrade` — réponse partielle **annoncée comme telle**.
- `escalate-human` — sortie de boucle vers un humain.

> Une borne sans comportement déclaré produit un silence en production. Le
> silence est le pire mode d'échec d'un système agentic : il ressemble à un
> succès. Une réponse tronquée sans mention est pire qu'une erreur.

---

## 4. Ce qui fait vraiment exploser un budget

Par ordre de fréquence observée, pas d'intuition :

1. **Le ping-pong superviseur ↔ spécialiste.** Chaque aller-retour paie
   l'overhead du superviseur *et* un contexte qui grossit. C'est le mode d'échec
   dominant du pattern `supervisor`, et `maxHops` est sa seule vraie défense.
2. **Le contexte qui s'accumule.** Une conversation de 20 tours sans politique de
   résumé facture le début de la conversation à chaque tour.
3. **Le retrieval agentique non plafonné.** L'agent décide de chercher encore,
   et encore. D'où `maxRetrievalCalls`, obligatoire pour ce pattern.
4. **La boucle de réflexion sans critère d'arrêt sur delta.** Le critique trouve
   toujours quelque chose à redire. `max_reflections` ne suffit pas : il faut
   s'arrêter quand le gain devient marginal.
5. **Le retry sur un outil lent.** Trois tentatives à 30 s font 90 s et trois
   fois le coût, pour un service qui était simplement en panne.
6. **Le tier trop haut par défaut.** Un classifieur en `deep` coûte cinq fois un
   classifieur en `fast`, pour une tâche qu'un `fast` fait aussi bien — et c'est
   mesurable, donc ce n'est pas un débat.

---

## 5. Où l'on gagne, sans perdre en qualité

- **Descendre un tier sur ce qui est vérifiable par script.** Classification,
  extraction structurée, reformulation bornée : un `fast` validé par un schéma
  vaut un `deep` non validé.
- **Déplacer un jugement vers un outil déterministe.** Le gain est d'un ordre de
  grandeur, pas de quelques pourcents. C'est le premier réflexe du
  `architect-topology`.
- **Retirer un agent.** Chaque hop supprimé retire un appel de modèle *et* une
  copie de contexte.
- **Le cache de prompt**, quand le provider le supporte : la partie stable du
  prompt (rôle, outils, règles) se cache ; seule la partie volatile se paie.
- **Résumer au lieu de tronquer.** Tronquer perd de l'information et n'économise
  qu'une fois ; résumer économise à chaque tour suivant.

---

## 6. Budget de construction

Hérité de SDD_Pro, mécanisme identique :

| Clé | Défaut | Effet |
|---|---|---|
| `MaxCostPerRun` | $50 | hard stop du pipeline |
| `BuildLoopMaxCostUsd` | $15 | plafond d'une boucle de correction |
| `BuildLoopMaxIter` | 3 | nombre de tentatives de correction |
| `MaxParallel` | 3 | agents simultanés |

La boucle de correction (`build_loop`) s'arrête à la **première** des trois
conditions : succès, itérations épuisées, budget épuisé. Elle ne relance jamais à
l'identique : sans changement de cause, une nouvelle tentative achète le même
échec.

---

## 7. La discipline des bypasses

Chaque gate a un bypass explicite, borné, et tracé dans
`workspace/.sys/.audit/bypasses.jsonl` avec horodatage, opérateur et raison.

**Le cumul est bloquant** : au-delà de `MaxBypassesPerRun` (1), le run s'arrête
(`preflight_force_cumul`, hérité de SDD_Pro).

La raison est empirique : un bypass isolé est un arbitrage assumé ; trois
bypasses cumulés sont une personne qui veut voir du vert. C'est exactement le
moment où un garde-fou doit exister.
