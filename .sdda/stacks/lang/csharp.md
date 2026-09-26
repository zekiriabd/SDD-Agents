# Stack: csharp (lang)

Stack ID: lang-csharp
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: csharp
Scope: langage et runtime de l'application agentic générée (outillage, structure de projet, conventions transverses) sur .NET. Le framework agentic est hors périmètre → `.sdda/stacks/framework/*.md`.

---

## 1. Rôle et périmètre

Cette fiche fixe **le socle .NET** sur lequel s'appuient les stacks C# de
SDD_Agents (`framework/ms-agent-framework.md`, et les fiches transverses qui en
dépendent). Elle décide :

- la version du langage, le TFM et la gestion des dépendances ;
- l'outillage déterministe (format, analyse statique, tests) exécuté en L0/L1
  sans aucun token ;
- la **structure de projet agentic** — où vivent agents, outils, retrieval,
  orchestration, prompts chargés, bornes, tracing ;
- les conventions transverses qui rendent les principes du framework
  **vérifiables par un analyseur** : aucun prompt inline (P1), aucun secret lu
  via `Environment.GetEnvironmentVariable`, toute borne typée (P12).

Elle **ne décide pas** : le framework agentic, le pattern d'orchestration, le
store vectoriel, la surface d'exposition. Ces choix sont déclarés dans
`workspace/stack/STACK.md` et documentés dans leurs fiches.

> **Ce qui change vraiment par rapport à Python**, et qu'il ne faut pas traiter
> comme un détail de traduction : le C# a un système de types **appliqué à la
> compilation**. Une frontière de confiance déclarée en type (§5.3) n'est pas
> une convention relue par un humain — c'est une erreur de build. Là où la
> fiche Python demande `mypy --strict` en L0, le C# l'obtient gratuitement, à
> condition de ne pas le désactiver ; d'où la sévérité imposée en §5.2.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `lang-csharp` |
| **Langage** | C# **14** — `<LangVersion>latest</LangVersion>` refusé (une montée de SDK changerait le langage sans qu'aucun fichier ne bouge) |
| **TFM** | `net10.0` (LTS) |
| **Gestionnaire** | `dotnet` + **Central Package Management** (`Directory.Packages.props`) — une seule version par paquet pour toute la solution |
| **Format** | `dotnet format` sur `.editorconfig` versionné |
| **Analyse statique** | analyseurs .NET intégrés, `<AnalysisLevel>latest-all</AnalysisLevel>`, `<TreatWarningsAsErrors>true</TreatWarningsAsErrors>` |
| **Typage** | `<Nullable>enable</Nullable>` — non négociable (§5.2) |
| **Tests** | `xunit.v3` 4.0.x + `xunit.runner.visualstudio` + `Microsoft.NET.Test.Sdk` + `JunitXml.TestLogger` + `NSubstitute` 6.2.x — `eval/xunit-eval.md` (§5.4) ; commande : `dotnet test --logger "junit;LogFilePath=<fichier>"` |
| **Disposition** | **plate**, couches en minuscules sous `workspace/src/{AppName}/` (§4) — exigée par la matrice d'ownership |
| **Modèles de données** | `record` + `required` members + `System.Text.Json` (source-generated) |
| **Logs** | `ILogger<T>` avec logging source-generated — jamais `Console.WriteLine` |
| **Namespace racine** | `{AppName}` (PascalCase), ex. `SupportAssistant` |

> Les versions sont portées par les catalogues des fiches actives, seuls
> fichiers qui fassent foi : `framework/ms-agent-framework.libs.json` (agents,
> provider, MCP), `vectorstore/pgvector-dotnet.libs.json` (Npgsql, Pgvector,
> migrations), `eval/xunit-eval.libs.json` (tests),
> `observability/otel-genai-dotnet.libs.json` (traces). Pas de `.libs.json`
> pour la fiche `lang` : comme en Python, `csharp` seul n'installe rien.

### 2.0 Les fiches .NET de la combinaison C1-NET

| Catégorie | Fiche | Ce qu'elle décide |
|---|---|---|
| framework | `framework/ms-agent-framework.md` | `AIAgent`, `IChatClient`, bornes en code, Workflows à la demande |
| serving | `serving/cli-dotnet.md` | la CLI du contrat `serving/cli.md` §3.1-3.3, en .NET |
| vectorstore | `vectorstore/pgvector-dotnet.md` | schéma `rag`, Npgsql + Pgvector, migrations DbUp |
| rag | `rag/hybrid-dotnet.md` | deux jambes, `Rrf.Fuse` pure, client Voyage REST, commande `retrieve` |
| dataaccess | `dataaccess/view-per-agent-dotnet.md` | vues par agent, enveloppe Npgsql paramétrée |
| tools | `tools/mcp-dotnet.md` | SDK `ModelContextProtocol`, allowlist, `AIFunction` sous contrat |
| eval | `eval/xunit-eval.md` | tests L0–L2, rapport JUnit, conventions lues par G3 |
| observability | `observability/otel-genai-dotnet.md` | spans GenAI, JSONL de trace au format du framework |

### 2.1 Init (idempotent)

```bash
if [ ! -f "workspace/src/{AppName}/{AppName}.csproj" ]; then
  mkdir -p workspace/src/{AppName}
  cd workspace/src/{AppName}
  dotnet new console --name {AppName} --output . --framework net10.0      # l'application EST le projet racine
  dotnet new install xunit.v3.templates::4.0.1                             # le gabarit xunit3 n'est PAS livré avec le SDK (seul `xunit`, v2, l'est)
  dotnet new xunit3 --name {AppName}.Tests --output tests --framework net10.0
  dotnet new sln --format slnx --name {AppName}
  dotnet sln {AppName}.slnx add {AppName}.csproj tests/{AppName}.Tests.csproj
  dotnet add tests/{AppName}.Tests.csproj reference {AppName}.csproj
fi
```

`Directory.Packages.props`, `Directory.Build.props` et `.editorconfig` sont
écrits à la racine de `workspace/src/{AppName}/` et versionnés : ce sont eux qui
portent les conventions de §5, et un projet qui les redéfinit localement les
contourne.

Le projet de test généré par le gabarit est ensuite **corrigé** : ses
`PackageReference` perdent leur `Version=` (Central Package Management), il
reçoit `JunitXml.TestLogger` et
`<IsTestingPlatformApplication>false</IsTestingPlatformApplication>`
(`eval/xunit-eval.md` §2.1 — sans ce réglage, `dotnet test` échoue sur le SDK
.NET 10).

Le projet racine porte trois réglages que la disposition plate rend
obligatoires :

```xml
<!-- workspace/src/{AppName}/{AppName}.csproj — extrait -->
<PropertyGroup>
  <OutputType>Exe</OutputType>                                                   <!-- la CLI est le livrable par défaut (cli-exe) -->
  <DefaultItemExcludes>$(DefaultItemExcludes);tests/**</DefaultItemExcludes>    <!-- sinon le projet racine compile aussi les tests -->
</PropertyGroup>
<ItemGroup>
  <None Include="prompts/**" CopyToOutputDirectory="PreserveNewest" />          <!-- les prompts partent avec l'exécutable -->
  <None Include="skills/**;rules/**" CopyToOutputDirectory="PreserveNewest" />
</ItemGroup>
```

Pourquoi : sans `DefaultItemExcludes`, le globbing par défaut de MSBuild fait
entrer `tests/**/*.cs` dans l'application (types en double, références xunit
dans l'exécutable livré). Sans la copie de `prompts/`, l'exécutable publié
cherche ses prompts dans un répertoire resté dans le dépôt — le défaut qu'ARCHITECTURE
§2.ter décrit. Ces trois réglages ont été vérifiés sur une sonde (SDK 10.0.301) :
build de la racine, copie de `prompts/` dans la sortie, `dotnet test` depuis la
racine malgré la présence conjointe du `.csproj` et du `.slnx`.

```xml
<!-- workspace/src/{AppName}/Directory.Build.props -->
<Project>
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <LangVersion>14.0</LangVersion>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
    <AnalysisLevel>latest-all</AnalysisLevel>
    <EnforceCodeStyleInBuild>true</EnforceCodeStyleInBuild>
    <ManagePackageVersionsCentrally>true</ManagePackageVersionsCentrally>
  </PropertyGroup>
</Project>
```

---

## 3. Mapping des concepts SDD_Agents → idiomes .NET

Les chemins sont relatifs à `workspace/src/{AppName}/`. Les répertoires de
couche sont en **minuscules** (§4), les espaces de noms restent en PascalCase
(`namespace {AppName}.Tools;` dans `tools/`).

| Concept | Idiome | Où |
|---|---|---|
| **AGENT** | un dossier `agents/{agent}/` exposant `Build(AgentDeps deps, Bounds bounds)` ; le type concret dépend du framework | `agents/{agent}/` (`dev-agent`, une instance par agent) |
| **PROMPT** (`prompt_ref` + hash) | `LoadedPrompt(string Text, string Sha256, string Path)` chargé au démarrage par `Prompts.LoadSystemPrompt(slug)` depuis `prompts/{slug}.system.md`, copié dans la sortie par le `.csproj`. **Jamais de texte système dans le code.** | `app/Prompts.cs` ; le texte : `prompts/` (`dev-prompt`) |
| **TOOL** | une méthode `async Task<TOut>` typée + un `ToolSpec` (record, init-only) portant `Name`, `Description`, `SideEffectClass`, `Trust`, `TimeoutSeconds`, `RetryPolicy` ; schémas dérivés des records d'entrée/sortie | `tools/{ToolSlug}.cs` |
| **BOUNDS** (P12) | `record Bounds` : `MaxIterations`, `MaxToolCalls`, `MaxDelegationDepth`, `TimeoutSeconds`, `BudgetUsd` ; hiérarchie `BoundExceededException` → `IterationsExceeded`, `ToolCallsExceeded`, `DelegationDepthExceeded`, `TimeoutExceeded`, `BudgetExceeded` | `shared/Bounds.cs` (pré-passe 4.0, gelé) |
| **MODEL BINDING** (tier) | `AppOptions.RuntimeTierMap : IReadOnlyDictionary<Tier, string>` — le code manipule `Tier`, jamais un nom de modèle ; la résolution se fait dans `Models.Resolve(tier) -> IChatClient` (pipeline : `observability/otel-genai-dotnet.md` §3.2) | `app/AppOptions.cs`, `app/Models.cs` |
| **RETRIEVER / INDEX** | `retrieval/{index_slug}/` exposant `RetrieveAsync(query, topK, ctx, ct) -> IReadOnlyList<RetrievedChunk>` ; `RetrievedChunk` porte `DocId`, `ChunkId`, `Score`, `Content` (`Untrusted`), `Citation`, `Provenance` | `retrieval/` — `rag/hybrid-dotnet.md`, `vectorstore/pgvector-dotnet.md` |
| **DATA ACCESS** | vues, enveloppe et outils de vue | `data/` — `dataaccess/view-per-agent-dotnet.md` |
| **MCP** | client MCP → `AIFunction` allowlistées sous contrat | `tools/mcp/` — `tools/mcp-dotnet.md` |
| **ORCHESTRATION PATTERN** | `orchestration/Graph.cs` (ou `Pipeline.cs`) — **seul endroit où le framework apparaît nommément** | `orchestration/` |
| **GUARDRAIL** | `{Id}.cs` : `Check(payload) -> GuardrailVerdict(bool Passed, string Reason, OnTrip OnTrip)` | `app/guardrails/` |
| **TRACE SPAN** | une `ActivitySource` nommée `sdda` + conventions GenAI ; helpers `Run()`, `AgentTurn()`, `Retrieval()`, `DataQuery()` ; `chat` / `execute_tool` / `embeddings` émis par `Microsoft.Extensions.AI` ; JSONL de trace au format du framework | `app/tracing/` — `observability/otel-genai-dotnet.md` |
| **Secrets** | `IOptions<AppOptions>` liés depuis `IConfiguration` ; le code lit `options.Value.LlmApiKey` — **jamais** `Environment.GetEnvironmentVariable` → `[SEC_ENV_VAR_FORBIDDEN]` | `app/AppOptions.cs` |
| **Trust posture** (P8) | `readonly record struct Untrusted(string Value)` pour tout texte non maîtrisé ; les méthodes qui construisent un message système n'acceptent **pas** `Untrusted` — le compilateur le refuse | `shared/Trust.cs` |
| **SURFACE** | la CLI du contrat `serving/cli.md` §3.1-3.3 et §3.5 | `serving/` — `serving/cli-dotnet.md` |

### 3.1 Chargement et hash des prompts

```csharp
// app/Prompts.cs
namespace {AppName}.App;

public sealed record LoadedPrompt(string Text, string Sha256, string Path);

public static class Prompts
{
    // Le hash est calculé sur le texte NORMALISÉ (LF, sans BOM, sans espaces de
    // fin) : sinon un checkout Windows change le hash sans que le prompt ait
    // changé, et l'épinglage P10 déclencherait une régression fantôme.
    public static LoadedPrompt LoadSystemPrompt(string slug, string promptsRoot)
    {
        var path = System.IO.Path.Combine(promptsRoot, $"{slug}.system.md");
        var raw = File.ReadAllText(path, new UTF8Encoding(false));
        var text = string.Join("\n", raw.Replace("\r\n", "\n").Split('\n').Select(l => l.TrimEnd()));
        var hash = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(text)));
        return new LoadedPrompt(text, hash, path);
    }
}
```

Un prompt système écrit en littéral dans le code est `[PROMPT_INLINE_FORBIDDEN]`,
détecté en L0 (§5.2). La raison n'est pas esthétique : un prompt qui n'est pas
un fichier n'a pas de hash, donc pas d'épinglage, donc aucune eval rejouable.

### 3.2 Bornes

```csharp
// shared/Bounds.cs
namespace {AppName}.Shared;

public enum OnBoundExceeded { FailExplicit, Degrade, EscalateHuman }

public sealed record Bounds
{
    public required int MaxIterations { get; init; }
    public required int MaxToolCalls { get; init; }
    public required int MaxDelegationDepth { get; init; }
    public required int TimeoutSeconds { get; init; }
    public required decimal BudgetUsd { get; init; }
    public OnBoundExceeded OnExceeded { get; init; } = OnBoundExceeded.FailExplicit;
}

public abstract class BoundExceededException(string message) : Exception(message);
public sealed class IterationsExceeded(string m) : BoundExceededException(m);
public sealed class ToolCallsExceeded(string m) : BoundExceededException(m);
public sealed class DelegationDepthExceeded(string m) : BoundExceededException(m);
public sealed class TimeoutExceeded(string m) : BoundExceededException(m);
public sealed class BudgetExceeded(string m) : BoundExceededException(m);
```

`required` sur chaque borne n'est pas décoratif : une borne oubliée devient une
erreur de compilation au lieu d'un `0` par défaut, c'est-à-dire d'une boucle
sans borne qui part en production avec l'air d'être bornée.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/            # ce répertoire EST le projet (layout plat, ARCHITECTURE §2)
├── {AppName}.csproj                # OutputType Exe ; DefaultItemExcludes tests/** ; copie prompts/ skills/ rules/  (dev-backend)
├── {AppName}.slnx                  # {AppName}.csproj + tests/{AppName}.Tests.csproj
├── Directory.Build.props           # TFM, nullable, warnings-as-errors, analyseurs
├── Directory.Packages.props        # Central Package Management — 1 version par paquet, union des catalogues actifs
├── .editorconfig
├── CLAUDE.md                       # contexte projet — écrit par gen-app-context, jamais à la main
├── app/                            # coquille (dev-backend) : composition DI, AppOptions, Models, Prompts, Domaine
│   ├── guardrails/
│   └── tracing/                    # cf. observability/otel-genai-dotnet.md
├── shared/                         # Bounds, Trust (Untrusted), ToolContext, types de handoff — pré-passe 4.0, gelés
├── agents/
│   └── {agent}/                    # Agent.cs (Build(deps, bounds)), Schemas.cs, Deps.cs — un répertoire par instance dev-agent
├── tools/                          # ToolSpec, ToolRegistry (GetForAgent : moindre privilège), {ToolSlug}.cs
│   └── mcp/                        # cf. tools/mcp-dotnet.md
├── data/                           # cf. dataaccess/view-per-agent-dotnet.md
├── retrieval/                      # cf. rag/hybrid-dotnet.md, vectorstore/pgvector-dotnet.md
├── memory/                         # interface (pré-passe) puis implémentation (dev-orchestration)
├── orchestration/                  # cf. framework/ms-agent-framework.md — SEUL endroit nommant le framework
├── serving/                        # dev-api — Program.cs (Main) + la CLI (serving/cli-dotnet.md) : run, resume, retrieve, health, inspect, trace, version
│   └── http/                       # seulement si aspnet-minimal est actif : commande `serve` (serving/aspnet-minimal.md)
├── prompts/{agent}.system.md       # l'exécutable hashé de chaque agent (dev-prompt)
├── skills/  rules/                 # fragments (dev-prompt)
└── tests/
    ├── {AppName}.Tests.csproj      # xunit v3, VSTest, JunitXml.TestLogger — cf. §5.4
    └── {couche}/…Tests.cs          # tests/tools/, tests/retrieval/, tests/data/… — L0 → L2, modèle mocké
```

**Pourquoi plat et en minuscules.** La matrice d'ownership (`loader.yml`)
attribue les zones d'écriture par répertoire de couche **directement** sous
`workspace/src/{AppName}/` — `agents/{agent}/`, `tools/`, `data/`,
`retrieval/`, `memory/`, `orchestration/`, `serving/`, `shared/`, `app/`,
`prompts/`, et `**/tests/**`. Sous Linux, la correspondance est sensible à la
casse : un `Tools/` ou un `src/{AppName}/src/{AppName}/` imbriqué ne correspond
à aucune zone, et l'écriture est **refusée** aux `dev-*`. Les espaces de noms
restent en PascalCase (`namespace {AppName}.Tools;` dans `tools/`) : la casse du
répertoire est un contrat avec la matrice, celle de l'espace de noms une
convention .NET, et les deux coexistent sans conflit (l'analyseur de
correspondance dossier ↔ espace de noms, IDE0130, est désactivé nommément dans
`.editorconfig`).

**Un seul projet exécutable.** Pas de `{AppName}.Core`, `{AppName}.Serving.Cli`
ni `{AppName}.Serving.Http` : les surfaces sont des dossiers de `serving/`, et
la surface HTTP ajoute seulement `<FrameworkReference Include="Microsoft.AspNetCore.App" />`
au projet (vérifié sur une sonde). Un projet séparé sortirait des zones de la
matrice et ne répondrait pas à `dotnet run --project workspace/src/{AppName}`,
la commande par laquelle les runners lancent l'application (§8). L'isolement que
donnait un projet `Core` sans ASP.NET devient une règle vérifiée en L0 : aucun
`using Microsoft.AspNetCore` hors de `serving/http/`.

Aucun répertoire racine hors couche (`migrations/`, `scripts/`…) : il
n'appartiendrait à aucune zone. Les migrations de l'index vivent sous
`retrieval/migrations/`, celles des vues sous `data/migrations/`.

Les evals (L3+) vivent dans `workspace/pipeline/`, hors de la solution — ownership
`qa-evals`, jamais `dev-*` (ARCHITECTURE §7).

---

## 5. Conventions imposées

### 5.1 Style

1. **Un fichier = un type public.** Les agents sont des dossiers disjoints :
   deux `dev-agent` écrivent en parallèle, et un fichier partagé est un conflit.
2. **`sealed` par défaut** sur toute classe non conçue pour l'héritage. Une
   hiérarchie ouverte par inadvertance devient un point d'extension que
   quelqu'un utilisera.
3. **`record` pour toute donnée**, `class` pour tout comportement. Un modèle
   d'entrée d'outil est immuable : `init`-only, jamais de setter.
4. **`CancellationToken` en dernier paramètre de toute méthode `async`**, et
   réellement propagé. Un timeout de borne qui ne peut pas annuler l'appel en
   cours n'est pas un timeout, c'est un compteur.
5. **`ILogger<T>` source-generated** (`[LoggerMessage]`), jamais d'interpolation
   dans un log : elle évalue la chaîne même quand le niveau est désactivé, et
   elle fait entrer des PII dans le message plutôt que dans des champs
   redigeables.
6. **`ConfigureAwait` non requis** : l'application n'a pas de contexte de
   synchronisation. Une bibliothèque extraite plus tard le réintroduira ;
   ce n'en est pas une.

### 5.2 Interdits — vérifiés en L0 (0 token)

| Interdit | Classe | Vérification |
|---|---|---|
| Prompt système en littéral dans le code | `[PROMPT_INLINE_FORBIDDEN]` | recherche de littéraux multi-lignes hors `Prompts.cs` |
| `Environment.GetEnvironmentVariable` | `[SEC_ENV_VAR_FORBIDDEN]` | recherche ; les secrets passent par `IOptions` |
| `Console.WriteLine` hors `serving/` | `[LOG_UNSTRUCTURED]` | recherche |
| `#pragma warning disable` sans justification écrite sur la ligne précédente | `[ANALYZER_SUPPRESSED]` | recherche ; une suppression muette annule §5.2 en une ligne |
| `<Nullable>disable</Nullable>` ou `!` (null-forgiving) hors interop | `[NULLABILITY_DISABLED]` | recherche dans les `.csproj` et le code |
| `dynamic` | `[TYPE_ERASED]` | recherche — il annule la garantie de §5.3 |
| Nom de framework hors `orchestration/` | `[FRAMEWORK_LEAK_IN_CONTRACT]` | `validate_ir.py` sur les contrats |

`TreatWarningsAsErrors` fait la moitié du travail. L'autre moitié est
l'interdiction de le désactiver localement : c'est `#pragma warning disable` qui
transforme une garantie de compilation en intention.

### 5.3 Frontière de confiance dans le type system

```csharp
// shared/Trust.cs
namespace {AppName}.Shared;

/// <summary>Texte écrit par un tiers : une donnée, jamais une instruction (P8).</summary>
public readonly record struct Untrusted(string Value);

public static class Trust
{
    public static Untrusted Wrap(string text, string source) =>
        new($"<untrusted source=\"{source}\">\n{text}\n</untrusted>");
}
```

La règle qui fait tout le travail : **aucune méthode construisant un message
système n'accepte `Untrusted`**, et `Untrusted` n'a pas de conversion implicite
vers `string`. Un développeur — ou un agent générateur — qui veut concaténer un
commentaire client dans un prompt système doit écrire `.Value` explicitement, et
ce `.Value` est ce que la revue cherche.

C'est l'avantage réel du C# ici : en Python la même frontière est un `NewType`
que `mypy --strict` défend **si** on lance mypy. Ici, le build échoue.

### 5.4 Testing

Le détail est dans `eval/xunit-eval.md` ; ce qui suit est ce que tout projet C#
doit respecter, parce que la TOOL GATE (G3) le **lit** :

| Règle | Forme exacte | Pourquoi |
|---|---|---|
| Un projet de test | `tests/{AppName}.Tests.csproj` — `xunit.v3`, `xunit.runner.visualstudio`, `Microsoft.NET.Test.Sdk`, `JunitXml.TestLogger`, `NSubstitute` | la zone `**/tests/**` de la matrice d'ownership |
| Mode VSTest | `<IsTestingPlatformApplication>false</IsTestingPlatformApplication>` dans le projet de test ; aucune clé `test.runner` dans `global.json` | sans lui, `dotnet test` **échoue** sur le SDK .NET 10 avec xunit.v3 4.x (vérifié) et aucun rapport n'est écrit |
| Fichiers | `*Tests.cs` | découverte par la gate |
| Un cas de contrat | un `[Theory]` avec une ligne `[InlineData("{case_id}")]` par cas déclaré du tool-contract : le nom affiché (`Case(caseId: "happy-1")`) contient l'identifiant | la gate relie un `testcase` JUnit à un cas déclaré par cet identifiant |
| Tests réseau | classe dont le nom contient `Network` (`LookupNetworkTests`) **et** `[Trait("Category","network")]` | le nom pour le rapport, le trait pour le filtre |
| Jeton d'annulation | `TestContext.Current.CancellationToken` passé à tout appel async | l'analyseur xUnit1051 en fait une **erreur** sous `TreatWarningsAsErrors` (vérifié) |

**La commande de test du framework** — lancée depuis `workspace/src/{AppName}/`,
qui porte à la fois `{AppName}.csproj` et `{AppName}.slnx` (vérifié : pas
d'ambiguïté de projet) :

```bash
dotnet test --logger "junit;LogFilePath=<fichier absolu>"
```

Le rapport JUnit porte `testcase/@classname` = nom complet de la classe,
`testcase/@name` = méthode et arguments du `[Theory]`, et les traits en
`<property>` — c'est ce que parse la gate. Le smoke exclut le réseau par
`dotnet test --filter "Category!=network"`.

---

## 6. Commande de smoke

Aucun modèle, aucun réseau, 0 token :

```bash
cd workspace/src/{AppName}
dotnet restore                     # résolution contre Directory.Packages.props
dotnet format --verify-no-changes  # style, sans rien réécrire
dotnet build -warnaserror          # analyseurs + nullabilité = erreurs
dotnet test --filter "Category!=network"
dotnet run --project . -- --help   # la CLI démarre ; codes de sortie listés
dotnet run --project . -- health --json
```

Smoke Timeout : 180 s (la première restauration NuGet domine).

---

## 7. Pièges connus

1. **`<LangVersion>latest</LangVersion>`.** Une montée du SDK change le langage
   sans qu'aucun fichier du dépôt ne bouge, et du code qui compilait cesse de
   compiler — ou pire, compile différemment. Pin explicite, toujours.
2. **La nullabilité désactivée projet par projet.** Un seul `<Nullable>disable</Nullable>`
   dans un `.csproj` de test, et la frontière de §5.3 n'existe plus pour la
   moitié du code. `Directory.Build.props` l'impose ; un projet qui la redéfinit
   est un contournement, pas une exception.
3. **`async void`.** Une exception y est levée sur le thread pool et termine le
   processus sans passer par la gestion de bornes. Interdit hors gestionnaires
   d'événements, dont cette application n'a pas.
4. **Le `CancellationToken` accepté puis ignoré.** C'est le mode d'échec le plus
   courant du portage depuis un code synchrone : la signature est correcte, le
   timeout de borne se déclenche, et l'appel HTTP continue. Un test L1 par outil
   doit vérifier qu'une annulation annule réellement.
5. **`System.Text.Json` et les `required` members.** Une désérialisation qui
   omet un membre `required` lève — c'est voulu — mais le message ne nomme pas
   toujours le champ. Utiliser la génération de source (`JsonSerializerContext`)
   donne des diagnostics utilisables et supprime la réflexion au démarrage.
6. **Les décimaux monétaires.** `double` sur un montant produit des totaux faux
   de quelques centimes, que personne ne remarque avant la facturation.
   `decimal` pour tout montant, et l'unité dans la description de l'outil.
7. **`DateTime` au lieu de `DateTimeOffset`.** Un `DateTime` sans offset laisse
   le fuseau implicite, et l'agent calcule « depuis N jours » dans le mauvais.
   `DateTimeOffset` partout, sérialisé en ISO 8601 avec offset.
8. **Central Package Management partiellement appliqué.** Un `PackageReference`
   qui porte encore `Version=` dans un `.csproj` échappe au fichier central :
   deux versions du même paquet cohabitent, et le comportement dépend de l'ordre
   de résolution. `dotnet build` l'avertit — encore faut-il que les
   avertissements soient des erreurs (§2).
9. **Les analyseurs désactivés « temporairement ».** `AnalysisLevel` abaissé
   pour faire passer un build pressé ne remonte jamais. Si une règle doit
   sauter, elle saute **nommément** dans `.editorconfig`, avec la raison — pas
   par abaissement global du niveau.
10. **Le projet de test qui référence l'implémentation du framework.** Il rend
    les tests L1 dépendants du framework agentic, donc lents et non
    déterministes. Les tests référencent `{AppName}` et les abstractions, jamais
    le paquet d'implémentation — c'est aussi ce qui garde `orchestration/` comme
    seul endroit nommant le framework.
11. **Un répertoire de couche en PascalCase.** `Tools/` au lieu de `tools/`
    passe sous Windows (système de fichiers insensible à la casse) et fait
    refuser l'écriture sous Linux : le projet se construit sur le poste du
    développeur et pas dans le pipeline. Casse des répertoires = celle de §4,
    toujours.
12. **Le gabarit `xunit3` absent.** `dotnet new xunit3` échoue sur un SDK nu
    (seul `xunit`, v2, est livré) ; le générateur qui se rabat sur `xunit`
    produit un projet xunit v2, hors catalogue. §2.1 installe le gabarit épinglé.
13. **La sortie de build de `dotnet run` sur stdout.** Quand un runner lance
    l'application par `dotnet run --project … --` et qu'un build est
    nécessaire, les messages MSBuild peuvent précéder le NDJSON. Construire
    avant (`dotnet build -warnaserror`), ou lancer l'exécutable publié.

---

## 8. Contrat d'exécution

C'est la section que `serving/cli.md` §3.5 renvoie à chaque fiche de langage,
et que `gen-app-context` recopie dans le contexte projet.

L'application .NET implémente la CLI de `serving/cli.md` §3.1-3.3 **à
l'identique** — mêmes commandes (`run`, `resume`, `health`, `inspect`, `trace`,
`version`), même NDJSON `RunEvent` (`event_schema: "1"`, champs snake_case :
`JsonNamingPolicy.SnakeCaseLower`), mêmes codes de sortie ; le détail .NET est
dans `serving/cli-dotnet.md`. Elle honore en plus les trois points de
`serving/cli.md` §3.5, cités tels quels :

1. `run --json --input-file -` lit l'entrée sur stdin ;
2. si `SDDA_EVAL_ISOLATION=mocked`, l'app sert les outils depuis
   `SDDA_EVAL_FIXTURES` (dossier de fixtures JSONL par outil,
   `tools/{outil}.jsonl`) et le retrieval figé depuis le même dossier
   (`retrieval/{index}.jsonl`), sans aucun appel réseau d'outil ;
3. `retrieve --json --index ID --query-file - [--k N]` émet un événement
   `retrieval` (`index_id`, `result_ids[]`, `scores[]`) puis `run_finished` —
   c'est ce que la RETRIEVAL GATE mesure, sans agent.

| | Commande |
|---|---|
| **Développement** | `dotnet run --project workspace/src/{AppName} --` puis la commande, ex. `dotnet run --project workspace/src/{AppName} -- run --json --input-file -` |
| **Livrable `cli-exe`** | `dotnet publish workspace/src/{AppName} -c Release -r linux-x64 -p:PublishSingleFile=true -p:SelfContained=true` → l'exécutable `…/bin/Release/net10.0/linux-x64/publish/{AppName}` (`{AppName}.exe` sous `win-x64`) |
| **Runners du framework** | `--executor cli` — commande dérivée du langage actif : `dotnet run --project workspace/src/{AppName} --` ; ou `--executor cmd:<commande>`, typiquement le chemin de l'exécutable publié |
| **Tests (TOOL GATE)** | `dotnet test --logger "junit;LogFilePath=<fichier absolu>"` depuis `workspace/src/{AppName}/` (§5.4) |

`dotnet run --project workspace/src/{AppName}` fonctionne parce que le projet
exécutable **est** le `.csproj` de la racine (§4) : un projet imbriqué sous
`src/` ou dans un sous-dossier de surface ne répondrait pas à cette commande. Le
lancement avec une entrée sur stdin a été vérifié sur une sonde
(`printf … | dotnet run --project <racine> -- --input-file -`, code 0).

Chaque fiche .NET transverse précise ce qu'elle doit au contrat :
`rag/hybrid-dotnet.md` §8 porte la commande `retrieve`,
`tools/mcp-dotnet.md` §8 et `dataaccess/view-per-agent-dotnet.md` §8
l'isolement des outils, `vectorstore/pgvector-dotnet.md` §8 l'absence de
connexion en isolement, `observability/otel-genai-dotnet.md` §8 la trace de
chaque invocation, `eval/xunit-eval.md` §8 les tests L1 qui prouvent le contrat.
