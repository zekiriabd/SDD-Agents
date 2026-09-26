# Stack: xunit-eval (eval)

Stack ID: eval-xunit-eval
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: csharp
Scope: tests déterministes **L0 → L2** d'une application **.NET** dans **xUnit v3** — structure du projet de test, conventions de nommage lues par la TOOL GATE, marquage `network`, rapport **JUnit** par `dotnet test --logger "junit;LogFilePath=…"` — et contrat avec le runner d'évaluation du framework, qui mesure L3 → L9 **en lançant l'application par sa CLI** (`python .sdda/sdda.py eval-runner`). `Microsoft.Extensions.AI.Evaluation` y figure comme graders optionnels, jamais comme runner. Suppose `lang/csharp.md`. Catalogue : `xunit-eval.libs.json`.

---

## 1. Rôle et périmètre

**Un test assert, une eval score** (TESTING-AND-EVAL). Les deux ne vivent pas au
même endroit, et c'est la décision structurante de cette fiche :

| | Tests L0–L2 | Evals L3–L9 |
|---|---|---|
| **Quoi** | lint, fonctions pures, contrats d'outil (happy, chaque erreur, timeout, auth), connectivité live | retrieval, agent isolé, trajectoire, bout-en-bout, adversarial, régression |
| **Runner** | `dotnet test` (xUnit v3, VSTest) | `python .sdda/sdda.py eval-runner` / `run-retrieval-eval` / `run-adversarial-suite` |
| **Où** | `workspace/src/{AppName}/tests/` | `workspace/pipeline/suites/**` + datasets |
| **Qui écrit** | `qa-tests` (et les `dev-*` pour leur couche) | `qa-evals` — **jamais** un `dev-*` |
| **Ce qui en sort** | rapport JUnit → TOOL GATE (G3) | rapports d'eval → G4 à G8 |

Pourquoi le runner d'eval n'est **pas** xUnit, alors qu'il pourrait l'être :
le protocole d'évaluation (k runs par item, verdict vert / jaune / rouge,
tuple d'épinglage P10, holdout disjoint, juge calibré, baseline déplacée par
script) est **celui du framework**, et il est déjà implémenté une fois, testé,
en Python. Le réimplémenter en C# produirait un second protocole — et le jour où
deux implémentations de la même MISSION se comparent, elles ne seraient plus
jugées par la même règle. Le runner lance donc l'application .NET **comme un
utilisateur**, par sa CLI (§8) : ce qu'on mesure est ce qu'on livre, dans tous
les langages.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `eval-xunit-eval` |
| **Langage** | C# 14 / `net10.0` (`lang/csharp.md`) |
| **Framework de test** | `xunit.v3` 4.0.x + `xunit.runner.visualstudio` 4.0.x + `Microsoft.NET.Test.Sdk` 18.x (VSTest) |
| **Rapport** | `JunitXml.TestLogger` 8.0.x — logger VSTest de nom `junit` |
| **Doubles** | `NSubstitute` 6.2.x |
| **Commande de la TOOL GATE** | `dotnet test --logger "junit;LogFilePath=<fichier>"`, lancée depuis `workspace/src/{AppName}/` |
| **Runner d'eval** | `python .sdda/sdda.py eval-runner` — **hors** de ce catalogue |
| **Graders optionnels** | `Microsoft.Extensions.AI.Evaluation(.Quality, .Reporting)` 10.10.x — advisory (§3.4) |

### 2.1 Le piège de .NET 10 : VSTest ou Microsoft Testing Platform

`xunit.v3` 4.x dépend de `xunit.v3.mtp-v2` : un projet de test est, par défaut,
une application **Microsoft Testing Platform**. Or, sur le SDK .NET 10,
`dotnet test` en mode VSTest **refuse** un projet MTP :

```
error : Testing with VSTest target is no longer supported by Microsoft.Testing.Platform
on .NET 10 SDK and later. If you use dotnet test, you should opt-in to the new dotnet test experience.
```

(Relevé en exécutant `dotnet test` sur une sonde, SDK 10.0.301.) Les loggers
VSTest — donc `--logger junit` — n'existent qu'en mode VSTest. Le projet de test
pose donc :

```xml
<IsTestingPlatformApplication>false</IsTestingPlatformApplication>
```

et le dépôt ne porte **aucune** clé `test.runner` dans `global.json`. Avec ce
réglage, `xunit.runner.visualstudio` expose les tests à VSTest, et le logger
JUnit écrit son rapport (vérifié sur la même sonde).

L'alternative — opter pour MTP (`global.json` → `"test": { "runner": "Microsoft.Testing.Platform" }`)
et utiliser le rapport natif de xunit `--report-junit --report-junit-filename <fichier>` —
existe et est documentée par xunit. Elle n'est **pas** retenue : la commande de
la TOOL GATE est `--logger junit`, et une gate dont la commande dépend d'un
réglage de `global.json` change de comportement sans que la fiche bouge.

---

## 3. Mapping des concepts SDD_Agents → idiomes xUnit

| Concept | Idiome xUnit v3 | Notes |
|---|---|---|
| **L0** (lint, statique) | `dotnet format --verify-no-changes`, `dotnet build -warnaserror` + tests `…CatalogTests` qui lisent des fichiers (`.sql`, prompts, contrats) | 0 token, sans réseau |
| **L1** (fonctions pures) | `[Fact]` / `[Theory]` sur `Rrf.Fuse`, routeurs, `ExitCodes.Resolve`, redaction, parsing | modèle **jamais** appelé : `IChatClient` = double NSubstitute qui lève |
| **L2** (contrat d'outil) | un `[Theory]` par outil, **une ligne `[InlineData("{case_id}")]` par cas du contrat** (§4 du tool-contract) | le nom affiché contient `case_id` : c'est par lui que la TOOL GATE relie un résultat JUnit à un cas déclaré |
| **Connectivité live** | classe `…NetworkTests` + `[Trait("Category","network")]` | seconde moitié de G3 ; exclue du smoke par `--filter "Category!=network"` |
| **Rapport** | `--logger "junit;LogFilePath=…"` | `testcase/@classname` = nom complet de la classe ; `testcase/@name` = `Method(arg: "happy-1")` ; traits en `<property name="Category" value="network"/>` (vérifié) |
| **Isolement** (L4, mesuré par le runner) | l'application sert ses outils et son retrieval depuis `SDDA_EVAL_FIXTURES` quand `SDDA_EVAL_ISOLATION=mocked` (§8) | le test L1 `IsolationTests` vérifie qu'aucun client réseau n'est construit dans ce mode |
| **k runs, verdict, épinglage, baseline, calibration** | **absents d'xUnit** — portés par `eval-runner` | un test xUnit ne score pas |

### 3.1 Un test de contrat d'outil

```csharp
// tests/tools/LookupCustomerContractTests.cs
public sealed class LookupCustomerContractTests
{
    // Une ligne par cas déclaré dans workspace/pipeline/contracts/tools/{n}-lookup-customer.tool.md §4.
    // Le nom affiché devient « Case(caseId: "happy-1") » : la TOOL GATE y lit l'identifiant.
    [Theory]
    [InlineData("happy-1")]
    [InlineData("err-not-found")]
    [InlineData("err-invalid-args")]
    [InlineData("timeout")]
    [InlineData("auth-failed")]
    public async Task Case(string caseId)
    {
        var fixture = ContractCases.Load("lookup-customer", caseId);          // entrée + résultat attendu, depuis le contrat
        var tool = LookupCustomerTool.Create(fixture.Transport, fixture.Context);
        var result = await tool.InvokeAsync(fixture.Arguments, TestContext.Current.CancellationToken);
        fixture.AssertMatches(result);                                        // ok | code d'erreur déclaré | truncated
    }
}

[Trait("Category", "network")]
public sealed class LookupCustomerNetworkTests
{
    [Fact]
    public async Task LiveEndpointAnswersWithDeclaredSchema() { /* vrai service, environnement de test */ }
}
```

Pourquoi ces deux conventions exactement : la TOOL GATE ne lit pas du C#, elle
lit un rapport JUnit. Le seul moyen, pour elle, de savoir qu'un cas `timeout`
déclaré a un test vert, c'est de trouver `timeout` dans le nom d'un `testcase` ;
et le seul moyen de savoir qu'un test est la connectivité live, c'est `Network`
dans `classname`. Un test correct mais nommé autrement est, pour la gate, un
cas **non couvert**.

### 3.2 Ce que le runner d'eval attend de l'application

Le runner ne charge pas d'assembly .NET : il lance la CLI (`--executor cli` ou
`--executor cmd:…`), écrit l'entrée sur stdin, lit le NDJSON `RunEvent` sur
stdout et le code de sortie, puis relit la trace
`workspace/.sys/traces/runs/{run_id}.jsonl` (format imposé par
`observability/otel-genai-dotnet.md` §3.4). Trois graders du framework
(`trajectory`, `cost`, `latency`) lisent **la trace**, pas la réponse : une
application .NET dont le JSONL ne suit pas le contrat n'a pas de L5, et son coût
est « non recalculable ».

### 3.3 Isolement L4 — ce que l'application doit, pas ce que le test fait

En Python, l'isolement L4 passe par un exécuteur en processus. En .NET, il passe
par **l'application elle-même** : avec `SDDA_EVAL_ISOLATION=mocked`, elle
remplace chaque outil et chaque retriever par une variante qui lit
`SDDA_EVAL_FIXTURES/tools/{outil}.jsonl` et `SDDA_EVAL_FIXTURES/retrieval/{index}.jsonl`
(fixtures écrites par `qa-evals` sous `workspace/pipeline/fixtures/`). La
substitution se fait **à la composition** (`app/`), une fois : si chaque outil
testait lui-même la variable, un outil oublié toucherait le vrai monde pendant
une eval qui se dit isolée.

### 3.4 `Microsoft.Extensions.AI.Evaluation` : des graders, pas un runner

Le paquet fournit `IEvaluator` et des évaluateurs LLM (`GroundednessEvaluator`,
`RelevanceEvaluator`, `RetrievalEvaluator`, `ToolCallAccuracyEvaluator`,
`TaskAdherenceEvaluator`…). Ils sont utiles pour **explorer** — et ils ne
rendent **aucun** verdict de gate, pour trois raisons :

1. **Ce sont des juges LLM non calibrés.** P9 : un juge sans calibration
   (`kappa ≥ JudgeCalibrationMinKappa` sur `JudgeCalibrationMinItems` items
   étiquetés) est `advisory` — score rapporté, verdict non bloquant.
2. **La liste des graders du framework est close** (`sdda_lib/graders`) : il
   n'existe pas, à ce jour, de grader « externe » par lequel un évaluateur C#
   rendrait un score à `eval-runner`. L'ajouter est une évolution du framework,
   pas de cette fiche.
3. **Le cache de réponses de `.Reporting`** rejoue une réponse enregistrée : k
   runs deviennent un run copié k fois, variance nulle, vert menteur. Interdit
   sur toute mesure.

Usage admis : un projet d'exploration hors gate, sous ADR, dont les scores sont
publiés comme `advisory` et jamais recopiés dans une baseline.

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── {AppName}.csproj                  # l'application (OutputType Exe) — <DefaultItemExcludes>…;tests/**</DefaultItemExcludes>
├── {AppName}.slnx                    # référence {AppName}.csproj et tests/{AppName}.Tests.csproj
└── tests/
    ├── {AppName}.Tests.csproj        # OutputType Exe (xunit v3) ; IsTestingPlatformApplication=false ; ProjectReference ../{AppName}.csproj
    ├── ContractCases.cs              # charge les cas déclarés des tool-contracts (lecture seule)
    ├── app/        BoundsTests.cs  PromptsTests.cs  IsolationTests.cs
    ├── serving/    ExitCodesTests.cs  RunEventJsonTests.cs  CliJsonTests.cs  RetrieveCommandTests.cs
    ├── tools/      {Tool}ContractTests.cs  {Tool}NetworkTests.cs
    ├── retrieval/  RrfTests.cs  QueryPrepTests.cs  HybridSqlNetworkTests.cs
    ├── data/       ViewCatalogTests.cs  EnvelopeTests.cs  {View}NetworkTests.cs
    └── tracing/    JsonlTraceContractTests.cs  RedactionTests.cs

workspace/pipeline/                    # ownership qa-evals — rien de ce catalogue n'y est installé
├── suites/  datasets/  fixtures/  calibration/  baselines/
```

`DefaultItemExcludes` sur `tests/**` n'est pas optionnel : sans lui, le projet
de l'application, à la racine, compile aussi les fichiers du projet de test
(globbing par défaut de MSBuild) — double définition des types, ou pire,
références xunit dans l'exécutable livré.

---

## 5. Conventions imposées

1. **Un seul projet de test, `tests/{AppName}.Tests.csproj`**, sous-dossiers par
   couche (`tests/tools/`, `tests/retrieval/`…). La matrice d'ownership donne
   `workspace/src/**/tests/**` à `qa-tests` et aux `dev-*` par couche.
2. **Fichiers `*Tests.cs`**, une classe publique scellée par fichier.
3. **Un cas de contrat = une ligne `[InlineData("{case_id}")]`** d'un
   `[Theory]` ; `case_id` est exactement celui du tool-contract.
4. **Tests réseau : classe `…NetworkTests` + `[Trait("Category","network")]`**,
   les deux. Le trait sert au filtre, le nom sert au rapport.
5. **`IsTestingPlatformApplication=false`** dans le projet de test, aucune clé
   `test.runner` dans `global.json` (§2.1).
6. **Le modèle n'est jamais appelé en L0–L2** : un `IChatClient` réel dans un
   test xUnit est une eval déguisée, sans k runs ni verdict.
7. **`TestContext.Current.CancellationToken`** passé à tout appel async : un
   test qui ne l'honore pas ne s'arrête pas au timeout du runner.
8. **Aucun saut dynamique** (`Assert.Skip` selon l'environnement) : un test non
   applicable est désélectionné par trait. Un test sauté apparaît vert dans
   certains lecteurs JUnit.
9. **Aucun test n'écrit sous `workspace/pipeline/`** — datasets, suites,
   baselines, calibration, fixtures sont hors de portée de tout `dev-*` et de
   tout test (frontière du jugement, ARCHITECTURE §2.ter).
10. **Pas de `--no-build` dans la commande de gate** : la gate teste le code
    qu'elle vient de compiler, pas un binaire resté d'un build précédent.

---

## 6. Commande de smoke

Déterministe, 0 token, sans réseau :

```bash
cd workspace/src/{AppName}
dotnet restore
dotnet format --verify-no-changes
dotnet build -warnaserror
dotnet test --filter "Category!=network"
```

Commande de la TOOL GATE (G3), depuis la même racine — le rapport est ce que
parse la gate :

```bash
dotnet test --logger "junit;LogFilePath=<fichier>"
```

`<fichier>` est choisi par le runner de la gate, pas par l'application. Il doit
être **absolu** : la base de résolution d'un `LogFilePath` relatif n'a pas été
vérifiée pour cette fiche (le rapport par défaut, sans `LogFilePath`, va sous
`TestResults/` du projet de test), et un rapport écrit ailleurs que là où le
runner le cherche se lit comme « aucun test ».

Smoke Timeout : 180 s (la première restauration NuGet domine).

---

## 7. Pièges connus

1. **Le mode MTP de xunit v3 4.x sur le SDK .NET 10** — §2.1. Symptôme : erreur
   au lancement, aucun rapport. Le plus coûteux de la fiche, parce qu'une gate
   qui n'obtient pas de rapport conclut « aucun test » et non « mauvaise
   configuration ».
2. **`global.json` avec `test.runner`.** Posé par un développeur « pour
   moderniser », il fait disparaître `--logger` sans toucher aucun `.csproj`.
3. **Le projet racine qui compile les tests** — `DefaultItemExcludes` (§4).
4. **Un `[Theory]` avec `MemberData`** dont les données sont des objets : le
   nom affiché devient `Case(fixture: ContractCase { … })`, l'identifiant du cas
   n'y est plus lisible. Les cas passent par une chaîne `case_id`.
5. **`Category` écrit `Network` ici et `network` là.** Le filtre VSTest est
   insensible à la casse sur la valeur (vérifié), mais le rapport garde la
   graphie : une seule graphie, en minuscules, partout.
6. **Tests parallèles et base partagée.** xUnit parallélise par collection (par
   classe par défaut) ; deux classes `…NetworkTests` sur la même base Testcontainers
   se marchent dessus. Une collection par base, ou une base par classe.
7. **Un test qui lit `Environment.GetEnvironmentVariable`** pour un secret de
   test : `[SEC_ENV_VAR_FORBIDDEN]` s'applique aussi aux tests ; la
   configuration de test passe par `IConfiguration` (fichier de test, user-secrets).
8. **Les évaluateurs MEAI pris pour des tests.** Un `GroundednessEvaluator`
   dans un `[Fact]` avec un seuil fait échouer ou passer le build selon un juge
   non calibré, sur un tirage unique. C'est une eval sans protocole.
9. **La sortie de `dotnet run` dans l'eval.** Quand le runner lance
   l'application par `dotnet run --project … --`, un build nécessaire écrit ses
   messages avant le NDJSON. Construire d'abord (`dotnet build -warnaserror`),
   ou lancer l'exécutable publié par `--executor cmd:…`.

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
l'exécutable publié en livrable
(`dotnet publish workspace/src/{AppName} -c Release -r linux-x64 -p:PublishSingleFile=true -p:SelfContained=true`).
Les runners l'invoquent par `--executor cli` (commande dérivée du langage
actif : `dotnet run --project workspace/src/{AppName} --`) ou par
`--executor cmd:<commande>`.

**Ce que cette fiche doit au contrat.** Le contrat d'exécution est testé **en
L1, dans xUnit**, avant qu'aucun runner ne s'y fie :

- `CliJsonTests` : `run --json --input-file -` avec une entrée sur stdin et un
  `RunService` doublé → chaque ligne de stdout parse en `RunEvent`,
  `run_finished` est la dernière, stderr porte les logs ;
- `RetrieveCommandTests` : `retrieve --json --index X --query-file - --k 3` avec
  un retriever doublé → un événement `retrieval` puis `run_finished`, aucun
  `IChatClient` résolu ;
- `IsolationTests` : avec `SDDA_EVAL_ISOLATION=mocked` et un dossier de fixtures
  de test, la composition ne construit ni `NpgsqlDataSource`, ni client MCP, ni
  `HttpClient` d'outil ; un outil sans fixture → code `8` (`[CONFIG_INVALID]`) ;
- `ExitCodesTests` : la table `[CLASS] → code` de `cli.md` §3.3, exhaustive.

Ces quatre classes sont la preuve, côté .NET, que le runner Python parle à une
application qui tient le même contrat que l'application Python.
