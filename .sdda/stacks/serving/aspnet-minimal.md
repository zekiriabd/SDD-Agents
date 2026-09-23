# Stack: aspnet-minimal (serving)

Stack ID: serving-aspnet-minimal
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: csharp
Scope: surface d'exposition **HTTP .NET** de l'application agentic — Minimal API, streaming SSE, identité de l'appelant, sondes de vivacité, OpenAPI dérivé de l'IR, livraison en exécutable auto-contenu ou en conteneur. C'est la surface du livrable `backend-api` sur `lang/csharp.md`. Le contrat d'événements et le mapping `[CLASS]` sont **identiques** à `serving/cli.md` §3.2-3.3 et `serving/fastapi-sse.md` §3.2-3.3 : c'est la même application vue par un autre transport, dans un autre écosystème.

---

## 1. Rôle et périmètre

Le pendant .NET de `fastapi-sse.md`. La règle qui gouverne la fiche est la même
et mérite d'être répétée parce que l'écosystème .NET invite à l'enfreindre :

> **Le HTTP est un transport, pas une couche métier.** Un `Controller` qui
> décide, un `Middleware` qui reformule une réponse de modèle, un
> `IExceptionFilter` qui « corrige » une sortie non conforme : chacun place de
> la logique hors du chemin que les evals parcourent. Ce qui n'est pas dans
> `RunService` n'est pas mesuré.

Pourquoi Minimal API et non MVC : le service expose **six routes**, aucune vue,
aucun binding de formulaire, aucune convention de routage à découvrir. Un
`Controller` par route ajoute une indirection dont personne ne tire parti, et
la génération d'OpenAPI depuis des attributs est plus difficile à confronter à
l'IR qu'une déclaration explicite (§6). `aspnet-mvc` reste disponible pour un
service qui doit s'insérer dans une application MVC existante — c'est le seul
cas où il gagne.

Hors périmètre : hébergement, TLS, passerelle, dimensionnement.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `serving-aspnet-minimal` |
| **Langage** | C# / .NET 9 (`lang/csharp.md`) |
| **Librairies** | `Microsoft.AspNetCore.App` (framework partagé) · `Microsoft.AspNetCore.OpenApi` 9.x · `System.Text.Json` (source-generated) · `Microsoft.Extensions.Diagnostics.HealthChecks` 9.x — capability `serving-aspnet-minimal` du `.libs.json` du framework actif |
| **Point d'entrée** | `{AppName}.Serving.Http/Program.cs` → `dotnet run` ou binaire publié |
| **Paramètres STACK.md** | `DeliverableType`, `ApiFramework: aspnet-minimal`, `## Active Architecture Pattern` (couches de la coquille), `## Active Backend Stack` → `backend/dotnet-minimalapi.md` (le projet, la DI, la config, le packaging autour de cette surface), `ApiContractFirst`, `ApiAuthMode`, `ServingLocalPort`, `StreamingEnabled`, `HumanInTheLoopEnabled` |
| **Contrat machine** | OpenAPI 3.1 dérivé de l'IR + flux SSE dont chaque `data:` est un `RunEvent` |
| **Livrables possibles** | `backend-api` (service), `container` (image), `cli-exe` (`PublishSingleFile`), `library` (`.dll` référençable) — §7 |

---

## 3. Mapping des concepts SDD_Agents → idiomes ASP.NET

### 3.1 Endpoints

Identiques à `fastapi-sse.md` §3.1 — mêmes chemins, mêmes rôles, mêmes
garanties. Un client écrit contre l'un fonctionne contre l'autre, et c'est le
but : la surface est un transport, le produit est le même.

| Méthode | Chemin | Idiome .NET |
|---|---|---|
| `POST` | `/v1/runs` | `MapPost` rendant `IResult` ; SSE via `IAsyncEnumerable<RunEvent>` |
| `POST` | `/v1/runs/{threadId}/resume` | enregistré **seulement** si l'IR déclare `humanInTheLoop` |
| `GET` | `/v1/runs/{runId}` | lecture de la trace, 0 token |
| `DELETE` | `/v1/runs/{runId}` | `CancellationTokenSource.Cancel()` sur le run |
| `GET` | `/v1/inspect` | IR résumé |
| `GET` | `/healthz` · `/readyz` | `MapHealthChecks` avec deux `tags` distincts — `live` et `ready` |

### 3.2 Streaming

`IAsyncEnumerable<RunEvent>` est l'idiome naturel, et il porte un piège précis :
**la réponse doit être écrite sans mise en tampon**.

```csharp
app.MapPost("/v1/runs", async (RunRequest req, HttpContext ctx, IRunService svc, CancellationToken ct) =>
{
    ctx.Response.Headers.ContentType   = "text/event-stream";
    ctx.Response.Headers.CacheControl  = "no-cache";
    ctx.Response.Headers["X-Accel-Buffering"] = "no";
    // Sans DisableBuffering, la réponse part par blocs : le streaming est
    // implémenté, testé, et n'existe pas pour le client.
    ctx.Features.Get<IHttpResponseBodyFeature>()?.DisableBuffering();

    await foreach (var evt in svc.RunAsync(req.ToInput(), Identity.From(ctx), ct))
    {
        await ctx.Response.WriteAsync($"event: {evt.Event}\ndata: {Json.Serialize(evt)}\n\n", ct);
        await ctx.Response.Body.FlushAsync(ct);   // par événement, jamais par bloc
    }
});
```

`ct` est le `CancellationToken` de la requête : il **est** la propagation
d'annulation à la déconnexion, gratuitement — à condition de le passer jusqu'au
bout et de ne jamais le remplacer par `CancellationToken.None` « pour que ça
marche ».

Keepalive : un `: keepalive\n\n` toutes les 15 s via un `PeriodicTimer`, pour
la même raison qu'en Python — un proxy coupe une connexion inactive pendant
qu'un agent réfléchit.

### 3.3 Statuts HTTP ↔ `[CLASS]`

Table identique à `fastapi-sse.md` §3.3. Le mapping vit dans
`Serving/Http/StatusMapping.cs`, dérivé de `ExitCodes.cs` — **une seule table de
vérité, trois projections** (code de sortie CLI, statut HTTP, statut HTTP .NET).

Un point propre à ASP.NET : le middleware de `ProblemDetails` doit être
**configuré pour ne pas fuir**. Par défaut, en environnement `Development`, il
inclut la `stack trace` — qui porte les chemins, parfois un fragment de prompt.
La configuration est explicite et identique dans tous les environnements
(§5.3) : un comportement de sécurité qui dépend de `ASPNETCORE_ENVIRONMENT` est
un comportement qu'on livrera un jour par accident.

### 3.4 Mapping des entités

| Concept | Idiome .NET |
|---|---|
| **Identité de l'appelant** | `ClaimsPrincipal` → `Identity` typée, construite **uniquement** depuis `ctx.User` / en-têtes selon `ApiAuthMode`. `azure-ad` → `AddMicrosoftIdentityWebApi` ; `api-key` → `AuthenticationHandler` dédié ; `mtls` → `ClientCertificate` |
| **`ToolContext`** | injecté en `Scoped` par requête ; contient `TenantId`, `RunId`, `ThreadId`, `Deadline`, `BudgetUsd` |
| **Configuration** | `IOptions<{AppName}Settings>` lié depuis `appsettings.json` + variables d'environnement. **Jamais** `Environment.GetEnvironmentVariable` direct pour un secret → `[SEC_ENV_VAR_FORBIDDEN]` |
| **Secrets** | `appsettings.json` (non commité) ou Key Vault ; projetés depuis `STACK.md ## Active Secrets` par `dev-api` |
| **TRACE** | `ActivitySource` + exportateur OTel ; `traceparent` W3C propagé nativement |
| **Sérialisation** | `JsonSerializerContext` source-generated — obligatoire pour l'AOT (§7) et 2 à 3× plus rapide sur un flux d'événements |

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/src/
├── {AppName}.Core/                      # PARTAGÉ — aucune dépendance ASP.NET
│   ├── Serving/IRunService.cs           # le contrat que toute surface consomme
│   ├── Serving/RunEvent.cs              # schéma d'événements, = celui de la CLI
│   ├── Serving/ExitCodes.cs             # [CLASS] -> code, source de StatusMapping
│   └── Serving/ToolContext.cs
├── {AppName}.Serving.Http/
│   ├── Program.cs                       # WebApplication ; MapX ; lifetime (flush traces)
│   ├── Endpoints/RunEndpoints.cs        # /v1/runs* — aucun métier
│   ├── Endpoints/HealthEndpoints.cs     # /healthz, /readyz, /v1/inspect
│   ├── Sse/SseWriter.cs                 # RunEvent -> flux ; keepalive ; flush par événement
│   ├── Security/IdentityFactory.cs      # ClaimsPrincipal -> Identity ; JAMAIS depuis le corps
│   ├── StatusMapping.cs                 # [CLASS] -> StatusCode, dérivé d'ExitCodes
│   ├── Contracts/                       # RunRequest/RunResponse GÉNÉRÉS depuis l'IR (§6)
│   └── appsettings.json                 # projeté depuis STACK.md ; les secrets viennent de .env, jamais du fichier
└── {AppName}.Serving.Cli/               # si serving/cli.md est aussi actif — même Core

workspace/src/{AppName}/tests/
└── {AppName}.Serving.Http.Tests/
    ├── OpenApiMatchesIrTests.cs         # L1 : API GATE
    ├── IdentityTests.cs                 # L1 : tenant du corps refusé ; sans jeton -> 401
    ├── SseContractTests.cs              # L1 : chaque data: désérialise ; run_finished en dernier
    ├── StatusMappingTests.cs            # L1 : table exhaustive ; bornes != 5xx
    └── CancellationTests.cs             # L1 : déconnexion -> CancellationToken -> budget arrêté
```

`{AppName}.Core` **ne référence pas** ASP.NET. C'est ce qui permet au même
`RunService` de servir la CLI, le HTTP et un lot — et ce qui rend le livrable
`library` possible (§7). Une référence ASP.NET qui remonte dans `Core` est le
premier pas vers une logique métier dans un endpoint.

---

## 5. Conventions imposées

1. **L'identité ne vient jamais du corps.** `IdentityFactory` lit `ctx.User` et
   les en-têtes, rien d'autre. Un `tenantId` dans le corps → `400`
   `[SERVING_IDENTITY_FROM_PAYLOAD]`, jamais une valeur ignorée en silence.
2. **`RequireAuthorization()` est le défaut de chaque endpoint** ; `AllowAnonymous`
   est explicite, endpoint par endpoint, et refusé si la MISSION ne déclare pas
   d'acteur anonyme.
3. **`ProblemDetails` ne contient jamais de `stack trace`, dans aucun
   environnement.** Réponse d'erreur : `{ class, message, runId }`. Un
   comportement de sécurité qui varie selon `ASPNETCORE_ENVIRONMENT` finit livré
   en production un jour de hâte.
4. **`CancellationToken` propagé de bout en bout**, jamais remplacé par
   `CancellationToken.None`. C'est l'annulation à la déconnexion, donc l'arrêt
   du budget.
5. **`ConfigureAwait(false)` dans `Core`**, pas dans les endpoints. Un blocage
   synchrone (`.Result`, `.Wait()`) sur un chemin de requête est un
   `[SERVING_SYNC_OVER_ASYNC]` en revue : sous charge, il épuise le pool de
   threads et le service cesse de répondre pour une raison sans rapport avec
   les modèles.
6. **`Kestrel.Limits.MaxRequestBodySize`** aligné sur `max_input_bytes` : `413`
   avant toute désérialisation, donc avant tout appel LLM.
7. **`/healthz` et `/readyz` sont deux `tags` de health check distincts.** Les
   confondre fait redémarrer en boucle un service dont la base est
   momentanément injoignable.
8. **`/readyz` ne coûte aucun token** et vérifie : `Settings` lié, prompts
   présents et hashés, outils enregistrés = contrats, IR à jour, checkpointer
   joignable.
9. **`IHostApplicationLifetime.ApplicationStopping` flushe les traces.** Un
   `SIGTERM` d'orchestrateur ne doit pas perdre les spans du dernier run.
10. **CORS fermé par défaut.** Aucune origine tant que `STACK.md` n'en déclare.
11. **`JsonSerializerContext` source-generated** pour tous les types du flux :
    exigé par l'AOT, et la réflexion sur un `RunEvent` par token est un coût
    mesurable sur un run bavard.
12. **`HumanInTheLoopEnabled` sans checkpointer partagé refuse le démarrage**
    dès que plus d'une instance est prévue : une reprise tomberait sur une
    instance qui n'a jamais vu le thread.

---

## 6. L'API GATE

Mécanisme identique à `fastapi-sse.md` §6, mêmes classes
(`[API_CONTRACT_DRIFT]`, `[API_ROUTE_UNBACKED]`, `[API_STATUS_UNMAPPED]`).

Spécificité .NET : les `record` de `Contracts/` sont **générés** depuis les
`inputSchema` / `outputSchema` de l'IR, et `OpenApiMatchesIrTests` confronte le
document produit par `Microsoft.AspNetCore.OpenApi` à l'IR. Écrire ces `record`
à la main est le raccourci qui fait diverger le contrat au premier changement de
schéma — et la divergence se découvre chez l'appelant.

---

## 7. Livrables : ce que cette surface peut produire

C'est ce qui distingue `lang/csharp.md` du reste du catalogue — quatre
`DeliverableType` sont réellement atteignables depuis le même code source.

| `DeliverableType` | Commande | Ce qu'on obtient | Contrainte |
|---|---|---|---|
| `backend-api` | `dotnet publish -c Release` | service + `appsettings.json` | le cas nominal |
| `container` | `dotnet publish /t:PublishContainer` | image OCI, sans Dockerfile | base `mcr.microsoft.com/dotnet/aspnet:9.0` |
| `cli-exe` | `dotnet publish -r win-x64 -p:PublishSingleFile=true --self-contained` | un `.exe` autonome | serving/cli.md actif ; ~70 Mo |
| `library` | `dotnet pack` sur `{AppName}.Core` | `.dll` + `.nupkg` référençable | **`Core` uniquement** — `Serving.Http` n'est jamais une bibliothèque |

**AOT (`PublishAot`) n'est pas activé par défaut.** Le gain au démarrage est
réel, et le coût l'est aussi : toute réflexion non annotée casse à l'exécution,
pas à la compilation. C'est un choix à mesurer sur le produit, pas un défaut à
subir — et `dev-api` ne l'active que si `STACK.md` le demande.

---

## 8. Commande de smoke

Déterministe, 0 token :

```bash
cd workspace/src/{AppName}
dotnet build -c Release --nologo                       # exit 0
dotnet test tests/{AppName}.Serving.Http.Tests -v q    # API GATE + identité + SSE + annulation

# avec le service lancé :
curl -fsS localhost:${ServingLocalPort}/healthz
curl -fsS localhost:${ServingLocalPort}/readyz         # 503 si IR périmé / prompt absent
curl -fsS localhost:${ServingLocalPort}/openapi/v1.json | python -m json.tool > /dev/null
curl -fsS localhost:${ServingLocalPort}/v1/runs -X POST -d '{}' -H 'Content-Type: application/json'
#   -> 401 attendu si ApiAuthMode != none
```

---

## 9. Pièges connus

1. **`tenantId` lu dans le corps « en attendant l'authentification ».** Même
   faille qu'en Python, et elle passe les revues parce qu'elle ressemble à du
   câblage. Tout le filtrage à la source devient décoratif.
2. **Réponse bufferisée.** Sans `DisableBuffering()` **et** `FlushAsync()` par
   événement, le SSE part par blocs : le client voit tout à la fin.
3. **`CancellationToken.None` passé « pour faire compiler ».** L'annulation à
   la déconnexion disparaît, et un client qui recharge sa page paie deux runs.
4. **`.Result` / `.Wait()` sur un chemin de requête.** Sous charge, le pool de
   threads s'épuise et le service cesse de répondre — un incident qui ressemble
   à une panne de modèle et n'en est pas une.
5. **`ProblemDetails` en `Development` qui fuit la `stack trace`.** Elle porte
   les chemins et parfois un fragment de prompt. La configuration doit être
   identique dans tous les environnements.
6. **`AddCors(policy => policy.AllowAnyOrigin())` laissé après le
   développement.** Le service porte une identité au transport : une origine
   libre transforme chaque navigateur en appelant authentifié.
7. **Sérialisation par réflexion sur un flux de tokens.** Mesurable sur un run
   bavard, et bloquant en AOT. `JsonSerializerContext` source-generated.
8. **`Controller` MVC ajouté « pour une route de plus ».** La surface repart en
   couche métier, et les evals cessent de couvrir ce que le client appelle.
9. **`/readyz` qui appelle le modèle.** Chaque sonde coûte des tokens, et un
   quota atteint retire du service une instance parfaitement saine.
10. **Traces perdues au `SIGTERM`.** Sans `ApplicationStopping`, le run qu'on
    voulait analyser est précisément celui qui manque.
11. **Plusieurs instances avec un checkpointer en mémoire.** Une reprise tombe
    sur une instance qui n'a jamais vu le thread. Vérifié par `/readyz`.
12. **`Core` qui référence ASP.NET.** Le livrable `library` devient impossible,
    la CLI traîne tout le framework web, et rien n'empêche plus une règle
    métier de s'installer dans un endpoint.
