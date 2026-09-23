# MEMORY CONTRACT: 1-memory

MISSION: 1-SupportDesk
Status: Draft
Memory Strategy: buffer

> Contrat **neutre framework** (P11) : aucun nom de classe de checkpointer, de
> mémoire de conversation propriétaire ni de store framework ici. Ce fichier
> décrit *ce qui persiste, pour combien de temps, avec quelles PII, et qui lit
> quoi*. L'implémentation vit dans la stack (`.sdda/stacks/memory/buffer.md`).
>
> **Le défaut reste : pas de mémoire longue.** `LongTermEnabled: false` dans
> `STACK.md`. Aucune CAP allouée en topologie (`1-topology.md §1, §6`) ne nomme
> un besoin de continuité entre deux sessions — la topologie le dit
> explicitement : « un support n'a pas à se souvenir d'un client entre deux
> sessions, c'est le SI qui s'en souvient » (STACK.md, commentaire projet).

---

## 1. Ce que la mémoire doit permettre — et ce qu'elle ne doit pas

- **Besoin métier** : une session console (`--thread-id`) peut porter plusieurs
  tours ; le `support-router` reclasse chaque nouveau message « depuis le début
  avec l'historique » (`1-topology.md §4`, chemin de reprise après
  clarification) — sans fenêtre, il perdrait le fil d'une clarification
  demandée au tour précédent.
- **CAP(s) qui l'exigent** : aucune CAP relue par cet agent (hors périmètre de
  contexte de cette invocation, `caps/` exclu) ne nomme littéralement un besoin
  de mémoire multi-tours. Le besoin est déclaré par l'architecte en
  `STACK.md ## Active Memory Strategy` et confirmé par `1-topology.md §6`
  (« memory : buffer 12 tours, lu par le routeur seul »). Faute d'accès aux
  CAPs dans ce périmètre, je ne peux pas vérifier moi-même la traçabilité vers
  une AC précise — signalé, pas inventé.
- **Ce qui est explicitement hors périmètre** : mémoire long terme entre
  sessions, profilage du client, mémorisation de préférences non demandées.
  Le CRM/SI, pas cet assistant, porte la continuité inter-session (commentaire
  `STACK.md ## Active Memory Strategy`).
- **Alternative sans mémoire écartée parce que** : le pattern `router` relit
  l'historique pour reclasser un message de clarification (« le tour suivant
  reclasse depuis le début avec l'historique », `1-support-router.agent.md §14`) ;
  sans fenêtre, chaque tour redemanderait au client de répéter ce qu'il vient de
  dire. Mesuré : `1-topology.md §5` chiffre le pire cas borné par
  `ShortTermMaxTurns = 12` à exactement 30 000 tokens — le plafond
  `TokenCeilingPerRun` — 6 appels LLM, $0.100, `fail-explicit`. C'est ce
  couplage mesuré, pas une intuition, qui fixe la fenêtre à 12 tours.

---

## 2. Mémoire court terme (conversation)

| Clé | Valeur | Commentaire |
|---|---|---|
| `ShortTermPolicy` | `sliding-window` | repris de `STACK.md`, aucun override : le pattern `router` classe en un seul passage par tour (`maxHops = 2`), aucun besoin de préserver un raisonnement long |
| `ShortTermMaxTurns` | `12` | fenêtre glissante : au-delà, les tours les plus anciens sortent — **perdus**, pas résumés (§ ci-dessous). Mesuré contre `TokenCeilingPerRun` : `1-topology.md §5` chiffre 12 tours d'historique à 30 000 tokens = le plafond exact |
| `SummarizeTriggerTokens` | `24000` | valeur héritée de `STACK.md`/`buffer.md`, **sans effet sous `sliding-window` pur** : aucune étape de résumé n'est câblée (la politique n'est ni `summarize-over` ni `hybrid`). Conservée en l'état comme garde-fou déclaratif pour une bascule future ; ne pas en déduire qu'un résumé a lieu |
| Ce que le résumé DOIT conserver | sans objet — aucun résumé sous `sliding-window` | — |
| Ce que le résumé DOIT écarter | sans objet | — |
| Portée | par session (`--thread-id`) | pas de portée par utilisateur au-delà de la session : `LongTermEnabled: false` |

Seul `support-router` lit `conversation` (`1-support-router.agent.md §12`) : les
trois spécialistes ne la lisent ni ne l'écrivent (`1-order-tracking-agent.agent.md §12`
et les contrats `billing-agent`/`claims-agent` équivalents) — confirmé par
`1-topology.md §7` : « l'historique de conversation, lui, n'est **pas**
transmis aux spécialistes ». Ce qu'un spécialiste reçoit est le payload du
handoff (`{message, intent, confidence, entities, as_of}`), pas la mémoire
`conversation` — un appel direct, pas une lecture de scope.

> Porté dans l'IR : `memory.shortTermPolicy`, `memory.shortTermMaxTurns`,
> `memory.summarizeTriggerTokens`.

---

## 3. Mémoire long terme

| Clé | Valeur | Commentaire |
|---|---|---|
| `LongTermEnabled` | `false` | aucune CAP visible dans ce périmètre ne nomme « se souvenir de X entre deux sessions » ; `STACK.md` le confirme explicitement — pas d'activation |
| `LongTermStore` | `none` | sans objet |
| `LongTermWritePolicy` | `explicit` | sans objet, valeur héritée telle quelle pour cohérence de lecture |
| `LongTermRetentionDays` | `0` | sans objet — pas de store |
| Schéma d'un souvenir | sans objet | — |
| Clé de portée | sans objet | — |
| Droit à l'oubli | sans objet | — |
| Rappel | sans objet | — |

> Porté dans l'IR : `memory.longTermEnabled: false`, le reste `null`/absent.

### 3.1 Ce qui ne doit JAMAIS être écrit

- un secret, une clé, un jeton (`SecretsFile`, valeurs dans `.env` uniquement — P `agent-safety.md §4`) ;
- le contenu brut d'un champ `free_text` non maîtrisé (`delivery_note`,
  `carrier_message`, `failure_reason`, `description`, `resolution`, `notes` —
  `STACK.md ## Active Data Sources`) : ces champs sont du **contenu**, jamais
  une instruction (P8) ; s'ils sont cités dans une réponse d'agent qui entre
  ensuite dans `conversation`, ils y entrent **comme citation balisée**, pas
  comme fait mémorisable réutilisable (voir §6) ;
- une PII hors politique du §4 (`email`, `phone`, `card_last4` — jamais répétés
  sauf demande explicite du client, `STACK.md` descriptions `customers`/`payments`) ;
- une instruction : un souvenir — ici, un tour de conversation — contient des
  faits échangés, jamais une consigne de comportement pour un tour futur.

---

## 4. PII

| Clé | Valeur | Commentaire |
|---|---|---|
| `MemoryPIIPolicy` | `redact-before-write` | repris de `STACK.md`, pas `allow` — aucun ADR requis sur cette valeur |
| Catégories de PII rencontrées | `first_name`, `last_name`, `email`, `phone` (source `customers`) ; `card_last4` (source `payments`, non sensible — 4 derniers chiffres seulement) | déclarées par `STACK.md ## Active Data Sources` (`pii:` par source), pas supposées |
| Mécanisme de redaction | guardrail `pii-redaction` (`.sdda/stacks/guardrails/pii-redaction.md`) — **non activé** dans `STACK.md ## Active Guardrails` | voir écart signalé ci-dessous |
| Vérification | scan PII de G7 sur les résumés/fenêtres persistées (ici : la fenêtre de conversation, seule chose qui survit au tour) | `[PII_IN_INDEX]` bloquant si l'écart ci-dessous n'est pas résolu avant G7 |
| Base légale / durée | sans objet — aucune rétention au-delà de la session (`LongTermEnabled: false`) | — |

```
WARN: agent architect-memory — politique PII sans mécanisme câblé
CAUSE: [MEMORY_PII_POLICY_MISSING] MemoryPIIPolicy: redact-before-write exige
       de nommer le guardrail pii-redaction en sortie d'agent avant écriture
       (STEP 5) ; STACK.md ## Active Guardrails ne l'active pas (commenté
       « à activer si les traces sortent du poste local ») — seuls
       injection-detection (entrée) et schema-validation (sortie) sont actifs
FIX: activer .sdda/stacks/guardrails/pii-redaction.md dans STACK.md
     ## Active Guardrails avant que le support-router n'écrive un tour
     contenant first_name/last_name/email/phone/card_last4 dans `conversation`,
     ou documenter par ADR pourquoi la fenêtre locale de session n'en a pas
     besoin (console locale, sans egress — SourceEgressAllowlist: [])
```

> `TracePIIPolicy: redact` (observabilité) est une décision **distincte**,
> déjà cohérente : elle gouverne les traces, pas la fenêtre de conversation.
> Les deux sont désormais alignées sur *redact*, mais seule la trace a son
> mécanisme câblé aujourd'hui.

---

## 5. État partagé inter-agents — qui lit quoi

| Clé | Valeur |
|---|---|
| `CrossAgentSharedState` | `scoped` — repris de `STACK.md`, confirmé par le contrat de chaque agent (§12 de chacun) |

| Scope / section d'état | Contenu | Lecture | Écriture (owner exclusif) | Contrat de handoff associé |
|---|---|---|---|---|
| `conversation` | historique court terme (buffer 12 tours) | `support-router` **seul** | orchestration (append-only, hors du tour d'un agent) | — |
| `routing_decision` (= `shared` dans les contrats d'agent) | `{intent, confidence, entities}` — rien d'autre | le spécialiste destinataire de la condition de routage (`order-tracking-agent` \| `billing-agent` \| `claims-agent`, un seul par run) | `support-router` **seul** | `1-topology.md §7 Handoffs` |
| `long_term` | — | aucun agent | aucun agent | — (`LongTermEnabled: false`) |

- **Ce qu'un agent ne doit PAS voir** : un spécialiste ne voit jamais
  `conversation` (les tours précédents restent au routeur — `1-topology.md §7`) ;
  `customer_id` ne transite par **aucun** scope de mémoire : il reste dans le
  contexte runtime et alimente le `required_filter` des outils de données
  (`1-support-router.agent.md §13`), jamais fourni ni lu par un modèle.
- **Contradiction entre sections** : sans objet — un seul owner en écriture par
  section (`support-router` pour `routing_decision`, l'orchestration pour
  `conversation`), aucun recouvrement d'écriture possible par construction du
  graphe (`maxHops = 2`, aucun cycle — `1-topology.md §4`).

---

## 6. Posture de confiance de la mémoire

> **Ce qui est écrit en mémoire depuis du texte non maîtrisé reste non
> maîtrisé (P8).** Un fait cité depuis `carrier_message`, `delivery_note`,
> `description`, `resolution` ou `notes` et repris dans la réponse d'un
> spécialiste peut, une fois cette réponse ajoutée au tour suivant de
> `conversation`, être relu par `support-router` — une injection indirecte
> déposée dans une donnée devient alors une injection **relue à chaque
> reclassification** tant que la fenêtre la contient (jusqu'à 12 tours).

| Règle | Application |
|---|---|
| Provenance obligatoire | chaque tour de `conversation` hérite du `trust` de sa source : `user` pour le message client, `agent` (généré) pour la réponse — une réponse d'agent qui cite un champ `free_text` reste balisée comme citation, pas comme fait mémorisé |
| Relecture encadrée | `support-router` ne relit `conversation` que pour reclasser (skill `classify_intent`/`extract_entities`), jamais comme source de faits sur une commande, une facture ou une réclamation — ce rôle reste aux outils du tour courant |
| Aucune consigne mémorisée | un tour qui ressemble à une instruction adressée à un tour futur (« désormais, réponds toujours… ») n'a aucun mécanisme d'écriture dédié ici : la fenêtre stocke des tours de conversation bruts, pas des faits extraits — le filtre porte donc sur ce que `support-router` **fait** de ce qu'il relit, pas sur l'écriture elle-même |
| Suite d'injection | famille « injection via conversation empoisonnée » à ajouter à `workspace/proof/datasets/adversarial/support-router.jsonl` (déjà obligatoire, `1-support-router.agent.md §10`) : un tour antérieur fait écho à `carrier_message`/`delivery_note` avec une instruction, le tour suivant vérifie que le routage n'en est pas altéré |

---

## 7. Coût et bornes

| Poste | Estimation | Compté dans |
|---|---|---|
| Résumé court terme | 0 — `sliding-window`, aucun résumé câblé | — |
| Écriture long terme | 0 — `LongTermEnabled: false` | — |
| Lecture long terme | 0 | — |
| Coût de la fenêtre (refacturation quadratique, `buffer.md §5`) | déjà chiffré par `1-topology.md §5` : pire cas 12 tours = 30 000 tokens = `TokenCeilingPerRun`, 6 appels, $0.100 (`fail-explicit`) ; nominal `support-router` $0.0013 sans historique / $0.0034 avec 12 tours (`1-support-router.agent.md §9`) | `TokenCeilingPerRun`, `CostPerRunHardCapUsd` |
| Plafond d'écritures par run | sans objet — aucun outil `remember_fact` ; l'unique écriture mémoire est l'ajout du tour courant à `conversation` par l'orchestration, borné par `ShortTermMaxTurns` (12), pas par un plafond d'outil | `1-topology.md §5` |

---

## 8. Tests et evals

| Niveau | Contenu |
|---|---|
| L1 | fonction pure de fenêtre glissante (troncature à 12 tours, plus ancien sorti en premier) ; filtre de provenance (`user` vs `agent`) — pas de résumé à tester (sans objet) |
| L2 | sans objet — aucun outil d'écriture mémoire |
| L4 | `support-router` évalué avec `conversation` **figée** (fixture de N tours pinnée) : la variation de reclassification est attribuable au routeur, pas au contenu de la fenêtre ; les trois spécialistes évalués sans accès à `conversation` (confirmé par leur §12) |
| L5 | trajectoire : `conversation` lue **uniquement** par `support-router`, jamais par un spécialiste ; `routing_decision` écrite **uniquement** par `support-router`, lue par le seul spécialiste destinataire — vérifié sur la trace (`parent_span_id`) |
| L8 | conversation empoisonnée : un tour antérieur reprend `carrier_message`/`delivery_note` avec une instruction, vérifier que la reclassification du tour suivant n'en est pas altérée ; exfiltration d'un tour d'un autre `thread_id` (isolation de session) ; scan PII (`scan_pii.py`) sur la fenêtre persistée dès que le guardrail §4 sera câblé |
| L9 | fixture de conversation multi-tours épinglée pour L4/L5 : un changement de son contenu périme la baseline de reclassification, au même titre qu'un store long terme modifié |

---

## 9. Décisions à ADR

- Aucune ici : `MemoryPIIPolicy` reste `redact-before-write` (pas `allow`),
  `CrossAgentSharedState` reste `scoped` (pas `full`), `LongTermWritePolicy`
  est sans objet (`LongTermEnabled: false`), aucune rétention > 90 jours
  (aucune rétention du tout).
- Le seul point ouvert — activer `pii-redaction` ou justifier son absence par
  ADR — est un **FIX**, pas une décision déjà prise ; voir bloc WARN §4.

- ADR-{timestamp}-{slug}: —
