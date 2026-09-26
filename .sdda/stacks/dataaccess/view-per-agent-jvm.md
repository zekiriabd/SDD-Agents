# Stack: view-per-agent-jvm (dataaccess)

Stack ID: dataaccess-view-per-agent-jvm
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: kotlin, java
Scope: stratégie **DATA ACCESS** la plus sûre, côté **JVM** (Kotlin et Java) — une vue SQL dédiée par agent et par besoin, exposée comme outil `read-only` Spring AI dont la description est le commentaire SQL de la vue. **Mêmes vues, mêmes commentaires, même grammaire de tags que `dataaccess/view-per-agent.md`** ; ce qui change est l'enveloppe d'exécution (`JdbcClient`, transaction en lecture seule, `set_config`), le câblage de l'outil (`@Tool` / `ToolCallback`) et la migration (Flyway). Variante PostgreSQL détaillée ; les autres `DatabaseType` suivent le tableau de la fiche Python §7. Pas de `.libs.json` : `JdbcClient`, le pilote et Flyway sont portés par `vectorstore/pgvector-jvm.libs.json` (ou, sans RAG, par les mêmes modules ajoutés au catalogue du framework).

---

## 1. Rôle et périmètre

La question que pose l'accès base n'est pas « comment le modèle écrira-t-il le
SQL ? » mais **« qu'est-ce que le modèle ne pourra jamais atteindre ? »**
(`dataaccess/view-per-agent.md` §1). La réponse est structurelle et ne dépend
pas du langage : surface définie en SQL par un humain, commentaire de vue =
description de l'outil, filtre d'identité **dans la vue**, rôle en lecture
seule au serveur. Cette fiche dit comment la JVM **garde** ces quatre
propriétés — et où elle pourrait les perdre sans qu'un compilateur le voie :
un `tenantId` passé en paramètre d'outil, un `SET` de session sur un pool, un
nom de colonne concaténé.

Périmètre : fichiers de vues (renvoi), migration Flyway, enveloppe d'exécution,
outil Spring AI généré depuis le contrat, erreurs déclarées, tests.
**Hors périmètre** : écritures (`repository-tools`), text-to-sql, documents
(`rag/hybrid-jvm.md`).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `dataaccess-view-per-agent-jvm` |
| **Famille** | DATA ACCESS · lecture seule · besoins connus et stables |
| **Base cible détaillée** | PostgreSQL 16+ (`dataaccess/view-per-agent.md` §7 pour les autres) |
| **Client** | JDK 21 · `JdbcClient` sur HikariCP · `org.postgresql:postgresql` (`vectorstore/pgvector-jvm.libs.json`) |
| **Migrations** | Flyway, SQL brut — une migration par vue (`data/migrations/V1x__view_*.sql`) |
| **Outil exposé** | méthode `@Tool` Spring AI (ou `FunctionToolCallback`) ; identité lue dans `ToolContext`, jamais dans les arguments |
| **Paramètres STACK.md** | `DbAgentRole: readonly`, `DbStatementTimeoutMs: 5000`, `DbMaxRowsReturned: 500`, `DbAllowedSchemas: [agent_views]`, `DbForbiddenStatements`, `DbQueryLogging: full` |

### 2.1 Ce qui n'existe pas encore côté outillage

La fiche Python cite un générateur `gen_view_tools.py` (catalogue → outils) ;
il n'y a **pas** de générateur JVM, et `validate_envelope`
(`.sdda/python/sdda_scripts/validate_envelope.py`) analyse l'AST **Python** de
`data/` : en JVM, seul le SQL des vues est vérifié par script. Conséquence
imposée : `dev-data` écrit les outils depuis le contrat `{n}-data-*.tool.md`,
et un test L0 (`ViewCatalogTest`, §4) compare vue ↔ outil ↔ contrat. Ce qui
n'est pas vérifié par un script l'est par un test, sinon ce ne l'est pas.

---

## 3. Mapping des concepts SDD_Agents → idiomes JVM

Le tableau de `dataaccess/view-per-agent.md` §3 vaut tel quel (nommage
`agent_views.v_{agent_slug}__{purpose}`, `COMMENT ON VIEW` = description,
tags `@filter` `@required` `@pii` `@free-text` `@unit:`, erreurs `TIMEOUT`,
`TOO_MANY_ROWS`, `AUTH_FAILED`, `INVALID_FILTER`, `LIMIT maxRows + 1`,
`search_path = agent_views`, un rôle par agent si les périmètres diffèrent).
Ce qui change :

| Concept | Idiome JVM | Notes |
|---|---|---|
| **Une vue** | `data/views/v_{agent_slug}__{purpose}.sql` (identique au fichier Python, §3.1 de la fiche Python) | un fichier = une vue = une migration = un outil = un contrat |
| **Migration** | `data/migrations/V1{nn}__view_{agent_slug}__{purpose}.sql` qui **inclut** le fichier de vue (copie vérifiée par hash en L0) | Flyway n'a pas d'`include` : la migration porte le SQL, `ViewCatalogTest` vérifie qu'il est octet pour octet celui de `data/views/` |
| **TOOL** `input_schema` | `record` / `data class` d'entrée **générée depuis le contrat** ; un champ par colonne `@filter`, `@required` non nul | le modèle ne voit que ce schéma ; aucun champ `tenantId` |
| **TOOL** `output_schema` | `record Row(...)` + `record Output(List<Row> rows, boolean truncated, int rowCount)` | `@pii` → redaction dans le span |
| **Filtrage par identité** | `set_config('app.tenant_id', :tenant, true)` dans la transaction ; la vue lit `current_setting('app.tenant_id', true)` | tenant tiré de `ToolContext` (posé par la surface), jamais d'un argument |
| **Enveloppe** | `ViewQueryEnvelope.run(...)` — `TransactionTemplate` lecture seule, `set_config('statement_timeout', …, true)`, colonnes et vue **issues du catalogue** (jamais de l'argument), valeurs en paramètres liés, `ORDER BY` clé, `LIMIT maxRows + 1` | écrit une fois, testé en L2 |
| **Trust** | `@free-text` → le champ de `Row` est `Untrusted<String>` ; outil `trust: untrusted` | P8 |
| **Journal** | span `sdda.data.query {view}` : `db.query.text` **paramétré**, `db.response.returned_rows`, `sdda.data.truncated` | `observability/otel-genai-jvm.md` |

### 3.1 L'enveloppe

**Java**

```java
// data/ViewQueryEnvelope.java — package {package}.data
public final class ViewQueryEnvelope {
    private final JdbcClient jdbc;             // DataSource du rôle agent_ro — jamais celui de l'ingestion
    private final TransactionTemplate readOnlyTx;
    private final ViewCatalog catalog;         // vues et colonnes autorisées, chargées de agent_views au démarrage
    private final DataSettings settings;       // maxRows, statementTimeoutMs — depuis STACK.md
    private final DataSpans spans;

    public <R> ViewResult<R> run(String view, Map<String, Object> filters, RowMapper<R> mapper, ToolIdentity who) {
        ViewSpec spec = catalog.require(view);                 // [DATA_VIEW_UNKNOWN] sinon — le nom vient du code, pas du modèle
        spec.checkFilters(filters);                            // clés ⊆ colonnes @filter ; @required présents -> INVALID_FILTER sinon
        String sql = spec.selectSql(filters.keySet());         // SELECT <colonnes du catalogue> FROM agent_views.<vue>
                                                               //   WHERE <col> = :<col> [AND …] ORDER BY <pk> LIMIT :limit
        try (var span = spans.dataQuery(spec, sql)) {          // db.query.text = le texte PARAMÉTRÉ
            List<R> rows = readOnlyTx.execute(status -> {
                jdbc.sql("SELECT set_config('app.tenant_id', :t, true)").param("t", who.tenantId()).query().singleValue();
                jdbc.sql("SELECT set_config('statement_timeout', :ms, true)")
                    .param("ms", Long.toString(settings.statementTimeoutMs())).query().singleValue();
                return jdbc.sql(sql).params(filters).param("limit", settings.maxRows() + 1).query(mapper).list();
            });
            boolean truncated = rows.size() > settings.maxRows();
            List<R> served = truncated ? rows.subList(0, settings.maxRows()) : rows;
            span.rows(served.size(), truncated);
            return new ViewResult<>(served, truncated);        // truncated -> erreur déclarée TOO_MANY_ROWS côté outil
        } catch (QueryTimeoutException e) {
            throw new DeclaredToolError("TIMEOUT", e);
        } catch (PermissionDeniedDataAccessException e) {
            throw new DeclaredToolError("AUTH_FAILED", e);
        }
    }
}
```

**Kotlin**

```kotlin
// data/ViewQueryEnvelope.kt — package {package}.data
class ViewQueryEnvelope(
    private val jdbc: JdbcClient,
    private val readOnlyTx: TransactionTemplate,
    private val catalog: ViewCatalog,
    private val settings: DataSettings,
    private val spans: DataSpans,
) {
    fun <R : Any> run(view: String, filters: Map<String, Any>, mapper: RowMapper<R>, who: ToolIdentity): ViewResult<R> {
        val spec = catalog.require(view)
        spec.checkFilters(filters)
        val sql = spec.selectSql(filters.keys)
        return spans.dataQuery(spec, sql).use { span ->
            val rows = try {
                readOnlyTx.execute {
                    jdbc.sql("SELECT set_config('app.tenant_id', :t, true)").param("t", who.tenantId).query().singleValue()
                    jdbc.sql("SELECT set_config('statement_timeout', :ms, true)")
                        .param("ms", settings.statementTimeoutMs.toString()).query().singleValue()
                    jdbc.sql(sql).params(filters).param("limit", settings.maxRows + 1).query(mapper).list()
                }.orEmpty()
            } catch (e: QueryTimeoutException) {
                throw DeclaredToolError("TIMEOUT", e)
            } catch (e: PermissionDeniedDataAccessException) {
                throw DeclaredToolError("AUTH_FAILED", e)
            }
            val truncated = rows.size > settings.maxRows
            val served = if (truncated) rows.take(settings.maxRows) else rows
            span.rows(served.size, truncated)
            ViewResult(served, truncated)
        }
    }
}
```

Trois choses que le compilateur ne voit pas et que l'enveloppe impose :

- **les identifiants SQL (vue, colonnes) viennent du catalogue** chargé de
  `information_schema` au démarrage, jamais d'un argument — un identifiant ne
  se lie pas en paramètre, donc un identifiant venant du modèle serait
  concaténé ;
- **les valeurs sont des paramètres liés** (`:col`), y compris la liste d'un
  `IN` (≤ 20 valeurs, `@filter` de la fiche Python §3.2) ;
- **tout tient dans une transaction** : hors transaction, `set_config(…, true)`
  s'éteint à la fin de sa propre instruction, et la vue — `missing_ok = true`
  — rend zéro ligne (fail-closed, mais silencieux).

### 3.2 L'outil exposé au modèle

**Java**

```java
// data/tools/BillingSpecialistUnpaidInvoices.java — écrit par dev-data depuis {n}-billing-specialist-unpaid-invoices.tool.md
@Component
final class BillingSpecialistUnpaidInvoices {
    static final String NAME = "billing_specialist_unpaid_invoices";
    private final ViewQueryEnvelope envelope;

    public record Input(@Nullable @Pattern(regexp = "^INV-\\d{4}-\\d{4}$") String invoiceId,
                        @Nullable String customerId,
                        @Nullable @Size(max = 20) List<String> status) {}

    @Tool(name = NAME, description = ToolDescriptions.BILLING_SPECIALIST_UNPAID_INVOICES)   // = COMMENT ON VIEW, hash vérifié
    Output call(Input in, ToolContext ctx) {
        ViewResult<Row> r = envelope.run("v_billing_specialist__unpaid_invoices",
                Filters.of("invoice_id", in.invoiceId(), "customer_id", in.customerId(), "status", in.status()),
                Row.MAPPER, ToolIdentity.from(ctx));             // tenant : ToolContext, jamais Input
        return new Output(r.rows(), r.truncated(), r.rows().size());
    }
}
```

**Kotlin**

```kotlin
// data/tools/BillingSpecialistUnpaidInvoices.kt
@Component
class BillingSpecialistUnpaidInvoices(private val envelope: ViewQueryEnvelope) {
    data class Input(
        @field:Pattern(regexp = "^INV-\\d{4}-\\d{4}$") val invoiceId: String? = null,
        val customerId: String? = null,
        @field:Size(max = 20) val status: List<String>? = null,
    )

    @Tool(name = NAME, description = ToolDescriptions.BILLING_SPECIALIST_UNPAID_INVOICES)
    fun call(input: Input, ctx: ToolContext): Output {
        val r = envelope.run(
            "v_billing_specialist__unpaid_invoices",
            Filters.of("invoice_id" to input.invoiceId, "customer_id" to input.customerId, "status" to input.status),
            Row.MAPPER, ToolIdentity.from(ctx),
        )
        return Output(r.rows, r.truncated, r.rows.size)
    }

    companion object { const val NAME = "billing_specialist_unpaid_invoices" }
}
```

`ToolDescriptions` est une classe **générée** depuis les contrats (les
descriptions sont du prompt : P1 interdit de les écrire en littéral à la
main) ; `health` recalcule leur hash contre `tool_schema_hash`. `ToolContext`
est rempli par l'orchestration (`.toolContext(Map.of("tenant", …))`) depuis
l'identité posée par la surface — `framework/spring-ai.md` §3.3. `Filters.of`
ignore les valeurs nulles : un filtre non fourni n'est pas un `IS NULL`.

---

## 4. Structure de fichiers générée

Zone de `dev-data` (`workspace/src/**/data/**`), disposition à plat
(`lang/*.md` §4) ; `.kt` ou `.java` selon le langage actif.

```
workspace/src/{AppName}/data/
├── views/v_{agent_slug}__{purpose}.sql        # CREATE OR REPLACE VIEW + COMMENT ON VIEW + COMMENT ON COLUMN — identique à la fiche Python
├── migrations/
│   ├── V100__agent_views_schema.sql           # CREATE SCHEMA agent_views ; propriétaire ; REVOKE public
│   ├── V101__agent_views_roles.sql            # agent_ro (+ agent_ro_{agent}) ; ALTER ROLE … SET … ; mot de passe : jamais ici
│   └── V1nn__view_{agent_slug}__{purpose}.sql # le SQL du fichier de vue, recopié
├── schemas/                                    # schémas figés des sources (actif d'exécution)
├── ViewQueryEnvelope                           # §3.1
├── ViewCatalog                                 # introspection agent_views + obj_description / col_description
├── DeclaredToolError · ViewResult · ToolIdentity
└── tools/{AgentSlug}{Purpose}                  # §3.2 — un par vue

workspace/src/{AppName}/tests/data/
├── ViewCatalogTest                             # L0 : chaque vue a COMMENT ON VIEW ≥ 200 car., tags connus ; migration == fichier de vue (hash) ;
│                                               #      description de l'outil == commentaire (hash) ; pas de SELECT * ; current_setting('app.tenant_id') présent
├── ViewQueryEnvelopeTest                       # L1 : SQL construit — colonnes du catalogue seulement, LIMIT maxRows+1, ORDER BY pk ; filtre inconnu -> INVALID_FILTER
├── BillingSpecialistUnpaidInvoicesTest         # L2 : @ParameterizedTest(name = "{0}", quoteTextArguments = false) — happy, vide, INVALID_FILTER, TOO_MANY_ROWS, TIMEOUT, AUTH_FAILED
└── BillingSpecialistUnpaidInvoicesNetworkTest  # @Tag("network") : Testcontainers — sans tenant posé -> 0 ligne ; INSERT refusé ; SELECT hors agent_views refusé
```

Les migrations de vues tournent comme celles du RAG : tâche Gradle
d'opérateur (`dataMigrate`, `mainClass = "{package}.data.DataMigrate"`,
`locations = classpath:data/migrations`), jamais au démarrage de
l'application (`spring.flyway.enabled: false`) — l'application se connecte en
`agent_ro`, qui ne peut rien créer.

---

## 5. Conventions imposées

Les dix règles de `dataaccess/view-per-agent.md` §5 s'appliquent (chaîne vue →
migration → outil → contrat, nommage à double underscore, commentaire ≥ 200
caractères rédigé comme du prompt, pas de `SELECT *`, filtre d'identité dans la
vue, filtres = égalité ou `IN`, `ORDER BY` déterministe, rôle vérifié au
démarrage, agrégats = vues d'agrégats, types sérialisés explicitement). En
plus :

1. **Aucun paramètre d'outil ne porte l'identité.** Un champ `tenantId`,
   `customerScope`, `userId` dans un `Input` est refusé en revue et par
   `ViewCatalogTest` (liste des champs ⊆ colonnes `@filter`).
2. **`DataSource` `agent_ro` distinct** de ceux du RAG et de l'opérateur ;
   `TransactionTemplate` `setReadOnly(true)`. Au démarrage :
   `SHOW transaction_read_only` = `on` et `current_user` attendu, sinon
   `[DATA_ROLE_NOT_READONLY]`, fail-fast.
3. **`numeric` → `BigDecimal`**, sérialisé en chaîne si `@unit:EUR`
   (`view-per-agent.md` §7.8) ; `timestamptz` → `OffsetDateTime` ISO 8601 ;
   jamais `double` pour un montant.
4. **Pas de `JdbcTemplate.queryForList` en `Map`** : chaque vue a son `Row`
   typé ; une colonne ajoutée en base sans revue n'apparaît pas dans la réponse.

---

## 6. Commande de smoke

Base de test requise (`network`), 0 token :

```bash
cd workspace/src/{AppName}
./gradlew dataMigrate                                   # rôle propriétaire, depuis la configuration
./gradlew test --tests '*.data.ViewCatalogTest' --tests '*.data.ViewQueryEnvelopeTest'
./gradlew test -PincludeTags=network --tests '*.data.*'
cd ../../..
java -jar workspace/src/{AppName}/build/libs/{AppName}.jar health --live --json
#   → connexion agent_ro ; transaction_read_only == on ; search_path == agent_views ; statement_timeout == DbStatementTimeoutMs
#   → pour chaque vue : SELECT … LIMIT 1 sans tenant posé -> 0 ligne (fail-closed) ; avec tenant de test -> OK
```

Smoke Timeout : 120 s.

---

## 7. Contrat d'exécution

L'application JVM implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique**. Les trois points de `serving/cli.md` §3.5, pour cette stratégie :

1. `run --json --input-file -` lit l'entrée sur `stdin` ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'application sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil,
   `tools/{nom_de_l_outil}.jsonl`) et le retrieval figé depuis le même
   dossier, **sans ouvrir le `DataSource` `agent_ro`** ; les outils de vue sont
   remplacés par un `FixtureToolCallback` qui rejoue les lignes
   `{"tool","args","result"}` ou les erreurs déclarées
   `{"tool","args","error":{"code":"TOO_MANY_ROWS",…}}` ; un outil sans fixture
   → refus de démarrer, code `8` ;
3. `retrieve --json --index ID --query-file - [--k N]` ne concerne pas cette
   stratégie (aucun index) : il est servi par `rag/hybrid-jvm.md` si un RAG est
   actif, et rend sinon `error` `[CONFIG_INVALID]` puis `run_finished`, code `8`.

Commande de lancement : `java -jar workspace/src/{AppName}/build/libs/{AppName}.jar`
depuis la racine du dépôt (livrable), `./gradlew run --args='…'` en
développement ; `--executor cli` la dérive du langage actif,
`--executor cmd:<commande>` l'impose. Le build produit exactement
`build/libs/{AppName}.jar`.

---

## 8. Pièges connus

Les onze pièges de `dataaccess/view-per-agent.md` §7 s'appliquent. Propres à
la JVM :

1. **Transaction oubliée.** `JdbcClient` sans `TransactionTemplate` = une
   transaction par instruction : le tenant posé par `set_config(…, true)` a
   disparu quand la vue s'exécute. Zéro ligne, et le modèle conclut « aucune
   facture impayée ».
2. **`@Transactional` sur une méthode appelée depuis la même classe.** Le proxy
   Spring ne s'applique pas à un appel interne : pas de transaction, même
   symptôme. D'où `TransactionTemplate` explicite dans l'enveloppe.
3. **`spring.flyway.enabled` laissé à `true`.** Spring Boot migrerait au
   démarrage avec le `DataSource` de l'application : échec avec `agent_ro`, ou
   perte de l'enveloppe si on lui donne un rôle propriétaire pour « que ça
   marche ».
4. **Description d'outil en littéral dans `@Tool(description = "…")`.** Elle
   dérive du `COMMENT ON VIEW` sans que le hash du contrat bouge : constante
   générée depuis le contrat, recalculée par `health`.
5. **`double` pour un montant.** `1234.5599999` dans la réponse du modèle ;
   `BigDecimal`, arrondi dans la vue.
6. **Jackson et les champs inconnus.** Un `Input` désérialisé sans
   `FAIL_ON_UNKNOWN_PROPERTIES` accepte un `tenant_id` inventé par le modèle —
   ignoré, mais la trajectoire ment. Le schéma d'entrée est validé avant la
   désérialisation (`additionalProperties: false`).
