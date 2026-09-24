# MEMORY CONTRACT: {n}-memory

MISSION: {n}-{MissionName}
Status: Draft
Memory Strategy: <buffer | summary | vector | entity | store>     # fiche .sdda/stacks/memory/*.md

> Contrat **neutre framework** (P11) : aucun nom de classe de checkpointer,
> de `ConversationBufferMemory` ni de store propriétaire ici. Ce fichier décrit
> *ce qui persiste, pour combien de temps, avec quelles PII, et qui lit quoi*
> (DOMAIN-MODEL.md — MEMORY SCOPE). L'implémentation vit dans la stack.
>
> **Le défaut est : pas de mémoire longue.** `LongTermEnabled: false` dans
> `STACK.md`. Toute mémoire persistante est une surface de fuite (PII), une
> source de dérive (un fait périmé rappelé comme vrai) et un vecteur
> d'injection **persistante** (une instruction hostile écrite une fois, relue à
> chaque session). Elle se justifie par écrit, comme un agent supplémentaire.

---

## 1. Ce que la mémoire doit permettre — et ce qu'elle ne doit pas

- **Besoin métier** : <ex. reprendre une conversation de support interrompue ;
  ne pas redemander le numéro de contrat à chaque tour>
- **CAP(s) qui l'exigent** : `{n}-{m}-…`
- **Ce qui est explicitement hors périmètre** : <ex. profilage de l'utilisateur,
  mémorisation de préférences non demandées>
- **Alternative sans mémoire écartée parce que** : <ex. l'état tient dans la
  fenêtre — mesuré : 18k tokens sur 24 tours, sous `SummarizeTriggerTokens`>

---

## 2. Mémoire court terme (conversation)

| Clé | Valeur | Commentaire |
|---|---|---|
| `ShortTermPolicy` | <none \| sliding-window \| summarize-over \| hybrid> | |
| `ShortTermMaxTurns` | <n> (défaut 12) | fenêtre glissante : ce qui sort de la fenêtre est **perdu** — dire ce que ça implique |
| `SummarizeTriggerTokens` | <n> (défaut 24000) | résumé déclenché au-delà : un appel LLM, tier <fast \| balanced> — compté dans le budget de la MISSION |
| Ce que le résumé DOIT conserver | <ex. identifiants métier, décisions prises, engagements donnés à l'utilisateur> | une perte ici est un bug fonctionnel, pas une dégradation |
| Ce que le résumé DOIT écarter | <ex. contenu des documents récupérés (relire l'index, ne pas résumer une source), PII non nécessaire> | |
| Portée | <par session \| par utilisateur> | |

> Porté dans l'IR : `memory.shortTermPolicy`, `memory.shortTermMaxTurns`,
> `memory.summarizeTriggerTokens`.

---

## 3. Mémoire long terme

| Clé | Valeur | Commentaire |
|---|---|---|
| `LongTermEnabled` | <false \| true> | `true` exige les lignes suivantes **toutes** remplies |
| `LongTermStore` | <none \| pgvector \| redis \| store-backed> | fiche `.sdda/stacks/memory/*.md` ; auth par variable d'environnement `{ENV_VAR}`, jamais la valeur |
| `LongTermWritePolicy` | <explicit \| automatic> | `explicit` : l'agent décide via un **outil** (`remember_fact`, contrat dans `workspace/pipeline/contracts/tools/`, classe `write-scoped`) — **recommandé**. `automatic` : tout est écrit — dire quoi, et pourquoi c'est acceptable |
| `LongTermRetentionDays` | <n> (défaut 90) | purge planifiée ; **qui** l'exécute et **comment** on vérifie qu'elle a eu lieu |
| Schéma d'un souvenir | <JSON Schema : `{ subject, fact, source, written_at, expires_at, written_by_agent }`> | un souvenir sans `source` ni date est un fait invérifiable : refusé |
| Clé de portée | <user_id \| tenant_id \| session_id> | filtrée **à la source** à la lecture, jamais par le modèle (même règle que le retrieval, RAG-PATTERNS.md §6) |
| Droit à l'oubli | <procédure de suppression sélective ; délai> | ce qui entre dans un vector store est difficile à retirer sélectivement — le dire ici, pas après l'incident |
| Rappel | <quand la mémoire est lue : à chaque tour \| sur demande de l'agent \| au début de session> ; top-k : <n> | |

> Porté dans l'IR : `memory.longTermEnabled`, `memory.longTermStore`,
> `memory.longTermWritePolicy`, `memory.longTermRetentionDays`.

### 3.1 Ce qui ne doit JAMAIS être écrit

- <secrets, jetons, mots de passe — scan déterministe avant écriture>
- <contenu brut d'un document récupéré ou d'une réponse d'outil `untrusted` — voir §6>
- <PII hors politique du §4>
- <instructions : une mémoire contient des **faits**, jamais des consignes de comportement>

---

## 4. PII

| Clé | Valeur | Commentaire |
|---|---|---|
| `MemoryPIIPolicy` | <forbid \| redact-before-write \| allow> | **`allow` exige un ADR** (invariant `pii-not-in-vector-store`) |
| Catégories de PII rencontrées | <nom, e-mail, téléphone, IBAN, adresse, identifiant client, …> | mesuré sur un échantillon, pas supposé |
| Mécanisme de redaction | <ex. détection déterministe + remplacement par jeton `{{PII:EMAIL:1}}` réversible côté application> | fiche `.sdda/stacks/guardrails/pii-redaction.md` |
| Vérification | scan PII de G7 (`scan_pii.py`) sur le store long terme **et** sur les résumés court terme persistés | `[PII_IN_INDEX]` bloquant |
| Base légale / durée | <référence à la politique de l'organisation ; cohérente avec `LongTermRetentionDays`> | |

> `TracePIIPolicy` (observabilité) est une décision **distincte** : ce contrat
> gouverne ce que l'application mémorise, pas ce que les traces enregistrent.
> Les deux doivent être cohérentes : mémoriser en `forbid` et tracer en `raw`
> est une contradiction.

---

## 5. État partagé inter-agents — qui lit quoi

| Clé | Valeur |
|---|---|
| `CrossAgentSharedState` | <none \| scoped \| full> — `full` : dire pourquoi `scoped` ne suffit pas |

> **Matrice d'ownership de l'état** — le mécanisme est celui de la matrice
> d'ownership de SDD_Pro, appliqué au runtime (ORCHESTRATION-PATTERNS.md,
> `blackboard`). Une section sans owner exclusif en écriture produira des
> conflits d'écriture et des écrasements. Porté dans l'IR : `agents[].memoryScopes`.

| Scope / section d'état | Contenu | Lecture | Écriture (owner exclusif) | Contrat de handoff associé |
|---|---|---|---|---|
| `conversation` | historique court terme | <tous les agents de la topologie> | orchestration (append-only) | — |
| `long_term` | souvenirs §3 | `{n}-…` | `{n}-…` via outil `remember_fact` | — |
| `shared.{section}` | <ex. `case_summary`, `customer_context`> | `{n}-…`, `{n}-…` | **un seul** agent | `topology §7 Handoffs` : état transmis `{…}` |

- **Ce qu'un agent ne doit PAS voir** : <ex. le spécialiste remboursement ne lit
  pas les documents techniques récupérés par le spécialiste technique —
  isolation de scope, P7 raison 1>
- **Contradiction entre sections** : <stratégie : dernier écrit gagne \| priorité
  par owner \| échec explicite> — jamais laissée au hasard.

---

## 6. Posture de confiance de la mémoire

> **Ce qui est écrit en mémoire depuis du texte non maîtrisé reste non
> maîtrisé (P8).** Un fait extrait d'un document récupéré ou d'une réponse
> d'outil `untrusted` et mémorisé devient une injection **persistante** : lue à
> chaque session, sans que l'attaquant ait à agir de nouveau.

| Règle | Application |
|---|---|
| Provenance obligatoire | chaque souvenir porte `source` (`user`, `tool:{name}`, `retriever:{index}`) et hérite du `trust` de sa source |
| Relecture encadrée | un souvenir de provenance `untrusted` est servi au modèle **balisé comme donnée**, jamais dans la zone d'instructions du prompt |
| Aucune consigne mémorisée | un souvenir qui ressemble à une instruction (« désormais, toujours… ») est refusé à l'écriture — filtre déterministe + cas adversarial |
| Suite d'injection | famille « injection via mémoire empoisonnée » ajoutée à `workspace/pipeline/datasets/adversarial/{agent}.jsonl` pour chaque agent qui lit la mémoire longue |

---

## 7. Coût et bornes

| Poste | Estimation | Compté dans |
|---|---|---|
| Résumé court terme | <n appels par session x tier> | budget MISSION (`CostPerRunTargetUsd`) |
| Écriture long terme | <n embeddings / écritures par session> | idem |
| Lecture long terme | <top-k x tokens par tour> | `TokenCeilingPerRun` |
| Plafond d'écritures par run | <n> — un agent qui « se souvient » de tout à chaque tour dérive | borne d'outil (`cap.perRun`) |

---

## 8. Tests et evals

| Niveau | Contenu |
|---|---|
| L1 | réducteurs d'état, politique de fenêtre, résumé (LLM mocké), filtre de provenance, redaction PII — fonctions pures |
| L2 | outil `remember_fact` : schéma, idempotence (même fait deux fois = un souvenir), erreurs déclarées |
| L4 | agents évalués avec **mémoire figée** (fixture) : la variation est attribuable à l'agent, pas au contenu mémorisé |
| L5 | trajectoire : la mémoire est lue / écrite aux moments déclarés, jamais ailleurs |
| L8 | mémoire empoisonnée ; exfiltration d'un souvenir d'un autre tenant ; PII dans le store (`scan_pii.py`) |
| L9 | le contenu du store de test fait partie de la fixture épinglée : un store modifié périme la baseline |

---

## 9. Décisions à ADR

- `MemoryPIIPolicy: allow`
- `CrossAgentSharedState: full`
- `LongTermWritePolicy: automatic`
- <toute rétention > 90 jours>

- ADR-{timestamp}-{slug}: <titre>
