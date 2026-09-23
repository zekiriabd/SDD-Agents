# Les sources de données d'un agent — registre, connecteurs, secrets

Consommé par `architect-data`. Complément de `DATA-ACCESS.md`, qui traite
le cas « la donnée est dans une base ». Ce document traite le cas beaucoup plus
fréquent en début de projet : **la donnée est ailleurs, et à plusieurs endroits
à la fois**.

> Rappel utile avant tout : une source n'est pas un problème de RAG parce
> qu'elle est dans un fichier. Un CSV de commandes se filtre et se compte ; le
> vectoriser produit un agent qui ne sait plus répondre « combien ». Le RAG
> commence là où la réponse est dans de la **prose**, pas dans des
> enregistrements.

---

## 1. Le modèle : store, source, manifeste

Trois objets, et une règle par objet.

| Objet | Répond à | La règle |
|---|---|---|
| **Store** | *où est la donnée, et avec quelles clés ?* | c'est le **seul** endroit qui touche à une authentification, et il n'en porte que le **nom de variable** |
| **Source** | *qu'est-ce que c'est, et qu'en expose-t-on ?* | elle ne porte ni URL, ni chemin absolu, ni secret — seulement un `store` et un localisateur relatif |
| **Déclaration** | *où est déclaré tout cela ?* | inline dans `STACK.md ## Active Data Sources`, **versionné** — il ne porte que des noms de variables, les valeurs sont dans `.env`. Seul manifeste optionnel : un `mcp.json` standard, importé tel quel |

Cette séparation est ce qui permet de déclarer trente sources sans multiplier
par trente les endroits où une clé d'API peut fuir. Elle est aussi ce qui rend
l'allowlist d'egress calculable : les hôtes joignables sont exactement ceux des
stores, et ils sont énumérables sans lire une seule source.

---

## 2. Les connecteurs

| `connector` | La donnée vient de | Index possible | `trust` par défaut |
|---|---|---|---|
| `file` | un objet lu sur un store de fichiers | oui, construit au démarrage | `trusted`, sauf champ `free_text` |
| `http-api` | une réponse HTTP paginée | non — chaque `lookup` est un appel | **`untrusted`** |
| `mcp` | un outil exposé par un serveur MCP | non | **`untrusted`** |

Le défaut pessimiste sur les deux derniers n'est pas une précaution de façade :
une réponse d'API et une sortie d'outil MCP sont écrites par un tiers, et une
chaîne de caractères écrite par un tiers qui atterrit dans le contexte d'un
modèle est une instruction potentielle. C'est la surface d'attaque propre à
l'agentic (P8), et elle existe sans RAG.

### 2.1 Les stores de fichiers

| `kind` | Pour | Ce qu'il faut vérifier |
|---|---|---|
| `local` | répertoire du poste ou du conteneur | monté en **lecture seule** ; sinon la lecture seule n'est qu'une convention du code |
| `smb` | partage Windows / CIFS (`//fs01/ops`) | le montage existe au démarrage, et survit à la nuit (piège n°7 de la fiche) |
| `nfs` | partage POSIX monté | idem |
| `s3` | Amazon S3 ou compatible (MinIO, Ceph) | `prefix` verrouillé : un bucket sans préfixe est une racine sans frontière |
| `azure-blob` | conteneur Azure | idem |
| `gcs` | bucket Google | idem |
| `sftp` | dépôt de fichiers d'un partenaire | l'empreinte de l'hôte est épinglée, pas acceptée à la volée |

### 2.2 Les formats

| Format | Ce qui manque et qu'il faut déclarer |
|---|---|
| `object` / `array` / `jsonl` | le schéma — JSON n'en a pas |
| `csv` / `tsv` | le schéma **et** les types : tout est une chaîne. Plus `delimiter` et `encoding` |
| `xlsx` | l'onglet, la ligne d'en-tête, et la conversion des dates Excel (des nombres) |
| `parquet` | rien — le schéma existe ; on le **transcrit** dans le schéma figé |

Le mode d'échec du CSV mérite d'être connu par cœur : il ne lève pas
d'exception. `0012345` devient `12345`, un code postal perd son zéro, une
jointure ne trouve plus rien, et l'agent répond « ce client n'existe pas ».

---

## 3. Matrice de décision

| Situation | Réponse |
|---|---|
| Les données sont dans une base, besoins connus | `view-per-agent` (cf. `DATA-ACCESS.md`) |
| Des exports de fichiers, un répertoire, des besoins connus | **`declared-sources`**, connecteur `file` |
| Un CRM / un ERP derrière une API REST gouvernée | **`declared-sources`**, connecteur `http-api` |
| Un serveur MCP interne qui expose déjà des **lectures** métier | **`declared-sources`**, connecteur `mcp` |
| Un serveur MCP qui expose des **actions** (créer, envoyer, rembourser) | `tools/mcp.md` + un tool-contract par action — pas une source |
| Tout cela à la fois | **`declared-sources`** : un registre, N stores, N sources |
| Les données sont des documents, pas des enregistrements | `RAG-PATTERNS.md` |
| Des enregistrements **et** des documents | composition : `declared-sources` **+** RAG, deux outils distincts |
| Une écriture est impliquée | `repository-tools` — jamais cette stack, qui est lecture seule par construction |

Le cas « tout cela à la fois » est le cas normal, pas l'exception. C'est la
raison d'être du registre : cinq intégrations écrites séparément donnent cinq
idées différentes de la sûreté, et c'est toujours la plus faible qui définit le
niveau réel du système.

---

## 4. Les secrets : des noms, dans le `.env` de l'application

La règle tient en une phrase : **une déclaration porte le nom d'une variable,
le fichier `workspace/src/{App}/.env` gitignoré porte sa valeur, et le framework
ne lit jamais la valeur.** Le fichier vit avec l'application, pas à la racine
du dépôt : c'est elle qui consomme la clé, et c'est de là qu'elle part en
exécutable ou en conteneur (convention SDD_Pro). `SourceSecretsFile` se résout
relativement à ce répertoire.

```yaml
# workspace/stack/STACK.md ## Active Data Sources   <- VERSIONNÉ (noms seulement)
Stores:
  - id: crm_api
    kind: http
    base_url: https://crm.example.com/api/v2
    auth: { mode: api-key, header: X-API-Key, key_env: CRM_API_KEY }
```

```bash
# workspace/src/{App}/.env   <- GITIGNORÉ, jamais commité, jamais lu par un agent
CRM_API_KEY=…
```

Ce que le validateur impose, et pourquoi :

| Contrôle | Classe | Le mode d'échec qu'il évite |
|---|---|---|
| Toute clé `*_env` porte un nom, pas une valeur | `[DATA_SECRET_INLINE]` | une clé d'API commitée, qui reste dans l'historique après suppression |
| `auth` est obligatoire, `mode: none` compris | `[DATA_AUTH_INCOMPLETE]` | un store « public » par oubli, dont personne n'a décidé qu'il l'était |
| Toute variable citée existe dans le `.env` de l'application | `[DATA_SECRET_VAR_UNDECLARED]` | un appel anonyme qui renvoie `200` et zéro ligne — l'agent répond « je ne trouve rien » |
| Le fichier de secrets est dans `.gitignore` | `[DATA_SECRET_FILE_UNIGNORED]` | le commit qui arrive trois semaines plus tard |
| Un `env` littéral dans un `.mcp.json` importé | `[DATA_SECRET_INLINE]` | la fuite la plus fréquente de cette famille de fichiers |

Le framework parse la partie gauche du `=` et rien d'autre. Une valeur qui
n'entre jamais en mémoire ne peut pas être recopiée dans un rapport de gate —
lequel, lui, n'est pas gitignoré partout.

---

## 5. L'enveloppe de sûreté — obligatoire dès qu'une source est déclarée

Déclarée dans `STACK.md ## Active Data Sources`, portée dans l'IR
(`dataAccess[].envelope`), vérifiée par la TOOL GATE et la SAFETY GATE. C'est la
transposition, terme à terme, de l'enveloppe DB.

| Clé | Défaut | Raison |
|---|---|---|
| `SourceAgentRole` | `readonly` | seule valeur admise ; une écriture est `repository-tools` |
| `SourceReadTimeoutMs` | 5000 | une lecture d'agent qui dure est une lecture qui a dérapé |
| `SourceMaxRecordsReturned` | 200 | protège le budget de tokens autant que la source |
| `SourceMaxObjectBytes` | 52428800 | un fichier ou une réponse plus gros est refusé |
| `SourceAllowedSources` | allowlist d'`id` | une source non allowlistée n'existe pas pour l'application |
| `SourceAllowedStores` | allowlist d'`id` | importer un fichier MCP ne câble rien à lui seul |
| `SourceEgressAllowlist` | allowlist d'**hôtes** | vide = aucune sortie réseau, jamais « tout permis » |
| `SourceForbiddenOps` | `WRITE, DELETE, EXEC, SYMLINK_FOLLOW, UNDECLARED_EGRESS` | vérifié sur l'AST, pas par regex |
| `SourceMaxStalenessHours` | 24 | au-delà → erreur `SOURCE_STALE` déclarée, pas un avertissement de log |
| `SourceQueryLogging` | `full` | sans le journal, aucun post-mortem n'est possible |

Les deux dernières lignes de la table `SourceForbiddenOps` méritent d'être
lues deux fois : `SYMLINK_FOLLOW` et `UNDECLARED_EGRESS` sont les deux
contournements de frontière qui **ne ressemblent pas à des écritures**. Un lien
symbolique dans une racine, et un appel vers un hôte que personne n'a déclaré,
sortent tous les deux du périmètre sans jamais modifier un octet.

**Filtrage par identité** : si les données sont cloisonnées par utilisateur ou
par tenant, le filtre est appliqué dans le `required_filter:` de la source ou
dans le paramètre injecté par le runtime — jamais délégué au modèle. Un filtre
post-génération est une fuite avec une étape de plus : le modèle a déjà vu la
ligne, elle est dans la trace, elle est dans le contexte du tour suivant. C'est
un finding bloquant de `review-safety`.

---

## 6. Ce qui est daté, et pourquoi c'est la première cause de réponse fausse

Une source déclarée est **toujours** un instantané : un export a une date, une
réponse en cache a une date, un appel MCP a une date. L'outil retourne `as_of`
dans **chaque** réponse, et `stale` quand l'instantané dépasse
`max_staleness_hours`.

Le cas d'école, et il est silencieux : « ma commande n'est pas arrivée depuis
10 jours », calculée sur un tracking exporté il y a 3 jours, donne un retard
faux de 3 jours — **dans le sens rassurant**. Aucun test unitaire ne le voit,
aucun log ne le signale, et le client raccroche satisfait d'une réponse fausse.

---

## 7. Ce qui reste hors de portée de cette stack

| Besoin | Où il va |
|---|---|
| Écrire dans une source | `repository-tools.md`, sur une vraie base, avec idempotence et classe d'effet de bord |
| Joindre deux sources | une vue SQL, ou un agrégat pré-calculé à l'ingestion — jamais deux appels que le modèle recolle |
| Chercher dans de la prose | `rag/*.md` |
| Plus de 200 Mo ou 2 M d'enregistrements par source | une base, et `view-per-agent.md` |
| Appeler un outil MCP qui **agit** | `tools/mcp.md` : une action est un outil contractualisé, pas une source |
