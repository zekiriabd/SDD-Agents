# Stack: dotnet-minimalapi (backend)

Stack ID: backend-dotnet-minimalapi
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: csharp
Scope: la **maison HTTP** en ASP.NET Core Minimal API autour de la surface `serving/aspnet-minimal.md` quand `DeliverableType: backend-api` et `ApiFramework: aspnet-minimal` — solution, projets, injection, `IOptions<T>`, middlewares, sécurité, Serilog, publication. Hérité de SDD_Pro `backend/dotnet-minimalapi.md`, transposé : EF Core, AutoMapper, Swashbuckle et MediatR sont retirés (raisons dans `serving/aspnet-minimal.libs.json`, `absentByDesign`). **Pas de `.libs.json` propre** : les pins vivent dans `serving/aspnet-minimal.libs.json`, aligné sur `framework/ms-agent-framework.libs.json` pour la famille OpenTelemetry. Suppose `lang/csharp.md`.

---

## 1. Rôle et périmètre

`serving/aspnet-minimal.md` dit par où l'on entre — les `MapGroup`, le flux
SSE, `Identity` dérivée du `ClaimsPrincipal`, le contrat OpenAPI natif confronté
à l'IR. Cette fiche dit dans quelle maison : le **projet unique**
`workspace/src/{AppName}/{AppName}.csproj` (disposition plate, couches en
minuscules — `lang/csharp.md` §4), la composition dans `app/`, la configuration
`IOptions<T>`, les middlewares, la publication.

> **Pas de projet `{AppName}.Serving.Http`, pas de `src/` imbriqué.** La
> matrice d'ownership attribue les zones d'écriture par répertoire de couche
> directement sous `workspace/src/{AppName}/` (`app/`, `serving/`, `data/`…),
> sensible à la casse sous Linux : un projet séparé ou un `src/{AppName}.*/`
> ne correspond à aucune zone et l'écriture est refusée aux `dev-*`. Et les
> runners d'eval lancent l'application par `dotnet run --project workspace/src/{AppName} --` :
> un second projet serait une autre application que celle qu'on mesure. La
> surface HTTP est le dossier `serving/http/` du projet unique, qui ajoute
> `<FrameworkReference Include="Microsoft.AspNetCore.App" />`
> (`serving/aspnet-minimal.md` §4) ; la CLI reste générée dans `serving/`.

Ce que la fiche garde de SDD_Pro : constructeur primaire, `record` immuables,
`FluentValidation` au bord, `ProblemDetails` RFC 9457 en middleware global,
Serilog structuré, `IConfiguration` seul lecteur des secrets, les en-têtes de
sécurité, l'interdiction du `.Result`/`.Wait()`. Ce qui est retiré : EF Core et
le scaffolding Database-First (aucune base possédée), AutoMapper (les contrats
sont générés, rien à mapper), MediatR (l'orchestration est dans le graphe),
Swashbuckle (`Microsoft.AspNetCore.OpenApi` est natif).

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `backend-dotnet-minimalapi` |
| **Langage** | C# 14, `net10.0`, `<Nullable>enable</Nullable>`, `TreatWarningsAsErrors` (`lang/csharp.md`) |
| **Framework** | ASP.NET Core Minimal API — pins dans `serving/aspnet-minimal.libs.json` |
| **Build** | `dotnet` + Central Package Management (`Directory.Packages.props`) |
| **Validation** | `FluentValidation` sur des `record` **générés** depuis l'IR |
| **Config** | `IConfiguration` → `IOptions<T>` validés au démarrage (`ValidateOnStart`) ; `appsettings.json` sans valeur secrète, variables d'environnement en production |
| **Logs** | Serilog, `JsonFormatter`, enrichi du `TraceId` |
| **Surface associée** | `serving/aspnet-minimal.md` (obligatoire) · `serving/cli-dotnet.md` (toujours générée) |
| **Paramètres STACK.md** | `DeliverableType: backend-api`, `ApiFramework: aspnet-minimal`, `ApiAuthMode`, `ApiContractFirst`, `## Active Architecture Pattern` |
| **Smoke** | §8 |

---

## 3. Mapping couche → répertoire

Racine `workspace/src/{AppName}/` — **ce répertoire est le projet** ; chemins
relatifs à lui, couches en minuscules, espaces de noms en PascalCase
(`namespace {AppName}.App;` dans `app/`) :

| Couche | Emplacement | Owner |
|---|---|---|
| Projet | `{AppName}.csproj` (`OutputType Exe`, `FrameworkReference Microsoft.AspNetCore.App`, `DefaultItemExcludes tests/**`) · `{AppName}.slnx` · `Directory.Packages.props` · `Directory.Build.props` · `README.md` · `Dockerfile` · `appsettings.json` | `dev-backend` |
| Point d'entrée | `serving/Program.cs` — `Main` : commandes CLI, et `serve` → `serving/http/HttpHost.cs` (builder, `AddSystem()`, middlewares, `MapRuns()`, `Run()`) | `dev-api` |
| Entrée HTTP | `serving/http/RunsEndpoints.cs` (`MapGroup("/v1/runs")`), `HealthEndpoints.cs`, `OpenApi/` | `dev-api` |
| Service | `serving/RunService.cs` — **partagé** par la CLI et le HTTP (`serving/aspnet-minimal.md` §4) | `dev-api` |
| Transverse HTTP | `serving/http/Middleware/{ProblemDetails,Identity,SecurityHeaders,Correlation}Middleware.cs` | `dev-api` |
| Sécurité | `serving/http/IdentityFactory.cs` (`ClaimsPrincipal` → `Identity`) | `dev-api` |
| Composition | `app/Composition.cs` — `IServiceCollection.AddSystem(IConfiguration)` : la seule méthode qui enregistre le moteur | `dev-backend` |
| Cas d'usage | `app/UseCases/` | `dev-backend` |
| Domaine | `app/domain/{contexte}/` — `Values.cs`, `Rules.cs`, `Ports.cs` (`interface`) — aucune dépendance | `dev-backend` |
| Config | `app/AppOptions.cs` (`IOptions<T>`) · `appsettings.json` · `app_config.json` | `dev-backend` |
| Modèles d'échange | `serving/http/Contracts/` — **générés** depuis l'IR (§6 de `serving/aspnet-minimal.md`) | `dev-api` |
| Schémas des sources | `data/schemas/` (ressources embarquées) | `dev-data` |
| Résilience | `app/Resilience.cs` — `AddStandardResilienceHandler` par client nommé | `dev-backend` |
| Tests | `tests/{AppName}.Tests.csproj` (xunit.v3, VSTest : `IsTestingPlatformApplication=false`) — `tests/serving/http/OpenApiMatchesIrTests.cs` compris | `qa-tests` |

Règle de frontière qui remplace l'ancien projet séparé : **aucun
`using Microsoft.AspNetCore` hors de `serving/http/`** (recherche L0). `app/`,
le Domaine, les agents et les outils ne voient ni `HttpContext` ni
`IResult` — c'est ce qui garde la CLI des evals indépendante du transport.

---

## 4. Idiomes imposés

- **`AddSystem()`** est l'unique point de composition ; `Program.cs` l'appelle
  et n'enregistre rien du moteur lui-même.
- Constructeur primaire pour l'injection ; `record` avec `required` pour tout
  modèle d'échange ; aucune classe mutable dans le contrat.
- `IOptions<AppOptions>` avec `.ValidateDataAnnotations().ValidateOnStart()` :
  un secret manquant arrête le processus au démarrage avec le nom de la clé.
- Identité : `IdentityFactory` dérive `Identity` du `ClaimsPrincipal` posé par
  `UseAuthentication()` ; jamais depuis le corps (`[SERVING_IDENTITY_FROM_PAYLOAD]`).
- Erreurs : `UseExceptionHandler` + `IProblemDetailsService` ; la table
  `[CLASS] → StatusCode` est **dérivée** des `ExitCodes` de la CLI, pas
  réécrite.
- `async Task<T>` partout ; `.Result`, `.Wait()`, `async void` interdits.
- `JsonSerializerContext` source-generated pour `RunEvent` : le flux SSE ne
  passe pas par la réflexion.
- En-têtes de sécurité (HSTS derrière TLS, `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`) en middleware ; CORS explicite depuis
  `AppOptions.CorsOrigins`, jamais `AllowAnyOrigin()` avec identifiants.
- Logs : `ILogger<T>` source-generated ; Serilog enrichi de `RunId`, `TraceId`,
  `Tenant` haché ; aucun `Console.WriteLine`.

---

## 5. Librairies

Source de vérité : `serving/aspnet-minimal.libs.json`. Cette fiche n'ajoute
aucune dépendance.

| Usage | Paquet |
|---|---|
| contrat OpenAPI natif | `Microsoft.AspNetCore.OpenApi`, `Microsoft.OpenApi` |
| sondes | `Microsoft.Extensions.Diagnostics.HealthChecks` |
| logs | `Serilog.AspNetCore`, `Serilog.Sinks.Console` |
| validation | `FluentValidation`, `FluentValidation.DependencyInjectionExtensions` |
| résilience | `Microsoft.Extensions.Http.Resilience`, `Polly` |
| identité | `Microsoft.Identity.Web` (`auth-azure-ad`), `Microsoft.AspNetCore.Authentication.JwtBearer` (`auth-jwt`) — ON-DEMAND |
| traces | `OpenTelemetry.Instrumentation.AspNetCore`, `OpenTelemetry.Exporter.OpenTelemetryProtocol` — ON-DEMAND |

**Absents par conception** : `Microsoft.EntityFrameworkCore`, `AutoMapper`,
`MediatR`, `Swashbuckle.AspNetCore`.

---

## 6. Interdits

- secret, clé d'API, chaîne de connexion en dur ; `Environment.GetEnvironmentVariable`
  sur une clé sensible (lecture par `IConfiguration` seule) ;
- logique métier dans un endpoint ou un middleware ; appel de modèle hors
  `agents/` ;
- un second projet (`{AppName}.Serving.Http`, `{AppName}.Core`…) ou un
  répertoire de couche en PascalCase (`App/`, `Serving/`) : hors des zones de la
  matrice d'ownership ;
- `DbContext`, entité, migration : aucune base possédée ;
- exception brute au client ; `Console.WriteLine` ;
- `.Result`, `.Wait()`, `async void` ;
- `dynamic` / `object` non justifié dans une signature publique ;
- `AllowAnyOrigin()` avec identifiants ; endpoint sensible sans
  `RequireAuthorization()` quand `ApiAuthMode` ≠ `none` ;
- `<Nullable>disable</Nullable>`, `TreatWarningsAsErrors` retiré,
  `<LangVersion>latest</LangVersion>` ;
- `TODO`, `FIXME`, placeholder ; `PublishAot` activé sans avoir annoté la
  réflexion ; `bin/`, `obj/`, `appsettings.*.json` avec valeurs commités.

---

## 7. Pièges connus

1. **Deux générateurs OpenAPI.** Swashbuckle et `Microsoft.AspNetCore.OpenApi`
   produisent deux documents ; l'API GATE ne saurait pas lequel confronter.
   Un seul, le natif.
2. **`ConnectionStrings:Default` en dur.** Post-mortem SDD_Pro : la clé lue
   n'était pas celle écrite. Ici il n'y a pas de base — mais le même piège
   existe pour `Llm:ApiKey` : le nom dans `appsettings.json` est celui que
   `IOptions<T>` lie, et il vient de `STACK.md ## Active Secrets`.
3. **`/readyz` qui répond avant la composition.** Le `HealthCheck` de
   disponibilité vérifie que le `RunService` est résolu et que le checkpointer
   partagé répond.
4. **`PublishAot` « pour aller plus vite ».** Toute réflexion non annotée casse à
   l'exécution ; hors périmètre tant que rien ne le mesure.
5. **HSTS en développement sans TLS.** Le navigateur mémorise ; poser HSTS
   uniquement quand `IsProduction()`.
6. **Le projet séparé « pour isoler le web ».** Réflexe hérité de SDD_Pro
   (`{AppName}.Serving.Http`). Ici il sort de la zone `serving/**` de `dev-api`,
   et `dotnet run --project workspace/src/{AppName}` lance alors une application
   sans surface HTTP — ou une autre que celle qu'on livre. L'isolement se
   vérifie par la règle `using Microsoft.AspNetCore` de §3.

---

## 8. Smoke

```bash
cd workspace/src/{AppName}
dotnet restore --locked-mode
dotnet format --verify-no-changes
dotnet build --no-restore --nologo -warnaserror
dotnet test --no-build --filter "Category!=network"    # projet de test unique, VSTest
dotnet run --project . --no-build -- serve --urls http://localhost:8080 & PID=$!; sleep 4
curl -sf http://localhost:8080/healthz -o /dev/null
curl -sf http://localhost:8080/openapi/v1.json > /tmp/openapi.json   # chemin par défaut de Microsoft.AspNetCore.OpenApi, comme serving/aspnet-minimal.md §8
kill $PID
python .sdda/sdda.py validate-api-contract --mission {n} --json
```

Timeout : 120 s.
