"""Parseur du sous-ensemble Mermaid `flowchart` utilisé par `{n}-topology.mmd`.

Le graphe d'orchestration est DESSINÉ par le architect-topology (Mermaid) ; le
compilateur IR le lit ici. Supporté :

    flowchart TD
      entry([entrée]) --> router{classifier}
      router -->|billing| billing[agent billing]
      router -- "label" --> x
      a --> b --> c

Formes : `id([txt])` stadium, `id{txt}` losange (routeur), `id[txt]` rectangle,
`id((txt))` cercle, `id>txt]`. Le label d'arête devient la `condition` de l'IR.
Les commentaires `%%`, `subgraph`/`end`, `classDef`, `style`, `click` sont ignorés.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_NODE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*(\(\[.*?\]\)|\(\(.*?\)\)|\[.*?\]|\{.*?\}|>.*?\])?\s*$")
_EDGE_TOKEN_RE = re.compile(
    r"(?P<arrow>-->|---|-\.->|==>|--\s*\"(?P<lbl1>[^\"]*)\"\s*-->|--\s*(?P<lbl2>[^-|>\"]+?)\s*-->)"
    r"(?:\|(?P<lbl3>[^|]*)\|)?"
)
#: Lignes de directive, ignorées. Frontière de MOT obligatoire : `endpoint -->
#: finalize` commence par `end` et n'est pas un `end` de subgraph — avec un
#: simple `startswith`, la ligne entière disparaissait du graphe, agent et arête
#: compris, sans aucune erreur.
_DIRECTIVE_RE = re.compile(
    r"^(?:%%|(?:flowchart|graph|subgraph|classDef|class|style|click|linkStyle|direction)\b|end\s*$)"
)


def _is_directive(line: str) -> bool:
    return bool(_DIRECTIVE_RE.match(line))


@dataclass
class MermaidNode:
    id: str
    label: str
    shape: str  # stadium | diamond | rect | circle | flag | plain


@dataclass
class MermaidEdge:
    src: str
    dst: str
    label: str = ""
    #: Arête pointillée (`-.->`) : « gratuite », ne compte pas comme hop dans l'IR.
    free: bool = False


@dataclass
class MermaidGraph:
    nodes: dict[str, MermaidNode] = field(default_factory=dict)
    edges: list[MermaidEdge] = field(default_factory=list)

    def add_node(self, node_id: str, decoration: str | None) -> None:
        shape, label = _shape_of(decoration, node_id)
        existing = self.nodes.get(node_id)
        if existing is None:
            self.nodes[node_id] = MermaidNode(node_id, label, shape)
        elif decoration and existing.shape == "plain":
            self.nodes[node_id] = MermaidNode(node_id, label, shape)


def _shape_of(decoration: str | None, node_id: str) -> tuple[str, str]:
    if not decoration:
        return "plain", node_id
    d = decoration.strip()
    if d.startswith("(["):
        return "stadium", d[2:-2].strip().strip('"')
    if d.startswith("(("):
        return "circle", d[2:-2].strip().strip('"')
    if d.startswith("{"):
        return "diamond", d[1:-1].strip().strip('"')
    if d.startswith("["):
        return "rect", d[1:-1].strip().strip('"')
    if d.startswith(">"):
        return "flag", d[1:-1].strip().strip('"')
    return "plain", node_id


def parse(text: str) -> MermaidGraph:
    g = MermaidGraph()
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip().rstrip(";")
        if not line or _is_directive(line):
            continue
        # Découpe en segments nœud / flèche / nœud / flèche …
        pos = 0
        segments: list[str] = []
        arrows: list[tuple[str, bool]] = []
        for m in _EDGE_TOKEN_RE.finditer(line):
            segments.append(line[pos:m.start()])
            label = m.group("lbl3") or m.group("lbl1") or m.group("lbl2") or ""
            arrows.append((label.strip(), m.group("arrow").startswith("-.")))
            pos = m.end()
        segments.append(line[pos:])
        parsed: list[str | None] = []
        for seg in segments:
            nm = _NODE_RE.match(seg)
            if not nm:
                parsed.append(None)
                continue
            g.add_node(nm.group(1), nm.group(2))
            parsed.append(nm.group(1))
        for i, (label, free) in enumerate(arrows):
            a, b = parsed[i], parsed[i + 1]
            if a and b:
                g.edges.append(MermaidEdge(a, b, label, free))
    return g
