"""Les cinq bornes, en code. GÉNÉRÉ, ne pas éditer.

Une borne déclarée dans un contrat et absente du code est une borne qui n'existe
pas. C'est le mode d'échec le plus cher de l'agentique : rien ne plante, la
boucle tourne, et la facture arrive. `Bounds` n'a **aucune valeur par défaut** —
construire un agent sans ses bornes est une `TypeError` à l'instanciation, pas
un run à 40 dollars. C'est le sens de « une borne absente doit être impossible à
exprimer ».

`0` en revanche est une valeur **légitime**, et la plus stricte : `max_tool_calls
= 0` décrit un agent qui n'a le droit d'appeler aucun outil, `max_delegation_depth
= 0` un agent qui ne délègue pas (c'est le cas nominal de `single-agent`). Rejeter
zéro obligerait à écrire `1` pour dire « aucun », et un contrat qui ment d'une
unité ment.

Les cinq bornes et ce qu'elles empêchent :

    max_iterations        la boucle de raisonnement qui ne conclut pas
    max_tool_calls        l'agent qui sonde ses outils au lieu de répondre
    max_delegation_depth  la délégation qui se replie sur elle-même
    timeout_s             l'appel distant qui pend et facture l'attente
    budget_usd            tout le reste, y compris ce qu'on n'a pas prévu

La dernière est le filet des quatre autres : c'est la seule qui borne un coût
qu'aucun compteur ne modélise (prompt qui grossit, cache qui manque, retry du
fournisseur).

`on_bound_exceeded` dit ce qui se passe **quand** la borne tombe, et les trois
réponses sont des résultats différents pour l'appelant, pas trois façons
d'échouer : `fail-explicit` rend un échec structuré (code 3), `degrade` rend la
meilleure réponse partielle en l'annonçant (code 7), `escalate-human` s'arrête et
attend une décision (code 10). La L5 vérifie que la politique **observée** est
celle qui était déclarée — `[BOUND_BEHAVIOR_MISMATCH]`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

OnBoundExceeded = Literal["fail-explicit", "degrade", "escalate-human"]

#: Les politiques admises, en liste close. Une valeur hors liste n'est pas
#: « comme fail-explicit par prudence » : c'est une déclaration qu'on n'a pas
#: comprise, et la deviner produirait un comportement que personne n'a revu.
POLICIES: tuple[str, ...] = ("fail-explicit", "degrade", "escalate-human")

#: Les noms des cinq bornes, dans l'ordre où elles apparaissent dans un contrat.
#: Nommés une fois : ils servent d'attribut de span, de clé de configuration et
#: de champ d'événement, et trois graphies feraient trois vérités.
BOUND_NAMES: tuple[str, ...] = (
    "max_iterations", "max_tool_calls", "max_delegation_depth", "timeout_s", "budget_usd")


class BoundExceeded(Exception):
    """Une borne est tombée. Porte laquelle, et l'état partiel.

    L'état partiel n'est pas du confort : `degrade` doit produire une réponse
    depuis lui, et `fail-explicit` doit dire à l'appelant *où* le run s'est
    arrêté. Une exception nue obligerait la couche du dessus à reconstituer
    l'information depuis des variables qu'elle n'a pas.
    """

    bound: str = ""

    def __init__(self, bound: str, limit: float, observed: float,
                 *, policy: OnBoundExceeded = "fail-explicit",
                 partial_state: dict[str, Any] | None = None) -> None:
        super().__init__(f"[BUDGET_BOUND_EXCEEDED] {bound}: observed={observed} limit={limit}")
        self.bound = bound
        self.limit = limit
        self.observed = observed
        self.policy: OnBoundExceeded = policy
        self.partial_state: dict[str, Any] = dict(partial_state or {})

    def to_dict(self) -> dict[str, Any]:
        return {"bound": self.bound, "limit": self.limit, "observed": self.observed,
                "policy": self.policy}


class IterationsExceeded(BoundExceeded):
    bound = "max_iterations"


class ToolCallsExceeded(BoundExceeded):
    bound = "max_tool_calls"


class DelegationDepthExceeded(BoundExceeded):
    bound = "max_delegation_depth"


class TimeoutExceeded(BoundExceeded):
    bound = "timeout_s"


class BudgetExceeded(BoundExceeded):
    bound = "budget_usd"


class HopsExceeded(BoundExceeded):
    bound = "max_hops"


class TokensExceeded(BoundExceeded):
    bound = "max_tokens_per_run"


#: Borne -> exception. Une table plutôt qu'une chaîne de `if` : le garde lève
#: par NOM de borne, et une borne ajoutée sans son exception se voit ici.
#: `max_hops` et `max_tokens_per_run` sont des bornes du RUN (IR :
#: `orchestration.maxHops`, `budget.tokenCeilingPerRun`), portées par
#: `RunLimits` et non par un agent : un graphe de dix agents sages peut quand
#: même faire cinquante hops.
EXCEPTIONS: dict[str, type[BoundExceeded]] = {
    "max_iterations": IterationsExceeded,
    "max_tool_calls": ToolCallsExceeded,
    "max_delegation_depth": DelegationDepthExceeded,
    "timeout_s": TimeoutExceeded,
    "budget_usd": BudgetExceeded,
    "max_hops": HopsExceeded,
    "max_tokens_per_run": TokensExceeded,
}


@dataclass(frozen=True)
class Bounds:
    """Les cinq bornes d'un agent. Aucun défaut : elles viennent du contrat.

    Gelée (`frozen`) pour une raison de mesure, pas de style : une borne qu'un
    nœud peut relever en cours de run rend le budget estimé de G2 sans rapport
    avec le coût mesuré de G6, et l'écart s'expliquerait par du code plutôt que
    par une décision.
    """

    max_iterations: int
    max_tool_calls: int
    max_delegation_depth: int
    timeout_s: float
    budget_usd: float
    on_bound_exceeded: OnBoundExceeded

    def __post_init__(self) -> None:
        for name in ("max_iterations", "max_tool_calls", "max_delegation_depth"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"`{name}` : entier >= 0 attendu, reçu {value!r}")
        for name in ("timeout_s", "budget_usd"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"`{name}` : nombre >= 0 attendu, reçu {value!r}")
        if self.on_bound_exceeded not in POLICIES:
            raise ValueError(
                f"`on_bound_exceeded: {self.on_bound_exceeded!r}` hors liste close {list(POLICIES)}")

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "Bounds":
        """Depuis l'IR ou `app_config.json`. Une clé absente est une ERREUR.

        Le repli sur un défaut serait le retour exact du problème que ce module
        existe pour fermer : l'agent tournerait avec une borne que personne n'a
        écrite, et la trace afficherait une limite qu'aucun contrat ne porte.
        """
        missing = [n for n in BOUND_NAMES if payload.get(n) is None]
        if missing:
            raise ValueError(
                f"borne(s) absente(s) : {missing} — elles viennent du contrat d'agent via l'IR, "
                "jamais d'un défaut du code")
        return cls(
            max_iterations=int(payload["max_iterations"]),
            max_tool_calls=int(payload["max_tool_calls"]),
            max_delegation_depth=int(payload["max_delegation_depth"]),
            timeout_s=float(payload["timeout_s"]),
            budget_usd=float(payload["budget_usd"]),
            on_bound_exceeded=str(payload.get("on_bound_exceeded") or "fail-explicit"),  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in BOUND_NAMES} | {
            "on_bound_exceeded": self.on_bound_exceeded}


@dataclass
class BoundGuard:
    """Le compteur qui fait tomber les bornes. Un par tour d'agent.

    Il compte *avant* d'agir, jamais après : `check_tool_calls(n)` est appelé
    avec le nombre d'appels que le modèle vient de demander, et refuse le lot
    entier s'il dépasse. Compter après l'exécution ferait que la borne ne
    protège plus de rien — l'effet de bord a déjà eu lieu, et sur un outil
    `write-destructive` il ne se reprend pas.

    Le temps vient d'une horloge injectable : un test qui doit attendre 60 s
    pour prouver qu'un `timeout_s` de 60 s tombe est un test qu'on désactive.
    """

    bounds: Bounds
    clock: Callable[[], float] = time.monotonic
    #: Profondeur de départ : un agent appelé PAR un autre agent démarre à la
    #: profondeur de son appelant + 1, sinon chaque niveau repartirait de zéro
    #: et `max_delegation_depth` ne bornerait qu'un seul étage.
    depth: int = 0
    iterations: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    _started: float = 0.0

    def __post_init__(self) -> None:
        self._started = self.clock()

    # -- État partiel, joint à chaque dépassement ---------------------------
    def state(self) -> dict[str, Any]:
        return {"iterations": self.iterations, "tool_calls": self.tool_calls,
                "depth": self.depth, "cost_usd": round(self.cost_usd, 6),
                "elapsed_s": round(self.elapsed, 3)}

    @property
    def elapsed(self) -> float:
        return self.clock() - self._started

    def fail(self, bound: str, limit: float, observed: float) -> None:
        """Lève la borne nommée, avec l'état partiel et la politique déclarée.

        Publique parce que la boucle s'en sert : sortir de `for … in range(N)`
        sans conclusion EST un dépassement de `max_iterations`, et il doit
        produire exactement le même résultat qu'un dépassement détecté en
        cours de route — sinon la politique déclarée ne s'applique qu'à
        certaines façons d'atteindre la même limite.
        """
        raise EXCEPTIONS[bound](bound, limit, observed,
                                policy=self.bounds.on_bound_exceeded,
                                partial_state=self.state())

    # -- Les cinq vérifications ---------------------------------------------
    def enter_iteration(self) -> int:
        """Ouvre un tour de boucle. Rend l'indice du tour (0-based)."""
        if self.iterations >= self.bounds.max_iterations:
            self.fail("max_iterations", self.bounds.max_iterations, self.iterations + 1)
        self.check_timeout()
        self.check_budget()
        index = self.iterations
        self.iterations += 1
        return index

    def check_tool_calls(self, requested: int = 1) -> None:
        """Le LOT demandé tient-il sous le plafond ? Vérifié avant exécution."""
        if self.tool_calls + requested > self.bounds.max_tool_calls:
            self.fail("max_tool_calls", self.bounds.max_tool_calls, self.tool_calls + requested)

    def record_tool_calls(self, executed: int = 1) -> None:
        self.tool_calls += executed

    def enter_delegation(self) -> None:
        if self.depth + 1 > self.bounds.max_delegation_depth:
            self.fail("max_delegation_depth", self.bounds.max_delegation_depth, self.depth + 1)
        self.depth += 1

    def leave_delegation(self) -> None:
        self.depth = max(0, self.depth - 1)

    def check_timeout(self) -> None:
        elapsed = self.elapsed
        if elapsed > self.bounds.timeout_s:
            self.fail("timeout_s", self.bounds.timeout_s, round(elapsed, 3))

    def add_cost(self, usd: float) -> None:
        """Ajoute un coût RECALCULÉ depuis des tokens, puis vérifie le plafond."""
        self.cost_usd += max(0.0, float(usd))
        self.check_budget()

    def check_budget(self) -> None:
        if self.cost_usd > self.bounds.budget_usd:
            self.fail("budget_usd", self.bounds.budget_usd, round(self.cost_usd, 6))

    def remaining_budget(self) -> float:
        return max(0.0, self.bounds.budget_usd - self.cost_usd)

    def remaining_time(self) -> float:
        """Secondes restantes avant `timeout_s`. Sert de délai à l'appel EN COURS.

        Vérifier le temps entre deux tours ne borne rien : un appel au modèle
        qui pend trois minutes passe entre deux vérifications. Le reste du
        budget de temps devient donc le délai de chaque appel distant.
        """
        return max(0.0, self.bounds.timeout_s - self.elapsed)


@dataclass
class RunLimits:
    """Les bornes du RUN, au-dessus de celles de chaque agent.

    `max_hops` vient de `orchestration.maxHops`, `max_tokens` de
    `budget.tokenCeilingPerRun` (IR). Elles ne sont pas des bornes d'agent : un
    graphe de dix agents qui respectent chacun les leurs peut quand même
    enchaîner cinquante hops ou consommer dix fois le plafond de tokens. `None`
    = non déclaré : aucun plafond n'est inventé ici.
    """

    max_hops: int | None = None
    max_tokens: int | None = None
    policy: OnBoundExceeded = "fail-explicit"
    hops: int = 0
    tokens: int = 0

    def enter_hop(self) -> None:
        self.hops += 1
        if self.max_hops is not None and self.hops > self.max_hops:
            raise HopsExceeded("max_hops", self.max_hops, self.hops, policy=self.policy,
                               partial_state={"hops": self.hops, "tokens": self.tokens})

    def add_tokens(self, count: int) -> None:
        self.tokens += max(0, int(count))
        if self.max_tokens is not None and self.tokens > self.max_tokens:
            raise TokensExceeded("max_tokens_per_run", self.max_tokens, self.tokens, policy=self.policy,
                                 partial_state={"hops": self.hops, "tokens": self.tokens})
