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
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

#: Seuil par défaut. Assez haut pour qu'une règle faible seule ne déclenche
#: pas, assez bas pour qu'une redéfinition explicite (0.6) le franchisse.
DEFAULT_THRESHOLD = 0.5

#: Le texte est scanné EN ENTIER. Il l'était par tête et par queue au-delà de
#: 200 000 caractères : une consigne placée au milieu passait sans score, et les
#: positions de la queue, relatives à l'échantillon, étaient appliquées au texte
#: original par `neutralize` — qui remplaçait donc le mauvais passage et
#: laissait l'attaque intacte. Les motifs sont linéaires ; la taille d'entrée
#: est déjà bornée en amont (`maxInputBytes`, `wrap(max_chars)`).

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
    # « Ignore tout ce qui précède » sans nommer d'objet : la forme la plus
    # courante des injections réelles, que `override.ignore` ne voyait pas.
    _rule("override.ignore_above",
          r"\b(ignore[rz]?|ignoring|disregard|forget|oublie[rz]?)\b[^.\n]{0,20}\b(everything|all|anything|tout|"
          r"ce\s+qui)\b[^.\n]{0,20}\b(above|before|previous(ly)?|prior|earlier|ci-dessus|pr[ée]c[èe]de|"
          r"pr[ée]c[ée]dent|au-dessus)\b",
          0.55, "override"),
    # Les mêmes consignes dans les langues européennes courantes : un filtre
    # anglais-français se contourne en changeant de langue.
    _rule("override.ignore_multilingual",
          r"\b(olvida|ignora|ignore|vergiss|ignoriere|dimentica|esquece|esque[çc]a|negeer|vergeet)\w*\b[^.\n]{0,40}"
          r"\b(instrucci[oó]n(es)?|anweisung(en)?|istruzion[ei]|instru[çc][õo]es|instructies|regeln|reglas|regole|"
          r"regras|vorgaben|indicaciones)\b",
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

#: Homoglyphes (cyrillique, grec) et « leet » ramenés à l'ASCII, caractère pour
#: caractère — la LONGUEUR est conservée, donc les positions d'un hit trouvé
#: sur le texte normalisé désignent le même passage dans le texte original, et
#: `neutralize` remplace le bon. `іgnore` (i cyrillique) ou `1gn0re` n'étaient
#: pas une autre attaque : c'était la même, habillée pour passer le motif.
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j",
    "ѕ": "s", "ԁ": "d", "һ": "h", "ӏ": "l", "ո": "n", "ս": "u", "ɡ": "g", "ı": "i",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "І": "I", "Ј": "J",
    "Ѕ": "S", "М": "M", "Н": "H", "К": "K", "В": "B",
    "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u",
    "Α": "A", "Ε": "E", "Ι": "I", "Κ": "K", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y",
})
_LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}


#: Un chiffre n'est du leet qu'au contact d'une lettre : « 2 000 € » reste un
#: montant, « 1gn0re » redevient « ignore ».
_LEET_RE = re.compile(r"(?<=[^\W\d_])[013457@$]|[013457@$](?=[^\W\d_])")


def _normalize(text: str) -> str:
    """NFKC, homoglyphes et leet, SANS changer la longueur (cf. `_CONFUSABLES`)."""
    if not text.isascii():
        chars = []
        for char in text:
            folded = unicodedata.normalize("NFKC", char)
            chars.append(folded if len(folded) == 1 else char)
        text = "".join(chars).translate(_CONFUSABLES)
    return _LEET_RE.sub(lambda m: _LEET[m.group(0)], text)


#: Voies dont les positions ne désignent PAS un passage du texte original : le
#: texte débarrassé de ses invisibles (décalé), les caractères « tags » (qui ne
#: sont pas lisibles en place). Un hit par l'une d'elles neutralise TOUT le
#: texte — remplacer les seuls passages repérés en clair puis retirer les
#: invisibles rendait l'attaque… en clair.
_WHOLE_TEXT_VIAS = frozenset({"invisible-stripped", "tags"})


@dataclass(frozen=True)
class Hit:
    """Une règle touchée : identifiant, catégorie, poids, position. JAMAIS le texte."""

    rule: str
    category: str
    weight: float
    start: int
    end: int
    via: str = "plain"      # plain | normalized | base64 | tags | invisible-stripped | hex

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
        sample = text or ""
        hits: list[Hit] = list(self._match(sample, via="plain"))
        normalized = _normalize(sample)
        if normalized != sample:
            seen = {(h.rule, h.start, h.end) for h in hits}
            hits.extend(h for h in self._match(normalized, via="normalized")
                        if (h.rule, h.start, h.end) not in seen)

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
        if any(h.via in _WHOLE_TEXT_VIAS and h.category != "encoding" for h in verdict.hits):
            return f"[neutralisé:{','.join(verdict.rules)}]"
        spans = sorted({(h.start, h.end, h.rule) for h in verdict.hits
                        if h.end > h.start and h.via in ("plain", "normalized", "base64", "hex")})
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
