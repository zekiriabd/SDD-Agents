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
| **Tests** | `xunit.v3` 4.0.x + `NSubstitute` 6.2.x — cf. `eval/*.md` pour la couche eval |
| **Modèles de données** | `record` + `required` members + `System.Text.Json` (source-generated) |
| **Logs** | `ILogger<T>` avec logging source-generated — jamais `Console.WriteLine` |
| **Namespace racine** | `{AppName}` (PascalCase), ex. `SupportAssistant` |

> Les versions sont portées par `framework/ms-agent-framework.libs.json`, seul
> fichier qui fasse foi. Pas de `.libs.json` pour la fiche `lang` : comme en
> Python, `csharp` seul n'installe rien — c'est le framework actif qui décide
> du jeu de paquets.

### 2.1 Init (idempotent)

```bash
if [ ! -f "workspace/src/{AppName}/{AppName}.slnx" ]; then
  mkdir -p workspace/src/{AppName}
  cd workspace/src/{AppName}
  dotnet new sln --format slnx --name {AppName}
  dotnet new classlib -o src/{AppName} --framework net10.0
  dotnet new xunit3 -o tests/{AppName}.Tests --framework net10.0
  dotnet sln add src/{AppName} tests/{AppName}.Tests
  dotnet add tests/{AppName}.Tests reference src/{AppName}
fi
```

`Directory.Packages.props`, `Directory.Build.props` et `.editorconfig` sont
écrits à la racine de `workspace/src/{AppName}/` et versionnés : ce sont eux qui
portent les conventions de §5, et un projet qui les redéfinit localement les
contourne.

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

| Concept | Idiome | Où |
|---|---|---|
| **AGENT** | un dossier `Agents/{AgentSlug}/` exposant `Build(AgentDeps deps, Bounds bounds)` ; le type concret dépend du framework | `src/{AppName}/Agents/` |
| **PROMPT** (`prompt_ref` + hash) | `LoadedPrompt(string Text, string Sha256, string Path)` chargé au démarrage par `Prompts.LoadSystemPrompt(slug)` depuis `workspace/prompts/{slug}.system.md`. **Jamais de texte système dans le code.** | `src/{AppName}/Prompts.cs` |
| **TOOL** | une méthode `async Task<TOut>` typée + un `ToolSpec` (record, init-only) portant `Name`, `Description`, `SideEffectClass`, `Trust`, `TimeoutSeconds`, `RetryPolicy` ; schémas dérivés des records d'entrée/sortie | `src/{AppName}/Tools/{ToolSlug}.cs` |
| **BOUNDS** (P12) | `record Bounds` : `MaxIterations`, `MaxToolCalls`, `MaxDelegationDepth`, `TimeoutSeconds`, `BudgetUsd` ; hiérarchie `BoundExceededException` → `IterationsExceeded`, `ToolCallsExceeded`, `DelegationDepthExceeded`, `TimeoutExceeded`, `BudgetExceeded` | `src/{AppName}/Bounds.cs` |
| **MODEL BINDING** (tier) | `AppOptions.RuntimeTierMap : IReadOnlyDictionary<Tier, string>` — le code manipule `Tier`, jamais un nom de modèle ; la résolution se fait dans `Models.Resolve(tier) -> IChatClient` | `src/{AppName}/AppOptions.cs`, `Models.cs` |
| **RETRIEVER / INDEX** | `Retrieval/{IndexSlug}/` exposant `RetrieveAsync(query, topK, tenant) -> IReadOnlyList<RetrievedChunk>` ; `RetrievedChunk` porte `DocId`, `ChunkId`, `Score`, `Content`, `Citation` | `src/{AppName}/Retrieval/` |
| **DATA ACCESS** | `Data/` : vues et outils générés (cf. `dataaccess/*.md`) | `src/{AppName}/Data/` |
| **ORCHESTRATION PATTERN** | `Orchestration/Graph.cs` (ou `Pipeline.cs`) — **seul endroit où le framework apparaît nommément** | `src/{AppName}/Orchestration/` |
| **GUARDRAIL** | `Guardrails/{Id}.cs` : `Check(payload) -> GuardrailVerdict(bool Passed, string Reason, OnTrip OnTrip)` | `src/{AppName}/Guardrails/` |
| **TRACE SPAN** | `ActivitySource` + conventions GenAI d'OpenTelemetry ; helpers `AgentTurn()`, `LlmCall()`, `ToolCall()`, `Retrieval()` rendant un `Activity` disposable | `src/{AppName}/Tracing/` |
| **Secrets** | `IOptions<AppOptions>` liés depuis `IConfiguration` ; le code lit `options.Value.LlmApiKey` — **jamais** `Environment.GetEnvironmentVariable` → `[SEC_ENV_VAR_FORBIDDEN]` | `src/{AppName}/AppOptions.cs` |
| **Trust posture** (P8) | `readonly record struct Untrusted(string Value)` pour tout texte non maîtrisé ; les méthodes qui construisent un message système n'acceptent **pas** `Untrusted` — le compilateur le refuse | `src/{AppName}/Trust.cs` |

### 3.1 Chargement et hash des prompts

```csharp
// src/{AppName}/Prompts.cs
namespace {AppName};

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
// src/{AppName}/Bounds.cs
namespace {AppName};

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
workspace/src/{AppName}/
├── {AppName}.slnx
├── Directory.Build.props        # TFM, nullable, warnings-as-errors, analyseurs
├── Directory.Packages.props     # Central Package Management — 1 version par paquet
├── .editorconfig
├── src/{AppName}/
│   ├── AppOptions.cs            # IOptions : tiers, secrets, bornes par défaut
│   ├── Models.cs                # Resolve(tier) -> IChatClient — seul contact avec le provider
│   ├── Prompts.cs               # LoadSystemPrompt + hash normalisé
│   ├── Bounds.cs                # Bounds, BoundExceededException*, OnBoundExceeded
│   ├── Trust.cs                 # Untrusted, WrapUntrusted(text, source)
│   ├── Agents/
│   │   └── {AgentSlug}/
│   │       ├── Agent.cs         # Build(deps, bounds) ; lit le prompt, câble outils + bornes
│   │       ├── Schemas.cs       # records d'entrée / sortie (contrat §6)
│   │       └── Deps.cs          # AgentDeps : outils, retrievers, model, tracer — injection explicite
│   ├── Tools/
│   │   ├── ToolSpec.cs          # ToolSpec, SideEffectClass, Trust, RetryPolicy
│   │   ├── ToolRegistry.cs      # Register(spec, fn) ; GetForAgent(agentId) — moindre privilège
│   │   ├── {ToolSlug}.cs
│   │   └── Mcp/                 # cf. tools/mcp.md
│   ├── Retrieval/
│   │   └── {IndexSlug}/         # cf. rag/*.md, vectorstore/*.md
│   ├── Data/                    # cf. dataaccess/*.md
│   ├── Guardrails/
│   ├── Orchestration/           # cf. framework/*.md — SEUL endroit nommant le framework
│   ├── Tracing/                 # cf. observability/*.md
│   └── Serving/                 # cf. serving/*.md
└── tests/{AppName}.Tests/       # L1 (unit) + L2 (contrats d'outils) — modèle mocké
    ├── BoundsTests.cs
    ├── PromptsTests.cs
    └── Tools/{ToolSlug}Tests.cs
```

Les evals (L3+) vivent dans `workspace/evals/`, hors de la solution — ownership
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
| `Console.WriteLine` hors `Serving/` | `[LOG_UNSTRUCTURED]` | recherche |
| `#pragma warning disable` sans justification écrite sur la ligne précédente | `[ANALYZER_SUPPRESSED]` | recherche ; une suppression muette annule §5.2 en une ligne |
| `<Nullable>disable</Nullable>` ou `!` (null-forgiving) hors interop | `[NULLABILITY_DISABLED]` | recherche dans les `.csproj` et le code |
| `dynamic` | `[TYPE_ERASED]` | recherche — il annule la garantie de §5.3 |
| Nom de framework hors `Orchestration/` | `[FRAMEWORK_LEAK_IN_CONTRACT]` | `validate_ir.py` sur les contrats |

`TreatWarningsAsErrors` fait la moitié du travail. L'autre moitié est
l'interdiction de le désactiver localement : c'est `#pragma warning disable` qui
transforme une garantie de compilation en intention.

### 5.3 Frontière de confiance dans le type system

```csharp
// src/{AppName}/Trust.cs
namespace {AppName};

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

---

## 6. Commande de smoke

Aucun modèle, aucun réseau, 0 token :

```bash
cd workspace/src/{AppName}
dotnet restore                     # résolution contre Directory.Packages.props
dotnet format --verify-no-changes  # style, sans rien réécrire
dotnet build -warnaserror          # analyseurs + nullabilité = erreurs
dotnet test --filter "Category!=Network"
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
    le paquet d'implémentation — c'est aussi ce qui garde `Orchestration/` comme
    seul endroit nommant le framework.
