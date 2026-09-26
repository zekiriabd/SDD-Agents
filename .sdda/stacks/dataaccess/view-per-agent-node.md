# Stack: view-per-agent-node (dataaccess)

Stack ID: dataaccess-view-per-agent-node
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: typescript
Scope: stratégie **DATA ACCESS** la plus sûre, côté **Node/TypeScript** — une vue SQL dédiée par agent et par besoin, exposée comme outil `read-only` dont la description est le commentaire SQL de la vue ; enveloppe de sûreté complète (rôle readonly, timeout, plafond de lignes, filtre d'identité dans la vue). Pendant Node de `dataaccess/view-per-agent.md` : **les vues, les commentaires, la grammaire de tags, les rôles et les règles sont identiques** ; seuls le wrapper d'outil et son schéma (Zod au lieu de pydantic) changent. Variante PostgreSQL. Pas de `.libs.json` : `pg`, `zod` et `node-pg-migrate` sont portés par `vectorstore/pgvector-node.libs.json` (même pilote, même outil de migration, un seul pool par rôle).

---

## 1. Rôle et périmètre

La thèse de `dataaccess/view-per-agent.md` §1 ne dépend d'aucun langage : **la
surface est définie en SQL par un humain**, **le commentaire de la vue est la
description de l'outil**, **le filtre d'identité est dans la vue**, **le rôle est
en lecture seule au niveau du serveur**. Un agent Node qui interroge une base
de production doit répondre à la même question — « qu'est-ce que le modèle ne
pourra jamais atteindre ? » — par la même construction.

Ce qui change en Node, et qui justifie une fiche :

- le typage est effacé à l'exécution : l'`Input` de l'outil n'est une frontière
  que parce que **Zod le parse** avant la requête, et `Row` n'est `Untrusted`
  (colonnes `@free-text`) que parce que le parsing le **marque** ;
- le pilote `pg` rend `numeric` et `bigint` en **chaîne** et `date` en `Date`
  JavaScript à minuit **local** : la sérialisation de §5.10 de la fiche Python
  doit être écrite explicitement, sans quoi le modèle lit un montant faux ou une
  date décalée d'un jour ;
- **aucun générateur** n'existe côté Node : `gen_view_tools.py` produit des
  outils Python. Les wrappers TypeScript sont écrits par `dev-data` depuis le
  catalogue (§3.3), et relus comme du code.

Périmètre, hors périmètre, variantes par `DatabaseType` : ceux de la fiche
Python (§1 et « Variantes »). Seule la variante PostgreSQL est décrite en Node ;
les autres sont **non vérifiées** ici.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `dataaccess-view-per-agent-node` |
| **Famille** | DATA ACCESS · lecture seule · besoins connus et stables |
| **Base cible** | PostgreSQL 16+ |
| **Client** | Node 22 · TypeScript 6.0.x · `pg` (mêmes pins que `vectorstore/pgvector-node.libs.json`) |
| **Migrations** | `node-pg-migrate`, migrations **SQL** sous `data/migrations/` — une par ajout/modification de vue |
| **Paramètres STACK.md** | `DbAgentRole: readonly`, `DbStatementTimeoutMs: 5000`, `DbMaxRowsReturned: 500`, `DbAllowedSchemas: [agent_views]`, `DbForbiddenStatements`, `DbQueryLogging: full` |
| **Générateur** | **aucun en TypeScript** — `gen_view_tools.py` est Python. `dev-data` écrit les wrappers depuis le catalogue ; `data/catalog.ts` les compare au catalogue réel au démarrage (§5.2) |

---

## 3. Mapping des concepts SDD_Agents → idiomes SQL / TypeScript

La table de `dataaccess/view-per-agent.md` §3 s'applique **entièrement** :
schéma `agent_views`, nommage `v_{agent_slug}__{purpose}`, nom d'outil
`{agent_slug}_{purpose}`, description = `COMMENT ON VIEW` intégral, filtres =
colonnes `@filter`, sortie = colonnes de la vue, `side_effect_class: read-only`,
`trust` selon `@free-text`, erreurs `TIMEOUT` / `TOO_MANY_ROWS` / `AUTH_FAILED`
/ `INVALID_FILTER`, filtre d'identité dans la vue, `LIMIT maxRows + 1`,
`search_path = agent_views`, rôle par agent. La vue de §3.1 et la grammaire de
tags de §3.2 (`@filter`, `@required`, `@pii`, `@free-text`, `@unit:…`) sont
reprises **mot pour mot** : une même base sert indifféremment une application
Python et une application Node.

### 3.1 Le wrapper d'outil

```ts
// workspace/src/{AppName}/data/tools/billing-specialist-unpaid-invoices.ts — écrit par dev-data depuis le catalogue
import { z } from "zod";
import { runViewQuery } from "../envelope.js";
import { toolSpec } from "#app/tools/spec.js";

export const SPEC = toolSpec("1-billing-specialist-unpaid-invoices");   // description == COMMENT ON VIEW (hash vérifié au démarrage)

export const Input = z.strictObject({
  invoice_id: z.string().regex(/^INV-\d{4}-\d{4}$/).optional(),
  customer_id: z.uuid().optional(),
  status: z.array(z.enum(["open", "overdue"])).max(20).optional(),
});

export const Row = z.strictObject({
  invoice_id: z.string(),
  customer_id: z.string(),
  customer_name: z.string(),                 // @pii — redigé dans le span
  issued_on: z.string(),                     // ISO 8601, sérialisé PAR LA VUE (to_char) — cf. §5.3
  due_on: z.string(),
  amount_due_eur: z.string(),                // numeric → chaîne, @unit:EUR — jamais number
  days_overdue: z.coerce.number().int(),
  status: z.string(),
});

export const billingSpecialistUnpaidInvoices = (params: unknown, ctx: ToolContext) =>
  runViewQuery({
    view: "agent_views.v_billing_specialist__unpaid_invoices",
    orderBy: "invoice_id",                   // clé déterministe (§5.7 de la fiche Python)
    filters: Input.parse(params),            // la frontière : un argument hors schéma est INVALID_FILTER, pas une requête
    row: Row,
    ctx,                                     // tenantId (identité appelant), runId, tracer
    piiColumns: ["customer_name"],
    freeTextColumns: [],                     // non vide → sortie enveloppée wrapUntrusted, trust: untrusted
  });
```

`z.strictObject` et non `z.object` : un argument inconnu (`tenant_id` que le
modèle aurait inventé) doit être **refusé**, pas ignoré en silence.

### 3.2 L'enveloppe — écrite une fois, testée en L2

```ts
// workspace/src/{AppName}/data/envelope.ts
export async function runViewQuery<R>(q: ViewQuery<R>): Promise<ViewResult<R>> {
  const cols = Object.keys(q.row.shape);                           // colonnes = schéma, jamais SELECT *
  const where = Object.entries(q.filters).filter(([, v]) => v !== undefined);
  const params: unknown[] = [];
  const clauses = where.map(([col, v]) => {
    assertIdent(col, cols);                                        // colonne connue, identifiant SQL sûr
    params.push(v);
    return Array.isArray(v) ? `${quoteIdent(col)} = ANY($${params.length})` : `${quoteIdent(col)} = $${params.length}`;
  });
  params.push(settings.dbMaxRowsReturned + 1);
  const sql = `SELECT ${cols.map(quoteIdent).join(", ")} FROM ${q.view}` +
              (clauses.length ? ` WHERE ${clauses.join(" AND ")}` : "") +
              ` ORDER BY ${quoteIdent(q.orderBy)} LIMIT $${params.length}`;
  return withAgentTx(q.ctx, async (c) => {                          // BEGIN READ ONLY ; set_config tenant + statement_timeout (portée tx)
    const rows = (await c.query(sql, params)).rows;
    const truncated = rows.length > settings.dbMaxRowsReturned;
    const parsed = rows.slice(0, settings.dbMaxRowsReturned).map((r) => q.row.parse(r));
    // span sdda.data.query : db.query.text = `sql` (paramétré), jamais `params` ; @pii redigés
    return { rows: q.freeTextColumns.length ? parsed.map((r) => markUntrusted(r, q.freeTextColumns)) : parsed,
             truncated, row_count: parsed.length };
  });
}
```

La seule interpolation de la fonction porte sur des **identifiants** issus du
schéma Zod (jamais des valeurs) et passe par `assertIdent` + `quoteIdent` ; les
valeurs sont toutes liées (`$n`). `truncated: true` accompagne l'erreur déclarée
`TOO_MANY_ROWS` quand le contrat le demande.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/data/                      # zone dev-data
├── migrations/
│   ├── {ts}_agent-views-schema.sql                # CREATE SCHEMA agent_views ; rôle owner ; REVOKE public
│   ├── {ts}_agent-views-roles.sql                 # agent_ro (+ agent_ro_{agent}) ; ALTER ROLE SET … ; jamais de PASSWORD
│   └── {ts}_view-{agent_slug}--{purpose}.sql      # -- Up : CREATE OR REPLACE VIEW + COMMENT ON VIEW/COLUMN (1 fichier = 1 vue)
├── envelope.ts                                    # runViewQuery, pool agent_ro, withAgentTx, LIMIT+1, redaction, span
├── catalog.ts                                     # introspection agent_views + obj_description/col_description → ViewCatalog
├── tools/{agent-slug}-{purpose}.ts                # Input / Row (Zod) + fonction outil
└── tests/
    ├── catalog-complete.test.ts                   # L0 : COMMENT ON VIEW ≥ 200 caractères, tags connus
    ├── sql-ast.test.ts                            # L0 : 1 CREATE VIEW + COMMENTs par .sql ; current_setting('app.tenant_id') présent ; pas de SELECT *
    ├── envelope.test.ts                           # L2 : LIMIT+1 → truncated ; timeout → TIMEOUT ; rôle non readonly → refus au démarrage
    └── {agent-slug}-{purpose}.test.ts             # describe("network …") : happy, vide, filtre invalide, TOO_MANY_ROWS
```

Les squelettes de tool-contracts (`workspace/pipeline/contracts/tools/`) restent
écrits comme en Python : c'est `architect-data` / `architect-tools` qui les
produisent, pas le langage de l'application.

---

## 5. Conventions imposées

Les règles 1 à 10 de `dataaccess/view-per-agent.md` §5 s'appliquent telles
quelles. En Node :

1. **Zod au bord, dans les deux sens** : `Input.parse` avant la requête,
   `Row.parse` après ; une ligne qui ne parse pas est `[TOOL_CONTRACT_FAILED]`,
   pas une donnée transmise « à peu près ».
2. **Catalogue vérifié au démarrage** : `catalog.ts` relit les vues et
   commentaires de `agent_views` et compare le `sha256` du `COMMENT ON VIEW` au
   hash de la description du contrat ; divergence → `[TOOL_SCHEMA_DRIFT]`,
   code 8. Faute de générateur TypeScript, c'est ce contrôle qui tient la chaîne
   « commentaire → description → hash » que `gen_view_tools --check` tient en
   Python.
3. **Types sérialisés PAR LA VUE** : `to_char(d, 'YYYY-MM-DD')` pour `date`,
   `to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')` pour
   `timestamptz`, `numeric` laissé en chaîne. Laisser `pg` convertir une `date`
   en `Date` JavaScript la place à minuit **local**, et `toISOString()` la
   décale d'un jour à l'est de Greenwich.
4. **`z.strictObject`** pour `Input` : aucun argument inconnu accepté.
5. **Un pool par rôle d'agent** si les périmètres diffèrent (piège 10 de la
   fiche Python) ; le pool est choisi par `ctx.agentId`, jamais par un argument.
6. **Aucun `pool.query` direct** hors `envelope.ts` — grep L0.

---

## 6. Commande de smoke

Base de test requise (`network`), 0 token :

```bash
cd workspace/src/{AppName}
pnpm exec node-pg-migrate up -m data/migrations
pnpm build
node dist/data/envelope.js --ping
#   → connexion agent_ro ; transaction_read_only == on ; search_path == agent_views ; statement_timeout == DbStatementTimeoutMs
#   → INSERT INTO agent_views… doit échouer (permission denied) ; SELECT hors agent_views doit échouer
#   → pour chaque vue : SELECT … LIMIT 1 sans tenant posé → 0 ligne (fail-closed) ; avec tenant de test → OK
#   → hash(COMMENT ON VIEW) == hash de la description du contrat pour chaque outil   sinon [TOOL_SCHEMA_DRIFT]
npx --no-install vitest run data
```

Smoke Timeout : 60 s.

---

## 7. Contrat d'exécution

L'application Node implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** — mêmes commandes, même flux NDJSON `RunEvent`, mêmes codes de
sortie — et les trois points de `serving/cli.md` §3.5 :

1. `run --json --input-file -` lit l'entrée sur stdin ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'app sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil) et le retrieval
   figé depuis le même dossier, sans aucun appel réseau d'outil ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished`.

Pour cette fiche : en isolement, chaque outil de vue est servi par
`SDDA_EVAL_FIXTURES/tools/{outil}.jsonl` — lignes `{"tool", "args", "result"}`,
`{"tool", "args", "error": {"code", "message"}}`, réponse par défaut sans
`args`, format repris à l'identique du squelette Python — et **le pool
`agent_ro` n'est pas construit** : aucune connexion à la base. Un outil de vue
sans fixture fait refuser le démarrage (code 8, `[CONFIG_INVALID]`) ; un appel
qu'aucune ligne ne couvre rend l'erreur `TOOL_FIXTURE_MISSING`, pas une liste
vide — une liste vide se confondrait avec « aucune facture impayée ». La
comparaison des arguments se fait sur un JSON **canonique** (clés triées),
calculé des deux côtés par la même fonction TypeScript.

Lancement : `node workspace/src/{AppName}/dist/cli.js` après `pnpm build`
(point d'entrée exact, `lang/typescript.md` §4), ou le `bin` du package ; les
runners le choisissent par `--executor cli` ou l'imposent par
`--executor cmd:<commande>`.

---

## 8. Pièges connus

Les pièges 1 à 11 de `dataaccess/view-per-agent.md` §7 valent tous. Propres à
Node :

1. **`numeric` en `number`** : `parseFloat("1234.56")` passe, `0.1 + 0.2` non ;
   un total recalculé par le code applicatif devient faux. Chaîne jusqu'au
   modèle, agrégats dans une vue.
2. **`bigint` / `count(*)`** revient en chaîne : `z.coerce.number()` si la
   valeur tient dans 2^53, chaîne sinon — décidé dans le schéma, pas au hasard.
3. **`date` décalée d'un jour** (§5.3).
4. **`z.object` au lieu de `z.strictObject`** : l'argument `tenant_id` inventé
   par le modèle est ignoré, le test passe, et le jour où quelqu'un le branche
   la fuite est ouverte.
5. **Tableaux de filtres vides** : `status: []` → `= ANY('{}')` → zéro ligne
   « trouvée ». Un tableau vide est refusé par le schéma (`.min(1)`) ou retiré
   du filtre.
