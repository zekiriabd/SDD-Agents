# Les sources de données d'un agent — registre, connecteurs, secrets

Consommé par `architect-data`. Complément de `DATA-ACCESS.fr.md`, qui traite
le cas « la donnée est dans une base ». Ce document traite le cas beaucoup plus
fréquent en début de projet : **la donnée est ailleurs, et à plusieurs endroits
à la fois**.

> Rappel utile avant tout : une source n'est pas un problème de RAG parce
> qu'elle est dans un fichier. Un CSV de commandes se filtre et se compte ; le
> vectoriser produit un agent qui ne sait plus répondre « combien ». Le RAG
> commence là où la réponse est dans de la **prose**, pas dans des
> enregistrements.

> **Ce qui s'exécute aujourd'hui.** Le modèle ci-dessous décrit trois
> connecteurs et une dizaine de sortes de stores ; le runtime généré
> (`templates/runtime/python/data/`) n'en lit qu'une partie : le connecteur
> `file`, sur un store `local` — ou `smb` / `nfs` **montés**, lus comme un
> chemin que l'OS résout, montage non vérifié. Tout le reste est accepté par le
> parseur et **refusé au preflight** par `preflight_stack_combo`
> (`[STACK_VALUE_UNIMPLEMENTED]`) : connecteurs `http-api` et `mcp`, stores
> `s3`, `azure-blob`, `gcs`, `sftp`, `http`, `mcp`. Une déclaration valide que
> rien n'exécute est exactement le cas où l'agent invente le client qui manque ;
> le hook l'arrête avant qu'il en ait l'occasion. La fiche est
> `dataaccess/declared-sources.md` (`[python]`).

---

## 1. Le modèle : store, source, manifeste

Trois objets, et une règle par objet.

| Objet | Répond à | La règle |
|---|---|---|
| **Store** | *où est la donnée, et avec quelles clés ?* | c'est le **seul** endroit qui touche à une authentification, et il n'en porte que le **nom de variable** |
| **Source** | *qu'est-ce que c'est, et qu'en expose-t-on ?* | elle ne porte ni URL, ni chemin absolu, ni secret — seulement un `store` et un localisateur relatif |
| **Déclaration** | *où est déclaré tout cela ?* | inline dans `STACK.md ## Active Data Sources`, **versionné** — il ne porte que des noms de variables, les valeurs sont dans `workspace/assets/.env`. Seul manifeste optionnel : un `mcp.json` standard à côté de `STACK.md`, importé tel quel |

Cette séparation est ce qui permet de déclarer trente sources sans multiplier
par trente les endroits où une clé d'API peut fuir. Elle est aussi ce qui rend
l'allowlist d'egress calculable : les hôtes joignables sont exactement ceux des
stores, et ils sont énumérables sans lire une seule source.

---

## 2. Les connecteurs

| `connector` | La donnée vient de | Index possible | `trust` par défaut | Runtime |
|---|---|---|---|---|
| `file` | un objet lu sur un store de fichiers | oui, construit au démarrage | `trusted`, sauf champ `free_text` | **implémenté** |
| `http-api` | une réponse HTTP paginée | non — chaque `lookup` est un appel | **`untrusted`** | refusé au preflight |
| `mcp` | un outil exposé par un serveur MCP | non | **`untrusted`** | refusé au preflight |

Le défaut pessimiste sur les deux derniers n'est pas une précaution de façade :
une réponse d'API et une sortie d'outil MCP sont écrites par un tiers, et une
chaîne de caractères écrite par un tiers qui atterrit dans le contexte d'un
modèle est une instruction potentielle. C'est la surface d'attaque propre à
l'agentic (P8), et elle existe sans RAG. Quand ces connecteurs auront un
client, l'IR le dira (`dataAccess[].connectors`) et la SAFETY GATE exigera pour
eux une `egressAllowlist` non vide et une suite d'injection.

### 2.1 Les stores de fichiers

| `kind` | Pour | Ce qu'il faut vérifier | Runtime |
|---|---|---|---|
| `local` | répertoire du poste ou du conteneur | monté en **lecture seule** ; sinon la lecture seule n'est qu'une convention du code | **implémenté** |
| `smb` | partage Windows / CIFS (`//fs01/ops`) | le montage existe au démarrage, et survit à la nuit (piège n°7 de la fiche) | lu s'il est monté — dit au preflight, pas refusé |
| `nfs` | partage POSIX monté | idem | idem |
| `s3` | Amazon S3 ou compatible (MinIO, Ceph) | `prefix` verrouillé : un bucket sans préfixe est une racine sans frontière | refusé au preflight |
| `azure-blob` | conteneur Azure | idem | refusé au preflight |
| `gcs` | bucket Google | idem | refusé au preflight |
| `sftp` | dépôt de fichiers d'un partenaire | l'empreinte de l'hôte est épinglée, pas acceptée à la volée | refusé au preflight |

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

### 2.3 Du schéma figé à l'outil — ce que le pipeline génère

Une source déclarée devient un outil sans qu'un LLM écrive son contrat :

1. **Le schéma figé.** `python .sdda/sdda.py gen-source-tools --infer --source {id}`
   le propose depuis les données ; un humain le **relit**. Il vit dans
   `workspace/src/{App}/data/schemas/{id}.schema.json` et part avec
   l'application : c'est un actif d'exécution, revalidé au démarrage sur
   `SourceSchemaCheckSample` enregistrements. `/sdda-full` n'attend pas la
   PHASE 2 pour le demander : `gen-source-tools --infer --missing` infère,
   avant la PHASE 0, chaque source sans schéma, et la relecture est la seule
   question posée avant le premier agent.
2. **Les contrats, en PHASE 2.** `gen-source-tools --write --scope contracts`
   (`/sdda-topology` STEP 4.bis, avant la compilation de l'IR) : l'IR et G2 voient
   les outils de source comme les autres. Une source sans schéma figé est
   `[DATA_SOURCE_SCHEMA_MISSING]` — STOP humain, il n'y a rien à contracter.
3. **Le code, en PHASE 3.** `gen-source-tools --write --scope code`, juste avant
   le spawn de `dev-data` : wrappers, runtime `data/`, `sources.json`,
   `tool_specs.json`. `dev-data` complète autour sans éditer le généré, et finit
   par `gen-source-tools --check --scope code`. Un contrat absent à ce stade est
   `[DATA_TOOL_MISSING]` : il n'est jamais créé après coup, il décrirait un
   outil que l'IR et G2 n'ont pas vu.

---

## 3. Matrice de décision

| Situation | Réponse |
|---|---|
| Les données sont dans une base, besoins connus | `view-per-agent` (cf. `DATA-ACCESS.fr.md`) |
| Des exports de fichiers, un répertoire, des besoins connus | **`declared-sources`**, connecteur `file` |
| Un CRM / un ERP derrière une API REST gouvernée | `declared-sources`, connecteur `http-api` — **refusé au preflight tant qu'il n'a pas de client runtime** |
| Un serveur MCP interne qui expose déjà des **lectures** métier | `declared-sources`, connecteur `mcp` — **même statut** |
| Un serveur MCP qui expose des **actions** (créer, envoyer, rembourser) | `tools/mcp.md` + un tool-contract par action — pas une source |
| Tout cela à la fois | **`declared-sources`** : un registre, N stores, N sources |
| Les données sont des documents, pas des enregistrements | `RAG-PATTERNS.fr.md` |
| Des enregistrements **et** des documents | composition : `declared-sources` **+** RAG, deux outils distincts |
| Une écriture est impliquée | la stratégie `repository-tools` (cataloguée, sans fiche à ce jour) — jamais cette stack, qui est lecture seule par construction |

Le cas « tout cela à la fois » est le cas normal, pas l'exception. C'est la
raison d'être du registre : cinq intégrations écrites séparément donnent cinq
idées différentes de la sûreté, et c'est toujours la plus faible qui définit le
niveau réel du système. Aujourd'hui, ce cas se construit sur des fichiers ;
l'API et le MCP en lecture passent, en attendant leur client, par un export
déposé sur un store `local`.

---

## 4. Les secrets : des noms dans STACK.md, des valeurs dans `assets/.env`

La règle tient en une phrase : **une déclaration porte le nom d'une variable,
un fichier `.env` gitignoré porte sa valeur, et le framework ne lit jamais la
valeur.** L'humain dépose ce fichier là où il dépose le reste de ses entrées,
`workspace/assets/.env` ; `python .sdda/sdda.py install-env` le **copie** vers
`workspace/src/{App}/.env`, sans LLM, parce que c'est de là que l'application
part en exécutable ou en conteneur (convention SDD_Pro). `SourceSecretsFile`
(défaut `.env`) se résout relativement à `workspace/src/{App}/`, c'est-à-dire
cette copie. Aucun agent ne lit l'un ou l'autre fichier :
`preflight_forbidden_reads` et `preflight_bash_ownership` refusent
`[SECRET_READ_FORBIDDEN]`, y compris à `architect-data`.

```yaml
# workspace/stack/STACK.md ## Active Data Sources   <- VERSIONNÉ (noms seulement)
Stores:
  - id: crm_api
    kind: http
    base_url: https://crm.example.com/api/v2
    auth: { mode: api-key, header: X-API-Key, key_env: CRM_API_KEY }
```

```bash
# workspace/assets/.env   <- GITIGNORÉ, jamais commité, jamais lu par un agent
#                           (copié vers workspace/src/{App}/.env par install-env)
CRM_API_KEY=…
```

(L'exemple montre la forme d'une clé ; un store `kind: http` est lui-même
refusé au preflight tant qu'il n'a pas de client.)

Ce que le validateur (`python .sdda/sdda.py validate-data-access`) impose, et
pourquoi :

| Contrôle | Classe | Le mode d'échec qu'il évite |
|---|---|---|
| Toute clé `*_env` porte un nom, pas une valeur | `[DATA_SECRET_INLINE]` | une clé d'API commitée, qui reste dans l'historique après suppression |
| `auth` est obligatoire, `mode: none` compris | `[DATA_AUTH_INCOMPLETE]` | un store « public » par oubli, dont personne n'a décidé qu'il l'était |
| Le fichier de secrets de l'application existe dès qu'une variable est citée | `[DATA_SECRET_FILE_MISSING]` | un `install-env` oublié : l'application part sans aucune clé |
| Toute variable citée existe dans ce fichier | `[DATA_SECRET_VAR_UNDECLARED]` | un appel anonyme qui renvoie `200` et zéro ligne — l'agent répond « je ne trouve rien » |
| Le fichier de secrets est dans `.gitignore` | `[DATA_SECRET_FILE_UNIGNORED]` | le commit qui arrive trois semaines plus tard |
| Un `env` littéral dans un `mcp.json` importé | `[DATA_SECRET_INLINE]` | la fuite la plus fréquente de cette famille de fichiers |

Le framework parse la partie gauche du `=` et rien d'autre. Une valeur qui
n'entre jamais en mémoire ne peut pas être recopiée dans un rapport de gate —
lequel, lui, n'est pas gitignoré partout. Une valeur écrite en clair dans
STACK.md lui-même est `[STACK_SECRET_IN_CLEAR]` au `smoke-check`.

---

## 5. L'enveloppe de sûreté — obligatoire dès qu'une source est déclarée

Déclarée dans `STACK.md ## Active Data Sources`, portée dans l'IR
(`dataAccess[].envelope`), vérifiée par la TOOL GATE et la SAFETY GATE — côté
déclaration par `validate-data-access`, côté code par `validate-envelope`
(l'enveloppe unique `lookup_record` / `search_records` / `count_records`,
l'identité tirée du contexte d'exécution, les bornes, et **aucune** écriture de
fichier sous `data/`). C'est la transposition, terme à terme, de l'enveloppe DB.

| Clé | Défaut | Raison |
|---|---|---|
| `SourceAgentRole` | `readonly` | seule valeur admise ; une écriture relève de `repository-tools` |
| `SourceReadTimeoutMs` | 5000 | une lecture d'agent qui dure est une lecture qui a dérapé |
| `SourceMaxRecordsReturned` | 200 | protège le budget de tokens autant que la source ; au-delà, `truncated: true` |
| `SourceMaxObjectBytes` | 52428800 | un fichier ou une réponse plus gros est refusé |
| `SourceSchemaCheckSample` | 500 | enregistrements revalidés contre le schéma figé au démarrage : une dérive se voit au boot, pas en production |
| `SourceAllowedSources` | allowlist d'`id` (vide = toutes les sources déclarées) | une source hors liste n'existe pas pour l'application |
| `SourceAllowedStores` | allowlist d'`id` (vide = tous les stores déclarés) | importer un fichier MCP ne câble rien à lui seul |
| `SourceEgressAllowlist` | allowlist d'**hôtes** | vide = aucune sortie réseau, jamais « tout permis » |
| `SourceForbiddenOps` | `WRITE, DELETE, EXEC, SYMLINK_FOLLOW, UNDECLARED_EGRESS` | appliqué par le runtime, pas promis par le prompt |
| `SourceMaxStalenessHours` | 24 | au-delà → donnée servie avec `stale: true` et `as_of`, que l'agent doit dire ; ni un log, ni une exception |
| `SourceQueryLogging` | `full` | sans le journal, aucun post-mortem n'est possible |

Les deux dernières valeurs de `SourceForbiddenOps` méritent d'être lues deux
fois : `SYMLINK_FOLLOW` et `UNDECLARED_EGRESS` sont les deux contournements de
frontière qui **ne ressemblent pas à des écritures**. Un lien symbolique dans
une racine, et un appel vers un hôte que personne n'a déclaré, sortent tous les
deux du périmètre sans jamais modifier un octet. Le runtime refuse de suivre un
lien symbolique à l'indexation.

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
| Écrire dans une source | la stratégie `repository-tools`, sur une vraie base, avec idempotence et classe d'effet de bord |
| Joindre deux sources | une vue SQL, ou un agrégat pré-calculé à l'ingestion — jamais deux appels que le modèle recolle |
| Chercher dans de la prose | `rag/*.md` |
| Plus de 200 Mo ou ~2 M d'enregistrements par source | une base, et `view-per-agent.md` |
| Appeler un outil MCP qui **agit** | `tools/mcp.md` : une action est un outil contractualisé, pas une source |
