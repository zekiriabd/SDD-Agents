"""Pattern `router` — classifier puis spécialiste, un seul passage. GÉNÉRÉ, ne pas éditer.

Un nœud de classification bon marché détermine l'intention, puis délègue à un
spécialiste à prompt court et à périmètre d'outils étroit. C'est souvent **moins
cher** que `single-agent`, contrairement à l'intuition.

Le mode d'échec dominant est le misroute, et il est **silencieux et
irrécupérable** : une fois parti chez le mauvais spécialiste, rien ne le
rattrape — il répond avec assurance dans son domaine, à une question qui n'en
relevait pas. C'est la différence de fond avec `single-agent`, où une mauvaise
sélection d'outil se corrige au tour suivant.

Trois obligations en découlent, et elles sont EN CODE ici, pas en commentaire :

1. **Un chemin de repli**, marqué `isFallback`. Sans lui, une entrée qui ne
   ressemble à aucune classe part quand même quelque part — au hasard de la
   sortie du classifieur. `[ROUTER_NO_FALLBACK]` à la construction : le graphe
   refuse de se construire, il n'échoue pas au premier run non classé.
2. **Un seuil de confiance.** Sous le seuil, on clarifie plutôt qu'on route.
   Une classification à 0,3 de confiance qui part chez un spécialiste est un
   misroute que rien n'a signalé.
3. **Un seul passage.** Un routeur qui reroute est un superviseur déguisé :
   `max_hops` reste à 2, et il n'y a aucune arête de retour vers le classifieur.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .base import Graph

#: Seuil sous lequel on clarifie au lieu de router. Défaut prudent : au-delà de
#: la moitié des cas, un classifieur qui hésite se trompe plus souvent qu'il ne
#: devine. La valeur réelle vient du contrat, elle est mesurée sur le golden de
#: routing, pas choisie.
DEFAULT_CONFIDENCE_THRESHOLD = 0.7


@dataclass(frozen=True)
class Route:
    """Une branche : une classe d'intention, un nœud, un spécialiste."""

    intent: str
    node_id: str
    ref: str = ""


@dataclass(frozen=True)
class Decision:
    """Ce que rend le classifieur. Structuré, jamais du texte libre.

    Un classifieur en texte libre est un classifieur qu'on devra parser, donc
    réparer : `{intent: enum, confidence: number}` est vérifiable par un schéma,
    et son espace de sortie est fini.
    """

    intent: str
    confidence: float = 1.0


class RouterGraph(Graph):
    """Le graphe d'un routeur, construit depuis ses routes déclarées.

    Le classifieur est `kind: "router"` et non `kind: "agent"` : `validate_ir.py`
    exige alors au moins une arête `isFallback: true`, et c'est cette exigence
    que le constructeur reproduit côté code.
    """

    def __init__(self, *, classifier_id: str = "classifier", classifier_ref: str = "",
                 routes: tuple[Route, ...] = (), fallback: Route | None = None,
                 confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
                 handlers: Mapping[str, Callable[..., Any]] | None = None) -> None:
        if fallback is None:
            raise ValueError(
                "[ROUTER_NO_FALLBACK] un routeur sans chemin de repli envoie les entrées "
                "non classées chez un spécialiste au hasard — déclarer la branche "
                "« aucune classe ne correspond » (clarifier, ou escalader)")
        if not routes:
            raise ValueError("un routeur sans route n'est pas un routeur : "
                             "utiliser `SingleAgentGraph`")
        super().__init__(entry_node=classifier_id, max_hops=2)
        self.confidence_threshold = float(confidence_threshold)
        self.routes = tuple(routes)
        self.fallback = fallback
        handlers = dict(handlers or {})

        # Pas d'outils sur le classifieur : un classifieur qui appelle des
        # outils fait le travail du spécialiste et détruit l'économie du pattern.
        self.add_node(classifier_id, kind="router", ref=classifier_ref,
                      handler=handlers.get(classifier_id))
        for route in (*routes, fallback):
            self.add_node(route.node_id, kind="agent", ref=route.ref,
                          handler=handlers.get(route.node_id))
            self.terminal_nodes.append(route.node_id)

        threshold = self.confidence_threshold
        for route in routes:
            self.add_edge(classifier_id, route.node_id,
                          condition=f"intent=='{route.intent}' && confidence>={threshold}")
        self.add_edge(classifier_id, fallback.node_id,
                      condition=f"confidence<{threshold}", is_fallback=True)

    def route(self, decision: Decision) -> str:
        """Nœud cible d'une décision. Sous le seuil ou hors classes -> repli.

        Les deux cas mènent au même endroit, et c'est voulu : « je ne sais pas »
        et « je sais mal » doivent produire le même comportement observable,
        sinon le second se déguise en premier dans les mesures.
        """
        if decision.confidence < self.confidence_threshold:
            return self.fallback.node_id
        for route in self.routes:
            if route.intent == decision.intent:
                return route.node_id
        return self.fallback.node_id
