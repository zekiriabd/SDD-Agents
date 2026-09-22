# Patterns d'orchestration — catalogue et matrice de sélection

Consommé par `architect-topology`. SSoT machine :
`.sdda/registry/patterns.registry.json`.

> **Le défaut est `single-agent`.** Tout autre choix doit être justifié par écrit
> dans `topology/{n}-topology.md` (P7). Ce document donne les critères qui rendent
> cette justification vérifiable plutôt qu'esthétique.

---

## 1. Le catalogue

### `single-agent` — un agent, des outils
Un modèle, une boucle, N outils. L'agent décide quoi appeler.

- **Quand** : ≤ ~8 outils, un seul domaine, une seule posture de sortie.
- **Coût** : 1 × (tours). Le moins cher, le plus rapide, le plus débogable.
- **Dégrade quand** : au-delà de ~10-15 outils, la sélection devient bruitée ;
  au-delà de ~2 personas contradictoires dans un prompt, les instructions se
  neutralisent.
- **Signal qu'il faut escalader** : la matrice de confusion des appels d'outils
  montre des erreurs systématiques entre outils voisins, **mesurée**, pas
  soupçonnée.

### `router` — classifier puis déléguer
Un nœud de classification bon marché route vers un spécialiste.

- **Quand** : intentions disjointes traitées très différemment.
- **Coût** : 1 classification `fast` + 1 spécialiste. Souvent **moins cher** que
  single-agent, parce que chaque spécialiste a un prompt court.
- **Mode d'échec dominant** : le **misroute silencieux et irrécupérable**. Une
  fois parti chez le mauvais spécialiste, rien ne le rattrape.
- **Obligations** : seuil de confiance + chemin de repli (`fallback` ou
  `clarify`) ; golden set de routing avec **accuracy par classe**, pas globale —
  une accuracy globale de 0.95 peut cacher 0.40 sur la classe critique.

### `sequential` — pipeline d'étapes ordonnées *(SeqAgent)*
Étapes fixes et connues, chacune raffine la précédente.

- **Quand** : l'ordre est une propriété du métier (extraire → normaliser →
  valider → rédiger).
- **Coût** : somme des étapes. Prévisible, linéaire.
- **Mode d'échec dominant** : **l'erreur compose**. Une extraction à 0.9 suivie
  d'une normalisation à 0.9 donne 0.81, et personne ne l'a vu passer.
- **Obligations** : validation par étape (schéma ou grader), et une eval par
  étape en plus de l'eval bout-en-bout. Sinon on débogue un résultat final sans
  savoir quelle étape l'a abîmé.

### `parallel` — fan-out / gather
N sous-tâches indépendantes lancées ensemble, puis fusionnées.

- **Quand** : la latence contraint et les sous-tâches sont vraiment indépendantes.
- **Coût** : N × tokens, 1 × latence. On achète du temps avec de l'argent.
- **Mode d'échec dominant** : **l'étape de fusion**, systématiquement sous-estimée.
  Réconcilier des sorties contradictoires est le vrai travail.
- **Obligations** : la stratégie de fusion est spécifiée (vote, priorité,
  synthèse LLM, échec si divergence) et évaluée **séparément**.

### `supervisor` — hiérarchique, délégation dynamique
Un superviseur décompose, délègue à des spécialistes, recueille, décide de
continuer ou de conclure.

- **Quand** : tâches hétérogènes, décomposition non connue d'avance.
- **Coût** : overhead du superviseur **à chaque hop**, et le contexte grossit à
  chaque retour. C'est le pattern le plus cher et celui qui dérape le plus vite.
- **Mode d'échec dominant** : le **ping-pong** superviseur ↔ spécialiste, qui
  consomme le budget sans progresser.
- **Obligations** : `maxHops` **dur** ; les handoffs portent un contrat explicite
  (quel état passe, quelle condition de retour) ; une eval de trajectoire vérifie
  la distribution du nombre de hops, pas seulement la réponse finale.

### `graph` — machine à états explicite
Nœuds, arêtes conditionnelles, cycles bornés, état persisté, reprise possible,
interruption humaine.

- **Quand** : il faut des cycles contrôlés, du human-in-the-loop, de la
  reprise après interruption, ou de la durabilité.
- **Coût** : explicite et calculable — c'est son principal avantage.
- **Mode d'échec dominant** : la **complexité de conception**. Un graphe à 15
  nœuds qu'on n'a jamais dessiné est ingérable.
- **Obligations** : le graphe est dessiné (`{n}-topology.mmd`) et validé
  déterministiquement sur l'IR — atteignabilité, terminaison, cycles bornés.

### `plan-execute` — plan explicite puis exécution
Un appel `deep` produit un plan ; des appels `fast` l'exécutent étape par étape.

- **Quand** : horizon long, nombreuses étapes, le plan a de la valeur pour
  l'humain (auditabilité).
- **Coût** : 1 cher + N bon marché. Souvent le meilleur rapport qualité/prix sur
  les tâches longues.
- **Mode d'échec dominant** : le **plan périmé** — la réalité diverge et
  l'exécuteur suit quand même.
- **Obligations** : condition de **replanification** spécifiée (échec d'étape,
  découverte contredisant une prémisse), et plafond de replanifications.

### `reflection` — rédacteur / critique
Un agent produit, un critique note selon une grille, on itère.

- **Quand** : la qualité prime, et il existe une **grille explicite** — pas un
  « c'est mieux ».
- **Coût** : × 2 à × 4.
- **Mode d'échec dominant** : le **polissage infini**, ou pire, la dérive où
  chaque itération dégrade.
- **Obligations** : `max_reflections` **et** un critère d'arrêt sur *delta*
  d'amélioration (on s'arrête si le gain < seuil). Le critique ne peut pas être
  le même agent que le rédacteur (P7, raison 4).

### `blackboard` — état partagé
Plusieurs agents contribuent à un artefact commun.

- **Quand** : élaboration collaborative d'un document ou d'un plan.
- **Coût** : croît avec la taille de l'état — chaque agent relit tout.
- **Mode d'échec dominant** : **conflits d'écriture** et écrasements.
- **Obligations** : une matrice d'ownership sur les sections de l'état — le
  mécanisme est exactement celui de la matrice d'ownership de SDD_Pro, appliqué
  au runtime.

### `network` — handoff libre entre pairs
Tout agent peut passer la main à tout agent.

- **Statut : refusé par défaut.** Coût non borné, trajectoires non prédictibles,
  évaluation quasi impossible. Un ADR est exigé pour l'activer, et il doit
  démontrer qu'aucun `graph` ne convient.

---

## 2. Matrice de sélection

| Critère | single | router | sequential | parallel | supervisor | graph | plan-exec | reflection |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Intentions disjointes | ○ | **●** | ○ | ○ | ◐ | ◐ | ○ | ○ |
| Ordre imposé par le métier | ○ | ○ | **●** | ○ | ○ | ● | ◐ | ○ |
| Décomposition inconnue d'avance | ○ | ○ | ○ | ○ | **●** | ◐ | ● | ○ |
| Latence contrainte | ● | ● | ◐ | **●** | ○ | ◐ | ◐ | ○ |
| Budget serré | **●** | ● | ◐ | ○ | ○ | ◐ | ● | ○ |
| Cycles nécessaires | ○ | ○ | ○ | ○ | ◐ | **●** | ◐ | ● |
| Human-in-the-loop | ○ | ○ | ◐ | ○ | ◐ | **●** | ● | ○ |
| Reprise après interruption | ○ | ○ | ◐ | ○ | ○ | **●** | ● | ○ |
| Qualité > coût, grille explicite | ○ | ○ | ○ | ○ | ◐ | ● | ◐ | **●** |
| Auditabilité du raisonnement | ◐ | ● | ● | ◐ | ◐ | ● | **●** | ● |
| Facilité d'évaluation | **●** | ● | ● | ◐ | ○ | ◐ | ● | ◐ |

● adapté · ◐ possible · ○ inadapté

---

## 3. Composition

Un pattern peut en imbriquer un autre : un nœud d'un `graph` peut être un
`reflection`, une branche d'un `router` peut être un `sequential`. Le
**pattern racine** est déclaré dans `STACK.md` ; les imbrications vivent dans
`topology/{n}-topology.md` et apparaissent dans l'IR.

**Limite dure** : profondeur d'imbrication ≤ 2. Au-delà, plus personne ne peut
raisonner sur le coût ni sur les trajectoires — et une architecture sur laquelle
on ne peut pas raisonner ne peut pas être évaluée.

---

## 4. Anti-patterns refusés par la TOPOLOGY GATE

| Anti-pattern | Pourquoi il apparaît | Classe |
|---|---|---|
| **Un agent par outil** | Confusion entre « séparation des responsabilités » et topologie. Un outil est un outil | `[TOPOLOGY_UNJUSTIFIED]` |
| **Superviseur à 1 seul spécialiste** | Reste d'une conception abandonnée. Un hop payé pour rien | `[TOPOLOGY_REDUNDANT_HOP]` |
| **Boucle sans plafond de hops** | « L'agent s'arrêtera quand il aura fini » | `[UNBOUNDED_LOOP]` |
| **Routeur sans repli** | Le cas « aucune classe ne correspond » n'a pas été pensé | `[ROUTER_NO_FALLBACK]` |
| **Critique = rédacteur** | Économie apparente. Le modèle valide sa propre sortie | `[REFLECTION_SELF_GRADING]` |
| **Handoff sans contrat d'état** | On suppose que « le contexte suit » | `[HANDOFF_UNCONTRACTED]` |
| **Agent sans CAP** | Ajouté « pour la structure » | `[AGENT_SERVES_NO_CAP]` |
| **Agent avec des outils qu'aucune de ses CAPs n'exige** | Copie d'une liste d'outils globale | `[TOOL_SCOPE_EXCESS]` |
