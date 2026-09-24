"""Mini-parseur YAML, stdlib uniquement (pas de PyYAML).

Sous-ensemble volontairement limité à ce que les fichiers SDD_Agents utilisent :
  - mappings `clé: valeur`, imbriqués par indentation ; clés nues ou entre
    guillemets ;
  - listes bloc `- item` et listes de mappings `- clé: valeur`, y compris une
    liste au MÊME retrait que la clé qui la porte (`clé:` puis `- a`) ;
  - listes flow `[a, b]` et mappings flow `{a: 1, b: 2}` (imbriqués) ;
  - scalaires bloc `|` / `>` avec leurs indicateurs de troncature (`-`, `+`)
    et d'indentation (`|2`) : lignes vides internes, lignes plus indentées et
    lignes commençant par `#` sont du CONTENU, pas de la structure ;
  - commentaires `#` hors guillemets ; scalaires typés (int, float, bool, null,
    chaînes avec ou sans guillemets, échappements `\\"` et `''`).
    `on`/`off`/`yes`/`no` restent des chaînes.

Non supporté, délibérément : ancres, tags, documents multiples, clés complexes,
scalaires nus sur plusieurs lignes. Un fichier qui en aurait besoin n'est pas un
fichier de config SDD_Agents — et il échoue BRUYAMMENT (`YamlMiniError`) plutôt
que d'être lu de travers.

**Pourquoi ce parseur est testé contre PyYAML.** Il lit `loader.yml`, donc la
matrice d'ownership sur laquelle reposent les hooks : une ligne de
`forbidden_writes` perdue ici est un interdit qu'aucun hook n'applique, sans
qu'aucune erreur ne le dise. Trois écarts silencieux existaient :

1. un `'` au milieu d'un mot (`l'agent`) ouvrait une « chaîne », si bien que le
   `# commentaire` qui suivait entrait dans la valeur — les auteurs de
   `loader.yml` écrivaient « l index » pour l'éviter ;
2. une ligne commençant par `#` DANS un scalaire bloc était jetée comme un
   commentaire, et les lignes vides internes disparaissaient ;
3. une clé dupliquée écrasait la première en silence — un second bloc
   `dev-agent:` remplaçait les `forbidden_writes` du premier. PyYAML l'accepte
   aussi ; ici c'est une erreur, parce que l'effet est une autorisation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_KEY_RE = re.compile(r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[A-Za-z0-9_.$@/][^:#]*?)\s*:(?:\s+(.*))?$""")
_INT_RE = re.compile(r"^[-+]?\d+$")
_FLOAT_RE = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")
_BLOCK_RE = re.compile(r"^([|>])([+-]?)([1-9]?)([+-]?)$")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\", "/": "/", " ": " "}


class YamlMiniError(ValueError):
    """Erreur de syntaxe dans le sous-ensemble supporté."""


@dataclass
class _Line:
    indent: int
    content: str
    lineno: int  # 1-based, index dans les lignes brutes = lineno - 1


def _strip_comment(s: str) -> str:
    """Retire ` # …` hors guillemets.

    Un guillemet n'ouvre une chaîne qu'en DÉBUT de jeton (après un blanc, `[`,
    `{`, `,` ou `:`) : l'apostrophe de `l'agent` n'est pas un délimiteur. Un
    `#` collé à un mot n'est pas un commentaire non plus.
    """
    quote: str | None = None
    prev = " "
    i = 0
    while i < len(s):
        ch = s[i]
        if quote:
            if quote == '"' and ch == "\\":
                prev = ch
                i += 2
                continue
            if ch == quote:
                if quote == "'" and i + 1 < len(s) and s[i + 1] == "'":
                    i += 2
                    continue
                quote = None
        elif ch in "'\"" and prev in " \t[{,:":
            quote = ch
        elif ch == "#" and prev in " \t":
            return s[:i].rstrip()
        prev = ch
        i += 1
    return s.rstrip()


def _prepare(raw: list[str]) -> list[_Line]:
    lines: list[_Line] = []
    for lineno, line in enumerate(raw, start=1):
        line = line.replace("\t", "  ")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(_Line(len(line) - len(line.lstrip(" ")), stripped, lineno))
    return lines


def _unquote(s: str) -> str:
    if s[0] == "'":
        return s[1:-1].replace("''", "'")
    out: list[str] = []
    body = s[1:-1]
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            out.append(_ESCAPES.get(body[i + 1], "\\" + body[i + 1]))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _is_single_quoted(s: str) -> bool:
    """`"a"` ou `'a'` d'un seul tenant — pas `'a' et 'b'`."""
    if len(s) < 2 or s[0] != s[-1] or s[0] not in "\"'":
        return False
    body = s[1:-1]
    if s[0] == "'":
        return "'" not in body.replace("''", "")
    i = 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
            continue
        if body[i] == '"':
            return False
        i += 1
    return True


def parse_scalar(raw: str) -> Any:
    """Typage d'un scalaire : null, bool, int, float, flow list/map, chaîne."""
    s = _strip_comment(raw).strip()
    if s == "":
        return None
    if _is_single_quoted(s):
        return _unquote(s)
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [parse_scalar(p) for p in _split_flow(inner)] if inner else []
    if s.startswith("{") and s.endswith("}"):
        inner = s[1:-1].strip()
        out: dict[str, Any] = {}
        for part in (_split_flow(inner) if inner else []):
            k, v = _split_flow_pair(part)
            key = _unquote(k) if _is_single_quoted(k) else k
            if key in out:
                raise YamlMiniError(f"clé dupliquée « {key} » dans un mapping flow")
            out[key] = parse_scalar(v)
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


def _split_flow_pair(part: str) -> tuple[str, str]:
    """`clé: valeur` d'un mapping flow — le `:` hors guillemets, suivi d'un blanc ou final."""
    quote: str | None = None
    for i, ch in enumerate(part):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'" and (i == 0 or part[i - 1] in " \t"):
            quote = ch
        elif ch == ":" and (i + 1 == len(part) or part[i + 1] in " \t"):
            return part[:i].strip(), part[i + 1:]
    return part.strip(), ""


def _split_flow(inner: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    prev = " "
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            prev = ch
            continue
        if ch in "\"'" and prev in " \t[{,:":
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
        prev = ch
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


class _Doc:
    """Les lignes brutes (pour les scalaires bloc) et les lignes structurelles."""

    def __init__(self, text: str) -> None:
        self.raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if self.raw and self.raw[0].startswith("﻿"):
            self.raw[0] = self.raw[0][1:]
        self.lines = _prepare(self.raw)


def parse(text: str) -> Any:
    """Parse un document YAML (sous-ensemble). Document vide -> {}."""
    doc = _Doc(text)
    if not doc.lines:
        return {}
    value, idx = _parse_block(doc, 0, doc.lines[0].indent)
    if idx != len(doc.lines):
        bad = doc.lines[idx]
        raise YamlMiniError(f"ligne {bad.lineno}: indentation inattendue « {bad.content} »")
    return value


def _is_list_item(content: str) -> bool:
    return content == "-" or content.startswith("- ")


def _parse_block(doc: _Doc, i: int, indent: int) -> tuple[Any, int]:
    lines = doc.lines
    if _is_list_item(lines[i].content):
        return _parse_list(doc, i, indent)
    # Valeur flow reportée à la ligne suivante — la forme d'agent-bounds.yaml :
    #     po-elicitor:
    #       { tier_default: balanced, … }
    # C'est du YAML valide, et le refuser obligerait chaque lecteur à écrire son
    # propre regex : c'est-à-dire autant de parseurs divergents que de lecteurs.
    content = _strip_comment(lines[i].content).strip()
    if content.startswith(("{", "[")) and not _KEY_RE.match(content):
        return parse_scalar(content), i + 1
    return _parse_map(doc, i, indent)


def _key_of(raw_key: str) -> str:
    raw_key = raw_key.strip()
    return _unquote(raw_key) if _is_single_quoted(raw_key) else raw_key


def _parse_map(doc: _Doc, i: int, indent: int) -> tuple[dict[str, Any], int]:
    lines = doc.lines
    out: dict[str, Any] = {}
    while i < len(lines) and lines[i].indent == indent and not _is_list_item(lines[i].content):
        line = lines[i]
        m = _KEY_RE.match(_strip_comment(line.content))
        if not m:
            raise YamlMiniError(
                f"ligne {line.lineno}: attendu « clé: valeur », trouvé « {line.content} »"
            )
        key = _key_of(m.group(1))
        if key in out:
            raise YamlMiniError(
                f"ligne {line.lineno}: clé dupliquée « {key} » — la seconde écraserait la première en silence"
            )
        rest = (m.group(2) or "").strip()
        if rest == "":
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            if nxt is not None and nxt.indent > indent:
                value, i = _parse_block(doc, i + 1, nxt.indent)
            elif nxt is not None and nxt.indent == indent and _is_list_item(nxt.content):
                # `clé:` puis `- a` au même retrait : YAML valide, et forme
                # courante sous la plume d'un humain.
                value, i = _parse_list(doc, i + 1, indent)
            else:
                value, i = None, i + 1
        elif _BLOCK_RE.match(rest):
            value, i = _parse_block_scalar(doc, i, indent, rest)
        else:
            value, i = parse_scalar(rest), i + 1
        out[key] = value
    if i < len(lines) and lines[i].indent > indent:
        bad = lines[i]
        raise YamlMiniError(f"ligne {bad.lineno}: indentation inattendue « {bad.content} »")
    return out, i


def _fold(body: list[str]) -> str:
    """Repliement `>` : un saut entre deux lignes « normales » devient un espace,
    une ligne vide devient un saut, une ligne plus indentée garde les siens."""
    out = ""
    k = 0
    n = len(body)
    while k < n and body[k] == "":
        out += "\n"
        k += 1
    if k == n:
        return out
    out += body[k]
    prev = body[k]
    k += 1
    while k < n:
        j = k
        while j < n and body[j] == "":
            j += 1
        empties = j - k
        nxt = body[j]
        if prev.startswith((" ", "\t")) or nxt.startswith((" ", "\t")):
            out += "\n" * (empties + 1) + nxt
        else:
            out += (" " if empties == 0 else "\n" * empties) + nxt
        prev = nxt
        k = j + 1
    return out


def _parse_block_scalar(doc: _Doc, i: int, parent_indent: int, indicator: str) -> tuple[str, int]:
    """Scalaire bloc lu sur les lignes BRUTES : `#` et lignes vides sont du contenu."""
    m = _BLOCK_RE.match(indicator)
    assert m is not None
    style = m.group(1)
    chomp = m.group(2) or m.group(4)
    explicit = int(m.group(3)) if m.group(3) else 0

    raw = doc.raw
    start = doc.lines[i].lineno  # index brut de la ligne qui suit la clé
    block_indent = parent_indent + explicit if explicit else 0
    body: list[str] = []
    k = start
    while k < len(raw):
        line = raw[k].replace("\t", "  ")
        if line.strip() == "":
            body.append("")
            k += 1
            continue
        ind = len(line) - len(line.lstrip(" "))
        if not block_indent:
            if ind <= parent_indent:
                break
            block_indent = ind
        if ind < block_indent:
            break
        body.append(line[block_indent:].rstrip("\n"))
        k += 1

    # Les lignes vides de fin appartiennent au bloc (troncature), pas à la suite.
    trailing = 0
    while body and body[-1] == "":
        body.pop()
        trailing += 1
    if style == "|":
        text = "\n".join(body)
    else:
        text = _fold(body)
    if body:
        if chomp == "-":
            pass
        elif chomp == "+":
            text += "\n" + "\n" * trailing
        else:
            text += "\n"
    elif chomp == "+":
        text = "\n" * trailing

    # Reprendre la structure après la dernière ligne brute consommée.
    consumed_until = k  # index brut exclusif
    j = i + 1
    while j < len(doc.lines) and doc.lines[j].lineno - 1 < consumed_until:
        j += 1
    return text, j


def _parse_list(doc: _Doc, i: int, indent: int) -> tuple[list[Any], int]:
    lines = doc.lines
    out: list[Any] = []
    while i < len(lines) and lines[i].indent == indent and _is_list_item(lines[i].content):
        line = lines[i]
        rest = line.content[1:].strip()
        if rest == "":
            if i + 1 < len(lines) and lines[i + 1].indent > indent:
                value, i = _parse_block(doc, i + 1, lines[i + 1].indent)
            else:
                value, i = None, i + 1
        elif _KEY_RE.match(_strip_comment(rest)) and not rest.startswith(("[", "{")) \
                and not _is_single_quoted(_strip_comment(rest)):
            # Liste de mappings : la première clé est sur la ligne du tiret ;
            # les suivantes sont indentées au même niveau que cette clé.
            offset = len(line.content) - len(rest)
            lines[i] = _Line(indent + offset, rest, line.lineno)
            value, i = _parse_map(doc, i, indent + offset)
        elif _BLOCK_RE.match(rest):
            value, i = _parse_block_scalar(doc, i, indent, rest)
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
