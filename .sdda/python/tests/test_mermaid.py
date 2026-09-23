"""mermaid — le sous-ensemble `flowchart` que dessine l'architecte et que lit l'IR.

Le graphe d'orchestration passe par ce parseur avant de devenir des nœuds et
des arêtes de l'IR. Une forme mal lue ici, c'est un agent absent du graphe ou
une condition perdue — sans erreur, puisque le texte reste du Mermaid valide.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sdda_lib import markdown_io
from sdda_lib.mermaid import MermaidEdge, parse

_TOPOLOGY = Path(__file__).resolve().parent / "fixtures/project_ok/workspace/feats/topology/1-topology.md"


class _Fixture:
    """Le graphe de la fixture : le bloc ```mermaid de `## 4. Le graphe` — sa seule source."""

    def read_text(self, encoding: str = "utf-8") -> str:
        return markdown_io.fenced_blocks(_TOPOLOGY.read_text(encoding=encoding), "mermaid")[0]


FIXTURE = _Fixture()


def edges_of(text: str) -> list[tuple[str, str, str]]:
    return [(e.src, e.dst, e.label) for e in parse(text).edges]


# ---------------------------------------------------------------------------
# Le graphe de la fixture, tel que l'IR le consomme
# ---------------------------------------------------------------------------
def test_the_fixture_topology_is_read_entirely() -> None:
    g = parse(FIXTURE.read_text(encoding="utf-8"))
    assert set(g.nodes) == {"classify", "billing", "clarify", "finalize"}
    assert g.nodes["classify"].shape == "diamond" and g.nodes["classify"].label == "intent-classifier"
    assert g.nodes["billing"].shape == "rect" and g.nodes["billing"].label == "billing-specialist"
    assert edges_of(FIXTURE.read_text(encoding="utf-8")) == [
        ("classify", "billing", "intent == 'billing'"),
        ("classify", "clarify", "aucune classe"),
        ("billing", "finalize", "resolved"),
        ("billing", "classify", "needs_reclassification"),
        ("clarify", "finalize", ""),
    ]
    assert not any(e.free for e in g.edges)


# ---------------------------------------------------------------------------
# Entrées vides, en-têtes, directions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["", "\n\n   \n", "flowchart TD", "graph LR\n"])
def test_nothing_to_read_is_an_empty_graph(text: str) -> None:
    g = parse(text)
    assert g.nodes == {} and g.edges == []


@pytest.mark.parametrize("header", ["flowchart TD", "flowchart LR", "flowchart RL", "flowchart BT",
                                    "graph TD", "graph LR", "flowchart TB;"])
def test_every_direction_header_is_skipped_not_parsed_as_a_node(header: str) -> None:
    g = parse(f"{header}\n  a --> b\n")
    assert set(g.nodes) == {"a", "b"}
    assert edges_of(f"{header}\n  a --> b\n") == [("a", "b", "")]


def test_crlf_input_parses_like_lf() -> None:
    lf = "flowchart TD\n  a --> b\n  b -->|ok| c\n"
    crlf = lf.replace("\n", "\r\n")
    assert parse(crlf) == parse(lf)


def test_indentation_and_trailing_semicolons_are_tolerated() -> None:
    g = parse("flowchart TD\n\t\ta[Agent] --> b;\n        b --> c ;\n")
    assert edges_of("flowchart TD\n\t\ta[Agent] --> b;\n        b --> c ;\n") == [("a", "b", ""), ("b", "c", "")]
    assert g.nodes["a"].label == "Agent"


# ---------------------------------------------------------------------------
# Formes de nœuds et libellés
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("decl,shape,label", [
    ("entry([entrée])", "stadium", "entrée"),
    ("c((cercle))", "circle", "cercle"),
    ("r{routeur}", "diamond", "routeur"),
    ("b[rectangle]", "rect", "rectangle"),
    ("f>drapeau]", "flag", "drapeau"),
    ("plain", "plain", "plain"),
])
def test_every_supported_shape_yields_its_label(decl: str, shape: str, label: str) -> None:
    node_id = decl.split("(", 1)[0].split("[", 1)[0].split("{", 1)[0].split(">", 1)[0]
    g = parse(f"flowchart TD\n  {decl} --> x\n")
    assert g.nodes[node_id].shape == shape
    assert g.nodes[node_id].label == label


def test_quoted_labels_lose_their_quotes_but_keep_their_punctuation() -> None:
    g = parse('flowchart TD\n  a["libellé, avec (virgule)"] --> b{"question ?"}\n')
    assert g.nodes["a"].label == "libellé, avec (virgule)"
    assert g.nodes["b"].label == "question ?"


def test_a_node_declared_alone_on_its_line_exists_without_an_edge() -> None:
    g = parse("flowchart TD\n  seul[Nœud isolé]\n")
    assert g.nodes["seul"].label == "Nœud isolé" and g.edges == []


def test_ids_with_underscores_and_dashes_are_accepted() -> None:
    g = parse("flowchart TD\n  intent_classifier --> billing-specialist\n")
    assert set(g.nodes) == {"intent_classifier", "billing-specialist"}


def test_a_later_plain_reference_keeps_the_first_decoration() -> None:
    g = parse("flowchart TD\n  a[Agent A] --> b\n  a --> c\n")
    assert g.nodes["a"].shape == "rect" and g.nodes["a"].label == "Agent A"


def test_a_later_decoration_upgrades_a_plain_node() -> None:
    g = parse("flowchart TD\n  a --> b\n  a[Agent A] --> c\n")
    assert g.nodes["a"].shape == "rect" and g.nodes["a"].label == "Agent A"


def test_the_first_decoration_wins_over_a_second_one() -> None:
    """Deux libellés pour un même id : on garde le premier, sans le remplacer en silence."""
    g = parse("flowchart TD\n  a[Premier] --> b\n  a[Second] --> c\n")
    assert g.nodes["a"].label == "Premier"


# ---------------------------------------------------------------------------
# Arêtes : syntaxes de libellé, chaînes, pointillés
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("line", ["a -->|oui| b", "a -- \"oui\" --> b", "a -- oui --> b", "a -->| oui | b"])
def test_every_edge_label_syntax_yields_the_same_condition(line: str) -> None:
    assert edges_of(f"flowchart TD\n  {line}\n") == [("a", "b", "oui")]


def test_an_edge_without_label_has_an_empty_condition() -> None:
    assert parse("flowchart TD\n  a --> b\n").edges == [MermaidEdge("a", "b", "", False)]


def test_a_chain_yields_one_edge_per_arrow() -> None:
    assert edges_of("flowchart TD\n  a --> b --> c --> d\n") == [("a", "b", ""), ("b", "c", ""), ("c", "d", "")]


def test_a_labelled_chain_keeps_each_label_on_its_own_arrow() -> None:
    assert edges_of("flowchart TD\n  a -->|x| b -->|y| c\n") == [("a", "b", "x"), ("b", "c", "y")]


def test_a_dotted_arrow_is_a_free_edge() -> None:
    """`-.->` ne compte pas comme hop dans l'IR : c'est la seule arête « gratuite »."""
    g = parse("flowchart TD\n  a -.-> b\n  b --> c\n")
    assert [(e.src, e.dst, e.free) for e in g.edges] == [("a", "b", True), ("b", "c", False)]


@pytest.mark.parametrize("arrow", ["==>", "---"])
def test_thick_and_open_links_are_edges_that_count_as_hops(arrow: str) -> None:
    g = parse(f"flowchart TD\n  a {arrow} b\n")
    assert [(e.src, e.dst, e.free) for e in g.edges] == [("a", "b", False)]


def test_a_labelled_dotted_arrow_keeps_both_properties() -> None:
    g = parse("flowchart TD\n  a -.->|repli| b\n")
    assert g.edges == [MermaidEdge("a", "b", "repli", True)]


def test_a_node_reached_by_several_edges_is_declared_once() -> None:
    g = parse("flowchart TD\n  a --> c\n  b --> c\n  c --> a\n")
    assert len(g.nodes) == 3 and len(g.edges) == 3


# ---------------------------------------------------------------------------
# Ce qui est ignoré : commentaires, styles, sous-graphes
# ---------------------------------------------------------------------------
def test_comments_and_styling_directives_are_ignored() -> None:
    text = (
        "flowchart TD\n"
        "  %% un commentaire a --> z\n"
        "  classDef agent fill:#fff\n"
        "  class a,b agent\n"
        "  style a stroke:#000\n"
        "  linkStyle 0 stroke:red\n"
        "  click a callback\n"
        "  a --> b\n"
    )
    g = parse(text)
    assert set(g.nodes) == {"a", "b"} and edges_of(text) == [("a", "b", "")]


def test_subgraph_boundaries_are_skipped_and_their_content_is_read() -> None:
    text = (
        "flowchart TD\n"
        "  subgraph agents [Agents]\n"
        "    direction LR\n"
        "    a --> b\n"
        "  end\n"
        "  b --> c\n"
    )
    g = parse(text)
    assert set(g.nodes) == {"a", "b", "c"}
    assert edges_of(text) == [("a", "b", ""), ("b", "c", "")]


# ---------------------------------------------------------------------------
# Lignes malformées : rien d'inventé, rien de planté
# ---------------------------------------------------------------------------
def test_a_dangling_arrow_yields_the_source_node_and_no_edge() -> None:
    g = parse("flowchart TD\n  a -->\n")
    assert set(g.nodes) == {"a"} and g.edges == []


def test_an_arrow_without_source_yields_the_target_node_and_no_edge() -> None:
    g = parse("flowchart TD\n  --> b\n")
    assert set(g.nodes) == {"b"} and g.edges == []


def test_garbage_lines_produce_nothing() -> None:
    g = parse("flowchart TD\n  ??? !!!\n  a -> b\n")
    # `->` n'est pas une flèche flowchart : ni arête, ni nœud fantôme.
    assert g.nodes == {} and g.edges == []


def test_an_invalid_source_id_drops_the_edge_but_keeps_the_valid_target() -> None:
    g = parse("flowchart TD\n  1nombre --> b\n")
    # `1nombre` n'est pas un identifiant ; `b` l'est, et reste déclaré.
    assert set(g.nodes) == {"b"} and g.edges == []


def test_a_broken_line_does_not_prevent_reading_the_next_one() -> None:
    text = "flowchart TD\n  a -->\n  ??? \n  b --> c\n"
    assert edges_of(text) == [("b", "c", "")]


def test_an_unclosed_decoration_is_not_a_node() -> None:
    g = parse("flowchart TD\n  a[ouvert --> b\n")
    assert "a" not in g.nodes and g.edges == []


def test_a_node_id_starting_with_a_keyword_is_still_a_node() -> None:
    """Corrigé : `_SKIP_PREFIXES` par `startswith` avalait `endpoint`, `styleguide`, `graphql`…
    — la ligne entière disparaissait, agent et arête compris, sans aucune erreur."""
    for src in ("endpoint", "styleguide", "graphql", "classifier", "direction_finder", "ending"):
        text = f"flowchart TD\n  {src} --> finalize\n"
        assert src in parse(text).nodes, src
        assert edges_of(text) == [(src, "finalize", "")], src
    # Les vraies directives restent ignorées.
    g = parse("flowchart TD\n  subgraph S\n  a --> b\n  end\n  style a fill:#f9f\n  class a big\n")
    assert set(g.nodes) == {"a", "b"}
