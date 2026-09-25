"""Lecture des artefacts Markdown : frontmatter, en-tête, sections, listes, tables.

PIÈGE DOCUMENTÉ — convention d'extraction de section (héritée de SDD_Pro) :
les scripts localisent une section avec le motif exact

    ^##\\s+(?:<numéro>\\.\\s+)?{titre}\\s*$

Un titre ANNOTÉ rend donc la section introuvable :

    ## Quantified Goal                -> trouvé
    ## 13. Quantified Goal            -> trouvé (préfixe numérique toléré)
    ## Quantified Goal (v2)           -> INTROUVABLE
    ## Quantified Goal — obligatoire  -> INTROUVABLE
    ### Quantified Goal               -> INTROUVABLE (niveau H3)

C'est voulu : un titre est une clé, pas de la prose. Les validateurs signalent
les titres approchants (`similar_headings`) pour que le FIX soit immédiat.

Toutes les fonctions de lecture normalisent BOM et CRLF (voir `read_text`).
"""
from __future__ import annotations

import re
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)^---\s*\n", re.DOTALL | re.MULTILINE)
_HEADER_KV_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _-]*?)\s*:\s*(.*?)\s*$")
_ANY_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*?)\s*$")
_KV_ITEM_RE = re.compile(r"^\**([^:*`]+?)\**\s*:\s*(.*)$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_FENCE_RE = re.compile(r"^```([A-Za-z0-9_-]*)\s*\n(.*?)^```\s*$", re.DOTALL | re.MULTILINE)

PLACEHOLDER_PATTERNS = (
    re.compile(r"<\s*[àa]\s+pr[ée]ciser\s*>", re.IGNORECASE),
    re.compile(r"<ex\.", re.IGNORECASE),
    re.compile(r"^<[^>]*>$"),                 # valeur entièrement entre chevrons
    re.compile(r"^(…|\.\.\.|TBD|TODO|N/A|\?)$", re.IGNORECASE),
    re.compile(r"\{(n|m|Name|MissionName|agent-slug|tool-slug|mission-hash-8)\}"),
    re.compile(r"^[-—]?\s*$"),
)


def read_text(path: Path) -> str:
    """Lit un fichier texte UTF-8 (BOM toléré), fins de ligne normalisées en LF."""
    raw = Path(path).read_text(encoding="utf-8-sig")
    return raw.replace("\r\n", "\n").replace("\r", "\n")


# --------------------------------------------------------------------------
# Frontmatter YAML fenced (`---`) et en-tête `Clé: valeur`
# --------------------------------------------------------------------------
def parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    """`---\\nk: v\\n---` en tête de fichier -> (dict, corps). None si absent.

    Valeurs à plat uniquement ; commentaires `#` en fin de valeur retirés.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        kv = _HEADER_KV_RE.match(line)
        if kv:
            out[kv.group(1).strip()] = strip_inline_comment(kv.group(2)).strip("\"'")
    return out, text[m.end():]


def parse_header_fields(text: str) -> dict[str, str]:
    """Champs `Clé: valeur` de l'en-tête (après le H1, avant le premier `##`).

    C'est la convention des templates (`MISSION ID:`, `Status:`, `Parent MISSION
    hash:`…). Les commentaires `  # …` en fin de ligne sont retirés. Un
    frontmatter fenced, s'il existe, est fusionné (l'en-tête gagne).
    """
    fields: dict[str, str] = {}
    body = text
    fm = parse_frontmatter(text)
    if fm:
        fields.update(fm[0])
        body = fm[1]
    for line in body.split("\n"):
        if line.startswith("## "):
            break
        if not line.strip() or line.startswith("#") or line.startswith(("-", "*", "|", ">")):
            continue
        m = _HEADER_KV_RE.match(line)
        if m:
            fields[m.group(1).strip()] = strip_inline_comment(m.group(2))
    return fields


def strip_inline_comment(value: str) -> str:
    """Retire un commentaire `  # …` (au moins un espace avant le `#`)."""
    idx = value.find(" #")
    return (value[:idx] if idx >= 0 else value).strip()


# --------------------------------------------------------------------------
# Sections `## Titre`
# --------------------------------------------------------------------------
def _section_pattern(heading: str, numbered: bool) -> re.Pattern[str]:
    esc = re.escape(heading.strip()).replace(r"\ ", r"\s+")
    num = r"(?:\d+(?:\.\d+)*\.?\s+)?" if numbered else ""
    return re.compile(rf"^##\s+{num}{esc}\s*$", re.MULTILINE)


def section_body(text: str, heading: str, *, numbered: bool = True) -> str | None:
    """Corps entre `## {heading}` et le prochain `## ` (ou la fin de fichier).

    Un `# ` en colonne 0 n'interrompt PAS la section : STACK.md utilise `#` pour
    ses commentaires.

    Titre comparé EXACTEMENT (voir le piège en tête de module). `numbered`
    tolère un préfixe `2.` ou `2.1`. Le corps est rendu tel quel (non strippé).
    """
    m = _section_pattern(heading, numbered).search(text)
    if not m:
        return None
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def list_headings(text: str) -> list[str]:
    return [h.strip() for h in _ANY_H2_RE.findall(text)]


def similar_headings(text: str, heading: str) -> list[str]:
    """Titres H2 qui CONTIENNENT le titre cherché sans lui être égaux (titre annoté)."""
    key = heading.strip().lower()
    out = []
    for h in list_headings(text):
        h_clean = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", h).lower()
        if key in h_clean and h_clean != key:
            out.append(h)
    return out


def section_is_empty(body: str | None) -> bool:
    """Vrai si la section n'a aucun contenu réel (blockquotes, `<…>` et `…` exclus)."""
    if body is None:
        return True
    for line in body.split("\n"):
        s = line.strip()
        if not s or s.startswith(">"):
            continue
        s = re.sub(r"\*\*[^*]+\*\*\s*:?", "", s).strip()
        if not s or is_placeholder(s):
            continue
        return False
    return True


# --------------------------------------------------------------------------
# Listes
# --------------------------------------------------------------------------
def parse_bullets(body: str, *, top_level_only: bool = True) -> list[str]:
    """Éléments `- texte` (niveau 0 par défaut)."""
    items: list[str] = []
    continuing = False
    for line in body.split("\n"):
        m = _BULLET_RE.match(line)
        if m:
            continuing = not top_level_only or len(m.group(1)) <= 1
            if continuing:
                items.append(m.group(2))
            continue
        # Ligne de CONTINUATION d'une puce (indentée, non vide, pas une puce) :
        # elle appartient à la puce précédente. L'ignorer tronquait toute valeur
        # écrite sur plusieurs lignes — `Entrées non maîtrisées` perdait trois
        # champs sur cinq, et les suites d'injection ne les couvraient plus.
        if continuing and items and line[:1] in (" ", "\t") and line.strip():
            items[-1] = items[-1].rstrip() + " " + line.strip()
        elif not line.strip() or not line[:1] in (" ", "\t"):
            continuing = False
    return items


def parse_kv_list(body: str) -> dict[str, str]:
    """`- KEY: value` (niveau 0) -> dict. Gère `- **Clé** : valeur` (gras, espace français)."""
    out: dict[str, str] = {}
    for item in parse_bullets(body):
        m = _KV_ITEM_RE.match(item)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def parse_nested_list(body: str) -> list[dict]:
    """Blocs `- AC-1:` suivis de sous-items `  - metric: x`.

    Renvoie [{"key": "AC-1", "value": "", "fields": {"metric": "x", …}}, …].
    Un sous-item sans `:` est ignoré. Un item de niveau 0 sans `:` a `key` = texte.
    """
    blocks: list[dict] = []
    for line in body.split("\n"):
        m = _BULLET_RE.match(line)
        if not m:
            continue
        indent, item = len(m.group(1)), m.group(2)
        kv = _KV_ITEM_RE.match(item)
        if indent <= 1:
            if kv:
                blocks.append({"key": kv.group(1).strip(), "value": kv.group(2).strip(), "fields": {}})
            else:
                blocks.append({"key": item.strip(), "value": "", "fields": {}})
        elif blocks and kv:
            blocks[-1]["fields"][kv.group(1).strip().lower()] = _unquote(kv.group(2).strip())
    return blocks


def _unquote(value: str) -> str:
    """`">= 0.85"` -> `>= 0.85`. Des guillemets APPARIÉS autour d'une valeur ne sont pas la valeur.

    Un agent qui a l'habitude du YAML met un seuil qui commence par `>` entre
    guillemets — en YAML, c'est même obligatoire. Refuser 22 AC pour ce seul
    motif, c'est renvoyer une découpe entière pour une différence que le
    lecteur humain ne voit pas. Les guillemets non appariés restent tels quels.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


# --------------------------------------------------------------------------
# Tables et blocs de code
# --------------------------------------------------------------------------
def parse_table(body: str) -> list[dict[str, str]]:
    """Première table Markdown du corps -> liste de lignes {colonne: cellule}."""
    lines = [l for l in body.split("\n")]
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in lines:
        s = line.strip()
        if not s.startswith("|"):
            if header is not None and rows:
                break
            continue
        if _TABLE_SEP_RE.match(s):
            continue
        cells = [c.strip() for c in _split_row(s)]
        if header is None:
            header = cells
            continue
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        rows.append({header[i]: cells[i] for i in range(len(header))})
    return rows


def _split_row(row: str) -> list[str]:
    s = row.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    # `\|` échappe un pipe dans une cellule
    parts, buf, i = [], [], 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "|":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def fenced_blocks(body: str, lang: str | None = None) -> list[str]:
    """Contenus des blocs ``` (filtrés par langage si `lang`)."""
    out = []
    for m in _FENCE_RE.finditer(body):
        if lang is None or m.group(1).lower() == lang.lower():
            out.append(m.group(2))
    return out


def first_yaml_block(text: str) -> str | None:
    """Le premier bloc ```yaml (ou ```yml) d'un document Markdown, None s'il n'y en a pas.

    C'est la convention des déclarations que `feats/` porte en Markdown — le
    roster (`{n}-roster.md`), un manifeste de sources en `.md` : la prose
    autour du bloc est de la documentation pour l'humain, le bloc est ce que
    la machine lit. Un seul bloc fait foi, le premier, pour qu'un exemple cité
    plus bas dans le même fichier ne devienne jamais la déclaration.
    """
    for lang in ("yaml", "yml"):
        blocks = fenced_blocks(text, lang)
        if blocks:
            return blocks[0]
    return None


def replace_first_fenced_block(text: str, lang: str, new_body: str) -> str | None:
    """Remplace le CONTENU du premier bloc ```{lang} par `new_body` ; None si aucun bloc."""
    for m in _FENCE_RE.finditer(text):
        if m.group(1).lower() == lang.lower():
            body = new_body if new_body.endswith("\n") else new_body + "\n"
            return text[: m.start(2)] + body + text[m.end(2):]
    return None


# --------------------------------------------------------------------------
# Valeurs
# --------------------------------------------------------------------------
def strip_code(value: str) -> str:
    """Retire les backticks et le gras entourant une valeur."""
    v = value.strip()
    v = re.sub(r"^\*\*(.*)\*\*$", r"\1", v)
    return v.strip("`").strip()


def code_spans(value: str) -> list[str]:
    """Tous les spans de code d'une cellule ou d'une puce, dans l'ordre, vides exclus.

    Une cellule `Fichier` peut lister DEUX contrats (« `a.tool.md`, `b.tool.md` ») :
    n'en lire que le premier vérifiait l'un et déclarait l'autre présent sans
    l'avoir ouvert. La note entre les spans (« *(généré par …)* ») reste hors
    des valeurs, comme pour `first_code_span`.
    """
    return [s.strip() for s in re.findall(r"`([^`]+)`", value or "") if s.strip()]


def first_code_span(value: str) -> str:
    """La valeur d'un champ à valeur UNIQUE : son premier span de code s'il y en a un.

    « `chemin` *(obligatoire — …)* », souvent continué sur la ligne suivante
    d'une puce : la suite est un commentaire, pas une partie du chemin. À ne
    pas appliquer aux champs qui portent une expression (`a` -> `b`), ni à une
    cellule qui peut en porter plusieurs (`code_spans`).
    """
    spans = code_spans(value)
    return spans[0] if spans else strip_code(value or "")


def split_code_list(value: str) -> list[str]:
    """`\\`a\\`, \\`b\\`` ou `a, b` -> ["a", "b"]. `aucun`/`none`/`-` -> []."""
    v = value.strip()
    if not v or v.lower() in ("aucun", "aucune", "none", "-", "—", "n/a"):
        return []
    codes = re.findall(r"`([^`]+)`", v)
    if codes:
        return [c.strip() for c in codes if c.strip()]
    return [p.strip() for p in re.split(r"[,;]", v) if p.strip()]


def is_placeholder(value: str | None) -> bool:
    """Vrai si la valeur est un trou de template (`<à préciser>`, `<ex. …>`, `…`, `{n}`…)."""
    if value is None:
        return True
    v = value.strip()
    if not v:
        return True
    return any(p.search(v) for p in PLACEHOLDER_PATTERNS)


def find_placeholders(text: str) -> list[str]:
    """Toutes les occurrences `<à préciser>` (le seul trou autorisé, et refusé par G0)."""
    return re.findall(r"<\s*[àa]\s+pr[ée]ciser\s*>", text, flags=re.IGNORECASE)


def replace_header_field(text: str, key: str, value: str) -> str:
    """Remplace la valeur d'un champ d'en-tête `Key: …` (première occurrence avant `##`)."""
    head, sep, tail = text.partition("\n## ")
    pattern = re.compile(rf"^({re.escape(key)}\s*:\s*)(.*?)(\s+#.*)?$", re.MULTILINE)
    new_head, n = pattern.subn(lambda m: f"{m.group(1)}{value}{m.group(3) or ''}", head, count=1)
    if n == 0:
        return text
    return new_head + sep + tail
