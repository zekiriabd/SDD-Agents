"""Moindre privilège — un agent ne reçoit que les outils que ses CAPs exigent.

C'est structurel, pas déclaratif : `get_for_agent` rend une liste CLOSE, et un
agent ne peut pas en obtenir un de plus en le demandant. L'écart entre outils
exposés et outils exigés est un finding bloquant de `review-safety`
(`[TOOL_SCOPE_EXCESS]`) — ce module est ce qui rend l'écart impossible plutôt
que détectable après coup.

Le cas qu'il empêche vraiment : un outil `write-destructive` câblé « au cas où »
dans le même contexte qu'une entrée non maîtrisée. C'est la cohabitation qui
transforme une injection en action (P8).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .spec import ToolError, ToolSpec

#: Classes d'effet de bord qui ne doivent JAMAIS cohabiter, dans un même agent,
#: avec un outil dont la sortie est `untrusted`.
DESTRUCTIVE = frozenset({"write-destructive", "external-side-effect"})


@dataclass
class RegisteredTool:
    spec: ToolSpec
    fn: Callable[..., Any]


@dataclass
class ToolRegistry:
    _tools: dict[str, RegisteredTool] = field(default_factory=dict)
    _by_agent: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def register(self, spec: ToolSpec, fn: Callable[..., Any]) -> None:
        if spec.name in self._tools:
            raise ToolError("TOOL_NAME_DUPLICATE",
                            f"`{spec.name}` est enregistré deux fois — c'est le nom que voit "
                            "le modèle, il doit être unique", tool=spec.name)
        spec.check_retry_is_safe()
        self._tools[spec.name] = RegisteredTool(spec=spec, fn=fn)

    def grant(self, agent_id: str, tool_names: Iterable[str]) -> None:
        """Déclare ce qu'un agent a le droit d'appeler. Vérifié à la déclaration."""
        names = tuple(tool_names)
        unknown = [n for n in names if n not in self._tools]
        if unknown:
            raise ToolError("TOOL_NOT_REGISTERED",
                            f"agent `{agent_id}` : outil(s) inconnu(s) {unknown}",
                            tool=agent_id)
        self._check_cohabitation(agent_id, names)
        self._by_agent[agent_id] = names

    def get_for_agent(self, agent_id: str) -> list[RegisteredTool]:
        """La liste CLOSE des outils de cet agent. Aucun moyen d'en obtenir un de plus."""
        granted = self._by_agent.get(agent_id)
        if granted is None:
            raise ToolError("TOOL_SCOPE_UNDECLARED",
                            f"agent `{agent_id}` : aucun périmètre d'outils déclaré. "
                            "Un périmètre absent n'est pas « tous les outils », c'est un oubli",
                            tool=agent_id)
        return [self._tools[name] for name in granted]

    def _check_cohabitation(self, agent_id: str, names: tuple[str, ...]) -> None:
        specs = [self._tools[n].spec for n in names]
        destructive = [s.name for s in specs if s.side_effect_class in DESTRUCTIVE]
        untrusted = [s.name for s in specs if s.trust == "untrusted"]
        if destructive and untrusted:
            raise ToolError(
                "UNSAFE_TOOL_COHABITATION",
                f"agent `{agent_id}` : {destructive} (effet de bord) partagent son contexte "
                f"avec {untrusted} (sortie non maîtrisée). Une phrase déposée dans la donnée "
                "devient alors une action — c'est l'injection indirecte qui aboutit (P8). "
                "Isoler dans deux agents, ou rendre l'outil non destructif",
                tool=agent_id)
