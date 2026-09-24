"""Rédaction des données personnelles à FORMAT VÉRIFIABLE — `pii-redaction.md §4-§5`. GÉNÉRÉ.

Ce module couvre la ligne « haute fiabilité » de la fiche, et elle seule :
e-mail, téléphone (France et international), IBAN (clé mod 97), carte bancaire
(Luhn), numéro de sécurité sociale français (clé NIR) et adresse IP (v4, v6).
Chacun de ces formats se VALIDE — c'est ce qui permet de rédiger sans rédiger le
vocabulaire métier. Une suite de seize chiffres qui ne passe pas Luhn n'est pas
une carte, et la remplacer rendrait le produit inutilisable (`false_redaction_rate`).

Ce qu'il ne fait pas, et qu'il faut dire : les noms de personnes, les adresses
postales, les données de santé. Un module qui prétendrait les rédiger par motif
mentirait ; ils relèvent d'un NER, mesuré, jamais annoncé à 100 %, ou du
cloisonnement à la source (vue SQL, filtre d'index) — la rédaction protège
contre la FUITE accidentelle, pas contre l'accès non autorisé.

Jetons stables et typés (§5)
----------------------------
`[EMAIL_1]`, `[PHONE_2]`… : typés, pour que le modèle garde la structure de la
phrase ; stables dans une instance de `PiiRedactor`, pour que deux occurrences
de la même valeur portent le même jeton (coréférence préservée). La table de
correspondance reste DANS l'objet — jamais dans le prompt — et seul le code
peut ré-hydrater (`restore`), jamais le modèle.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Callable, Iterator, Mapping, Sequence

#: Catégories, dans l'ordre de PRIORITÉ : un IBAN contient des suites de
#: chiffres qu'un téléphone reconnaîtrait, une carte aussi. Le premier qui
#: valide un passage le prend ; les suivants ne voient plus que le reste.
CATEGORIES: tuple[str, ...] = ("EMAIL", "IBAN", "CARD", "NIR", "PHONE", "IP")

_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
                    r"\.[A-Za-z]{2,24}(?![\w-])")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")
_CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
_NIR = re.compile(r"(?<!\d)[12][ ]?\d{2}[ ]?(?:0[1-9]|1[0-2]|[2-9]\d)[ ]?(?:\d{2}|2[AB])"
                  r"[ ]?\d{3}[ ]?\d{3}[ ]?\d{2}(?!\d)")
_PHONE_FR = re.compile(r"(?<![\d+])(?:(?:\+|00)33[ .-]?(?:\(0\)[ .-]?)?|0)[1-9](?:[ .-]?\d{2}){4}(?!\d)")
_PHONE_INTL = re.compile(r"(?<![\w+])\+(?!33)[1-9]\d{0,2}(?:[ .-]?\(?\d{1,4}\)?)(?:[ .-]?\d{2,4}){2,4}(?!\d)")
#: Bornée par des séparateurs NON alphanumériques : `1.2.3.4-beta` est un
#: numéro de version, pas une adresse — le rédiger rendrait un changelog illisible.
_IPV4 = re.compile(r"(?<![\w.-])(?:\d{1,3}\.){3}\d{1,3}(?![\w.-])")
_IPV6 = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")


def luhn_ok(digits: str) -> bool:
    """Somme de contrôle des cartes bancaires."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def iban_ok(raw: str) -> bool:
    """Clé mod 97 (ISO 13616) : pays et clé déplacés en fin, lettres en nombres."""
    compact = raw.replace(" ", "").upper()
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    number = "".join(str(int(c, 36)) for c in rearranged)
    return int(number) % 97 == 1


def nir_ok(raw: str) -> bool:
    """Clé du NIR : 97 - (numéro mod 97), Corse 2A/2B ramenée à 19/18."""
    compact = raw.replace(" ", "").upper()
    body, key = compact[:13], compact[13:]
    if len(key) != 2 or not key.isdigit():
        return False
    body = body.replace("2A", "19").replace("2B", "18")
    if not body.isdigit():
        return False
    return 97 - int(body) % 97 == int(key)


def _digits(raw: str) -> str:
    return re.sub(r"\D", "", raw)


def _card(raw: str) -> bool:
    digits = _digits(raw)
    return 13 <= len(digits) <= 19 and luhn_ok(digits)


def _phone_intl(raw: str) -> bool:
    return 8 <= len(_digits(raw)) <= 15


def _ipv4(raw: str) -> bool:
    try:
        ipaddress.IPv4Address(raw)
    except ValueError:
        return False
    return raw not in ("0.0.0.0", "255.255.255.255")  # noqa: S104 - comparaison, aucun bind


def _ipv6(raw: str) -> bool:
    if raw.count(":") < 2:
        return False
    try:
        ipaddress.IPv6Address(raw)
    except ValueError:
        return False
    return raw not in ("::", "::1")


Span = tuple[int, int]


def _matches(pattern: re.Pattern[str], valid: Callable[[str], bool]) -> Callable[[str], Iterator[Span]]:
    def spans(text: str) -> Iterator[Span]:
        for match in pattern.finditer(text):
            if valid(match.group(0)):
                yield match.span()
    return spans


def _iban_spans(text: str) -> Iterator[Span]:
    """Un IBAN suivi d'un mot en capitales (« … 189 EUR ») est d'abord reconnu trop long.

    Le motif est gourmand ; plutôt que de rater l'IBAN, on raccourcit la fin
    groupe par groupe jusqu'à ce que la clé mod 97 tombe juste.
    """
    for match in _IBAN.finditer(text):
        start, end = match.span()
        candidate = match.group(0)
        while len(candidate.replace(" ", "")) >= 15:
            if iban_ok(candidate):
                yield start, start + len(candidate)
                break
            cut = candidate.rstrip()
            candidate = cut[:-1].rstrip() if " " not in cut[-5:] else cut.rsplit(" ", 1)[0]


#: catégorie -> détecteurs. La validation (Luhn, mod 97, clé NIR, octets IP)
#: est ce qui fait la fiabilité : sans elle, une référence de commande devient
#: une carte, et le produit rédige son propre vocabulaire.
_DETECTORS: dict[str, list[Callable[[str], Iterator[Span]]]] = {
    "EMAIL": [_matches(_EMAIL, lambda _s: True)],
    "IBAN": [_iban_spans],
    "CARD": [_matches(_CARD, _card)],
    "NIR": [_matches(_NIR, nir_ok)],
    "PHONE": [_matches(_PHONE_FR, lambda _s: True), _matches(_PHONE_INTL, _phone_intl)],
    "IP": [_matches(_IPV4, _ipv4), _matches(_IPV6, _ipv6)],
}


@dataclass(frozen=True)
class Finding:
    """Une PII trouvée : catégorie et position. La VALEUR n'est pas exposée par `to_dict`."""

    category: str
    start: int
    end: int
    value: str = field(repr=False)

    def to_dict(self) -> dict[str, object]:
        return {"category": self.category, "start": self.start, "end": self.end}


def find(text: str, categories: Sequence[str] = CATEGORIES) -> list[Finding]:
    """Les PII à format vérifiable, sans chevauchement, dans l'ordre du texte."""
    taken: list[tuple[int, int]] = []
    found: list[Finding] = []
    for category in CATEGORIES:
        if category not in categories:
            continue
        for detector in _DETECTORS[category]:
            for start, end in detector(text):
                if any(start < e and s < end for s, e in taken):
                    continue
                taken.append((start, end))
                found.append(Finding(category, start, end, text[start:end]))
    return sorted(found, key=lambda f: f.start)


class PiiRedactor:
    """Remplace chaque PII par un jeton typé et stable ; garde la table pour `restore`."""

    def __init__(self, categories: Sequence[str] = CATEGORIES) -> None:
        unknown = sorted(set(categories) - set(CATEGORIES))
        if unknown:
            raise ValueError(f"catégories PII inconnues : {unknown} (connues : {list(CATEGORIES)})")
        self.categories = tuple(categories)
        self._tokens: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}

    @classmethod
    def from_config(cls, config: Mapping[str, object] | None) -> "PiiRedactor":
        raw = (config or {}).get("categories")
        if isinstance(raw, (list, tuple)):
            return cls([str(c) for c in raw])
        return cls()

    def token_for(self, category: str, value: str) -> str:
        key = (category, _normalize(category, value))
        if key not in self._tokens:
            self._counters[category] = self._counters.get(category, 0) + 1
            self._tokens[key] = f"[{category}_{self._counters[category]}]"
        return self._tokens[key]

    def redact(self, text: str) -> tuple[str, list[Finding]]:
        """(texte rédigé, trouvailles). Idempotent : un jeton n'est pas une PII."""
        findings = find(text, self.categories)
        if not findings:
            return text, []
        out: list[str] = []
        cursor = 0
        for finding in findings:
            out.append(text[cursor:finding.start])
            out.append(self.token_for(finding.category, finding.value))
            cursor = finding.end
        out.append(text[cursor:])
        return "".join(out), findings

    def restore(self, text: str) -> str:
        """Ré-hydratation par le CODE, après génération (§5) — jamais par le modèle."""
        reverse: dict[str, str] = {}
        for (_category, value), token in self._tokens.items():
            reverse.setdefault(token, value)
        token_re = r"\[(?:%s)_\d+\]" % "|".join(CATEGORIES)
        return re.sub(token_re, lambda m: reverse.get(m.group(0), m.group(0)), text)


def _normalize(category: str, value: str) -> str:
    if category in ("CARD", "NIR", "PHONE", "IBAN"):
        return re.sub(r"[\s.()-]", "", value).upper()
    return value.lower() if category == "EMAIL" else value


def redact_pii(text: str, categories: Sequence[str] = CATEGORIES) -> str:
    """Raccourci sans état : une rédaction ponctuelle (trace, message d'erreur)."""
    return PiiRedactor(categories).redact(text)[0]
