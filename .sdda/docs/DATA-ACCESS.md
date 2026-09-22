# Accès aux bases de données depuis un agent

Consommé par `architect-data`. C'est une décision de **sécurité** autant
que d'architecture : un agent avec du text-to-SQL non contraint sur une base de
production est un incident qui attend son heure.

> **Si la donnée n'est pas dans une base** — des exports de fichiers, un
> partage réseau, un stockage objet, une API REST, un serveur MCP — la
> stratégie est `declared-sources`, et le document de référence est
> **`DATA-SOURCES.md`**. Les principes sont les mêmes (surface déclarée par un
> humain, schéma figé, enveloppe bornée, filtrage à la source) ; ce qui change
> est la frontière à vérifier : une racine et une allowlist d'hôtes plutôt
> qu'un rôle et une allowlist de schémas.
>
> Rappel : si la réponse est dans une base, **ce n'est pas un problème de RAG**.
> Vectoriser des lignes de table pour ensuite ne pas savoir compter est l'erreur
> classique. Un agent qui doit répondre « combien de factures impayées ce
> trimestre ? » a besoin d'une requête, pas d'une similarité cosinus.

---

## 1. Les cinq stratégies

### `view-per-agent` — une vue SQL dédiée par agent ou par CAP
L'agent ne voit **que** des vues taillées pour ses besoins, sur un rôle en
lecture seule.

- **Sûreté : la plus haute.** La surface est définie en SQL, par un humain, et
  versionnée. Le modèle ne peut pas atteindre ce que la vue n'expose pas.
- **Coût cognitif pour le modèle : le plus bas.** Une vue bien nommée avec cinq
  colonnes métier bat n'importe quel schéma de 200 tables.
- **Coût de maintenance** : une vue par besoin ; le schéma évolue, les vues
  suivent.
- **Quand** : les besoins sont connus et stables. **Défaut recommandé** en
  production, et de loin.
- **Contrat** : chaque vue est un artefact versionné dans
  `workspace/src/data/views/`, avec un commentaire décrivant son intention
  métier — commentaire qui devient la `description` de l'outil, donc du prompt.

### `repository-tools` — outils paramétrés, requêtes figées
Le SQL est écrit par des humains et figé dans le code ; l'agent ne fournit que
des paramètres typés et validés.

- **Sûreté : très haute.** Aucune génération de SQL. Injection impossible par
  construction.
- **Testabilité : la meilleure.** Chaque outil est une fonction testable en L1 et
  L2 sans LLM.
- **Quand** : les opérations sont énumérables (`get_customer_by_id`,
  `list_unpaid_invoices(period)`). **Le meilleur choix dès qu'il y a écriture.**
- **Limite** : ne couvre pas l'exploration analytique ouverte.

### `semantic-layer` — couche métrique exposée en outil
L'agent interroge des métriques et dimensions définies (dbt Semantic Layer,
Cube, LookML), pas des tables.

- **Sûreté : haute.** La couche impose les jointures et les agrégations correctes.
- **Gain propre** : elle élimine la classe d'erreur la plus insidieuse du
  text-to-SQL — le SQL **syntaxiquement valide et métier-ment faux** (mauvaise
  jointure, double comptage, filtre de soft-delete oublié).
- **Quand** : l'organisation possède déjà une couche sémantique. Ne pas en
  construire une pour un agent.

### `text-to-sql` — génération de SQL par le modèle
- **Sûreté : la plus basse.** Ne s'envisage que sous **enveloppe complète** :

  1. rôle base **en lecture seule**, sur un réplica de préférence ;
  2. allowlist de schémas et de tables ;
  3. statements interdits : `DROP`, `TRUNCATE`, `ALTER`, `GRANT`, `CREATE`,
     `DELETE`, `UPDATE`, `INSERT` ;
  4. `statement_timeout` serveur (5 s par défaut) ;
  5. `LIMIT` forcé par réécriture, jamais par confiance dans le modèle ;
  6. **parsing de l'AST avant exécution** — pas une regex : une regex se contourne ;
  7. `EXPLAIN` préalable avec refus au-delà d'un coût estimé ;
  8. journalisation intégrale de toute requête émise ;
  9. le schéma servi au modèle est **restreint et annoté**, jamais un dump.

- **Quand** : exploration analytique ouverte, sur des données non sensibles, avec
  un humain dans la boucle pour les décisions.
- **À dire au client honnêtement** : la précision du text-to-SQL sur un schéma
  d'entreprise réel (jointures implicites, colonnes homonymes, soft-delete,
  conventions historiques) est **très inférieure** aux démonstrations sur des
  schémas jouets. Le mode d'échec dominant n'est pas l'erreur SQL — c'est la
  réponse plausible et fausse. `DbAgentRole: full` exige un ADR.

### `graphql` — un endpoint typé comme outil
- **Quand** : un GraphQL gouverné existe déjà et porte les autorisations.
- **Attention** : la profondeur et la complexité des requêtes doivent être
  plafonnées côté serveur, pas espérées côté agent.

---

## 2. Matrice de décision

| Situation | Stratégie |
|---|---|
| Besoins connus, lecture, production | **`view-per-agent`** |
| Opérations énumérables, **écriture impliquée** | **`repository-tools`** |
| Couche sémantique déjà en place | `semantic-layer` |
| Exploration analytique ouverte, données non sensibles, humain dans la boucle | `text-to-sql` sous enveloppe |
| GraphQL gouverné existant | `graphql` |
| Les données ne sont **pas** dans une base : fichiers, partage réseau, stockage objet, API REST, serveur MCP | **`declared-sources`** → `DATA-SOURCES.md` |
| Les données sont des documents, pas des lignes | → `RAG-PATTERNS.md` |
| Les deux | composition : `view-per-agent` **+** RAG documentaire, deux outils distincts |

---

## 3. L'enveloppe de sûreté — obligatoire dès que `DatabaseType != none`

Déclarée dans `STACK.md ## Active Data Access`, portée dans l'IR
(`dataAccess[].envelope`), vérifiée par la TOOL GATE et la SAFETY GATE.

| Clé | Défaut | Raison |
|---|---|---|
| `DbAgentRole` | `readonly` | `scoped-write` et `full` exigent un ADR |
| `DbStatementTimeoutMs` | 5000 | une requête d'agent qui dure est une requête qui a dérapé |
| `DbMaxRowsReturned` | 500 | protège le budget de tokens autant que la base |
| `DbAllowedSchemas` | `[public]` | allowlist, jamais denylist |
| `DbForbiddenStatements` | DDL + DML | vérifié sur l'AST, pas par regex |
| `DbQueryLogging` | `full` | sans le journal, aucun post-mortem n'est possible |

**Filtrage par identité** : si les données sont cloisonnées par utilisateur ou
par tenant, le filtre est appliqué **dans la vue ou dans le paramètre du
repository**, jamais délégué au modèle. Un filtre post-génération est une fuite
avec une étape de plus. C'est un finding bloquant de `review-safety`.

---

## 4. Écritures : classes d'effet de bord

Tout outil d'écriture porte une `side_effect_class` et une `safety_strategy`
(cf. `DOMAIN-MODEL.md`).

| Classe | Exemple | Stratégie exigée |
|---|---|---|
| `read-only` | `list_unpaid_invoices` | aucune |
| `write-scoped` | `update_ticket_status` | clé d'idempotence + allowlist de transitions |
| `write-destructive` | `delete_customer_record` | dry-run + confirmation + plafond par run + journal |
| `external-side-effect` | `send_email`, `create_refund` | idempotence sur clé naturelle + plafond + confirmation au-dessus d'un seuil |

**Le retry est la piège principal.** Un agent qui réessaie un outil d'écriture
non idempotent crée trois tickets, envoie trois e-mails, émet trois
remboursements. La `retry_policy` d'un outil non idempotent doit être
`no-retry` — ou l'outil doit devenir idempotent.
