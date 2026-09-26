# Stack: view-per-agent-dotnet (dataaccess)

Stack ID: dataaccess-view-per-agent-dotnet
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: csharp
Scope: stratégie **DATA ACCESS** la plus sûre côté **.NET** — une vue SQL dédiée par agent et par besoin, exposée comme outil `read-only` (`AIFunction`) dont la description est le commentaire SQL de la vue ; filtre d'identité **dans la vue**, enveloppe de sûreté (rôle readonly, timeout, plafond de lignes, schéma allowlisté) appliquée par un wrapper Npgsql **paramétré** et par le rôle. Variante PostgreSQL. Les vues et leurs commentaires sont ceux de `dataaccess/view-per-agent.md`. Suppose `lang/csharp.md` et `vectorstore/pgvector-dotnet.md` (même driver). Pas de `.libs.json` : Npgsql et dbup-postgresql sont portés par `vectorstore/pgvector-dotnet.libs.json`.

---

## 1. Rôle et périmètre

Le pendant .NET de `dataaccess/view-per-agent.md`, qui reste la référence du
**pourquoi** : la question n'est pas « comment le modèle va-t-il écrire le
SQL ? » mais « qu'est-ce que le modèle ne pourra jamais atteindre ? ». La
réponse est **dans la base**, pas dans le langage : la surface est définie en
SQL par un humain, le commentaire de la vue est la description de l'outil, le
filtre d'identité est dans la vue, le rôle est en lecture seule au niveau du
serveur.

> **Ce qui est commun aux langages, et ce qui ne l'est pas.** Les fichiers
> `data/views/v_{agent_slug}__{purpose}.sql` — `CREATE VIEW`, `COMMENT ON VIEW`,
> `COMMENT ON COLUMN` avec la grammaire close de tags (`@filter`, `@required`,
> `@pii`, `@free-text`, `@unit:`) — sont **identiques** à ceux de la fiche Python.
> Ce qui change : le wrapper (Npgsql au lieu de SQLAlchemy/psycopg), les types
> d'outil (records C# + `AIFunction` au lieu de pydantic), et — il faut le
> dire — **l'outillage de génération**, qui n'existe qu'en Python (§2.1).

Périmètre : vues et commentaires (renvoi), wrapper d'enveloppe, outils C#, rôle,
tests. **Hors périmètre** : écritures (`repository-tools`), exploration ouverte
(`text-to-sql` sous enveloppe), documents (`rag/hybrid-dotnet.md`).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `dataaccess-view-per-agent-dotnet` |
| **Famille** | DATA ACCESS · lecture seule · besoins connus et stables |
| **Base** | PostgreSQL 16+ (les variantes SQL Server / MySQL / Oracle / SQLite de `view-per-agent.md` §7 ne sont **pas** décrites en .NET ici) |
| **Client** | C# 14 / `net10.0` · `Npgsql` 10.0.x — `NpgsqlDataSource` dédié au rôle `agent_ro` |
| **Migrations** | `dbup-postgresql` — un script par vue, SQL brut (le même fichier `.sql` que la vue) |
| **Paramètres STACK.md** | `DbAgentRole: readonly`, `DbStatementTimeoutMs: 5000`, `DbMaxRowsReturned: 500`, `DbAllowedSchemas: [agent_views]`, `DbForbiddenStatements`, `DbQueryLogging: full` |

### 2.1 Honnêteté sur l'outillage

`sdda_scripts/gen_view_tools.py` (catalogue `information_schema` +
`obj_description` → outils) **génère du Python**, et
`sdda_scripts/validate_envelope.py` vérifie l'enveloppe par analyse `ast` de
code Python. Pour une application C#, à la date de cette fiche :

- les outils `data/tools/*.cs` sont écrits par `dev-data` **depuis les contrats
  `{n}-data-*`**, pas régénérés depuis le catalogue ;
- l'enveloppe C# n'est **pas** vérifiée par `validate_envelope` (seul le SQL des
  vues l'est) — c'est le test L2 `EnvelopeTests` (§4) qui porte la preuve, et la
  revue `review-safety` qui la relit.

La conséquence est écrite pour qu'on ne la découvre pas en G3 : la dérive
« commentaire de vue ↔ description de l'outil » que `gen_view_tools --check`
attrape en Python est attrapée ici par un test L0 (`ViewCatalogTests`) qui
recalcule le hash du `COMMENT ON VIEW` lu dans le fichier `.sql` et le compare à
la description portée par le contrat.

---

## 3. Mapping des concepts SDD_Agents → idiomes SQL / .NET

| Concept | Idiome | Notes |
|---|---|---|
| **Une vue** | `agent_views.v_{agent_slug}__{purpose}` — `view-per-agent.md` §3.1 | `security_barrier = true`, pas de `SELECT *` |
| **TOOL** `name` | `{agent_slug}_{purpose}` | nom du contrat, vu par le modèle |
| **TOOL** `description` | `COMMENT ON VIEW` **intégralement**, passé à `AIFunctionFactory.Create(…, name, description)` | le texte vient du contrat (hashé), jamais réécrit en C# |
| **TOOL** `input_schema` | `record Input` : une propriété nullable par colonne `@filter`, `required` pour `@required` ; listes `IN` bornées à 20 | égalité ou `IN`, jamais de prédicat libre |
| **TOOL** `output_schema` | `record Row` (colonnes typées) + `record Output(IReadOnlyList<Row> Rows, bool Truncated, int RowCount)` | `@pii` → redigé dans le span |
| `side_effect_class` | `read-only`, toujours | |
| `trust` | `trusted`, ou `untrusted` si une colonne `@free-text` — les valeurs de ces colonnes sont rendues en `Untrusted` | P8 |
| `errors[]` | `TIMEOUT` (SQLSTATE `57014`), `TOO_MANY_ROWS` (`LIMIT maxRows + 1`), `AUTH_FAILED` (`28P01`, `42501`), `INVALID_FILTER` (validation d'entrée) | chaque erreur a un test L2 |
| **Filtrage par identité** | `WHERE … tenant_id = current_setting('app.tenant_id', true)::uuid` **dans la vue** ; posé par `set_config('app.tenant_id', @t, true)` depuis `ToolContext` | jamais un paramètre d'outil |
| **Enveloppe** `statementTimeoutMs` | `ALTER ROLE agent_ro SET statement_timeout` **et** `set_config('statement_timeout', @st, true)` par transaction **et** `NpgsqlCommand.CommandTimeout` | trois niveaux : serveur, session, client |
| **Enveloppe** `maxRows` | `LIMIT @max_plus_one` → si `maxRows + 1` lignes, `Truncated = true` + erreur déclarée `TOO_MANY_ROWS` | le modèle sait qu'il manque des lignes |
| **Enveloppe** `schemas` | `search_path = agent_views` sur le rôle ; nom de vue pris dans une **allowlist compilée**, jamais dans une entrée | |
| **Journal** `DbQueryLogging: full` | span `sdda.data.query {view}` avec `db.query.text` **paramétré** | cf. `observability/otel-genai-dotnet.md` |

### 3.1 Le wrapper d'enveloppe — écrit une fois, testé en L2

```csharp
// data/Envelope.cs — namespace {AppName}.Data
public sealed class Envelope(AgentViewsDataSource source, IOptions<DataOptions> options, Tracing tracing)
{
    public async Task<Output<TRow>> QueryViewAsync<TRow>(
        ViewSpec view,                                   // nom, colonnes, clé d'ordre, colonnes @filter / @pii — issu du contrat, compilé
        IReadOnlyDictionary<string, object?> filters,    // clés ⊆ view.FilterColumns, sinon INVALID_FILTER
        Func<NpgsqlDataReader, TRow> readRow,
        ToolContext ctx,
        CancellationToken ct)
    {
        var unknown = filters.Keys.Except(view.FilterColumns, StringComparer.Ordinal).ToList();
        if (unknown.Count > 0) return Output<TRow>.Error("INVALID_FILTER", unknown);

        var max = options.Value.MaxRowsReturned;
        // Le NOM de vue et les NOMS de colonnes viennent de ViewSpec (allowlist compilée) ;
        // seules les VALEURS de filtre passent en paramètres liés.
        var sql = SqlBuilder.Select(view, filters.Keys) + " ORDER BY " + view.OrderKey + " LIMIT @max_plus_one";

        using var span = tracing.DataQuery(view.Name, sql, filters.Keys, view.PiiColumns);
        await using var conn = await source.DataSource.OpenConnectionAsync(ct);
        await using var tx = await conn.BeginTransactionAsync(ct);
        await SetLocalAsync(conn, tx, "app.tenant_id", ctx.TenantId, ct);                       // identité : de l'appelant
        await SetLocalAsync(conn, tx, "statement_timeout", options.Value.StatementTimeoutMs, ct);

        await using var cmd = new NpgsqlCommand(sql, conn, tx) { CommandTimeout = options.Value.CommandTimeoutSeconds };
        foreach (var (col, value) in filters) cmd.Parameters.AddWithValue(col, value ?? DBNull.Value);
        cmd.Parameters.AddWithValue("max_plus_one", max + 1);

        var rows = new List<TRow>(capacity: Math.Min(max + 1, 64));
        try
        {
            await using var reader = await cmd.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct)) rows.Add(readRow(reader));
        }
        catch (PostgresException e) when (e.SqlState == PostgresErrorCodes.QueryCanceled)
        {
            return Output<TRow>.Error("TIMEOUT");
        }
        await tx.CommitAsync(ct);

        var truncated = rows.Count > max;
        if (truncated) rows.RemoveAt(rows.Count - 1);
        span.Result(rows.Count, truncated);
        return truncated ? Output<TRow>.Truncated(rows, "TOO_MANY_ROWS") : Output<TRow>.Ok(rows);
    }
}
```

Trois choix qui se paient cher s'ils sautent :

1. **Les identifiants SQL ne viennent jamais d'une entrée.** Un paramètre lié
   ne peut pas porter un nom de table ou de colonne ; la tentation est alors de
   concaténer ce que le modèle a fourni. Ici, le nom de vue et les colonnes
   viennent de `ViewSpec`, une donnée compilée depuis le contrat, et les clés de
   filtre sont confrontées à l'allowlist **avant** de construire le SQL.
2. **Le tenant est posé par la transaction, pas par la requête.** La vue lit
   `current_setting('app.tenant_id', true)` ; si le wrapper oublie
   `set_config`, `missing_ok` rend `NULL`, `::uuid` rend `NULL`, la vue rend
   zéro ligne : **fail-closed**. C'est testé en L2 sans tenant posé.
3. **`SqlState` et non le message.** `57014` (`query_canceled`) est un code
   stable ; un message d'erreur PostgreSQL dépend de `lc_messages` du serveur.

### 3.2 Un outil de vue

```csharp
// data/tools/BillingSpecialistUnpaidInvoices.cs — écrit par dev-data depuis le contrat 1-billing-specialist-unpaid-invoices
public sealed record UnpaidInvoicesInput
{
    [RegularExpression(@"^INV-\d{4}-\d{4}$")] public string? InvoiceId { get; init; }   // @filter
    public Guid? CustomerId { get; init; }                                              // @filter
    [MaxLength(20)] public IReadOnlyList<string>? Status { get; init; }                 // @filter, IN
}

public sealed record UnpaidInvoiceRow(
    string InvoiceId, Guid CustomerId, string CustomerName /* @pii */,
    DateOnly IssuedOn, DateOnly DueOn, string AmountDueEur /* @unit:EUR, numeric → string */,
    int DaysOverdue, string Status);
```

L'outil est enregistré par `ToolRegistry.Bind(spec, fn)` (`framework/ms-agent-framework.md`
§3.4) : `name` et `description` viennent du contrat, la fonction appelle
`Envelope.QueryViewAsync` avec le `ViewSpec` de la vue. `ToolContext` n'est
**pas** un paramètre de l'`AIFunction` : il est capturé par la fermeture, donc
absent du schéma que voit le modèle.

`numeric` monétaire en `string` (ou `decimal` sérialisé en chaîne) : un `double`
fait écrire `1234.5599999` au modèle (`view-per-agent.md` §7.8).

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── data/
│   ├── views/v_{agent_slug}__{purpose}.sql    # IDENTIQUE à view-per-agent.md : CREATE VIEW + COMMENT ON VIEW/COLUMN — 1 fichier = 1 vue
│   ├── migrations/                            # scripts DbUp : 0010_agent_views_schema.sql, 0011_agent_views_roles.sql, 0012_view_*.sql (= le .sql de la vue)
│   ├── AgentViewsDataSource.cs                # NpgsqlDataSource du rôle agent_ro
│   ├── Envelope.cs                            # §3.1
│   ├── SqlBuilder.cs                          # SELECT <colonnes de ViewSpec> FROM <vue> WHERE <col> = @col | = ANY(@col)
│   ├── ViewSpec.cs                            # nom, colonnes, OrderKey, FilterColumns, PiiColumns, FreeTextColumns
│   ├── FixtureDataTools.cs                    # isolement L4 : sert SDDA_EVAL_FIXTURES/tools/{outil}.jsonl (§8)
│   └── tools/{AgentSlug}{Purpose}.cs          # Input / Row / fonction outil
└── tests/
    └── data/
        ├── ViewCatalogTests.cs                # L0 : chaque .sql = 1 CREATE VIEW + COMMENTs ≥ 200 car. ; tags connus ; current_setting('app.tenant_id', true) ; pas de SELECT * ; hash du COMMENT == contrat
        ├── SqlBuilderTests.cs                 # L1 : une clé de filtre hors allowlist est refusée ; seules des valeurs sont paramétrées
        ├── EnvelopeTests.cs                   # L2 : LIMIT+1 → truncated ; 57014 → TIMEOUT ; rôle non readonly → refus au démarrage
        └── UnpaidInvoicesNetworkTests.cs      # L2 [Trait("Category","network")] : happy, vide, filtre invalide, TOO_MANY_ROWS, sans tenant → 0 ligne
```

Les tests de contrat d'outil suivent la convention de G3 (`lang/csharp.md`
§5.4) : fichiers `*Tests.cs`, un cas de contrat = un `[Theory]` dont le nom
affiché contient l'identifiant du cas (`[InlineData("happy-1")]`), tests réseau
dans une classe dont le nom contient `Network` et portant
`[Trait("Category","network")]`.

---

## 5. Conventions imposées

Les dix règles de `view-per-agent.md` §5 valent telles quelles (une vue = un
`.sql` = une migration = un outil = un contrat ; nommage à double underscore ;
`COMMENT ON VIEW` ≥ 200 caractères rédigé comme du prompt ; pas de `SELECT *` ;
filtre d'identité dans la vue, fail-closed ; filtres = égalité ou `IN` ;
`ORDER BY` déterministe ; rôle vérifié au démarrage ; agrégats = vues
d'agrégats ; types sérialisés explicitement). Propres à .NET :

1. **Aucune concaténation d'une valeur dans un `CommandText`.** Les seuls
   fragments concaténés sont des identifiants tirés de `ViewSpec`. Test L1 :
   `SqlBuilder` ne produit que des `@param` pour les valeurs.
2. **`IN` s'écrit `= ANY(@col)`** avec un tableau Npgsql lié, borné à 20
   éléments par validation d'entrée — jamais une liste `IN (…)` construite.
3. **Un `NpgsqlDataSource` par rôle**, et un rôle par agent dès que les
   périmètres diffèrent (`view-per-agent.md` §7.10) : le pool est choisi par
   l'agent courant de `ToolContext`, pas par l'outil.
4. **`CommandTimeout` client ≥ `statement_timeout` serveur + 1 s**, pour que ce
   soit le serveur qui annule (erreur typée `57014`) plutôt que le client (qui
   laisse la requête tourner côté serveur).
5. **`DateOnly` / `DateTimeOffset`**, jamais `DateTime` : `date` → `DateOnly`,
   `timestamptz` → `DateTimeOffset` sérialisé ISO 8601 avec offset.
6. **Les colonnes `@free-text` sont lues en `Untrusted`** — le type de la
   propriété du `Row` est `Untrusted`, pas `string`.

---

## 6. Commande de smoke

Base de test requise (`network`), 0 token :

```bash
cd workspace/src/{AppName}
dotnet build -warnaserror
dotnet run --project . -- migrate --connection-name AgentViewsOwner
dotnet run --project . -- smoke data --ping
#   → connexion agent_ro ; transaction_read_only == on ; search_path == agent_views ; statement_timeout == DbStatementTimeoutMs
#   → INSERT INTO agent_views… → doit échouer (42501) ; SELECT hors agent_views → doit échouer
#   → pour chaque vue : SELECT … LIMIT 1 sans tenant posé → 0 ligne (fail-closed) ; avec tenant de test → OK
dotnet test --filter "FullyQualifiedName~Data"
```

Smoke Timeout : 120 s.

---

## 7. Pièges connus

Les onze pièges de `view-per-agent.md` §7 valent ici (commentaire modifié hors
migration, `security_invoker = true` par réflexe, `current_setting` sans
`missing_ok`, vue lente prise pour un outil cassé, plafond qui masque
l'information, vues sur vues, texte libre non taggé, décimaux en flottant,
fuseaux, un rôle pour tous les agents, mot de passe dans la migration). Propres à
.NET :

1. **`AddWithValue` et l'inférence de type.** Un filtre `string` passé à une
   colonne `uuid` échoue côté serveur (`42883`) ; le record d'entrée porte le
   type exact (`Guid?`), et le test L2 couvre chaque colonne `@filter`.
2. **`CommandTimeout` à 30 s par défaut.** Plus long que `DbStatementTimeoutMs` :
   sans réglage, c'est bien le serveur qui annule, mais une coupure réseau laisse
   l'appel pendre 30 s, au-delà de la borne `TimeoutSeconds` de l'agent.
3. **Un `set_config` hors transaction.** Sans `BeginTransactionAsync`,
   `is_local = true` ne dure que la commande `SELECT set_config` elle-même : la
   requête suivante voit un tenant vide, donc zéro ligne. Fail-closed, mais
   l'outil « ne marche pas » et le modèle invente.
4. **`DataAnnotations` pris pour une validation.** `[RegularExpression]` ne
   valide rien tout seul : la validation est appelée explicitement
   (`Validator.TryValidateObject`) avant `QueryViewAsync`, et son échec rend
   `INVALID_FILTER`, erreur déclarée.
5. **`ToolContext` exposé au modèle.** Un paramètre `tenantId` sur la méthode
   passée à `AIFunctionFactory.Create` apparaît dans le schéma JSON de l'outil :
   le modèle peut alors le remplir. Le contexte est capturé, jamais passé en
   paramètre de la fonction.

---

## 8. Contrat d'exécution

L'application .NET implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** — mêmes commandes, même NDJSON `RunEvent` (`event_schema: "1"`,
snake_case), mêmes codes de sortie ; détail .NET dans `serving/cli-dotnet.md`.
Elle honore en plus les trois points de `serving/cli.md` §3.5, cités tels quels :

1. `run --json --input-file -` lit l'entrée sur stdin ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'app sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil,
   `tools/{outil}.jsonl`) et le retrieval figé depuis le même dossier
   (`retrieval/{index}.jsonl`), sans aucun appel réseau d'outil ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished` —
   c'est ce que la RETRIEVAL GATE mesure, sans agent.

Lancement : `dotnet run --project workspace/src/{AppName} --` en développement,
l'exécutable publié en livrable. Les runners l'invoquent par `--executor cli`
(commande dérivée du langage actif : `dotnet run --project workspace/src/{AppName} --`)
ou par `--executor cmd:<commande>`.

**Ce que cette fiche doit au contrat.** Les outils de vue sont des outils comme
les autres : en `SDDA_EVAL_ISOLATION=mocked`, `FixtureDataTools` remplace
`Envelope` et sert `SDDA_EVAL_FIXTURES/tools/{outil}.jsonl` (une réponse
enregistrée par ligne, choisie par les arguments, erreurs déclarées rejouées) ;
`AgentViewsDataSource` n'est **pas** construit — aucune connexion PostgreSQL
n'est ouverte. Un outil de vue sans fixture en isolement → code `8`
(`[CONFIG_INVALID]`) au démarrage, avant tout appel de modèle. Le tenant de
`--tenant` alimente `ToolContext` dans les deux modes : une fixture qui dépend
du tenant le lit dans le contexte, jamais dans les arguments.
