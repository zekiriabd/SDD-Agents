"""Pattern `sequential` — N étapes validées, aucun cycle. GÉNÉRÉ, ne pas éditer.

Le piège du pipeline est arithmétique et personne ne fait le calcul avant de
construire : cinq étapes à 0,95 de fiabilité donnent 0,77 de bout en bout. La
conséquence architecturale n'est pas « faire mieux », c'est **valider à chaque
étape** — schéma de sortie au minimum — et **évaluer chaque étape**, en plus de
l'eval bout en bout. Sans eval par étape, on diagnostique un résultat final
dégradé sans savoir quelle étape l'a abîmé, et on finit par retoucher le prompt
de la dernière, qui n'y était pour rien.

Le comportement sur échec d'étape se **déclare**, il ne s'improvise pas :
`stop` (le plus honnête), `retry-once` (avec le message d'erreur en contexte),
`degrade` (sortie partielle **annoncée comme telle**). Une étape qui échoue en
silence et laisse passer sa valeur par défaut est la façon la plus sûre de
rendre un résultat faux avec assurance.

`max_hops` vaut le nombre d'étapes, et il n'y a aucun cycle : un pipeline qui
boucle est un `graph`, et il doit être déclaré comme tel.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .base import Graph

#: Les stratégies admises sur échec d'étape. Liste close : une quatrième
#: valeur serait un comportement que personne n'a revu.
FAILURE_MODES: tuple[str, ...] = ("stop", "retry-once", "degrade")


class StepFailed(Exception):
    """Une étape n'a pas produit une sortie conforme.

    Porte l'étape et ce qu'on avait jusque-là : le mode `degrade` doit pouvoir
    rendre la sortie partielle, et `stop` doit pouvoir dire OÙ le pipeline s'est
    arrêté — « ça n'a pas marché » n'aide personne au troisième maillon.
    """

    def __init__(self, step: str, message: str, *, partial: Any = None) -> None:
        super().__init__(f"[SEQUENTIAL_STEP_FAILED] {step}: {message}")
        self.step = step
        self.message = message
        self.partial = partial


@dataclass(frozen=True)
class Step:
    """Une étape. `validate` n'est pas optionnel — c'est la raison du pattern.

    `validate` rend `None` si la sortie est conforme, un message sinon. Une
    étape sans validation est `[SEQUENTIAL_STEP_UNVALIDATED]` : elle transmet ce
    qu'elle a produit à l'étape suivante, qui le traite comme un fait.
    """

    id: str
    handler: Callable[[Any], Any]
    validate: Callable[[Any], str | None]
    kind: str = "agent"
    ref: str = ""


class SequentialGraph(Graph):
    """Le graphe d'un pipeline : des étapes chaînées, sans retour."""

    def __init__(self, steps: Sequence[Step], *, on_step_failure: str = "stop") -> None:
        if not steps:
            raise ValueError("un pipeline sans étape n'est pas un pipeline")
        if on_step_failure not in FAILURE_MODES:
            raise ValueError(
                f"[SEQUENTIAL_NO_FAILURE_PATH] `on_step_failure: {on_step_failure!r}` hors "
                f"liste close {list(FAILURE_MODES)} — le comportement sur échec se déclare")
        super().__init__(entry_node=steps[0].id, terminal_nodes=[steps[-1].id],
                         max_hops=len(steps))
        self.steps = tuple(steps)
        self.on_step_failure = on_step_failure
        for step in self.steps:
            self.add_node(step.id, kind=step.kind, ref=step.ref, handler=step.handler)
        for previous, following in zip(self.steps, self.steps[1:]):
            self.add_edge(previous.id, following.id)

    async def run(self, payload: Any) -> tuple[Any, bool]:
        """Exécute les étapes dans l'ordre. Rend `(sortie, dégradé)`.

        `dégradé` remonte jusqu'à l'appelant et jusqu'au code de sortie 7 : une
        sortie partielle rendue comme une sortie complète est le faux vert que
        ce pattern produit le plus facilement.
        """
        import inspect  # noqa: PLC0415 - utilisé ici seulement

        current = payload
        for step in self.steps:
            for attempt in range(2 if self.on_step_failure == "retry-once" else 1):
                result = step.handler(current)
                if inspect.isawaitable(result):
                    result = await result
                problem = step.validate(result)
                if problem is None:
                    current = result
                    break
                if attempt == 0 and self.on_step_failure == "retry-once":
                    continue
                if self.on_step_failure == "degrade":
                    return current, True
                raise StepFailed(step.id, problem, partial=current)
        return current, False
