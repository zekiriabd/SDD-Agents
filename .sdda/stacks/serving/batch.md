# Stack: batch (serving)

Stack ID: serving-batch
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: surface d'exposition **par lot** de l'application agentic — un jeu de N entrées traité en une passe ordonnancée, avec reprise, concurrence bornée, budget par lot **et** par item, rapport par item et code de sortie agrégé. C'est la surface des traitements de nuit, des ré-analyses de corpus et des campagnes d'enrichissement. Suppose `lang/python.md` et réutilise le `RunService` et le schéma `RunEvent` de `serving/cli.md` (§3.2) — seul l'ordonnancement change. Pas de `.libs.json` propre : aucune dépendance au-delà de la stdlib et du framework actif.

---

## 1. Rôle et périmètre

Une application agentic d'automatisation ou d'analyse de données ne s'utilise
presque jamais une requête à la fois. On lui donne **un fichier de 12 000
lignes**, et on revient le lendemain. Exposer un tel système derrière une API
interactive est possible et c'est le mauvais modèle : la latence par item n'a
aucune importance, le débit en a une, le budget total en a une, et **la reprise
après interruption est la fonctionnalité principale**.

Trois propriétés qui n'existent sur aucune autre surface :

1. **Le lot a un budget, et l'item aussi.** Un item qui part en boucle ne doit
   pas consommer le budget des 11 999 autres. Deux plafonds, indépendants
   (§3.3) — c'est la différence entre « le lot a coûté cher » et « le lot a
   coûté cher à cause de l'item 4412 ».
2. **L'échec partiel est le cas nominal, pas l'exception.** Sur 12 000 items,
   il y en aura qui échouent. Un lot qui s'arrête au premier échec est
   inutilisable ; un lot qui masque les échecs dans un total est pire. Le
   contrat est : **chaque item a son verdict, le lot a le sien, et les deux se
   lisent séparément.**
3. **La reprise est exacte, pas approximative.** Relancer un lot interrompu à
   72 % ne doit ni re-payer les 72 %, ni sauter un item par excès de prudence.
   Le journal d'items (§4) est la source, et il est écrit **avant** l'appel, pas
   après.

Hors périmètre : l'ordonnancement externe (cron, Airflow, Azure Data Factory,
Control-M). Cette fiche produit **un exécutable idempotent qui prend un lot et
rend un rapport** ; le déclencher à 2 h du matin n'est pas son travail. C'est
délibéré : un ordonnanceur intégré serait un second système à exploiter, et
chaque organisation a déjà le sien.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `serving-batch` |
| **Langage** | Python 3.12 (`lang/python.md`) |
| **Librairies** | aucune au-delà de `lang/python.md` — `asyncio`, `csv`, `json`, `sqlite3` (journal) sont dans la stdlib. `typer` est réutilisé depuis `serving-cli` si la CLI est aussi active. |
| **Point d'entrée** | `[project.scripts] {AppName}-batch = "{AppName}.serving.batch:main"` |
| **Paramètres STACK.md** | `DeliverableType: batch-job`, `MaxParallel`, `CostPerRunHardCapUsd`, `OnBoundExceeded`, `TraceLevel` |
| **Contrat machine** | un fichier **résultat** NDJSON (1 ligne par item) + un **manifeste** de lot JSON |
| **Codes de sortie** | table §3.4 — agrégés, distincts de ceux d'un run unique |

---

## 3. Mapping des concepts SDD_Agents → idiomes batch

### 3.1 La commande

| Commande | Rôle | Options principales |
|---|---|---|
| `{AppName}-batch run` | traite un lot | `--input PATH` (jsonl/csv/parquet) · `--output PATH` (NDJSON) · `--batch-id ULID` (nouveau si absent) · `--concurrency N` · `--max-items N` · `--budget-usd X` (lot) · `--item-budget-usd X` · `--on-item-error {continue\|stop\|quarantine}` · `--tenant ID` · `--dry-run` |
| `{AppName}-batch resume` | reprend un lot interrompu | `--batch-id ULID` (**obligatoire**) · `--retry {failed\|quarantined\|none}` |
| `{AppName}-batch report` | relit le journal d'un lot | `--batch-id ULID` · `--json` · `--failed-only` |
| `{AppName}-batch estimate` | **0 token** : compte les items, projette le coût depuis le budget par item de l'IR | `--input PATH` · `--json` |

`estimate` avant `run` n'est pas une politesse : c'est le seul moment où l'on
peut refuser un lot de 400 000 items avant de le payer.

### 3.2 Entrées

| Format | Lecture | Clé d'item |
|---|---|---|
| `.jsonl` | une ligne = un item | champ `id`, sinon numéro de ligne |
| `.csv` / `.tsv` | une ligne = un item, en-tête obligatoire | colonne `id`, sinon numéro de ligne |
| `.parquet` | une ligne = un item | colonne `id`, sinon index |
| répertoire | un fichier = un item | nom de fichier |

**La clé d'item est stable ou le lot n'est pas reprenable.** Un fichier
d'entrée réordonné entre deux runs, avec des clés positionnelles, fait reprendre
sur les mauvais items — et le rapport dira que tout s'est bien passé. Si aucune
colonne `id` n'existe, la clé est le sha256 de la ligne canonicalisée, et le
manifeste le **déclare** (`keyStrategy: "content-hash"`), pour que personne ne
croie à une clé métier.

Chaque item est `Untrusted` : `wrap_untrusted(source="batch:{input}#{key}")`.
Un lot de 12 000 lignes venu d'un export client est exactement le vecteur
d'injection indirecte que la SAFETY GATE cherche (P8).

### 3.3 Bornes : deux plafonds, jamais un seul

| Borne | Portée | Dépassement |
|---|---|---|
| `--item-budget-usd` | un item | l'item est marqué `over_budget`, le lot **continue** |
| `--budget-usd` | le lot entier | le lot **s'arrête proprement**, journal flushé, code 5 |
| `MaxIterations`, `MaxToolCalls` | un item (héritées de l'IR) | comportement `OnBoundExceeded` de l'agent |
| `--concurrency` | plafonnée par `MaxParallel` | une valeur supérieure est **ramenée**, et le manifeste le dit |
| `--max-items` | troncature explicite du lot | le manifeste porte `truncated: true` et le compte laissé |

Le lot ne dépasse **jamais** `CostPerRunHardCapUsd × items` sans que
`--budget-usd` ait été posé explicitement au-dessus : un plafond par run qui ne
se compose pas en plafond de lot ne plafonne rien.

### 3.4 Codes de sortie

| Code | Signification | Qui le lit |
|---|---|---|
| `0` | tous les items en succès | ordonnanceur |
| `1` | erreur interne non classée | CI |
| `2` | usage : entrée illisible, format inconnu, clé absente | humain |
| `5` | **budget de lot dépassé** — arrêt propre, reprise possible | ordonnanceur, G6 |
| `7` | **succès partiel** — au moins un item échoué, aucun blocage | ordonnanceur : c'est le cas nominal à surveiller |
| `8` | configuration : `Settings` invalide, IR périmé, prompt absent | `health`, CI |
| `12` | **lot interrompu** (SIGTERM de l'ordonnanceur) — journal cohérent, `resume` possible | ordonnanceur |
| `13` | **seuil d'échec dépassé** (`--fail-over-pct`) — le lot s'arrête car quelque chose de systémique casse | opérateur |
| `130` | `SIGINT` — idem 12, déclenché à la main | humain |

Le code `7` est le plus important de la table : un lot avec 0,3 % d'échecs est
**normal**, et le confondre avec `0` supprime la seule information qui compte.
Le confondre avec `1` fait qu'on ignore les deux.

### 3.5 Mapping des entités

| Concept | Idiome batch |
|---|---|
| **MISSION** | un exécutable de lot = une mission |
| **BATCH** | un `batch_id` (ULID) ; répertoire de journal ; span racine `sdda.batch.id` |
| **RUN** | un `run_id` par **item** ; la trace de l'item porte `sdda.batch.id` en attribut |
| **thread** | absent par construction — un item n'a pas de conversation. `HumanInTheLoopEnabled` est **refusé** sur cette surface (§5.9) |
| **Identité de l'appelant** | `--tenant`, ou colonne `tenant_id` de l'entrée si le lot est multi-locataire — et alors **chaque item porte la sienne**, posée dans son `ToolContext` |
| **TRACE** | une trace par item sous `traces/runs/` ; `TraceLevel: sampled` est le réglage recommandé au-delà de 1 000 items (§7.4) |

---

## 4. Le journal d'items — le cœur de la reprise

```
workspace/.sys/.batch/{batch_id}/
├── manifest.json        # entrée, clé, bornes, concurrence, début, troncature, hashes épinglés
├── items.sqlite         # journal transactionnel : une ligne par item
└── results.ndjson       # sorties, append-only, une ligne par item terminé
```

`items.sqlite` (WAL) porte : `key`, `state`, `attempts`, `run_id`, `cost_usd`,
`duration_ms`, `error_class`, `started_at`, `finished_at`.

États : `pending → running → {done | failed | quarantined | over_budget}`.

**La ligne passe à `running` AVANT l'appel, dans une transaction committée.**
C'est ce qui rend la reprise honnête : un processus tué pendant un appel laisse
un item en `running`, et `resume` le traite comme `failed` avec une tentative
consommée — jamais comme `pending`, ce qui le ferait re-payer en silence, ni
comme `done`, ce qui le ferait sauter.

`quarantined` existe pour l'item qui échoue de la même façon à chaque tentative :
il sort du lot, il est nommé dans le rapport, et `resume --retry quarantined`
est un acte explicite. Un item qui boucle en échec consomme un budget que
personne n'a décidé de lui donner.

---

## 5. Conventions imposées

1. **`results.ndjson` est append-only et flushé par item.** Un lot de 12 h tué
   à la 11ᵉ ne perd pas 11 h de résultats.
2. **Le manifeste épingle les hashes** (`prompt_hash`, `ir_hash`, `model_id`,
   `index_hash`, `dataset_hash`). Une reprise dont un hash a bougé n'est **pas**
   une reprise : elle est refusée avec `[BATCH_PINS_DRIFTED]`, et l'opérateur
   choisit entre relancer à neuf ou assumer un lot hétérogène. Un lot moitié
   ancien prompt moitié nouveau est un jeu de résultats qu'on ne peut plus
   interpréter.
3. **La concurrence est bornée par `MaxParallel`**, jamais par ce que la
   machine supporte. Le facteur limitant est le quota du fournisseur, et le
   dépasser produit des `429` qui coûtent en latence sans produire de débit.
4. **Le backoff sur `429` / `503` est exponentiel avec jitter**, et le temps
   d'attente est compté dans `duration_ms` mais **pas** dans le budget : on ne
   facture pas l'attente.
5. **Un item ne voit jamais un autre item.** Pas d'état partagé, pas de mémoire
   entre items — sinon le résultat dépend de l'ordre, donc de la concurrence,
   donc il n'est pas reproductible. `CrossAgentSharedState` est ignoré ici.
6. **`--dry-run` lit l'entrée, valide les schémas, n'appelle aucun modèle** et
   rend le compte d'items et le coût projeté. 0 token.
7. **Le rapport distingue trois nombres** : items traités, items en succès,
   items en succès **non dégradés**. Aplatir les deux derniers est la façon de
   livrer un lot dont 30 % des réponses sont partielles sans que ça se voie.
8. **`--fail-over-pct` (défaut 25)** arrête le lot quand le taux d'échec
   dépasse le seuil sur une fenêtre glissante : un outil tombé ou une clé
   expirée ne doit pas brûler le budget sur 11 000 items condamnés.
9. **`HumanInTheLoopEnabled: true` est refusé** sur cette surface —
   `[BATCH_HITL_UNSUPPORTED]`. Un lot de nuit qui attend une décision humaine
   ne se termine jamais, et l'ordonnanceur le tuera au timeout sans que
   personne ne comprenne. Un item qui exige une décision est `quarantined` avec
   sa raison ; la décision se prend ensuite, item par item, via la CLI.
10. **Toute exécution écrit son manifeste avant le premier appel**, y compris
    un lot qui échoue au démarrage. Un lot sans manifeste est un lot dont on ne
    sait pas ce qu'il a tenté.

---

## 6. Commande de smoke

Déterministe, 0 token :

```bash
cd workspace/src/{AppName}
uv run {AppName}-batch --help                                  # exit 0
uv run {AppName}-batch estimate --input tests/fixtures/batch-10.jsonl --json
#   -> {"items": 10, "projectedCostUsd": 0.12, "keyStrategy": "field:id"}
uv run {AppName}-batch run --input tests/fixtures/batch-10.jsonl --dry-run --json
#   -> 10 items validés contre inputSchema, 0 appel modèle, exit 0
uv run pytest tests/serving/test_batch_journal.py -q            # reprise exacte, LLM mocké
```

Le premier lot réel appartient à la L7 (ORCH GATE) : le runner invoque
`{AppName}-batch run` sur un golden de 50 items et lit `results.ndjson`.

---

## 7. Pièges connus

1. **Reprendre sur des clés positionnelles.** Le fichier a été régénéré entre
   deux runs, les lignes ont bougé, la reprise traite les mauvais items et le
   rapport est vert. C'est la panne la plus coûteuse de cette surface, parce
   qu'elle est invisible. D'où `keyStrategy` **dans le manifeste**, et le refus
   de reprendre si le hash du fichier d'entrée a changé.
2. **Marquer `running` après l'appel.** Le processus meurt pendant l'appel,
   l'item reste `pending`, la reprise le re-paie. Sur 12 000 items et une
   interruption tardive, c'est le lot entier payé deux fois.
3. **`asyncio.gather` sur 12 000 coroutines.** Toutes sont créées d'un coup,
   la mémoire explose et le quota du fournisseur est saturé en trois secondes.
   Un `Semaphore(concurrency)` **et** une consommation en flux de l'entrée —
   jamais un `list(reader)`.
4. **Une trace par item à `TraceLevel: full` sur 12 000 items** produit des
   dizaines de milliers de fichiers et sature l'inode budget. Au-delà de 1 000
   items : `sampled` pour les succès, **`full` systématique pour les échecs** —
   ce sont eux qu'on relira.
5. **Compter le temps de backoff dans le budget de latence.** Le p95 devient
   celui du quota du fournisseur, pas celui du système, et l'optimisation part
   dans la mauvaise direction.
6. **Un échec d'écriture du journal traité comme un échec d'item.** Le disque
   est plein : tous les items suivants sont marqués `failed`, et le rapport
   accuse le modèle. Une erreur d'E/S sur le journal **arrête le lot** (code 1),
   elle ne se déguise pas en résultat.
7. **`--on-item-error stop` par défaut.** Le premier item mal formé d'un export
   de 12 000 lignes arrête tout. Le défaut est `continue` ; `stop` est réservé
   au débogage.
8. **Le lot qui écrit dans `datasets/`.** Un lot qui enrichit un jeu de test
   modifie le jeu qui juge le système. Interdit par l'ownership
   (`[OWNERSHIP_VIOLATION]`) : la sortie d'un lot va dans `--output`, jamais
   sous `workspace/proof/datasets/` ni `workspace/proof/`.
9. **Secrets dans le fichier d'entrée.** Un export client contient des jetons
   en clair, qui partent dans les traces et dans `results.ndjson`. Les mêmes
   règles de rédaction que les arguments d'outil s'appliquent à l'item
   (`TracePIIPolicy`), et la SAFETY GATE scanne `results.ndjson`.
10. **Une seule ligne de rapport pour 12 000 items.** « 11 964 ok » ne dit pas
    si les 36 échecs sont 36 items bizarres ou un outil tombé pendant 4
    minutes. Le rapport groupe par `error_class` et donne la fenêtre temporelle
    de chaque groupe.
11. **Encodage Windows sur `results.ndjson`.** `ensure_ascii=False` impose
    d'ouvrir le fichier en `encoding="utf-8"` explicitement ; le défaut
    `cp1252` fait planter le lot au premier caractère non latin — souvent après
    plusieurs heures.
12. **Relancer `run` au lieu de `resume`.** Un nouveau `batch_id` est créé, le
    lot entier est re-payé, et deux jeux de résultats coexistent sans qu'on
    sache lequel fait foi. `run` sur une entrée dont le hash correspond à un lot
    inachevé **le dit** et propose `resume`.
