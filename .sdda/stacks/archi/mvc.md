# Stack: mvc (archi)

Stack ID: archi-mvc
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: *
Scope: pattern d'architecture de la **coquille applicative** qui entoure le moteur agentic — l'entrée (serving), la composition (app/), la configuration, les adaptateurs vers le monde (tools, retrieval, data) et les règles métier de la MISSION portées en code pur. **Le défaut** du framework, dans les quatre langages. Hérité de SDD_Pro `archi/mvc.md`, transposé : il n'y a ni ORM ni entité persistante ici, et le « Model » est dérivé de l'IR. Ne s'applique **pas** au découpage interne du moteur (`agents/`, `tools/`, `orchestration/`, `retrieval/`, `data/`) : celui-là est imposé par la matrice d'ownership, pas par une fiche.

---

## 1. Rôle et périmètre

Un système agentic généré est deux choses collées : un **moteur** — les agents,
leurs outils, le graphe qui les orchestre — et une **coquille** — ce qui le
lance, le configure, l'expose et le range dans un exécutable ou un service. Le
moteur a une architecture imposée par le framework : cinq répertoires, cinq
owners, une gate par couche. La coquille, elle, n'en avait aucune : chaque
`dev-*` rangeait la configuration, la composition et l'entrée là où son
écosystème l'y habituait, et le projet en portait trois à la fin.

Cette fiche fixe **le découpage en couches de la coquille**. Elle dit où vit le
point d'entrée, où l'on compose les dépendances, où passe la validation, où
vivent les règles métier de la MISSION qui ne sont pas du jugement de modèle
(« un retard de plus de 7 jours est éligible » est une fonction pure, pas un
prompt), et comment les cinq répertoires du moteur s'y insèrent.

Ce qu'elle **ne décide pas** : le langage (`lang/`), le framework d'agents
(`framework/`), la surface d'entrée (`serving/`), le framework HTTP
(`backend/`), le pattern d'orchestration (`orchestration/`). Chacun a sa fiche ;
celle-ci dit comment ils se rangent ensemble.

> **MVC au sens SDD_Agents.** Il n'y a pas de View HTML : le « V » est le
> **contrat de sortie** dérivé de l'IR (`RunResponse`, `RunEvent`), sérialisé par
> la surface. Le « C » est la surface elle-même — commande CLI ou endpoint HTTP —
> qui reçoit, établit l'identité, valide, appelle et rend. Le « M » est
> `RunRequest`/`RunResponse` + les règles métier pures de la MISSION. Le
> « Service » est le `RunService` (cf. `serving/cli.md` §3) et la glu des cas
> d'usage. Les « Repositories » sont les adaptateurs déjà possédés : `data/`,
> `retrieval/`, `tools/`.

---

## 2. Couches canoniques

| # | Couche | Responsabilité | Dépend de | Owner |
|---|---|---|---|---|
| 1 | **Entrée** (Controller) | recevoir, établir l'identité de l'appelant **depuis le transport**, valider contre `inputSchema`, appeler `RunService`, rendre `RunResponse`/`RunEvent`. Aucune logique. | Service | `dev-api` (`serving/`) |
| 2 | **Service** | `RunService` : contexte d'exécution `{run_id, identity, deadline, budget}`, appel du graphe, mapping des erreurs nommées → Failure Policy. Cas d'usage transverses (ex. construire un `claim_request` depuis la sortie du graphe). | Domaine, Orchestration | `dev-backend` (`app/`) |
| 3 | **Domaine** | les règles métier de la MISSION qui se **calculent** : éligibilité, fenêtres, seuils, dates de référence. Fonctions pures, typées, testées en L1 sans LLM. | rien | `dev-backend` (`app/domain/`) |
| 4 | **Moteur** | agents, outils, orchestration, retrieval, accès données — le graphe de l'IR | Domaine (lecture), Adaptateurs | les cinq `dev-*` du moteur |
| 5 | **Adaptateurs** (Repositories) | ce qui touche le monde : sources déclarées, vues SQL, index, serveurs MCP, fournisseur de modèles. Enveloppe de sûreté comprise. | Config | `dev-data`, `dev-retrieval`, `dev-tools` |
| 6 | **Modèle** (DTO) | `RunRequest`, `RunResponse`, `RunEvent`, schémas d'outils — **générés** depuis l'IR, jamais écrits à la main | rien | `gen_app_skeleton`, `gen_source_tools` |
| 7 | **Config** | lecture des NOMS de variables, bornes, tiers, tarifs — `app_config.json` + Settings natifs de l'écosystème | rien | `gen_app_skeleton` / `dev-backend` |
| 8 | **Composition** | le seul endroit qui instancie : câble agents, outils, retrievers, graphe, traceur, surface. Injection de dépendances systématique. | tout | `dev-backend` (`app/composition`) |

Couches **transverses**, obligatoires : gestion centralisée des erreurs (une
erreur nommée `[CLASS]` → un code de sortie ou un statut, jamais une stack trace
au client) ; logs structurés ; traçage OTel-GenAI (`observability/`).

---

## 3. Mapping couche → répertoire

Chemin racine : `workspace/src/{AppName}/`. La profondeur du paquet est fixée
par la fiche de langage (`src/{AppName}/` en Python `uv --lib`, `src/` en
TypeScript, `src/main/kotlin/…` en Kotlin, `src/{AppName}/` en .NET) ; la
matrice d'ownership matche `**`, donc zéro segment ou plus.

| Couche | Emplacement canonique |
|---|---|
| Entrée | `…/serving/{cli,http,mcp,batch}/` |
| Service | `…/app/run_service.*` · `…/app/usecases/` |
| Domaine | `…/app/domain/` — un module par famille de règles (`eligibility`, `dates`, …) |
| Moteur | `…/agents/{agent}/` · `…/orchestration/` · `…/tools/` · `…/retrieval/` · `…/data/` |
| Modèle | `…/app/models.*` (généré) · `…/data/schemas/` (schémas figés) |
| Config | `…/app/config.*` · `…/app/app_config.json` (généré) |
| Composition | `…/app/composition.*` — importé par la surface, par personne d'autre |
| Prompts | `workspace/src/prompts/` — chargés par hash, jamais inline |
| Tests transverses | `workspace/src/{AppName}/tests/` |

Chaque fiche `backend/*.md` **surcharge** ce mapping dans son §3 quand la
convention du framework l'exige (`Endpoints/` en .NET, `controller/` en Kotlin).
La surcharge est locale et reste compatible avec ce squelette.

---

## 4. Principes non négociables

**Flux** :
- aucune logique métier dans l'Entrée : elle valide et délègue ;
- aucune décision de modèle hors du Moteur : un appel LLM dans `app/` ou
  `serving/` est un appel que les evals ne mesurent pas ;
- aucun accès aux données hors des Adaptateurs : une lecture de fichier ou de
  base dans un cas d'usage contourne l'enveloppe de sûreté ;
- le Domaine ne dépend de rien : ni du framework d'agents, ni d'un client HTTP,
  ni d'un logger. Il se teste en L1 en une milliseconde.

**Composition** :
- injection de dépendances systématique — pas de `new`/instanciation dans un
  agent ou une surface, pas de Service Locator ;
- **un seul point de composition**, importé par la surface. Deux compositions
  produisent deux applications qui divergent.

**Frontière** :
- validation de l'entrée contre `inputSchema` de l'IR, de la sortie contre
  `outputSchema`, **avant** tout coût de modèle et **avant** toute émission ;
- l'identité de l'appelant vient du transport (jeton, en-tête, opérateur du
  CLI) — jamais du corps du message (`[SERVING_IDENTITY_FROM_PAYLOAD]`).

**Secrets et config** :
- le code lit des NOMS de variables via la config native de l'écosystème ;
  aucun `os.environ` / `process.env` / `System.getenv` direct sur une clé
  sensible (`[SEC_ENV_VAR_FORBIDDEN]`) ;
- aucune valeur de secret dans un log, une trace, une erreur, un prompt
  (`[SECRET_LEAK]`).

**Erreurs et logs** :
- une erreur nommée du run (`BOUND_EXCEEDED:*`, `BUDGET_EXCEEDED_MEASURED`,
  refus) est mappée **une fois**, dans la couche transverse, vers la table
  `[CLASS] → code` de `serving/cli.md` §3.3 ;
- logger structuré injecté ; aucun `print`, `console.log`, `println`,
  `Console.WriteLine` dans le code livré.

---

## 5. Anti-patterns rejetés

| Anti-pattern | Pourquoi c'est faux ici |
|---|---|
| règle métier dans un prompt **et** dans le Domaine | deux vérités ; celle du prompt n'est pas testable en L1 et dérive au premier reformulation |
| `RunRequest` écrit à la main | il diverge de l'IR au premier changement de schéma ; l'API GATE le voit, trop tard |
| agent qui lit un fichier ou une table | sort de l'enveloppe ; la source de données devient invisible au validateur |
| composition dans la surface (`app = FastAPI(); graph = build()…`) | la CLI et le HTTP composent chacun leur système ; les evals mesurent l'un, on livre l'autre |
| `try/except` de formatage dans un endpoint | la table `[CLASS] → code` a un endroit, la couche transverse |
| `TODO`, `FIXME`, placeholder dans le code livré | le code généré est un livrable, pas un brouillon |

---

## 6. Surcharges par écosystème

| Concept | `backend/python-fastapi` | `backend/node-express` · `nestjs` | `backend/kotlin-spring-boot` | `backend/dotnet-minimalapi` |
|---|---|---|---|---|
| Entrée | `APIRouter` dans `serving/http/` | `Router` / `@Controller` dans `serving/http/` | `@RestController` dans `serving/http/` | `MapGroup` statique dans `Serving.Http/` |
| Injection | `Depends()` + composition explicite | fonction de composition (Express) / conteneur Nest | constructeur (Spring) | constructeur primaire + `AddScoped<>()` |
| Config | `pydantic-settings` | `zod`-parsed env dans `config.ts` | `@ConfigurationProperties` + `application.yml` | `IConfiguration` + `IOptions<T>` |
| Validation | Pydantic (généré) | Zod (généré) | Bean Validation sur data classes | FluentValidation / `required` records |
| Logs | `structlog` | `pino` | `kotlin-logging` (SLF4J) | `ILogger<T>` source-generated |
| Erreurs | middleware d'exceptions | `errorHandler` / `ExceptionFilter` | `@RestControllerAdvice` | middleware `ProblemDetails` |

---

## 7. Quand choisir autre chose

| | `mvc` (ici) | `ddd` | `microservice` |
|---|---|---|---|
| Domaine | quelques règles pures dans `app/domain/` | modèle riche : objets-valeurs, invariants, ports | idem `ddd` ou `mvc`, dans UN service déployable |
| Coût de structure | faible | moyen | élevé (santé, métriques, contrats versionnés, conteneur) |
| Choisir quand | la MISSION a peu de règles calculables ; livrable `cli-exe` ou API simple | les règles métier de la MISSION sont nombreuses, interdépendantes, et une erreur coûte de l'argent | le système est appelé par d'autres services, déployé seul, scalé seul |

**Défaut** : `mvc`. Un système agentic est d'abord un programme qu'on lance ;
la structure doit coûter moins que ce qu'elle protège.

---

## 8. Pour les agents

- **`dev-backend`** lit cette fiche en STEP 2 et matérialise les couches 2, 3,
  7 et 8 — puis le packaging. Il ne touche à aucun répertoire du Moteur.
- **`dev-api`** lit le §3 pour placer la surface, et le §4 pour ce qu'elle n'a
  pas le droit de contenir.
- **Les cinq `dev-*` du moteur** ne lisent que le §3 : il leur dit où trouver la
  composition et le Domaine qu'ils consomment.
- **`review-spec`** vérifie qu'une règle métier de la MISSION vit dans le
  Domaine **ou** dans un prompt — jamais dans les deux sans que l'un délègue à
  l'autre.

Précédence en cas de conflit : les idiomes de la fiche `backend/*.md` ou
`lang/*.md` priment sur le §6 ; les principes du §4 priment sur tout.
