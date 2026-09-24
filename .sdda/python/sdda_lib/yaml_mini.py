"""Mini-parseur YAML, stdlib uniquement (pas de PyYAML).

Sous-ensemble volontairement limité à ce que les fichiers SDD_Agents utilisent :
  - mappings `clé: valeur`, imbriqués par indentation ;
  - listes bloc `- item` et listes de mappings `- clé: valeur` ;
  - listes flow `[a, b]` et mappings flow `{a: 1, b: 2}` (un seul niveau) ;
  - scalaires bloc `|` / `>` (les lignes vides internes sont perdues) ;
  - commentaires `#` hors guillemets ; scalaires typés (int, float, bool, null,
    chaînes avec ou sans guillemets). `on`/`off`/`yes`/`no` restent des chaînes.

Non supporté, délibérément : ancres, tags, documents multiples, clés complexes.
Un fichier qui en aurait besoin n'est pas un fichier de config SDD_Agents.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Clé nue, ou clé ENTRE GUILLEMETS — la seule façon d'écrire une clé qui
#: contient `:`. Les tags Ollama (`qwen3:32b`) sont des identifiants de modèle, et
#: `providers/local-ollama.yaml` les porte en clé de sa table de tarifs : sans
#: cette forme, le fichier était illisible et ses tarifs n'existaient pour aucun
#: script.
_KEY_RE = re.compile(r"^(\"[^\"]+\"|'[^']+'|[A-Za-z_][^:#]*?)\s*:(?:\s+(.*))?$")
_INT_RE = re.compile(r"^[-+]?\d+$")
_FLOAT_RE = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


class YamlMiniError(ValueError):
    """Erreur de syntaxe dans le sous-ensemble supporté."""


@dataclass
class _Line:
    indent: int
    content: str
    lineno: int


def _strip_comment(s: str) -> str:
    """Retire ` # …` hors guillemets. Un `#` collé à un mot n'est pas un commentaire."""
    in_single = in_double = False
    for i, ch in enumerate(s):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or s[i - 1] in " \t":
                return s[:i].rstrip()
    return s.rstrip()


def _prepare(text: str) -> list[_Line]:
    lines: list[_Line] = []
    for lineno, raw in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        raw = raw.replace("\t", "  ")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(_Line(len(raw) - len(raw.lstrip(" ")), stripped, lineno))
    return lines


def parse_scalar(raw: str) -> Any:
    """Typage d'un scalaire : null, bool, int, float, flow list/map, chaîne."""
    s = _strip_comment(raw).strip()
    if s == "":
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [parse_scalar(p) for p in _split_flow(inner)] if inner else []
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        out: dict[str, Any] = {}
        for part in (_split_flow(inner) if inner else []):
            k, _, v = part.partition(":")
            out[k.strip().strip("\"'")] = parse_scalar(v)
        return out
    low = s.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if _INT_RE.match(s):
        return int(s)
    if _FLOAT_RE.match(s):
        return float(s)
    return s


def _split_flow(inner: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def parse(text: str) -> Any:
    """Parse un document YAML (sous-ensemble). Document vide -> {}."""
    lines = _prepare(text)
    if not lines:
        return {}
    value, idx = _parse_block(lines, 0, lines[0].indent)
    if idx != len(lines):
        bad = lines[idx]
        raise YamlMiniError(f"ligne {bad.lineno}: indentation inattendue « {bad.content} »")
    return value


def _is_list_item(content: str) -> bool:
    return content == "-" or content.startswith("- ")


def _parse_block(lines: list[_Line], i: int, indent: int) -> tuple[Any, int]:
    if _is_list_item(lines[i].content):
        return _parse_list(lines, i, indent)
    # Valeur flow reportée à la ligne suivante — la forme d'agent-bounds.yaml :
    #     po-elicitor:
    #       { tier_default: balanced, … }
    # C'est du YAML valide, et le refuser obligerait chaque lecteur à écrire son
    # propre regex : c'est-à-dire autant de parseurs divergents que de lecteurs.
    content = _strip_comment(lines[i].content).strip()
    if content.startswith(("{", "[")) and not _KEY_RE.match(content):
        return parse_scalar(content), i + 1
    return _parse_map(lines, i, indent)


def _parse_map(lines: list[_Line], i: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while i < len(lines) and lines[i].indent == indent and not _is_list_item(lines[i].content):
        line = lines[i]
        m = _KEY_RE.match(_strip_comment(line.content))
        if not m:
            raise YamlMiniError(
                f"ligne {line.lineno}: attendu « clé: valeur », trouvé « {line.content} »"
            )
        key = m.group(1).strip().strip("\"'")
        rest = (m.group(2) or "").strip()
        if rest == "":
            if i + 1 < len(lines) and lines[i + 1].indent > indent:
                value, i = _parse_block(lines, i + 1, lines[i + 1].indent)
            else:
                value, i = None, i + 1
        elif rest in ("|", "|-", ">", ">-"):
            value, i = _parse_block_scalar(lines, i + 1, indent, folded=rest.startswith(">"))
        else:
            value, i = parse_scalar(rest), i + 1
        out[key] = value
    if i < len(lines) and lines[i].indent > indent:
        bad = lines[i]
        raise YamlMiniError(f"ligne {bad.lineno}: indentation inattendue « {bad.content} »")
    return out, i


def _parse_block_scalar(
    lines: list[_Line], i: int, parent_indent: int, *, folded: bool
) -> tuple[str, int]:
    chunks: list[str] = []
    while i < len(lines) and lines[i].indent > parent_indent:
        chunks.append(lines[i].content)
        i += 1
    return (" " if folded else "\n").join(chunks), i


def _parse_list(lines: list[_Line], i: int, indent: int) -> tuple[list[Any], int]:
    out: list[Any] = []
    while i < len(lines) and lines[i].indent == indent and _is_list_item(lines[i].content):
        line = lines[i]
        rest = line.content[1:].strip()
        if rest == "":
            if i + 1 < len(lines) and lines[i + 1].indent > indent:
                value, i = _parse_block(lines, i + 1, lines[i + 1].indent)
            else:
                value, i = None, i + 1
        elif _KEY_RE.match(_strip_comment(rest)) and not rest.startswith(("[", "{", '"', "'")):
            # Liste de mappings : la première clé est sur la ligne du tiret ;
            # les suivantes sont indentées au même niveau que cette clé.
            offset = len(line.content) - len(rest)
            lines[i] = _Line(indent + offset, rest, line.lineno)
            value, i = _parse_map(lines, i, indent + offset)
        else:
            value, i = parse_scalar(rest), i + 1
        out.append(value)
    return out, i


def parse_mapping(text: str) -> dict[str, Any]:
    """Parse en exigeant un mapping à la racine (fichiers de config)."""
    data = parse(text)
    if data is None or data == {}:
        return {}
    if not isinstance(data, dict):
        raise YamlMiniError("le document doit être un mapping à la racine")
    return data
