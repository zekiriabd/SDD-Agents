# Stack: cli-dotnet (serving)

Stack ID: serving-cli-dotnet
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: csharp
Scope: surface d'exposition **ligne de commande .NET** de l'application agentic — commandes, entrée/sortie, streaming, mode machine (`--json` NDJSON), reprise après interruption, **codes de sortie stables** mappés sur la taxonomie `[CLASS]`, publication en exécutable auto-contenu. C'est la surface du livrable `cli-exe` sur `lang/csharp.md`, et le **défaut** du framework pour un projet C#. Le contrat d'événements et le mapping `[CLASS] → code` sont **identiques** à `serving/cli.md` §3.2-3.3 : c'est la même application vue depuis un autre écosystème.

---

## 1. Rôle et périmètre

Le pendant .NET de `serving/cli.md`. Il existe pour une raison précise : sans
lui, `DeliverableType: cli-exe` — le défaut du framework — était **inatteignable
en C#**. La seule fiche console déclarait `Languages: python`, donc
`preflight_stack_combo` refusait la combinaison par `[STACK_LANGUAGE_MISMATCH]`,
et un projet .NET n'avait d'autre choix que `backend-api`. Un défaut qu'un
langage sur deux ne peut pas honorer n'est pas un défaut, c'est un piège.

Les trois raisons qui font de la CLI la première surface générée valent mot pour
mot ici (`serving/cli.md` §1) :

1. **Elle est déterministe à câbler.** `stdin → run → stdout`, pas de serveur,
   pas de session, pas d'authentification.
2. **Elle est la surface des evals.** Le runner L4-L7 invoque l'application par
   la même voie qu'un humain au terminal : ce qu'on mesure est ce qu'on livre.
3. **Elle rend les bornes visibles.** Un code de sortie lu par un script ou une
   CI, sans parser de texte.

> **Le contrat ne se réécrit pas, il se réutilise.** Le schéma `RunEvent`
> (§3.2 de `cli.md`) et la table `[CLASS] → code` (§3.3) sont la propriété du
> produit, pas de l'écosystème. Un événement nommé autrement en C# obligerait le
> runner d'eval à connaître le langage de l'application qu'il mesure — et le
> jour où l'on compare deux implémentations de la même MISSION, la comparaison
> ne voudrait plus rien dire.

Périmètre : commandes, options, protocole NDJSON, codes de sortie, annulation,
publication. **Hors périmètre** : les autres surfaces (`aspnet-minimal.md`), qui
réutilisent le même `RunService` et le même schéma d'événements ; seul le
transport change.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `serving-cli-dotnet` |
| **Langage** | C# / .NET 10 (`lang/csharp.md`, TFM `net10.0`) |
| **Librairies** | `System.CommandLine` 2.x (analyse des commandes) · `Spectre.Console` 0.51.x (rendu TTY **uniquement**) · `System.Text.Json` source-generated — capability `serving-cli-dotnet` du `.libs.json` du framework actif |
| **Point d'entrée** | `{AppName}.Serving.Cli/Program.cs` → `dotnet run` ou binaire publié |
| **Paramètres STACK.md** | `DeliverableType: cli-exe`, `StreamingEnabled`, `HumanInTheLoopEnabled` (active `resume`), `TraceLevel` |
| **Contrat machine** | `--json` : une ligne NDJSON par événement, schéma `RunEvent`, `event_schema: "1"` — **identique à `cli.md` §3.2** |
| **Codes de sortie** | table `cli.md` §3.3, **sans écart** — stables, documentés dans `--help`, testés en L1 |
| **Livrables possibles** | `cli-exe` (`PublishSingleFile`, auto-contenu), `library` (`.dll` référençable), `container` |

---

## 3. Mapping des concepts SDD_Agents → idiomes .NET

### 3.1 Commandes

Mêmes commandes, mêmes rôles, mêmes options que `cli.md` §3.1. Un script écrit
contre l'une fonctionne contre l'autre, et c'est le but.

| Commande | Idiome .NET | Coûte des tokens |
|---|---|---|
| `{AppName} run` | `Command` racine ; `IAsyncEnumerable<RunEvent>` consommé avec `await foreach` | oui |
| `{AppName} resume` | enregistrée **seulement** si l'IR déclare `humanInTheLoop` | oui |
| `{AppName} health` | `IHealthCheck` réutilisés hors ASP.NET, exécutés en direct | **non** |
| `{AppName} inspect` | lecture de l'IR embarqué ; `--graph` imprime le Mermaid | non |
| `{AppName} trace` | relecture de `workspace/.sys/traces/runs/{run-id}.jsonl` | non |
| `{AppName} version` | version d'assembly, hash de l'IR, hash de la stack, `semconv_version` | non |

### 3.2 Protocole d'événements et codes de sortie

**Aucune redéfinition ici.** Voir `serving/cli.md` §3.2 (table `RunEvent`) et
§3.3 (table des codes). Ce que cette fiche ajoute, ce sont les contraintes .NET
qui font que le contrat est effectivement respecté :

| Contrainte | Pourquoi |
|---|---|
| `JsonSerializerOptions` source-generated, `PropertyNamingPolicy = SnakeCaseLower` | les champs sont `run_id`, `cost_usd_cumulative` : le camelCase par défaut de `System.Text.Json` casserait le contrat lu par le runner |
| `JsonIgnoreCondition.WhenWritingNull` | un `error_code: null` dans chaque `tool_result` alourdit la trace sans rien dire |
| `RunEvent` en hiérarchie `record` scellée, discriminée par `event` | l'équivalent du modèle figé côté Python ; un événement inconnu est une erreur de compilation, pas une surprise au parse |
| `Environment.ExitCode` posé depuis `ExitCodes.Resolve(exception)` | jamais un entier littéral dans une commande |

### 3.3 Mapping des entités

Identique à `cli.md` §3.4, avec ces correspondances :

| Concept | Idiome .NET |
|---|---|
| **RUN** | `run_id` (ULID) ; = `sdda.run.id` du span racine ; = nom du fichier de trace |
| **thread** | `--thread-id` ; = `gen_ai.conversation.id` ; = clé du checkpointer |
| **Entrée utilisateur** | `Untrusted` — `--input`, fichier, stdin : même traitement, `WrapUntrusted(source: "cli:stdin")` |
| **Identité de l'appelant** | `--tenant` ou `SDDA_TENANT_ID` ; posé dans `ToolContext`, **jamais visible du modèle** |
| **Bornes** | héritées de l'IR ; `--max-budget-usd` ne peut que **baisser** le plafond |
| **Annulation** | `CancellationToken` propagé depuis `PosixSignalRegistration` (SIGINT/SIGTERM) |
| **Secrets** | jamais en argument ; `IConfiguration` (env, `appsettings.json`, user-secrets) uniquement |

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}.Serving.Cli/
├── Program.cs              # RootCommand + sous-commandes ; aucun métier
├── RunService.cs           # .RunAsync(input, ctx) / .ResumeAsync(threadId, decision, ctx)
│                           #   -> IAsyncEnumerable<RunEvent> — PARTAGÉ avec Serving.Http
├── Events/RunEvent.cs      # records scellés, discriminés par `event`, event_schema = "1"
├── Events/RunEventJson.cs  # JsonSerializerContext source-generated (snake_case)
├── ExitCodes.cs            # enum + table [CLASS] -> code + Resolve(Exception)
├── Render.cs               # TTY (Spectre, avec Escape) vs NDJSON vs texte brut
├── Signals.cs              # SIGINT/SIGTERM -> annulation coopérative, flush des traces, 130
└── ToolContext.cs          # tenant, runId, threadId, caller — ne traverse jamais le modèle

workspace/src/{AppName}/tests/{AppName}.Serving.Cli.Tests/
├── ExitCodesTests.cs       # L1 : table exhaustive [CLASS] -> code ; classe inconnue -> 1
├── RunEventJsonTests.cs    # L1 : sérialisation snake_case ; args d'outil redigés ; aller-retour
├── CliJsonTests.cs         # L1 : stdout = NDJSON valide uniquement ; stderr porte les logs
└── HealthTests.cs          # L1 : health sans --live est 0 token et < 5 s
```

---

## 5. Conventions imposées

1. **`stdout` est le canal de résultat, `stderr` celui du diagnostic.** En
   `--json`, rien d'autre que du NDJSON valide ne sort sur `stdout`. Le logger
   par défaut de `Microsoft.Extensions.Logging` écrit sur `stdout` : il est
   redirigé sur `stderr` **avant** la première commande. Test L1.
2. **`run_finished` est toujours le dernier événement**, même après `error`,
   même sur annulation — il porte `exit_code` et `trace_path`.
3. **Le code de sortie est dérivé de la `[CLASS]`**, jamais choisi à la main.
4. **Streaming = flush par événement.** `Console.Out` est bufferisé hors TTY :
   `AutoFlush = true` sur le writer NDJSON, sinon le consommateur ne voit rien
   pendant trente secondes puis tout d'un coup.
5. **`Spectre.Console.Markup.Escape()` sur tout texte issu du modèle ou d'un
   outil.** Un modèle qui écrit `[bold]` change le rendu, voire masque du
   texte : c'est une injection d'affichage, pas un défaut visuel.
6. **Le tenant vient de l'appelant**, il est posé dans `ToolContext` et n'est
   jamais un paramètre d'outil ni un texte que le modèle voit.
7. **`health` ne coûte aucun token** et n'ouvre aucune connexion sans `--live`.
8. **`--max-budget-usd` ne peut que baisser** le plafond de `CostPerRunHardCapUsd`.
9. **`resume` exige un checkpointer.** Si la stack active ne le fournit pas, la
   commande n'est **pas enregistrée** plutôt que présente et cassée.
10. **Toute exécution écrit sa trace** avant de rendre la main, y compris un
    échec au démarrage.
11. **La console est en UTF-8 dès la première ligne** de `Main` :
    `Console.OutputEncoding = new UTF8Encoding(false)`. La console Windows est
    en cp1252 par défaut et un glyphe du modèle y lève une exception.
12. **Pas de REPL dans le MVP.** Une conversation est une suite de
    `run --thread-id X`, testable ligne par ligne.

---

## 6. Commande de smoke

Déterministe, 0 token :

```bash
cd workspace/src/{AppName}
dotnet build -warnaserror
dotnet run --project src/{AppName}.Serving.Cli -- --help          # exit 0
dotnet run --project src/{AppName}.Serving.Cli -- version --json  # NDJSON valide
dotnet run --project src/{AppName}.Serving.Cli -- health --json   # exit 0, sinon 8 + [CLASS]
dotnet test --filter "Category!=Network"
```

Publication du livrable `cli-exe` (auto-contenu, sans .NET installé sur la cible) :

```bash
dotnet publish src/{AppName}.Serving.Cli -c Release -r linux-x64 \
  -p:PublishSingleFile=true -p:SelfContained=true
```

Le premier `run` réel coûte des tokens et n'est pas dans le smoke : il appartient
à la L7 (ORCH GATE), via le runner d'eval qui invoque exactement
`{AppName} run --json --tenant … --input-file …` et lit le code de sortie.

---

## 7. Pièges connus

1. **`System.Text.Json` sérialise en camelCase.** Sans
   `SnakeCaseLower`, les champs deviennent `runId` et `costUsdCumulative` : le
   NDJSON reste valide, le contrat est rompu, et le runner d'eval lit `null`
   partout sans lever d'erreur. C'est le piège le plus coûteux de cette fiche,
   parce qu'il produit une mesure vide plutôt qu'une panne.
2. **`Environment.Exit()` tue le processus** sans laisser `BatchSpanProcessor`
   exporter. La commande collecte le code, sort de la boucle async, flush les
   traces, **puis** pose `Environment.ExitCode` et retourne.
3. **Ctrl-C perd la trace.** `Console.CancelKeyPress` ne suffit pas sous Linux :
   `PosixSignalRegistration` pour SIGINT et SIGTERM, annulation coopérative,
   `run_finished` émis, traces flushées, code 130. Second signal : sortie brutale.
4. **Console Windows en cp1252** — un `token` contenant une flèche ou un emoji
   lève une exception d'encodage. Convention 11.
5. **`Spectre.Console` interprète le balisage** — convention 5.
6. **Les logs de `HttpClient` et du SDK de modèle partent sur `stdout`** via le
   logger par défaut. Redirection sur `stderr` avant la première commande, et
   test L1 qui vérifie que `stdout` en `--json` ne contient que du NDJSON.
7. **`PublishAot` casse la réflexion** du SDK de modèle et des convertisseurs
   JSON non source-generated. Le défaut est `PublishSingleFile` sans AOT ; AOT
   est une optimisation à mesurer, pas un réglage par défaut.
8. **`IAsyncEnumerable` consommé sans `WithCancellation`** ignore le token :
   Ctrl-C n'interrompt alors rien et le run continue jusqu'à sa borne.
9. **Codes > 255 impossibles en POSIX.** La table reste sous 128 et laisse 128+
   aux signaux. Ne pas « encoder » la `[CLASS]` dans le code.
10. **stdin vide sur un TTY** : `run` sans `--input` attendrait indéfiniment.
    Détection `Console.IsInputRedirected` : sur TTY sans entrée, code 2 avec
    message ; hors TTY, lecture de stdin.
