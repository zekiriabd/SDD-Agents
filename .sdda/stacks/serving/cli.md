# Stack: cli (serving)

Stack ID: serving-cli
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: surface d'exposition **ligne de commande** de l'application agentic — commandes, entrée/sortie, streaming, mode machine (`--json` NDJSON), reprise après interruption, **codes de sortie stables** mappés sur la taxonomie `[CLASS]`. C'est la surface **minimale** : celle du smoke, des evals locales et des scripts. Suppose `lang/python.md` ; pas de `.libs.json` (`typer` et `rich` sont ajoutés au `.libs.json` du framework actif via la capability `serving-cli` ; pins de référence en §2).

---

## 1. Rôle et périmètre

La CLI est la première surface générée, pour trois raisons :

1. **Elle est déterministe à câbler.** Pas de serveur, pas de session, pas
   d'authentification : `stdin → run → stdout`. Le pipeline peut faire tourner
   la mission en PHASE 5 avant qu'une surface réseau existe.
2. **Elle est la surface des evals.** Le runner L4–L7 invoque l'application
   par la même voie qu'un humain au terminal : ce qu'on mesure est ce qu'on
   livre. Le mode `--json` en fait un contrat machine.
3. **Elle rend les bornes visibles.** Un code de sortie `3` pour « borne
   atteinte » ou `5` pour « budget dépassé » est lu par un script, un
   `Makefile`, une CI — sans parser de texte.

Périmètre : commandes, options, protocole d'événements NDJSON, streaming,
codes de sortie, interruption/reprise, gestion des signaux, traces. **Hors
périmètre** : les autres surfaces (`fastapi-sse.md`, `mcp-server.md`,
`slack-bot.md`, `chainlit.md`) — elles **réutilisent** le même `RunService` et
le même schéma d'événements (§3.2) ; seul le transport change.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `serving-cli` |
| **Langage** | Python 3.12 (`lang/python.md`) |
| **Librairies** | `typer` 0.21.x (sur `click` 8.x) · `rich` 14.x (rendu TTY uniquement) — ajoutées au `.libs.json` du framework actif, capability `serving-cli` |
| **Point d'entrée** | `[project.scripts] {AppName} = "{AppName}.serving.cli:app"` → `uv run {AppName} …` |
| **Paramètres STACK.md** | `StreamingEnabled: true`, `HumanInTheLoopEnabled` (active `resume`), `TraceLevel` |
| **Contrat machine** | `--json` : une ligne NDJSON par événement, schéma `RunEvent` (pydantic, versionné `event_schema: "1"`) |
| **Codes de sortie** | table §3.3 — **stables**, documentés dans `--help`, testés en L1 |

---

## 3. Mapping des concepts SDD_Agents → idiomes CLI

### 3.1 Commandes

| Commande | Rôle | Options principales | Coûte des tokens |
|---|---|---|---|
| `{AppName} run` | une exécution de la MISSION | `--input TEXT` \| `--input-file PATH` \| stdin ; `--thread-id ULID` (nouveau si absent) ; `--json` ; `--no-stream` ; `--tenant ID` (**identité de l'appelant**, cf. §5.6) ; `--max-budget-usd X` (≤ `CostPerRunHardCapUsd`, **jamais au-dessus**) ; `--trace-out PATH` | oui |
| `{AppName} resume` | reprend un run interrompu (`escalate-human`, `interrupt`) | `--thread-id ULID` (**obligatoire**) ; `--decision TEXT` \| `--decision-file PATH` ; `--json` | oui |
| `{AppName} health` | vérifications déterministes : config chargée, prompts présents et hashés, outils enregistrés = contrats, IR compilé à jour, checkpointer joignable (`--live`), serveurs MCP joignables (`--live`) | `--live` (ajoute les tests `network`) ; `--json` | **non** |
| `{AppName} inspect` | affiche l'IR résumé : agents, outils par agent, bornes, pattern, budget estimé ; `--graph` imprime le Mermaid généré | `--graph` ; `--json` | non |
| `{AppName} trace` | relit `workspace/.sys/traces/runs/{run-id}.jsonl` : arbre des spans, coût total, hops, outils appelés, bornes atteintes | `--run-id` ; `--json` | non |
| `{AppName} version` | version de l'application, hash de l'IR, hash de la stack, `semconv_version` | | non |

`ingest`, `eval` **ne sont pas** des commandes de la CLI applicative : l'ingestion
appartient à `retrieval/{index}/ingest.py` (ownership `dev-retrieval`), l'évaluation à
`eval/pytest-eval.md` (ownership `qa-evals`). La CLI applicative n'écrit
jamais dans `datasets/`, `evals/`, `prompts/`.

### 3.2 Protocole d'événements (`--json`)

Une ligne par événement, `stdout` **exclusivement** ; logs et diagnostics sur
`stderr`. Schéma `RunEvent` :

| `event` | Quand | Champs |
|---|---|---|
| `run_started` | début | `run_id`, `thread_id`, `mission_id`, `ir_hash`, `event_schema` |
| `agent_started` / `agent_finished` | tour d'agent | `agent_id`, `iteration`, `duration_ms`, `cost_usd_cumulative` |
| `token` | streaming du modèle (`StreamingEnabled`) | `agent_id`, `text` |
| `tool_call` | avant exécution | `agent_id`, `tool`, `call_id`, `args` (**redigés**) |
| `tool_result` | après | `call_id`, `ok`, `error_code?`, `bytes`, `truncated` |
| `retrieval` | après retrieve | `index_id`, `result_ids[]`, `scores[]` |
| `guardrail` | déclenchement | `id`, `stage`, `passed`, `on_trip` |
| `bound_exceeded` | borne atteinte | `agent_id`, `bound`, `limit`, `observed`, `policy` |
| `interrupted` | `interrupt()` / `escalate-human` | `thread_id`, `reason`, `payload` — **le run s'arrête ici, code 10** |
| `final` | réponse finale | `output` (objet conforme au `outputSchema` de l'agent racine), `degraded: bool`, `citations[]` |
| `error` | échec | `class` (`[CLASS]`), `message`, `exit_code` |
| `run_finished` | toujours en dernier | `exit_code`, `cost_usd`, `duration_ms`, `hops`, `tool_calls`, `trace_path` |

Sans `--json` (TTY) : `token` est écrit au fil de l'eau, `final` est rendu
(`rich`, **avec `escape()` du contenu**, §7.6), les autres événements vont sur
`stderr` sous forme compacte. Sans TTY et sans `--json` : pas de couleur, pas
de spinner, texte brut.

### 3.3 Codes de sortie

| Code | Signification | `[CLASS]` typiques | Qui le lit |
|---|---|---|---|
| `0` | succès — `final` émis, non dégradé | — | tout |
| `1` | erreur interne non classée | `[INTERNAL_ERROR]` | CI |
| `2` | usage : option invalide, entrée manquante (réservé par `click`) | `[CLI_USAGE]` | humain |
| `3` | **borne atteinte**, politique `fail-explicit` | `[BUDGET_BOUND_EXCEEDED]`, `[UNBOUNDED_LOOP]` (filet) | runner d'eval (L5 : comportement déclaré observé) |
| `4` | refus : guardrail `block-and-log` déclenché ou `refusal_policy` appliquée | `[SAFETY_GUARDRAIL_TRIPPED]`, `[AGENT_REFUSED]` | suite L8 : **le refus est le résultat attendu** |
| `5` | **budget de run dépassé** (`CostPerRunHardCapUsd` ou `--max-budget-usd`) | `[BUDGET_EXCEEDED_MEASURED]` | G6 |
| `6` | échec d'outil non déclaré ou connectivité | `[TOOL_CONTRACT_FAILED]`, `[TOOL_MCP_DISCONNECTED]` | G3 |
| `7` | succès **dégradé** — `final` émis avec `degraded: true` (politique `degrade`) | — | runner : compte comme jaune |
| `8` | configuration : `Settings` invalide, prompt absent, hash de contrat divergent, IR périmé | `[CONFIG_INVALID]`, `[PROMPT_MISSING]`, `[TOOL_SCHEMA_DRIFT]`, `[IR_STALE]` | `health`, CI |
| `9` | sortie du modèle non conforme au schéma après guardrail | `[AGENT_OUTPUT_INVALID]` | L4 |
| `10` | **interrompu** — attend une décision humaine ; `thread_id` imprimé ; reprendre avec `resume` | `[AGENT_INTERRUPTED]` | opérateur, orchestrateur |
| `11` | reprise impossible : `thread_id` inconnu, checkpoint absent, pas de checkpointer | `[RESUME_FAILED]` | opérateur |
| `130` | `SIGINT` (Ctrl-C) — traces flushées avant sortie | — | humain |

Règles : les codes `3`, `4`, `5`, `7`, `10` sont des **résultats**, pas des
plantages — l'événement `final` ou `interrupted` est émis avant `run_finished`.
Le mapping `[CLASS] → code` vit dans `serving/exit_codes.py`, **un seul endroit**,
testé en L1 (table exhaustive : toute classe connue a un code ; toute classe
inconnue → `1`).

### 3.4 Mapping des entités

| Concept | Idiome CLI |
|---|---|
| **MISSION** | une CLI = une mission ; `mission_id` dans `run_started` |
| **RUN** | un `run_id` (ULID) par invocation `run`/`resume` ; = `sdda.run.id` du span racine ; = nom du fichier de trace |
| **thread** (conversation / reprise) | `--thread-id` ; = `gen_ai.conversation.id` ; = `thread_id` du checkpointer |
| **Entrée utilisateur** | `Untrusted` — `--input`, fichier, stdin : même traitement, `wrap_untrusted(source="cli:stdin")` |
| **Identité de l'appelant** | `--tenant` (ou variable d'environnement `SDDA_TENANT_ID` via `Settings`) ; posé dans `ToolContext`, **jamais visible du modèle** ; obligatoire si `DatabaseType != none` ou RAG multi-tenant |
| **Bornes** | héritées de l'IR ; `--max-budget-usd` ne peut que **baisser** le plafond ; les autres bornes ne sont **pas** réglables en ligne de commande (elles sont dans les contrats) |
| **onBoundExceeded** | `fail-explicit` → code 3 + `final` d'échec structuré ; `degrade` → code 7 ; `escalate-human` → code 10 + `interrupted` |
| **TRACE** | `workspace/.sys/traces/runs/{run_id}.jsonl` toujours écrit ; chemin dans `run_finished.trace_path` et sur `stderr` |
| **Secrets** | jamais en argument (`ps` les voit) — `Settings`/`.env` uniquement ; une option `--api-key` est `[SEC_SECRET_IN_ARGV]` en L0 |

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/src/{AppName}/serving/
├── __init__.py
├── cli.py                # app = typer.Typer(no_args_is_help=True) ; commandes run/resume/health/inspect/trace/version ; aucun métier
├── run_service.py        # RunService.run(input, ctx) / .resume(thread_id, decision, ctx) -> AsyncIterator[RunEvent] — PARTAGÉ par toutes les surfaces
├── events.py             # RunEvent (pydantic frozen, discriminé par `event`), event_schema = "1"
├── exit_codes.py         # ExitCode(IntEnum) + CLASS_TO_EXIT: dict[str, ExitCode] + resolve(exc) -> ExitCode
├── render.py             # rendu TTY (rich, escape) vs NDJSON vs texte brut ; sélection par isatty() et --json
├── signals.py            # SIGINT/SIGTERM → annulation propre, flush traces, code 130
└── context.py            # ToolContext(tenant_id, run_id, thread_id, caller) construit depuis les options — le tenant ne traverse jamais le modèle

workspace/src/{AppName}/tests/serving/
├── test_exit_codes.py    # L1 : table exhaustive [CLASS] → code ; classe inconnue → 1 ; codes 3/4/5/7/10 émettent final|interrupted avant run_finished
├── test_events_schema.py # L1 : chaque RunEvent sérialisable/désérialisable ; event_schema présent ; args d'outil redigés
├── test_cli_json.py      # L1 : CliRunner + RunService mocké → stdout = NDJSON valide uniquement ; stderr porte les logs ; codes de sortie
└── test_cli_health.py    # L1 : health sans --live est 0 token et < 5 s ; --live marqué network
```

---

## 5. Conventions imposées

1. **`stdout` est le canal de résultat, `stderr` celui du diagnostic.** En
   `--json`, **rien** d'autre que du NDJSON valide ne sort sur `stdout` — pas de
   log, pas de warning de librairie (`warnings` redirigé), pas de barre de
   progression. Test L1 : chaque ligne de `stdout` parse en `RunEvent`.
2. **`run_finished` est toujours le dernier événement**, même après `error`,
   même sur `SIGINT` — il porte le `exit_code` effectif et `trace_path`.
3. **Le code de sortie est dérivé de la `[CLASS]`**, jamais choisi à la main
   dans une commande. `typer.Exit(code=resolve(exc))`.
4. **Streaming = `flush` par événement** (`sys.stdout.flush()`, ou
   `PYTHONUNBUFFERED=1` posé par `setup`). Un consommateur `--json` doit voir
   `token` en temps réel, pas en bloc à la fin.
5. **`escape()` de tout texte issu du modèle ou d'un outil** avant `rich`
   (§7.6) ; en NDJSON, `ensure_ascii=False` + échappement JSON standard.
6. **Le tenant vient de l'appelant** (`--tenant`, env), il est posé dans
   `ToolContext` et n'est **jamais** un paramètre d'outil ni un texte que le
   modèle voit. Absent alors que requis → code 8 `[CONFIG_INVALID]`, avant tout
   appel LLM.
7. **`health` ne coûte aucun token** et n'ouvre aucune connexion sans
   `--live`. C'est la commande du smoke (§6) et du `readinessProbe` des
   surfaces réseau.
8. **`--max-budget-usd` ne peut que baisser.** Une valeur supérieure à
   `CostPerRunHardCapUsd` est rejetée (code 2) : la CLI n'est pas un moyen de
   contourner P6.
9. **`resume` exige un checkpointer** (`framework/langgraph.md` avec
   `checkpointing: true`). Si la stack active ne le fournit pas, la commande
   n'est **pas enregistrée** (absente de `--help`) plutôt que présente et
   cassée.
10. **Toute exécution écrit sa trace** avant de rendre la main — y compris
    `health --live` (spans `sdda.gate`-like de vérification), y compris un
    échec au démarrage (span racine avec `error.type`).
11. **`--input-file` et stdin sont lus en UTF-8**, taille plafonnée
    (`Settings.max_input_bytes`, défaut 256 Ko) → au-delà, code 2 avant tout
    appel LLM.
12. **Pas de REPL/`chat` dans le MVP.** Une conversation multi-tours est une
    suite de `run --thread-id X` : même mécanique que la reprise, testable
    ligne par ligne.

---

## 6. Commande de smoke

Déterministe, 0 token :

```bash
cd workspace/src/{AppName}
uv run {AppName} --help                                   # exit 0 ; liste les commandes et la table des codes
uv run {AppName} version --json | python -c "import sys,json; json.loads(sys.stdin.read())"
uv run {AppName} health --json                            # exit 0 : Settings OK, prompts hashés, outils == contrats, IR à jour
#   exit 8 sinon, avec la [CLASS] précise dans l'événement error
uv run {AppName} inspect --graph > /tmp/graph.mmd && diff -w /tmp/graph.mmd ../../topology/{n}-topology.mmd
uv run pytest tests/serving -q
```

Avec dépendances joignables (`network`) : `uv run {AppName} health --live`.

Le premier `run` réel (coûte des tokens) n'est pas dans le smoke : il
appartient à la L7 (ORCH GATE), via le runner d'eval qui invoque exactement
`uv run {AppName} run --json --tenant … --input-file …` et lit les codes de sortie.

---

## 7. Pièges connus

1. **`click` réserve le code 2** pour les erreurs d'usage. Ne pas le réutiliser
   pour autre chose ; la table §3.3 le respecte.
2. **`sys.exit` dans du code async** lève `SystemExit` au milieu d'une boucle
   d'événements et saute le `force_flush` des traces. La commande `run`
   collecte le code dans une variable, sort de `asyncio.run`, **puis**
   `raise typer.Exit(code)`.
3. **Ctrl-C perd la trace.** Sans handler, `KeyboardInterrupt` interrompt
   `BatchSpanProcessor` avant l'export. `signals.py` : premier `SIGINT` →
   annulation coopérative (`task.cancel()`), `run_finished` émis, traces
   flushées, code 130 ; second `SIGINT` → sortie brutale.
4. **Sortie bufferisée sans TTY.** Redirigé vers un fichier ou un pipe,
   `stdout` est bufferisé par blocs : le consommateur `--json` ne voit rien
   pendant 30 s puis tout. `flush()` par événement, obligatoire.
5. **Encodage Windows.** Un `token` contenant « → » ou un emoji plante en
   `cp1252`. `PYTHONUTF8=1` posé par le point d'entrée **avant** tout `print`,
   `sys.stdout.reconfigure(encoding="utf-8")` en filet.
6. **`rich` interprète le balisage.** Un modèle qui écrit `[bold]` ou `[/]`
   — ou un document retrouvé contenant `[link=…]` — change le rendu, voire
   masque du texte. `rich.markup.escape()` sur **tout** contenu non maîtrisé ;
   c'est une injection d'affichage, pas seulement un bug visuel.
7. **Logs de librairies sur `stdout`.** `httpx`, `langchain`, `psycopg`
   peuvent logger sur `stdout` via le `root` logger mal configuré. `setup`
   redirige **tout** `logging` vers `stderr` (structlog) avant la première
   commande ; test L1 : `stdout` en `--json` ne contient que du NDJSON.
8. **Secrets dans `--help`.** Une option avec `default=settings.token` affiche
   la valeur dans l'aide. Aucune option ne porte de secret ; `show_default=False`
   par défaut sur les options sensibles à la configuration.
9. **`thread_id` réutilisé par accident** (copié-collé) = continuation d'une
   conversation étrangère. `run` sans `--thread-id` génère un ULID ; le
   réutiliser est un acte explicite, journalisé dans `run_started`.
10. **Codes > 255.** Impossible en POSIX ; la table reste sous 128 et laisse
    128+ aux signaux (130). Ne pas « encoder » la `[CLASS]` dans le code.
11. **Sortie structurée partielle en streaming.** Un `outputSchema` JSON
    streamé token par token n'est pas parsable avant la fin ; `token` porte
    le texte brut, `final` porte l'objet validé. Le consommateur affiche les
    tokens mais **agit** sur `final`.
12. **`health` qui appelle un modèle « pour vérifier la clé ».** Coûte des
    tokens à chaque probe, et échoue sur un quota sans que la config soit
    fausse. La vérification de clé est un test `network` explicite
    (`--live`), pas le défaut.
13. **stdin vide et TTY.** `run` sans `--input` sur un terminal attend stdin
    indéfiniment. Détection `isatty()` : sur TTY sans entrée → code 2 avec
    message ; hors TTY → lecture de stdin.
