# Memory: buffer (fenêtre glissante)

Stack ID: memory-buffer
Status: Stable
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *

---

## 1. Rôle

La mémoire de travail la plus simple : les N derniers tours de conversation sont
renvoyés au modèle à chaque appel. Rien n'est stocké au-delà de la session.

C'est le défaut du MVP, et c'est le bon défaut : **la mémoire à long terme est
une complexité qu'il faut mériter.**

---

## 2. Quand l'employer

- conversations courtes à moyennes (≤ ~20 tours) ;
- aucune continuité exigée entre deux sessions ;
- pas de personnalisation persistante.

## 3. Quand il ne suffit plus

| Signal | Piste |
|---|---|
| Conversations longues, contexte saturé | `summary` (résumé glissant) |
| L'utilisateur s'attend à ce qu'on se souvienne d'hier | `store` ou `vector` |
| Besoin de rappeler un fait précis énoncé 40 tours plus tôt | `vector` |
| Entités métier à suivre (client, dossier) | `entity` |

---

## 4. Configuration

```yaml
ShortTermPolicy: sliding-window
ShortTermMaxTurns: 12
SummarizeTriggerTokens: 24000
LongTermEnabled: false
CrossAgentSharedState: scoped
```

`ShortTermMaxTurns` est un plafond de **tours**, mais le vrai plafond est en
**tokens** : douze tours contenant chacun un document récupéré font exploser le
contexte. `SummarizeTriggerTokens` est le garde-fou réel.

---

## 5. Ce que coûte vraiment une fenêtre

**Le coût d'une conversation est quadratique**, pas linéaire : au tour N, on
refacture les N-1 tours précédents. Une conversation de 20 tours à 500 tokens
par tour ne coûte pas 10 000 tokens — elle en coûte environ 100 000.

C'est la deuxième cause d'explosion de budget la plus fréquente, et elle est
invisible tant qu'on teste sur des conversations de trois tours.

**Ce qui l'atténue** : le cache de prompt du provider (la partie stable est
facturée à tarif réduit), un `ShortTermMaxTurns` serré, et le passage à
`summary` dès que les conversations s'allongent.

---

## 6. Troncature vs résumé

Quand la fenêtre déborde :

- **Tronquer** — jeter les plus anciens tours. Gratuit, mais l'information est
  perdue et le modèle fera référence à quelque chose qu'il ne voit plus.
- **Résumer** — un appel `fast` condense les tours sortants. Coûte une fois,
  économise à chaque tour suivant. **Presque toujours le bon choix** dès que la
  conversation dépasse une dizaine de tours.

Dans les deux cas, **le dire au modèle** : « les tours antérieurs ont été résumés
ci-dessous ». Un modèle qui ignore qu'il a perdu du contexte comble les trous.

---

## 7. Ce qui ne doit jamais entrer dans la fenêtre

- un secret, une clé, un jeton — même transitoirement ;
- le contenu brut d'un document volumineux : ce qui entre dans l'historique est
  refacturé à chaque tour. Stocker une **référence**, pas le document ;
- une sortie d'outil massive non tronquée (d'où `maxResponseBytes`) ;
- des PII si `MemoryPIIPolicy: forbid`.

---

## 8. Mapping vers l'IR

```jsonc
"memory": {
  "shortTermPolicy": "sliding-window",
  "shortTermMaxTurns": 12,
  "summarizeTriggerTokens": 24000,
  "longTermEnabled": false,
  "piiPolicy": "redact-before-write",
  "crossAgentSharedState": "scoped"
}
```

Et par agent : `"memoryScopes": { "read": ["conversation"], "write": [] }`.

---

## 9. État partagé entre agents

`CrossAgentSharedState` gouverne ce qu'un agent voit de l'état des autres :

| Valeur | Effet |
|---|---|
| `none` | chaque agent part du message initial. Le plus sûr, le moins efficace |
| `scoped` | **défaut** — un agent voit ce que son contrat de handoff déclare, rien de plus |
| `full` | tous voient tout. Coût du contexte élevé, et un document empoisonné lu par un agent contamine tous les autres |

`full` est un choix de sécurité autant que de coût : il transforme une injection
locale en injection globale. Il exige un ADR.

---

## 10. Évaluation

La mémoire s'évalue **sur des conversations multi-tours**, jamais sur des
échanges isolés — sinon on ne mesure rien de ce qu'elle fait.

| Métrique | Ce qu'elle révèle |
|---|---|
| `cost_usd` par conversation complète | l'effet quadratique, la seule façon de le voir |
| `multiturn_consistency` | le système se contredit-il entre les tours |
| `reference_resolution` | « et pour celui-là ? » désigne-t-il le bon objet |

Un golden set multi-tours est plus coûteux à construire, et c'est pour cela qu'il
est presque toujours absent — donc que les régressions de mémoire passent
inaperçues jusqu'à la production.
