# Stack: python (lang)

Stack ID: lang-python
Status: Draft
Validation: 🟡 design-phase — non encore validé par un run mesuré
Languages: python
Scope: langage et runtime de l'application agentic générée (outillage, structure de projet, conventions transverses). Le framework agentic est hors périmètre → `.sdda/stacks/framework/*.md`.

---

## 1. Rôle et périmètre

Cette fiche fixe **le socle Python** sur lequel toutes les autres stacks Python
de SDD_Agents s'appuient (`framework/langgraph.md`, `framework/langchain.md`,
`vectorstore/pgvector.md`, `observability/otel-genai.md`, `eval/pytest-eval.md`,
`serving/cli.md`). Elle décide :

- la version du langage et le gestionnaire de dépendances ;
- l'outillage déterministe (lint, format, typage, tests) exécuté en L0/L1 sans
  aucun token ;
- la **structure de projet agentic** — où vivent agents, outils, retrieval,
  orchestration, prompts chargés, bornes, tracing ;
- les conventions transverses qui rendent les principes du framework
  **vérifiables par un script** : aucun prompt inline (P1), aucun secret lu via
  `os.environ` (contrat de propagation de `STACK.md`), toute borne typée (P12).

Elle **ne décide pas** : le framework agentic, le pattern d'orchestration, le
store vectoriel, la surface d'exposition. Ces choix sont déclarés dans
`workspace/stack/STACK.md` et documentés dans leurs fiches.

---

## 2. Identité

| | |
|---|---|
| **Stack ID** | `lang-python` |
| **Langage** | Python **3.12** (pin) — 3.13 toléré ; 3.14 refusé tant que les wheels des dépendances natives ne sont pas généralisées |
| **Gestionnaire** | `uv` 0.9.x — résolution, lockfile, venv, exécution (`uv run`) |
| **Lint / format** | `ruff` 0.16.5 (remplace flake8 + isort + black) |
| **Typage** | `mypy` 2.3.1, mode `strict` |
| **Tests** | `pytest` 9.0.x — cf. `eval/pytest-eval.md` pour la couche eval |
| **Modèles de données** | `pydantic` 2.13.5 + `pydantic-settings` 2.15.0 |
| **Logs** | `structlog` 26.1.0 — jamais `print`, jamais `logging.info` brut |
| **Namespace racine** | `{AppName}` (snake_case), ex. `support_assistant` |

> Les versions ci-dessus sont alignées sur le catalogue SDD_Pro (rebase
> 2026-09). Pas de `.libs.json` pour la fiche `lang` : les librairies sont
> portées par les fiches de code qui en dépendent (`framework/*.libs.json`, etc.).
> Le socle commun (`pydantic`, `pydantic-settings`, `structlog`, `ruff`, `mypy`)
> est répété dans chaque `.libs.json` pour que chaque stack soit installable seule.

### 2.1 Init (idempotent)

```bash
# Le projet et son pyproject.toml sont GÉNÉRÉS — jamais `uv init` à la main :
# le squelette porte la convention de layout (voir §4) et le point d'entrée console.
python .sdda/sdda.py gen-app-skeleton --write        # idempotent : ne réécrit que ce qui a dérivé
cd workspace/src/{AppName} && uv sync                 # résout et verrouille les dépendances des stacks actives
```

**Layout PLAT, celui de SDD_Pro** : `workspace/src/{AppName}/` EST le paquet Python.
`pyproject.toml`, `.env`, `README.md` à sa racine ; `agents/`, `tools/`, `data/`,
`orchestration/`, `serving/`, `app/` en dessous, un seul niveau. Le « src layout »
de uv (`{AppName}/src/{AppName}/`) doublait le nom du projet et cachait le code
deux répertoires plus bas. `pyproject.toml` (hatchling, `sources = {"" = "{AppName}"}`)
réécrit la racine en `{AppName}/` dans la roue, `tests/` en est exclu — le paquet
reste installable et `uv run {AppName}` fonctionne. Une seule fonction dit où est
ce répertoire : `sdda_lib.paths.app_src_root`.

Bloc à ajouter dans `pyproject.toml` (si absent) :

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC", "S", "T20", "RUF"]
# T20 : interdit print() ; S : bandit (secrets en dur, subprocess shell=True, eval)

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests", "../../evals"]
markers = [
  "llm: appelle un modèle (coûte des tokens)",
  "network: exige une connectivité externe",
  "eval: évaluation k-runs (cf. eval/pytest-eval.md)",
  "critical: CAP critique — k=5",
]
```

---

## 3. Mapping des concepts SDD_Agents → idiomes Python

| Concept (DOMAIN-MODEL) | Idiome Python imposé | Où |
|---|---|---|
| **AGENT** | un package `agents/{agent_slug}/` exposant `build(deps: AgentDeps, bounds: Bounds) -> Runnable` — la classe concrète dépend du framework | `src/{AppName}/agents/` |
| **PROMPT** (`prompt_ref` + hash) | `LoadedPrompt(text, sha256, path)` chargé au démarrage par `prompts.load_system_prompt(slug)` depuis `workspace/src/{App}/prompts/{slug}.system.md`. **Jamais de texte système dans le code.** | `src/{AppName}/prompts.py` |
| **TOOL** | fonction `async def` typée + `ToolSpec` (pydantic, frozen) portant `name`, `description`, `side_effect_class`, `trust`, `timeout_s`, `retry_policy` ; `input_schema`/`output_schema` dérivés de modèles pydantic | `src/{AppName}/tools/{tool_slug}.py` |
| **BOUNDS** (P12) | `Bounds(BaseModel, frozen=True)` : `max_iterations`, `max_tool_calls`, `max_delegation_depth`, `timeout_s`, `budget_usd` ; hiérarchie `BoundExceeded(Exception)` → `IterationsExceeded`, `ToolCallsExceeded`, `DelegationDepthExceeded`, `TimeoutExceeded`, `BudgetExceeded` ; `OnBoundExceeded = Literal["fail-explicit","degrade","escalate-human"]` | `src/{AppName}/bounds.py` |
| **MODEL BINDING** (tier) | `Settings.runtime_tier_map: dict[Tier, str]` — le code manipule `Tier`, jamais un nom de modèle ; la résolution se fait dans `models.resolve(tier) -> ChatModel` | `src/{AppName}/config.py`, `models.py` |
| **RETRIEVER / INDEX** | package `retrieval/{index_slug}/` exposant `retrieve(query, *, top_k, tenant) -> list[RetrievedChunk]` ; `RetrievedChunk` porte `doc_id`, `chunk_id`, `score`, `content`, `citation` | `src/{AppName}/retrieval/` |
| **DATA ACCESS** | `data/` : vues SQL versionnées + outils générés (cf. `dataaccess/view-per-agent.md`) | `src/{AppName}/data/` |
| **ORCHESTRATION PATTERN** | `orchestration/graph.py` (ou `pipeline.py`) — seul endroit où le framework apparaît nommément | `src/{AppName}/orchestration/` |
| **GUARDRAIL** | `guardrails/{id}.py` : `check(payload) -> GuardrailVerdict(passed, reason, on_trip)` | `src/{AppName}/guardrails/` |
| **TRACE SPAN** | context managers `agent_turn()`, `llm_call()`, `tool_call()`, `retrieval()` (cf. `observability/otel-genai.md`) | `src/{AppName}/tracing/` |
| **Secrets** | `Settings(BaseSettings)` avec `SecretStr` ; le code lit `settings.llm_api_key.get_secret_value()` — **jamais** `os.environ["..."]` → `[SEC_ENV_VAR_FORBIDDEN]` | `src/{AppName}/config.py` |
| **Trust posture** (P8) | type `Untrusted` (NewType sur `str`) pour tout texte non maîtrisé ; les fonctions qui construisent un message système n'acceptent **pas** `Untrusted` — mypy le refuse | `src/{AppName}/trust.py` |

### 3.1 Chargement et hash des prompts

```python
# src/{AppName}/prompts.py
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"   # workspace/src/{AppName}/prompts — AVEC l'application


@dataclass(frozen=True, slots=True)
class LoadedPrompt:
    slug: str
    text: str
    sha256: str
    path: Path


def load_system_prompt(slug: str) -> LoadedPrompt:
    path = PROMPTS_DIR / f"{slug}.system.md"
    raw = path.read_text(encoding="utf-8")
    # CRLF-safe : même hash sur Windows et Linux
    normalized = raw.replace("\r\n", "\n").rstrip("\n") + "\n"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return LoadedPrompt(slug=slug, text=normalized, sha256=f"sha256:{digest}", path=path)
```

Le hash calculé ici **doit** être identique à celui que `sdda_lib.hashing`
calcule côté framework (même normalisation) : c'est le `prompt_hash` du tuple
d'épinglage P10.

### 3.2 Bornes

```python
# src/{AppName}/bounds.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OnBoundExceeded = Literal["fail-explicit", "degrade", "escalate-human"]


class Bounds(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_iterations: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    max_delegation_depth: int = Field(ge=0)
    timeout_s: float = Field(gt=0)
    budget_usd: float = Field(gt=0)
    on_exceeded: OnBoundExceeded


class BoundExceeded(Exception):
    """Base. Porte l'état partiel pour permettre fail-explicit avec contexte."""

    def __init__(self, bound: str, limit: float, observed: float, partial_state: dict[str, object]) -> None:
        super().__init__(f"[BUDGET_BOUND_EXCEEDED] {bound}: observed={observed} limit={limit}")
        self.bound, self.limit, self.observed, self.partial_state = bound, limit, observed, partial_state


class IterationsExceeded(BoundExceeded): ...
class ToolCallsExceeded(BoundExceeded): ...
class DelegationDepthExceeded(BoundExceeded): ...
class TimeoutExceeded(BoundExceeded): ...
class BudgetExceeded(BoundExceeded): ...
```

Aucune valeur par défaut : les bornes viennent du contrat d'agent via l'IR. Un
agent construit sans `Bounds` ne compile pas (argument obligatoire de `build`).

---

## 4. Structure de fichiers générée

```
workspace/src/{AppName}/
├── pyproject.toml
├── uv.lock
├── __init__.py
├── config.py               # Settings pydantic-settings : tiers, secrets (SecretStr), bornes par défaut
├── models.py               # resolve(tier) -> ChatModel — seul point de contact avec le provider
├── prompts.py              # load_system_prompt + hash normalisé
├── bounds.py               # Bounds, BoundExceeded*, OnBoundExceeded
├── trust.py                # Untrusted, wrap_untrusted(text, source)
├── agents/
│   └── {agent_slug}/
│       ├── __init__.py
│       ├── agent.py        # build(deps, bounds) -> Runnable ; lit le prompt, câble outils + bornes
│       ├── schemas.py      # Input / Output pydantic (contrat §6)
│       └── deps.py         # AgentDeps : outils, retrievers, model, tracer — injection explicite
├── tools/
│   ├── __init__.py
│   ├── spec.py             # ToolSpec, SideEffectClass, Trust, RetryPolicy
│   ├── registry.py         # register(spec, fn) ; get_for_agent(agent_id) — moindre privilège
│   ├── {tool_slug}.py
│   └── mcp/                # cf. tools/mcp.md
├── retrieval/
│   └── {index_slug}/       # cf. rag/*.md, vectorstore/*.md
├── data/                   # cf. dataaccess/*.md
│   └── views/*.sql
├── guardrails/
├── orchestration/          # cf. framework/*.md — SEUL endroit nommant le framework
├── tracing/                # cf. observability/*.md
├── serving/                # cf. serving/*.md
└── tests/                      # L1 (unit) + L2 (contrats d'outils) — LLM mocké
    ├── conftest.py
    ├── test_bounds.py
    ├── test_prompts.py
    └── tools/test_{tool_slug}.py
```

Les evals (L3+) vivent dans `workspace/proof/`, hors du package — ownership
`qa-evals`, jamais `dev-*` (ARCHITECTURE §7).

---

## 5. Conventions imposées

### 5.1 Style

- `from __future__ import annotations` en tête de chaque module.
- Type hints sur **toute** signature publique ; `mypy --strict` vert en L0.
- `async def` pour toute I/O (provider, outils, base, MCP). Un `async def` sans
  `await` est refusé (ruff `ASYNC`).
- Modèles pydantic `frozen=True` pour tout ce qui traverse une frontière
  (schémas d'agent, `ToolSpec`, `Bounds`, événements de trace).
- `structlog` avec `bind(run_id=..., agent_id=...)` ; sortie JSON.
- Nommage : modules `snake_case.py`, classes `PascalCase`, fonctions/variables
  `snake_case`, constantes `SCREAMING_SNAKE_CASE`, slugs d'agent/outil en
  `kebab-case` dans les contrats et `snake_case` dans le code (conversion
  déterministe `slug.replace("-", "_")`).

### 5.2 Interdits — vérifiés en L0 (0 token)

| Interdit | Détection | Code d'erreur |
|---|---|---|
| Prompt système inline (chaîne > 200 caractères passée en rôle `system`, ou `SystemMessage("...")` littéral) | lint AST `sdda_scripts/lint_inline_prompts.py` | `[PROMPT_INLINE]` |
| `os.environ[...]` / `os.getenv(...)` hors `config.py` | ruff custom + grep | `[SEC_ENV_VAR_FORBIDDEN]` |
| Nom de modèle littéral (`"claude-…"`, `"gpt-…"`) hors `config.py` | grep | `[MODEL_NAME_HARDCODED]` |
| Boucle `while True` / `for _ in itertools.count()` dans `agents/` ou `orchestration/` | lint AST | `[UNBOUNDED_LOOP]` |
| `print(...)` | ruff `T20` | `[LOG_PRINT_FORBIDDEN]` |
| `eval`, `exec`, `subprocess(shell=True)` | ruff `S` | `[SEC_DANGEROUS_CALL]` |
| Dépendance non listée dans le `.libs.json` d'une stack active | diff `uv.lock` vs catalogues | `[STACK_LIBRARY_MISSING]` |
| Écriture dans `workspace/proof/datasets/` ou `workspace/src/{App}/prompts/` depuis `src/` | scan d'imports/`open(...)` | `[EVAL_OWNERSHIP_VIOLATION]` |
| `time.sleep` en code async | ruff `ASYNC` | — |
| `from x import *` | ruff `F403` | — |

### 5.3 Frontière de confiance dans le type system

```python
# src/{AppName}/trust.py
from __future__ import annotations

from typing import NewType

Untrusted = NewType("Untrusted", str)


def wrap_untrusted(text: str, *, source: str, max_chars: int) -> str:
    """Encadre un texte hostile pour insertion dans un message UTILISATEUR (jamais système)."""
    clipped = text[:max_chars]
    truncated = "true" if len(text) > max_chars else "false"
    return f'<untrusted source="{source}" truncated="{truncated}">\n{clipped}\n</untrusted>'
```

Tout ce qui sort d'un retriever, d'un outil `trust: untrusted`, ou du message
utilisateur est typé `Untrusted`. Les constructeurs de message système
n'acceptent que `str` : le mélange est une erreur mypy, donc L0.

---

## 6. Commande de smoke

Déterministe, 0 token, < 60 s :

```bash
cd workspace/src/{AppName}
uv sync --frozen                          # le lock fait foi — [STACK_LIBRARY_MISSING] si divergence
uv run ruff check . && uv run ruff format --check .
uv run mypy src/
uv run python -c "import {AppName}; from {AppName}.config import Settings; Settings()"   # fail-fast si config incomplète
uv run python -m sdda_scripts.lint_inline_prompts src/          # [PROMPT_INLINE]
uv run pytest tests/ -q -m "not llm and not network"           # L1 + L2 mockés
```

Smoke Timeout : 60 s. Tout échec est bloquant pour le passage en PHASE 4.

---

## 7. Pièges connus

1. **Hash de prompt divergent Windows/Linux.** Un fichier `.system.md` en CRLF
   donne un autre sha256 qu'en LF → baselines périmées à tort (P10). La
   normalisation `\r\n → \n` de §3.1 est obligatoire des deux côtés (code généré
   et `sdda_lib.hashing`). Ajouter `* text=auto eol=lf` dans `.gitattributes`.
2. **Encodage console Windows.** Sortie UTF-8 non garantie (`cp1252`) →
   `PYTHONUTF8=1` dans l'environnement d'exécution et `encoding="utf-8"` explicite
   sur tout `open()`/`read_text()`.
3. **Pydantic v1 résiduel.** Les frameworks agentic sont tous sur pydantic v2 ;
   un `from pydantic.v1 import ...` ou une lib tierce tirant v1 casse
   `with_structured_output`. Vérifier `uv tree | grep pydantic`.
4. **Wheels natives et 3.13/3.14.** `tiktoken`, `psycopg[binary]`, `numpy`
   traînent parfois. Le pin 3.12 est là pour ça ; ne pas le relâcher sans smoke vert.
5. **Boucle d'événements et tests.** `asyncio_mode = "auto"` évite les
   `RuntimeError: no running event loop` ; ne pas mélanger `asyncio.run()` dans
   un test async.
6. **`mypy --strict` et frameworks dynamiques.** Certains types LangChain sont
   `Any`-heavy ; contenir les `# type: ignore[...]` **avec code d'erreur** dans
   `orchestration/` et `models.py`, jamais dans `agents/` ni `tools/`.
7. **Le lock dérive silencieusement.** `uv add` sans `--frozen` en smoke
   remet à jour le lock : toujours `uv sync --frozen` en L0, `uv lock --upgrade`
   uniquement par une action tracée (mise à jour du `.libs.json` d'abord).
8. **Chemins relatifs vers `prompts/`, `skills/`, `rules/`.** Ils vivent DANS le
   paquet (`Path(__file__).parent`), donc partent avec la roue et le conteneur ;
   ne jamais les chercher depuis le répertoire courant ni depuis
   `workspace/` — une application lancée par le runner d'eval n'est pas lancée
   depuis sa racine. `SDDA_WORKSPACE_ROOT` ne sert qu'aux traces et aux datasets.
9. **`SecretStr` et logs.** `structlog` sérialise `SecretStr` en `**********` ;
   mais un `.get_secret_value()` dans un log reste un `[SECRET_LEAK]`. Le scan G7
   le voit, autant l'éviter.
10. **Slugs kebab vs snake.** `1-billing-specialist` (contrat) ↔
    `billing_specialist` (package). La conversion est déterministe et vit à un
    seul endroit (`sdda_lib.slugs`) ; la recoder à la main produit des
    références fantômes dans l'IR.
