"""Détection d'injection heuristique — couche « motifs » de `injection-detection.md §3`. GÉNÉRÉ.

Ce module est la couche la MOINS fiable de l'arsenal, et il le dit : un
détecteur d'injection est un classifieur, et un classifieur se contourne. Ce qui
protège réellement est structurel — moindre privilège d'outils, filtrage à la
source, bornes (`rules/agent-safety.md §3`). Ce détecteur attrape l'évident à
coût nul et, surtout, **journalise les tentatives** : sans lui, on ne saurait
jamais que le système est attaqué.

Conservateur par construction
-----------------------------
Chaque règle porte un poids ; le score d'un texte combine les règles touchées en
OU bruité (`1 - Π(1 - w)`), et seul un score au-dessus du seuil déclenche. Une
règle isolée de faible poids (un « tu es maintenant » dans une phrase métier) ne
suffit pas ; une redéfinition d'instructions explicite, si. Le piège décrit par
la fiche (§7) est réel : un client qui écrit « ignorez ma demande précédente »
dit quelque chose de normal — la règle de redéfinition exige donc un OBJET
d'instruction (« instructions », « consignes », « règles », « prompt »), pas un
simple « ignore ».

Les encodages sont décodés puis RE-SCANNÉS : une consigne en base64, en
caractères « tags » Unicode (contrebande ASCII) ou dissimulée par des caractères
de largeur nulle n'est pas une autre attaque, c'est la même, habillée.

Ce que le module ne fait JAMAIS : réinjecter l'extrait détecté dans un prompt.
Le verdict porte des identifiants de règles et des positions, pas le texte de
l'attaque — le placer dans un contexte de modèle serait exécuter l'attaque en la
signalant (§7, dernier piège).
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

#: Seuil par défaut. Assez haut pour qu'une règle faible seule ne déclenche
#: pas, assez bas pour qu'une redéfinition explicite (0.6) le franchisse.
DEFAULT_THRESHOLD = 0.5

#: Taille maximale scannée. Au-delà, le texte est scanné par tête et par queue :
#: une injection se place au début (prise d'autorité) ou à la fin (dernier mot),
#: et scanner 400 Ko de corpus à chaque requête est précisément ce que la fiche
#: interdit (§3 : le corpus se filtre à l'ingestion).
MAX_SCAN_CHARS = 200_000

_FLAGS = re.IGNORECASE | re.UNICODE


@dataclass(frozen=True)
class Rule:
    """Une règle : un identifiant stable (tracé), un motif, un poids dans ]0, 1]."""

    id: str
    pattern: re.Pattern[str]
    weight: float
    category: str


def _rule(rule_id: str, pattern: str, weight: float, category: str) -> Rule:
    return Rule(rule_id, re.compile(pattern, _FLAGS), weight, category)


#: Le catalogue. Les identifiants sont une interface : ils apparaissent dans les
#: traces et dans les rapports L8, et un renommage casse la comparaison de deux
#: runs. On ajoute, on ne renomme pas.
RULES: tuple[Rule, ...] = (
    # -- Redéfinition d'instructions ---------------------------------------
    _rule("override.ignore",
          r"\b(ignore[rz]?|ignoring|disregard|forget|oublie[rz]?|n['’]?(?:en)?\s*tiens?\s+pas\s+compte\s+d?e?s?)\b"
          r"[^.\n]{0,40}\b(instructions?|consignes?|directives?|r[eè]gles?|rules|prompt|system\s+message)\b",
          0.6, "override"),
    _rule("override.new_instructions",
          r"\b(new|updated|nouvelles?|real|vraies?)\s+(instructions?|consignes?|directives?)\b",
          0.4, "override"),
    _rule("override.policy_lifted",
          r"\b(politique|policy|r[èe]gles?|rules|restrictions?|filtres?|filters?|garde-fous|guardrails?)"
          r"(\s+de\s+refus)?\s+(est|sont|is|are|has\s+been|have\s+been|a\s+[ée]t[ée])\s+"
          r"(lev[ée]e?s?|d[ée]sactiv[ée]e?s?|suspendu[e]?s?|lifted|disabled|suspended|removed|off)\b",
          0.5, "override"),
    _rule("override.system_override",
          r"\b(system|admin|developer|syst[èe]me|administrateur)\s+(override|prompt\s+override)\b",
          0.5, "override"),
    _rule("override.always_answer",
          r"\b(always|toujours)\s+(say|answer|respond|reply|r[ée]pond[sr]e?|dire?s?)\b",
          0.25, "override"),
    _rule("override.from_now_on",
          r"\b(from\s+now\s+on|d[ée]sormais|[àa]\s+partir\s+de\s+maintenant)\b[^.\n]{0,30}\b(you|tu|vous)\b",
          0.3, "override"),
    # -- Balises de rôle et usurpation de frontière -------------------------
    _rule("role.chat_markup",
          r"<\|?(im_start|im_end|system|assistant|endoftext)\|?>|\[/?INST\]|<<\s*/?SYS\s*>>",
          0.55, "role"),
    _rule("role.line_prefix",
          r"(?m)^\s*(system|assistant|developer|syst[èe]me)\s*:\s*\S",
          0.35, "role"),
    _rule("role.envelope_forgery",
          r"</?\s*untrusted\b",
          0.6, "role"),
    _rule("role.inline_address",
          r"(?:^|[.!?;]\s+)(note\s+(?:à|a|to)\s+l?['’]?\s*)?(assistant|ai|ia|model|mod[èe]le|chatbot|llm)\s*[:,]\s*\S",
          0.35, "role"),
    _rule("hidden.markup_comment",
          r"<!--[^>]{0,400}?\b(instructions?|consignes?|assistant|ignore|r[ée]ponds?|respond|answer|say)\b",
          0.5, "hidden"),
    # -- Exfiltration -------------------------------------------------------
    _rule("exfil.system_prompt",
          r"\b(reveal|print|show|repeat|display|output|leak|dump|r[ée]v[èe]le[rz]?|affiche[rz]?|r[ée]p[èe]te[rz]?|donne[rz]?(?:-moi)?)\b"
          r"[^.\n]{0,30}\b(system\s+prompt|initial\s+(prompt|instructions)|hidden\s+instructions|prompt\s+syst[èe]me"
          r"|tes\s+(instructions|consignes)|your\s+(instructions|rules))\b",
          0.6, "exfiltration"),
    _rule("exfil.repeat_above",
          r"\b(repeat|print|output|copy|r[ée]p[èe]te[rz]?|recopie[rz]?)\b[^.\n]{0,25}\b(the\s+)?(text|words|everything|tout|le\s+texte)"
          r"\s+(above|ci-dessus|before|au-dessus|pr[ée]c[ée]dent)",
          0.55, "exfiltration"),
    _rule("exfil.other_users_data",
          r"\b(donn[ée]es?|data|informations?|records?|dossiers?)\s+(personnelles?\s+|personal\s+)?(des|of|from)\s+"
          r"(autres|other)\s+(clients?|utilisateurs?|users?|customers?|tenants?|comptes?|accounts?)",
          0.45, "exfiltration"),
    _rule("exfil.markdown_beacon",
          r"!\[[^\]]*\]\(\s*https?://[^)\s]*\?[^)\s]*=",
          0.55, "exfiltration"),
    _rule("exfil.send_to_url",
          r"\b(send|post|upload|forward|envoie[rz]?|transmet[sz]?|transf[èe]re[rz]?)\b[^.\n]{0,60}\bhttps?://",
          0.35, "exfiltration"),
    # -- Escalade d'outil ---------------------------------------------------
    _rule("tool.escalation",
          r"\b(call|invoke|execute|run|use|appelle[rz]?|ex[ée]cute[rz]?|utilise[rz]?)\b[^.\n]{0,20}\b(the\s+|l['’]\s*)?"
          r"(tool|outil|function|fonction|command|commande)\b[^.\n]{0,40}\b(delete|drop|refund|transfer|rembourse|supprime|virement|admin)",
          0.4, "tool"),
    _rule("tool.shell",
          r"(\brm\s+-rf\b|\bcurl\s+[^|\n]*\|\s*(ba)?sh\b|\bDROP\s+TABLE\b|;\s*--\s*$)",
          0.4, "tool"),
    # -- Jailbreak de persona ----------------------------------------------
    _rule("persona.jailbreak",
          # `DAN` en majuscules seulement : « Dan » est un prénom, « dans » un mot.
          r"(\b(?-i:DAN)\b|\b(do\s+anything\s+now|developer\s+mode|mode\s+d[ée]veloppeur|jailbreak|god\s+mode)\b)",
          0.45, "persona"),
    _rule("persona.you_are_now",
          r"\b(you\s+are\s+now|tu\s+es\s+(maintenant|d[ée]sormais)|pretend\s+(to\s+be|you\s+are)|fais\s+comme\s+si\s+tu\s+[ée]tais)\b",
          0.3, "persona"),
    _rule("persona.no_restrictions",
          r"\b(without|sans)\s+(any\s+|aucune?\s+)?(restrictions?|filters?|filtres?|limites?|censure|guidelines)\b",
          0.3, "persona"),
)

#: Caractères invisibles ou de contrôle de direction : le texte « blanc sur
#: blanc » de la fiche, en version Unicode.
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿]")
#: Caractères « tags » (U+E0000–U+E007F) : ils encodent de l'ASCII invisible —
#: la contrebande d'instructions la plus propre qui soit.
_TAGS = re.compile("[\U000e0000-\U000e007f]")
_BASE64 = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{32,}={0,2}(?![A-Za-z0-9+/=])")
_HEX_ESCAPES = re.compile(r"(?:\\x[0-9a-fA-F]{2}){8,}")

ENCODING_WEIGHT_INVISIBLE = 0.3
ENCODING_WEIGHT_HEX = 0.2


@dataclass(frozen=True)
class Hit:
    """Une règle touchée : identifiant, catégorie, poids, position. JAMAIS le texte."""

    rule: str
    category: str
    weight: float
    start: int
    end: int
    via: str = "plain"      # plain | base64 | tags | invisible-stripped | hex

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "category": self.category, "weight": self.weight,
                "start": self.start, "end": self.end, "via": self.via}


@dataclass(frozen=True)
class Verdict:
    """Le résultat d'un scan : un score dans [0, 1], le seuil, les règles touchées."""

    score: float
    threshold: float
    hits: tuple[Hit, ...] = ()

    @property
    def tripped(self) -> bool:
        return self.score >= self.threshold

    @property
    def rules(self) -> tuple[str, ...]:
        return tuple(sorted({h.rule for h in self.hits}))

    def to_dict(self) -> dict[str, Any]:
        return {"score": round(self.score, 4), "threshold": self.threshold,
                "tripped": self.tripped, "rules": list(self.rules),
                "categories": sorted({h.category for h in self.hits})}


def combine(weights: Iterable[float]) -> float:
    """OU bruité : `1 - Π(1 - w)`. Deux indices faibles valent plus qu'un seul."""
    remaining = 1.0
    for weight in weights:
        remaining *= 1.0 - max(0.0, min(1.0, weight))
    return round(1.0 - remaining, 6)


class InjectionDetector:
    """Le détecteur configuré. Sans état entre deux appels, donc sûr en concurrence."""

    def __init__(self, *, threshold: float = DEFAULT_THRESHOLD,
                 disabled_rules: Sequence[str] = (),
                 extra_rules: Sequence[Rule] = ()) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError(f"seuil d'injection hors ]0, 1] : {threshold}")
        disabled = set(disabled_rules)
        self.threshold = threshold
        self.rules: tuple[Rule, ...] = tuple(r for r in (*RULES, *extra_rules) if r.id not in disabled)

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "InjectionDetector":
        """`{threshold, disabledRules, extraRules: [{id, pattern, weight, category?}]}`."""
        raw = dict(config or {})
        extra = tuple(_rule(str(r["id"]), str(r["pattern"]), float(r.get("weight", 0.5)),
                            str(r.get("category", "custom")))
                      for r in raw.get("extraRules") or () if isinstance(r, Mapping))
        return cls(threshold=float(raw.get("threshold", DEFAULT_THRESHOLD)),
                   disabled_rules=tuple(str(r) for r in raw.get("disabledRules") or ()),
                   extra_rules=extra)

    # -- Scan ---------------------------------------------------------------
    def scan(self, text: str) -> Verdict:
        sample = _sample(text or "")
        hits: list[Hit] = list(self._match(sample, via="plain"))

        first = _INVISIBLE.search(sample)
        if first is not None:
            hits.append(Hit("encoding.invisible", "encoding", ENCODING_WEIGHT_INVISIBLE,
                            first.start(), first.end(), "invisible"))
            hits.extend(self._match(_INVISIBLE.sub("", sample), via="invisible-stripped"))
        smuggled = "".join(chr(ord(c) - 0xE0000) for c in _TAGS.findall(sample))
        if smuggled:
            hits.append(Hit("encoding.tags", "encoding", ENCODING_WEIGHT_INVISIBLE, 0, 0, "tags"))
            hits.extend(self._match(smuggled, via="tags"))
        for blob in _BASE64.finditer(sample):
            decoded = _b64_text(blob.group(0))
            if decoded:
                hits.extend(Hit(h.rule, h.category, h.weight, blob.start(), blob.end(), "base64")
                            for h in self._match(decoded, via="base64"))
        hexes = _HEX_ESCAPES.search(sample)
        if hexes:
            hits.append(Hit("encoding.hex_escapes", "encoding", ENCODING_WEIGHT_HEX,
                            hexes.start(), hexes.end(), "hex"))

        # Une règle compte UNE fois dans le score : dix « ignore les
        # instructions » ne sont pas plus sûrs d'être une attaque qu'un seul.
        best: dict[str, float] = {}
        for hit in hits:
            best[hit.rule] = max(best.get(hit.rule, 0.0), hit.weight)
        return Verdict(score=combine(best.values()), threshold=self.threshold, hits=tuple(hits))

    def neutralize(self, text: str, verdict: Verdict | None = None) -> str:
        """`sanitize-and-continue` : remplace les passages touchés par un marqueur inerte.

        Seuls les passages repérés en clair sont remplacés — un blob encodé est
        remplacé en entier. Le marqueur nomme la règle, jamais le texte retiré.
        """
        verdict = verdict or self.scan(text)
        spans = sorted({(h.start, h.end, h.rule) for h in verdict.hits
                        if h.end > h.start and h.via in ("plain", "base64", "hex")})
        out: list[str] = []
        cursor = 0
        for start, end, rule in spans:
            if start < cursor:
                continue
            out.append(text[cursor:start])
            out.append(f"[neutralisé:{rule}]")
            cursor = end
        out.append(text[cursor:])
        return _TAGS.sub("", _INVISIBLE.sub("", "".join(out)))

    def _match(self, text: str, *, via: str) -> Iterable[Hit]:
        for rule in self.rules:
            for m in rule.pattern.finditer(text):
                yield Hit(rule.id, rule.category, rule.weight, m.start(), m.end(), via)


def _sample(text: str) -> str:
    if len(text) <= MAX_SCAN_CHARS:
        return text
    half = MAX_SCAN_CHARS // 2
    return text[:half] + "\n" + text[-half:]


def _b64_text(blob: str) -> str:
    """Décode un blob base64 s'il donne du TEXTE lisible ; sinon chaîne vide."""
    try:
        raw = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True)
    except (binascii.Error, ValueError):
        return ""
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    printable = sum(1 for c in decoded if c.isprintable() or c in "\n\t")
    return decoded if decoded and printable / len(decoded) > 0.9 else ""
