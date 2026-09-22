# Stack: view-per-agent (dataaccess)

Stack ID: dataaccess-view-per-agent
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: stratégie **DATA ACCESS** la plus sûre — une vue SQL dédiée par agent et par besoin, exposée comme outil `read-only` dont la description est le commentaire SQL de la vue. Enveloppe de sûreté complète (rôle readonly, timeout, plafond de lignes, filtre d'identité dans la vue). Variante PostgreSQL détaillée ; les autres `DatabaseType` sont couverts en §7. Pas de `.libs.json` : dépendances portées par `vectorstore/pgvector.md` ou le driver du `DatabaseType` (SQLAlchemy 2.x + driver async, mêmes pins que SDD_Pro).

---

## 1. Rôle et périmètre

Quand un agent doit répondre « combien de factures impayées ce trimestre ? »,
il a besoin d'une requête, pas d'une similarité cosinus (DATA-ACCESS.md). Et
quand cette requête touche une base de production, la question n'est pas
« comment le modèle va-t-il écrire le SQL ? » mais **« qu'est-ce que le modèle
ne pourra jamais atteindre ? »**.

`view-per-agent` répond par construction :

- **La surface est définie en SQL par un humain**, versionnée, revue. Le modèle
  ne voit que des vues : cinq colonnes métier nommées, jamais 200 tables.
- **Le commentaire de la vue est la description de l'outil.** Un seul artefact
  porte l'intention métier, la lit le développeur, la lit le modèle, la hashe le
  pipeline (`tool_schema_hash`). Le drift entre « ce que la vue fait » et « ce
  que le modèle croit qu'elle fait » devient un diff SQL.
- **Le filtre d'identité est dans la vue**, posé depuis la session, invisible du
  modèle. Une fuite d'autorisation est impossible par construction, pas
  improbable par bonne volonté.
- **Le rôle est en lecture seule** au niveau du serveur ; l'enveloppe (timeout,
  plafond de lignes) est appliquée par le wrapper d'outil **et** par le rôle.

Périmètre : création et nommage des vues, convention de commentaires, génération
déterministe des outils depuis le catalogue, enveloppe de sûreté, rôle, tests.
**Hors périmètre** : écritures (→ `repository-tools.md`, seul choix dès qu'il y
a écriture), exploration analytique ouverte (→ `text-to-sql.md` sous enveloppe),
documents (→ `rag/*.md`).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `dataaccess-view-per-agent` |
| **Famille** | DATA ACCESS · lecture seule · besoins connus et stables |
| **Base cible détaillée** | PostgreSQL 16+ (variantes §7 pour SQL Server, MySQL, Oracle, SQLite) |
| **Client** | Python 3.12 · SQLAlchemy 2.x async · driver du `DatabaseType` (`psycopg` 3 pour PostgreSQL — cohérent avec `pgvector.md`) |
| **Migrations** | Alembic — une révision par ajout/modification de vue, SQL brut |
| **Paramètres STACK.md** | `DbAgentRole: readonly`, `DbStatementTimeoutMs: 5000`, `DbMaxRowsReturned: 500`, `DbAllowedSchemas: [agent_views]`, `DbForbiddenStatements`, `DbQueryLogging: full` |
| **Générateur** | `sdda_scripts/gen_view_tools.py` — lit le catalogue (`information_schema` + `obj_description`) → outils Python + squelettes de tool-contracts. 0 token. |

---

## 3. Mapping des concepts SDD_Agents → idiomes SQL / Python

| Concept | Idiome | Notes |
|---|---|---|
| **DATA ACCESS** (`dataAccess[]` de l'IR) | schéma SQL `agent_views` ; une entrée IR = un ensemble de vues `exposedTo` un agent | `envelope` = rôle + `SET` de session + wrapper |
| **Une vue** | `agent_views.v_{agent_slug}__{purpose}` | `agent_slug` en snake_case ; `purpose` = verbe ou nom métier court : `unpaid_invoices`, `customer_summary` |
| **TOOL** `name` | `{agent_slug}_{purpose}` dérivé du nom de la vue | ex. `billing_specialist_unpaid_invoices` |
| **TOOL** `description` | `COMMENT ON VIEW` **intégralement** | rédigé comme du prompt (quand appeler, quand ne pas appeler, ce qui est retourné) ; revu par `dev-prompt`, hashé |
| **TOOL** `input_schema` | colonnes marquées `@filter` dans `COMMENT ON COLUMN` → paramètres optionnels typés ; `@required` pour un filtre obligatoire | égalité stricte ou `IN` ; jamais de prédicat libre |
| **TOOL** `output_schema` | colonnes de la vue avec leurs types SQL → pydantic ; `@pii` → redaction dans la trace | `description` de champ = `COMMENT ON COLUMN` (sans les tags) |
| **TOOL** `side_effect_class` | `read-only` — toujours | une vue n'écrit pas ; le rôle ne peut pas |
| **TOOL** `trust` | `trusted` par défaut ; `untrusted` si une colonne porte `@free-text` (champ saisi par des tiers : commentaires, e-mails, descriptions de tickets) | P8 : un champ texte libre de la base est du texte hostile |
| **TOOL** `errors[]` | `TIMEOUT` (statement_timeout), `TOO_MANY_ROWS` (plafond atteint, résultat tronqué), `AUTH_FAILED` (rôle), `INVALID_FILTER` (valeur hors type) | chaque erreur a un test L2 |
| **Filtrage par identité** | `WHERE tenant_id = current_setting('app.tenant_id')::uuid` **dans la vue** ; posé par `set_config` depuis l'identité de l'appelant | jamais un paramètre d'outil |
| **Enveloppe** `statementTimeoutMs` | `ALTER ROLE agent_ro SET statement_timeout` **et** `SET LOCAL` par transaction | ceinture et bretelles |
| **Enveloppe** `maxRows` | `LIMIT maxRows + 1` dans le wrapper → si `maxRows + 1` lignes, `truncated: true` + erreur `TOO_MANY_ROWS` déclarée | le modèle **sait** qu'il manque des lignes ; il ne compte pas sur un résultat partiel |
| **Enveloppe** `schemas` | `search_path = agent_views` ; `REVOKE ALL ON SCHEMA public` | allowlist, jamais denylist |
| **Enveloppe** `forbidden` | inutile ici : le rôle ne peut pas exécuter de DML/DDL — mais l'AST du SQL généré est quand même vérifié en L0 (`SELECT` unique, tables ∈ `agent_views`) | défense en profondeur |
| **Journal** `DbQueryLogging: full` | span `execute_tool` avec `db.query.text` (paramètres redigés si `@pii`), `db.response.returned_rows` | + `log_statement = 'all'` côté rôle si le serveur le permet |
| **Moindre privilège** (P8) | `GRANT SELECT ON agent_views.v_billing_specialist__* TO agent_ro_billing` — **un rôle par agent** si les agents ont des périmètres différents | l'écart outils exposés / exigés est aussi visible dans `pg_class` |

### 3.1 Une vue, commentée comme du prompt

```sql
-- workspace/src/{AppName}/src/{AppName}/data/views/v_billing_specialist__unpaid_invoices.sql
CREATE OR REPLACE VIEW agent_views.v_billing_specialist__unpaid_invoices
WITH (security_barrier = true) AS
SELECT i.invoice_id,
       i.customer_id,
       c.display_name          AS customer_name,
       i.issued_on,
       i.due_on,
       i.amount_due_eur,
       (current_date - i.due_on) AS days_overdue,
       i.status
FROM   billing.invoices  i
JOIN   crm.customers     c ON c.customer_id = i.customer_id
WHERE  i.status IN ('open', 'overdue')
  AND  i.deleted_at IS NULL                                   -- règle métier BR-2 : soft-delete
  AND  i.tenant_id = current_setting('app.tenant_id', true)::uuid;   -- identité : posée par la session, jamais par le modèle

COMMENT ON VIEW agent_views.v_billing_specialist__unpaid_invoices IS
$$Liste les factures impayées (statut open ou overdue) du tenant courant, une ligne par facture.
Utiliser pour : répondre à « quelles factures sont en retard », « combien doit le client X », calculer un total impayé.
Ne pas utiliser pour : l'historique des factures payées, les avoirs, les détails de lignes de facture (autre outil).
Retourne au plus 500 lignes ; si truncated=true, affiner par customer_id ou status.
Les montants sont en EUR TTC. days_overdue est négatif tant que la facture n'est pas échue.$$;

COMMENT ON COLUMN agent_views.v_billing_specialist__unpaid_invoices.invoice_id    IS '@filter Identifiant de facture, format INV-YYYY-NNNN.';
COMMENT ON COLUMN agent_views.v_billing_specialist__unpaid_invoices.customer_id   IS '@filter Identifiant client (uuid).';
COMMENT ON COLUMN agent_views.v_billing_specialist__unpaid_invoices.customer_name IS '@pii Nom affiché du client.';
COMMENT ON COLUMN agent_views.v_billing_specialist__unpaid_invoices.status        IS '@filter open | overdue.';
COMMENT ON COLUMN agent_views.v_billing_specialist__unpaid_invoices.days_overdue  IS 'Jours de retard ; négatif = pas encore échue.';
```

`security_barrier = true` : empêche le planificateur de pousser une fonction
« fuyante » de l'appelant sous le filtre tenant. `security_invoker` reste
`false` (défaut) : la vue s'exécute avec les droits de **son propriétaire**
(`agent_views_owner`, qui lit `billing.*` et `crm.*`) ; `agent_ro` n'a **que**
`SELECT` sur la vue.

### 3.2 Tags de commentaire (grammaire close)

| Tag | Sur | Effet dans l'outil généré |
|---|---|---|
| `@filter` | colonne | paramètre optionnel, égalité ou `IN` (liste ≤ 20 valeurs) |
| `@required` | colonne | paramètre obligatoire ; la vue ne doit pas être interrogeable sans |
| `@pii` | colonne | valeur redigée dans les spans et les datasets ; `TracePIIPolicy` |
| `@free-text` | colonne | l'outil passe `trust: untrusted` ; valeur enveloppée par `wrap_untrusted` |
| `@unit:{EUR\|%\|days}` | colonne | ajouté à la description du champ |

Tout autre `@tag` → `[DATA_VIEW_UNKNOWN_TAG]` en L0.

### 3.3 Le wrapper d'outil généré

```python
# src/{AppName}/data/tools/billing_specialist_unpaid_invoices.py — GÉNÉRÉ par gen_view_tools.py, ne pas éditer
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from {AppName}.data.envelope import run_view_query
from {AppName}.tools.spec import ToolSpec

SPEC = ToolSpec.from_contract("1-billing-specialist-unpaid-invoices")   # description == COMMENT ON VIEW (hash vérifié)


class Input(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    invoice_id: str | None = Field(default=None, pattern=r"^INV-\d{4}-\d{4}$", description="Identifiant de facture, format INV-YYYY-NNNN.")
    customer_id: str | None = Field(default=None, description="Identifiant client (uuid).")
    status: list[str] | None = Field(default=None, max_length=20, description="open | overdue.")


class Row(BaseModel):
    model_config = ConfigDict(frozen=True)
    invoice_id: str
    customer_id: str
    customer_name: str          # @pii
    issued_on: str
    due_on: str
    amount_due_eur: float
    days_overdue: int
    status: str


class Output(BaseModel):
    model_config = ConfigDict(frozen=True)
    rows: list[Row]
    truncated: bool
    row_count: int


async def billing_specialist_unpaid_invoices(params: Input, *, ctx: ToolContext) -> Output:
    return await run_view_query(
        view="agent_views.v_billing_specialist__unpaid_invoices",
        filters=params.model_dump(exclude_none=True),
        row_model=Row,
        ctx=ctx,                          # porte tenant_id (identité appelant), run_id, tracer
        pii_columns={"customer_name"},
    )
```

`run_view_query` (écrit une fois, testé en L2) : ouvre une transaction sur le
pool `agent_ro`, `set_config('app.tenant_id', ctx.tenant_id, true)`,
`SET LOCAL statement_timeout`, construit `SELECT <cols> FROM <view> WHERE
<col> = $n [AND …] ORDER BY <pk> LIMIT maxRows + 1` avec des **paramètres liés**
(jamais d'interpolation), tronque, émet le span, redige les `@pii`.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── alembic/versions/
│   ├── 0010_agent_views_schema.py         # CREATE SCHEMA agent_views ; rôle owner ; REVOKE public
│   ├── 0011_agent_views_roles.py          # agent_ro (+ agent_ro_{agent} si périmètres distincts) ; ALTER ROLE SET …
│   └── 0012_view_billing_unpaid.py        # op.execute(Path('.../v_billing_specialist__unpaid_invoices.sql').read_text())
└── src/{AppName}/data/
    ├── views/
    │   └── v_{agent_slug}__{purpose}.sql   # CREATE OR REPLACE VIEW + COMMENT ON VIEW + COMMENT ON COLUMN — 1 fichier = 1 vue
    ├── envelope.py                        # run_view_query, pool agent_ro, set_config tenant, LIMIT+1, redaction, span
    ├── catalog.py                         # introspection : vues de agent_views + obj_description + col_description → ViewCatalog
    └── tools/
        └── {agent_slug}_{purpose}.py      # GÉNÉRÉ — Input/Row/Output + fonction outil

workspace/contracts/tools/
└── {n}-{agent-slug}-{purpose}.tool.md     # squelette GÉNÉRÉ (description = commentaire), complété par architect-tools (§7 Exposé à, §4 erreurs)

workspace/src/{AppName}/tests/data/
├── test_catalog_complete.py               # L0 : chaque vue a un COMMENT ON VIEW ≥ 200 caractères, tags connus
├── test_sql_ast.py                        # L0 : chaque .sql = 1 CREATE VIEW + COMMENTs ; contient current_setting('app.tenant_id') ; pas de SELECT *
├── test_envelope.py                       # L2 : LIMIT+1 → truncated ; timeout → TIMEOUT ; rôle non-readonly → refus au démarrage
└── test_{agent_slug}_{purpose}.py         # L2 (network) : happy, vide, filtre invalide, TOO_MANY_ROWS
```

---

## 5. Conventions imposées

1. **Une vue = un fichier `.sql` = une migration = un outil = un tool-contract.**
   La chaîne est régénérable par `gen_view_tools.py` ; éditer un outil généré à
   la main est `[DATA_TOOL_HAND_EDITED]` (empreinte de contenu généré, comme le
   schéma existant).
2. **Nommage** : `agent_views.v_{agent_slug}__{purpose}` — double underscore
   entre agent et besoin, pour que le parsing soit sans ambiguïté. Une vue
   partagée entre agents porte `v_shared__{purpose}` et chaque agent qui l'utilise
   la cite dans son contrat (le `GRANT` est par agent).
3. **`COMMENT ON VIEW` obligatoire, ≥ 200 caractères**, rédigé selon
   `rules/prompt-authoring.md` : quand utiliser / quand ne pas utiliser / ce qui
   est retourné / limites. C'est **du prompt** : il est revu par
   `dev-prompt`, pas seulement par le DBA, et entre dans `tool_schema_hash`.
4. **Pas de `SELECT *`** dans une vue : un ajout de colonne en base changerait
   la surface exposée sans revue.
5. **Le filtre d'identité est dans la vue** (`current_setting('app.tenant_id',
   true)`), même en mono-tenant. `missing_ok = true` + `::uuid` sur `NULL`
   renvoie zéro ligne si la session n'a pas posé le tenant — **fail-closed**.
6. **Filtres = égalité ou `IN` sur colonnes `@filter`.** Pas de `LIKE`, pas de
   plages, pas d'expressions : si le besoin existe, c'est une **autre vue**
   (`v_billing__invoices_overdue_more_than`, avec `@required days`) ou un
   `repository-tool`.
7. **`ORDER BY` déterministe** (clé primaire de la vue) dans le wrapper, pour
   que « les 500 premières » soient les mêmes d'un run à l'autre (evals
   reproductibles).
8. **Le rôle est vérifié au démarrage** : `SHOW transaction_read_only` = `on`,
   `current_user` ∈ rôles attendus, sinon fail-fast `[DATA_ROLE_NOT_READONLY]`.
   Un `DbAgentRole` différent de `readonly` sur cette stack est une
   contradiction : `text-to-sql`/`repository-tools` sont faits pour ça.
9. **Agrégats = vues d'agrégats.** Si l'agent doit compter ou sommer, la vue
   le fait (`v_billing_specialist__unpaid_totals_by_customer`). Faire compter
   le modèle sur 500 lignes tronquées produit un chiffre faux avec assurance.
10. **Types sérialisés explicitement** : `date`/`timestamptz` → ISO 8601 texte,
    `numeric` → `float` (ou `str` si précision comptable, tag `@unit`), `uuid`
    → `str`, `NULL` → `null` avec description qui dit ce que ça signifie.

---

## 6. Commande de smoke

Base de test requise (`network`), 0 token :

```bash
cd workspace/src/{AppName}
uv run alembic upgrade head
uv run python -m sdda_scripts.gen_view_tools --check
#   → régénère en mémoire les outils depuis le catalogue et compare aux fichiers : exit 0 si identiques, [DATA_TOOL_STALE] sinon
uv run python -m {AppName}.data.envelope --ping
#   → connexion agent_ro ; transaction_read_only == on ; search_path == agent_views ; statement_timeout == DbStatementTimeoutMs
#   → tentative `INSERT INTO agent_views…` → doit échouer (permission denied) ; tentative SELECT hors agent_views → doit échouer
#   → pour chaque vue : SELECT … LIMIT 1 sans tenant posé → 0 ligne (fail-closed) ; avec tenant de test → OK
uv run pytest tests/data -q
```

Smoke Timeout : 60 s.

---

## 7. Pièges connus

1. **Le commentaire dérive, le hash ne bouge pas.** Modifier `COMMENT ON VIEW`
   directement en base (hors migration) change la description que le modèle
   verra **à la prochaine génération** mais pas le contrat versionné.
   `gen_view_tools --check` compare base ↔ fichiers ↔ contrat ; en CI, c'est le
   fichier `.sql` qui fait foi.
2. **`security_invoker = true` par réflexe** (« plus sûr ») casse tout : la vue
   s'exécuterait avec les droits d'`agent_ro`, qui n'a pas accès à `billing.*`.
   La sécurité vient de « `agent_ro` ne voit que la vue », pas de l'invoker.
3. **`current_setting('app.tenant_id')` sans `missing_ok`** lève une erreur si
   la session n'a rien posé — c'est bien, mais le message d'erreur remonte au
   modèle comme texte. Préférer `missing_ok = true` → `NULL` → zéro ligne, et
   un test L2 qui le vérifie.
4. **Vue lente = timeout = « l'outil ne marche pas ».** Une jointure sans index
   sur 10 M de lignes dépasse 5 s ; le modèle conclut que l'outil est cassé et
   invente. `EXPLAIN` de chaque vue avec le tenant de test dans le smoke ;
   index sur les colonnes `@filter` **et** sur `tenant_id` des tables sources.
5. **Le plafond de lignes masque l'information.** 500 factures sur 2 300 et le
   modèle dit « vous avez 500 factures impayées ». `truncated: true` doit être
   dans le schéma **et** dans la description (« si truncated, affiner… »), et
   l'agrégat doit avoir sa vue (§5.9).
6. **Vues sur vues.** `v_a` qui lit `v_b` qui lit `v_c` : un changement de `v_c`
   change silencieusement `v_a`. Chaque vue d'agent lit des **tables**, pas
   d'autres vues d'agent (les vues techniques internes hors `agent_views` sont
   tolérées).
7. **Champs texte libre non taggés.** La colonne `ticket_description` remplie
   par des clients est du texte hostile ; sans `@free-text`, l'outil est
   `trusted` et le contenu arrive nu dans le contexte. Le lint L0 signale toute
   colonne `text` sans tag `@pii`/`@free-text`/`@filter` → avertissement
   `[DATA_TEXT_COLUMN_UNTAGGED]`.
8. **Décimaux en `float`.** `amount_due_eur: 1234.5599999` dans la réponse du
   modèle. Sérialiser `numeric` en `str` avec `@unit:EUR`, ou arrondir
   explicitement dans la vue (`round(..., 2)`).
9. **Fuseaux.** `timestamptz` → ISO avec offset ; `timestamp` sans zone est
   ambigu pour le modèle. Convertir dans la vue (`AT TIME ZONE 'UTC'`) et le
   dire dans le commentaire.
10. **Un rôle pour tous les agents.** Pratique, mais l'agent `support` peut
    alors lire `v_billing_specialist__*` si un prompt le lui suggère. Dès que
    deux agents ont des périmètres distincts : un rôle par agent, `GRANT` par
    vue, et le pool de connexions par agent.
11. **Le mot de passe du rôle dans la migration** — jamais. `CREATE ROLE agent_ro
    LOGIN` dans la migration ; `ALTER ROLE agent_ro PASSWORD …` par l'opérateur
    depuis `STACK.md`, ou authentification par certificat / IAM.

### Variantes par `DatabaseType`

| Base | Vue + commentaire | Identité de session | Timeout | Lecture seule |
|---|---|---|---|---|
| **PostgreSQL** | `COMMENT ON VIEW/COLUMN` | `set_config('app.tenant_id', …, true)` | `SET LOCAL statement_timeout` | `default_transaction_read_only` sur le rôle |
| **SQL Server** | `sp_addextendedproperty 'MS_Description'` sur vue et colonnes | `SESSION_CONTEXT(N'tenant_id')` via `sp_set_session_context` | `SET LOCK_TIMEOUT` + timeout côté driver (`aioodbc`) | rôle membre de `db_datareader` restreint au schéma `agent_views` + `DENY` sur le reste |
| **MySQL / MariaDB** | `COMMENT` de vue **inexistant** → commentaire dans une table `agent_views_meta(view_name, description)` maintenue par la migration | variable de session `@tenant_id` (`SET @tenant_id = ?`) | `SET SESSION max_execution_time` | `GRANT SELECT ON agent_views.*` uniquement |
| **Oracle** | `COMMENT ON TABLE view IS …` (fonctionne sur les vues) + `COMMENT ON COLUMN` | `SYS_CONTEXT('APP_CTX','TENANT_ID')` via package de contexte | `RESOURCE_MANAGER` ou timeout driver | rôle `SELECT` sur les vues ; VPD possible |
| **SQLite** | pas de commentaires → `agent_views_meta` | pas de contexte de session → filtre par **paramètre du wrapper uniquement**, jamais exposé au modèle | `PRAGMA busy_timeout` + timeout driver | `?mode=ro` dans l'URI ; dev/POC uniquement |

Dans tous les cas, `gen_view_tools.py` lit la description depuis la source
canonique de la base (ou `agent_views_meta`) : la chaîne « commentaire →
description → hash » reste la même.
