# Stack: ms-agent-framework (framework)

Stack ID: framework-ms-agent-framework
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: csharp
Scope: framework agentic **.NET** — `AIAgent` et `AgentThread` pour un agent et son fil de conversation, `Microsoft.Extensions.AI` (`IChatClient`, `AIFunction`) pour l'abstraction provider et les outils, `Microsoft.Agents.AI.Workflows` pour les patterns à graphe (cycles, reprise, human-in-the-loop). Le langage et l'outillage sont hors périmètre → `lang/csharp.md`. Catalogue de versions : `ms-agent-framework.libs.json` (versions **vérifiées** contre nuget.org le 2026-09-21).

---

## 1. Rôle et périmètre

Microsoft Agent Framework est la convergence de **Semantic Kernel** et
**AutoGen** en une seule pile .NET. Cette fiche décide comment l'IR de
SDD_Agents s'y matérialise — et, plus important, **ce qu'il ne faut pas lui
déléguer**.

Ce que la fiche impose, en une phrase : **le framework exécute, l'IR décide.**
Le framework sait faire tourner une boucle d'agent avec des outils ; il ne sait
pas quelles bornes cette boucle doit respecter, quelle politique appliquer
quand elle les franchit, ni quel texte est hostile. Ces trois choses viennent du
contrat et se matérialisent en code **autour** du framework, jamais en
configuration passée à lui.

Périmètre : correspondance IR → API, matérialisation des bornes, fil de
conversation et état, interruption et reprise, streaming, structure générée,
tests. **Hors périmètre** : le choix du pattern d'orchestration (→
`orchestration/*.md`), l'exposition (→ `serving/*.md`), le langage (→
`lang/csharp.md`), et **la migration d'un Semantic Kernel existant** — c'est un
ADR, pas une dépendance ajoutée (cf. `absentByDesign` du catalogue).

> **Un point d'honnêteté sur la maturité.** Cette fiche est `design-phase` :
> aucun projet .NET n'a encore été généré avec elle. Les versions du catalogue
> sont vérifiées, la correspondance IR → API ne l'est pas. Le premier bootstrap
> réel corrigera §3, et c'est attendu.

---

## 2. Identité

### 2.1 Identité

| | |
|---|---|
| **Stack ID** | `framework-ms-agent-framework` |
| **Langage** | C# 14 / `net10.0` (`lang/csharp.md`) |
| **Paquets pivots** | `Microsoft.Agents.AI` 1.22.0 · `Microsoft.Extensions.AI` 10.10.0 |
| **Graphe** | `Microsoft.Agents.AI.Workflows` 1.22.0 — **onDemand**, capability `orchestration-graph` |
| **Providers** | `provider-openai` → `Microsoft.Agents.AI.OpenAI` · `provider-anthropic` → `Anthropic.SDK` (communautaire, §7.9) |
| **MCP** | `ModelContextProtocol` 2.2.0 — capability `mcp-tools`, cf. `tools/mcp.md` |
| **Déclaration** | `STACK.md ## Active Agent Framework` → ` - .sdda/stacks/framework/ms-agent-framework.md` |
| **Catalogue** | `ms-agent-framework.libs.json` — seul fichier qui fasse foi sur les versions |

### 2.2 Ce que le framework apporte, et ce qu'il n'apporte pas

| Besoin SDD_Agents | Apporté ? | Conséquence |
|---|---|---|
| Boucle agent + appel d'outils | **oui** — `AIAgent.RunAsync` | on l'utilise tel quel |
| Fil de conversation persistable | **oui** — `AgentThread` | sert de support au checkpointing |
| Abstraction provider | **oui** — `IChatClient` | `Models.Resolve(tier)` n'expose jamais un nom de modèle |
| Sortie structurée | **oui** — `Microsoft.Extensions.AI` + `System.Text.Json` | remplace `with_structured_output` |
| Graphe, cycles, reprise | **oui**, en option — `Workflows` | à n'activer que si la TOPOLOGY l'exige (§3.5) |
| **Bornes** (`maxIterations`, `maxToolCalls`, `budgetUsd`) | **non** | §3.3 — c'est du C#, pas de la configuration |
| **Politique de dépassement** (`onBoundExceeded`) | **non** | §3.3 |
| **Frontière de confiance** (P8) | **non** | `lang/csharp.md` §5.3 — le type `Untrusted` |
| **Journal rejouable** | partiel | `observability/otel-genai.md` — spans GenAI explicites |

La colonne « non » est la raison d'être de cette fiche. Un framework qui ne
porte pas les bornes et qu'on croit les porter produit exactement la boucle non
bornée que P12 existe pour empêcher.

---

## 3. Mapping des concepts SDD_Agents → idiomes .NET

### 3.1 Table de correspondance IR → API

| IR (`system.ir.json`) | Microsoft Agent Framework | Notes |
|---|---|---|
| `agents[]` | un `AIAgent` construit par `Agents/{Slug}/Agent.cs : Build(deps, bounds)` | jamais instancié ailleurs |
| `agents[].promptRef` / `promptHash` | `Prompts.LoadSystemPrompt(slug)` au **build**, passé en instructions ; hash émis dans le span | P1 — jamais de littéral |
| `agents[].modelTier` | `Models.Resolve(tier)` → `IChatClient` | jamais un nom de modèle hors `AppOptions` |
| `agents[].tools[]` | liste **close** d'`AIFunction` passée aux options de l'agent | exactement les outils du contrat, sinon `[TOOL_SCOPE_EXCESS]` |
| `agents[].outputSchema` | réponse désérialisée en record `required` via `System.Text.Json` source-generated | équivalent du guardrail `schema-validation` |
| `agents[].trustPosture.untrustedInputs` | contenu inséré comme message **utilisateur** enveloppé par `Trust.Wrap(...)`, jamais dans les instructions | P8 |
| `agents[].bounds.*` | compteurs C# dans le `BoundedAgentRunner` (§3.3) | **aucune** équivalence framework |
| `agents[].onBoundExceeded` | branche explicite du runner | `FailExplicit` \| `Degrade` \| `EscalateHuman` |
| conversation d'un run | `AgentThread` créé par run, `RunId` en corrélation | le fil porte l'historique, pas l'état métier |
| `orchestration.nodes[kind=agent]` | un exécuteur du `Workflow`, ou un appel direct si `single-agent` | §3.5 |
| `orchestration.edges[].condition` | fonction de routage **pure**, testée en L1 par table de cas | ne lit que l'état, jamais un service |
| `orchestration.maxHops` | compteur `Hops` de l'état + garde **dans chaque routeur** | §3.3 |
| `orchestration.checkpointing` | persistance de l'état du `Workflow` + du `AgentThread` sérialisé | obligatoire si `humanInTheLoop` ou reprise |
| `orchestration.humanInTheLoop` | point d'interruption du `Workflow` ; reprise par ré-entrée avec la réponse | §3.6 |
| `tools[]` MCP | client MCP → `AIFunction` par outil **allowlisté** | cf. `tools/mcp.md` : le serveur propose, le contrat dispose |
| `dataAccess[]` | outils générés sous `Data/` | cf. `dataaccess/*.md` |

### 3.2 L'état d'orchestration

```csharp
// src/{AppName}/Orchestration/OrchestrationState.cs
namespace {AppName}.Orchestration;

/// <summary>État d'un run. Persisté par le checkpointing : aucun secret, aucun texte de prompt.</summary>
public sealed record OrchestrationState
{
    public required string RunId { get; init; }

    // --- bornes (P12) : des compteurs qu'on INCRÉMENTE, jamais qu'on affecte ---
    public int Hops { get; private init; }
    public int ToolCalls { get; private init; }
    public decimal CostUsd { get; private init; }

    // --- routage ---
    public string? Intent { get; init; }
    public bool Resolved { get; init; }
    public string? BoundExceeded { get; init; }

    public OrchestrationState AddHop() => this with { Hops = Hops + 1 };
    public OrchestrationState AddToolCalls(int n) => this with { ToolCalls = ToolCalls + n };
    public OrchestrationState AddCost(decimal usd) => this with { CostUsd = CostUsd + usd };
}
```

`private init` sur les trois compteurs n'est pas du purisme : c'est ce qui rend
`state with { Hops = 0 }` **impossible à compiler** depuis un nœud. En LangGraph
la même garantie s'obtient par un réducteur additif ; ici elle s'obtient par le
compilateur, et c'est plus fort — un nœud ne peut pas remettre une borne à zéro
par inadvertance.

L'état ne porte **aucun secret** et **aucun texte de prompt** : il est persisté.

### 3.3 Matérialiser les bornes — c'est du C#, pas de la configuration

```csharp
// src/{AppName}/Agents/BoundedAgentRunner.cs
namespace {AppName}.Agents;

public sealed class BoundedAgentRunner(AIAgent agent, Bounds bounds, IPricing pricing)
{
    public async Task<AgentOutcome> RunAsync(
        AgentThread thread, string input, CancellationToken ct)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(bounds.TimeoutSeconds));

        var iterations = 0;
        var toolCalls = 0;
        var costUsd = 0m;

        try
        {
            await foreach (var update in agent.RunStreamingAsync(input, thread, cancellationToken: timeout.Token))
            {
                if (++iterations > bounds.MaxIterations)
                    return Exceeded(nameof(bounds.MaxIterations), iterations, toolCalls, costUsd);

                toolCalls += CountToolCalls(update);
                if (toolCalls > bounds.MaxToolCalls)
                    return Exceeded(nameof(bounds.MaxToolCalls), iterations, toolCalls, costUsd);

                costUsd += pricing.Cost(update.Usage);
                if (costUsd > bounds.BudgetUsd)
                    return Exceeded(nameof(bounds.BudgetUsd), iterations, toolCalls, costUsd);
            }
        }
        catch (OperationCanceledException) when (timeout.IsCancellationRequested && !ct.IsCancellationRequested)
        {
            return Exceeded(nameof(bounds.TimeoutSeconds), iterations, toolCalls, costUsd);
        }

        return AgentOutcome.Completed(iterations, toolCalls, costUsd);
    }

    // La politique est appliquée ICI, une seule fois, pour tous les agents :
    // la dupliquer par agent garantit qu'un agent l'appliquera différemment.
    private AgentOutcome Exceeded(string bound, int it, int tc, decimal usd) => bounds.OnExceeded switch
    {
        OnBoundExceeded.FailExplicit  => AgentOutcome.Failed(bound, it, tc, usd),
        OnBoundExceeded.Degrade       => AgentOutcome.Partial(bound, it, tc, usd),
        OnBoundExceeded.EscalateHuman => AgentOutcome.Escalated(bound, it, tc, usd),
        _ => throw new InvalidOperationException($"OnBoundExceeded non géré : {bounds.OnExceeded}"),
    };
}
```

Trois points qui se paient cher s'ils sautent :

1. **Le `catch` distingue les deux annulations.** Sans la clause `when`, une
   annulation venue de l'appelant (utilisateur qui ferme la connexion) serait
   comptée comme un dépassement de borne, et le tableau de bord montrerait des
   timeouts qui n'ont jamais eu lieu.
2. **Le budget est calculé depuis l'usage réellement retourné**, pas estimé.
   Un budget estimé n'est pas une borne, c'est une prévision.
3. **Le dépassement retourne, il ne lève pas.** Un `AgentOutcome` explicite
   force l'appelant à traiter les quatre cas ; une exception se rattrape trois
   niveaux plus haut, où plus personne ne sait quelle borne a sauté.

### 3.4 Outils : du contrat à l'`AIFunction`

```csharp
// src/{AppName}/Tools/ToolRegistry.cs — extrait
public AIFunction Bind(ToolSpec spec, Delegate implementation) =>
    AIFunctionFactory.Create(
        implementation,
        name: spec.Name,                 // vient du contrat
        description: spec.Description);  // vient du contrat, hashée dans toolSchemaHash
```

La `description` passée ici est **celle du tool-contract**, jamais un résumé
réécrit dans le code : c'est sur elle seule que le modèle décide d'appeler
l'outil, elle est revue comme du prompt et elle entre dans `toolSchemaHash`. Un
écart entre les deux est une divergence que la TOOL GATE signale.

`GetForAgent(agentId)` rend exactement les outils du contrat de cet agent —
moindre privilège, sinon `[TOOL_SCOPE_EXCESS]`.

### 3.5 Quand activer `Workflows`, et quand s'en passer

| TOPOLOGY | Workflows ? |
|---|---|
| `single-agent` | **non** — un `AIAgent` et le runner de §3.3 suffisent |
| `sequential` sans cycle | **non** — une composition C# explicite est plus lisible et testable en L1 |
| `router` avec retour possible vers le routeur | **oui** — `maxHops` a besoin d'un graphe |
| `supervisor`, `reflection`, `parallel` | **oui** |
| `humanInTheLoop: true` ou reprise exigée | **oui** — l'interruption et le checkpointing en dépendent |

Activer `Workflows` pour un agent unique introduit un graphe que personne n'a
décidé, et un graphe non décidé est un graphe que personne ne borne. C'est la
raison pour laquelle le paquet est `onDemand` et non `core`.

### 3.6 Interruption et reprise

Un `escalate-human` n'est pas un `throw` : c'est un **point d'arrêt persisté**.
Le `Workflow` s'interrompt, l'état (§3.2) et le `AgentThread` sérialisé sont
écrits avec le `RunId` comme clé, et la reprise ré-entre avec la réponse humaine.

Deux invariants, tous les deux testés en L2 :

- **La reprise ne remet aucun compteur à zéro.** L'état rechargé porte ses
  `Hops`, `ToolCalls` et `CostUsd`. Un run repris après escalade qui repartirait
  de zéro contournerait toutes les bornes — et c'est le mode d'échec naturel si
  on reconstruit l'état au lieu de le recharger.
- **La réponse humaine est `Untrusted`.** Elle vient d'un formulaire, donc d'un
  tiers ; l'enveloppe de `lang/csharp.md` §5.3 s'y applique comme au reste.

### 3.7 Streaming

`RunStreamingAsync` rend un `IAsyncEnumerable` : les bornes se vérifient **au
fil** (§3.3), pas après. Vérifier après l'agrégation revient à laisser la boucle
finir avant de constater qu'elle était trop longue — ce qui a déjà coûté le
budget qu'on voulait protéger.

Le `CancellationToken` passé à `RunStreamingAsync` doit être celui du runner,
lié au timeout : un token ignoré fait du timeout un compteur (cf.
`lang/csharp.md` §7.4).

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── Models.cs                      # Resolve(tier) -> IChatClient
├── Agents/
│   ├── BoundedAgentRunner.cs      # bornes + politique de dépassement — PARTAGÉ
│   ├── AgentOutcome.cs            # Completed | Failed | Partial | Escalated
│   └── {AgentSlug}/
│       ├── Agent.cs               # Build(deps, bounds) -> AIAgent
│       ├── Schemas.cs             # records d'entrée / sortie
│       └── Deps.cs                # AgentDeps — injection explicite
├── Tools/
│   ├── ToolSpec.cs  ToolRegistry.cs
│   └── Mcp/                       # cf. tools/mcp.md
└── Orchestration/                 # SEUL endroit nommant Microsoft.Agents.AI
    ├── OrchestrationState.cs
    ├── Routers.cs                 # fonctions PURES, testées en L1
    └── Graph.cs                   # Workflow — présent seulement si §3.5 l'exige

workspace/src/{AppName}/tests/{AppName}.Tests/
├── BoundedAgentRunnerTests.cs     # L1 : les 5 bornes, les 3 politiques, l'annulation appelant
├── RoutersTests.cs                # L1 : table de cas exhaustive, fallback compris
└── Orchestration/ResumeTests.cs   # L2 : la reprise ne remet aucun compteur à zéro
```

---

## 5. Conventions imposées

1. **`Microsoft.Agents.AI` n'apparaît que dans `Orchestration/` et
   `Agents/{Slug}/Agent.cs`.** Partout ailleurs, y compris dans les contrats,
   c'est `[FRAMEWORK_LEAK_IN_CONTRACT]` — `validate_ir.py` connaît `AIAgent`,
   `ChatClientAgent` et `AgentThread`.
2. **Les bornes sont dans `BoundedAgentRunner`, pas dans chaque agent.** Un
   runner par agent garantit que l'un d'eux appliquera la politique autrement.
3. **Les fonctions de routage sont pures.** Elles lisent l'état et rien d'autre :
   pas d'appel de service, pas d'horloge, pas d'aléa. C'est ce qui les rend
   testables en L1 sans modèle, et reproductibles en eval.
4. **Tout routeur a un cas par défaut**, et il est testé. Un routeur sans
   fallback est `[ROUTER_NO_FALLBACK]` : en production, la branche manquante est
   celle qu'on n'avait pas prévue.
5. **Un `AgentThread` par run**, jamais réutilisé entre deux runs. Un fil
   partagé fait fuiter le contexte d'un utilisateur vers le suivant — et le
   cloisonnement par identité ne se rattrape pas après coup.
6. **Aucune instruction système construite par concaténation.** Les instructions
   viennent de `Prompts.LoadSystemPrompt` ; le contenu tiers entre en message
   utilisateur enveloppé.
7. **La description d'outil vient du contrat**, hashée, jamais réécrite en code.
8. **`Workflows` est `onDemand`** : ne l'activer que si §3.5 le justifie, et le
   dire dans la TOPOLOGY.

---

## 6. Commande de smoke

Aucun modèle, aucun réseau, 0 token :

```bash
cd workspace/src/{AppName}
dotnet restore
dotnet build -warnaserror
dotnet test --filter "Category!=Network"
#   -> BoundedAgentRunnerTests : chaque borne déclenche, chaque politique s'applique,
#      une annulation de l'appelant n'est PAS comptée comme un dépassement
#   -> RoutersTests            : table de cas exhaustive + fallback
#   -> ResumeTests             : reprise après escalade, compteurs conservés
```

Smoke Timeout : 180 s.

---

## 7. Pièges connus

1. **Croire que le framework porte les bornes.** Il porte la boucle, pas ses
   limites. Un agent « borné » par la seule configuration du framework n'est pas
   borné — et c'est invisible jusqu'au jour où une boucle tourne.
2. **Le budget estimé au lieu de mesuré.** Calculer le coût depuis la longueur
   du prompt donne un chiffre plausible et faux. `Usage` réel, toujours.
3. **La reprise qui reconstruit l'état.** Après une escalade, recréer un état
   neuf « parce que c'est plus simple » remet `Hops`, `ToolCalls` et `CostUsd` à
   zéro. Le run reprend avec toutes ses bornes rendues. C'est le piège le plus
   silencieux de cette fiche.
4. **L'annulation confondue avec le timeout.** Sans la clause `when` de §3.3, un
   utilisateur qui ferme sa connexion produit un faux dépassement de borne, et
   le taux de timeout mesuré ne veut plus rien dire.
5. **`AgentThread` réutilisé entre runs.** Le fil porte l'historique : le
   partager fait entrer la conversation d'un utilisateur dans le contexte du
   suivant. Un par run, corrélé au `RunId`.
6. **La description d'outil réécrite dans le code.** Le modèle décide d'après
   elle ; deux versions divergentes, et le contrat relu n'est pas celui qui
   s'exécute. La TOOL GATE le voit, à condition de comparer les deux.
7. **`Workflows` activé par défaut.** Un graphe pour un agent unique ajoute des
   chemins que personne n'a décidés, donc que personne ne borne ni ne teste.
8. **Le streaming agrégé avant vérification.** Attendre la fin de
   `RunStreamingAsync` pour compter les itérations, c'est constater le
   dépassement après l'avoir payé.
9. **`provider-anthropic` sans paquet officiel.** Il n'existe pas de
   `Microsoft.Agents.AI.Anthropic` : le provider passe par `Anthropic.SDK`
   (communautaire) derrière `IChatClient`. C'est le point de couplage le plus
   fragile de cette stack — une montée de version du SDK peut changer le mapping
   des `Usage`, donc le calcul de budget de §3.3. À réévaluer dès qu'un paquet
   officiel existe.
10. **Mélanger les versions de la famille `Microsoft.Agents.AI.*`.** Elle est
    versionnée solidairement (1.22.0). Monter un seul paquet produit une
    combinaison non testée, et Central Package Management est ce qui l'empêche —
    à condition qu'aucun `.csproj` ne porte encore un `Version=` local.
11. **Réintroduire `Microsoft.SemanticKernel`.** Le framework EST la convergence
    de SK et d'AutoGen. Les faire cohabiter met deux modèles d'agent dans un même
    processus ; un agent générateur qui ne trouve pas `AIAgent` ajoutera `Kernel`
    en croyant réparer un oubli. C'est pourquoi le paquet est dans
    `absentByDesign` avec sa raison écrite.
