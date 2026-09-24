"""Les erreurs DÉCLARÉES des outils d'accès aux données.

Chacune correspond à une ligne du `## 4. Erreurs déclarées` des tool-contracts
générés, et chacune a un test L2. Une erreur non déclarée qui survient en
production est un trou de spécification, pas un imprévu.

Elles portent `code` parce que c'est le code — et non le type Python — que
l'agent voit et sur lequel son contrat lui dit quoi faire.
"""
from __future__ import annotations


class DataAccessError(Exception):
    """Toute erreur d'accès aux données. `code` est ce que voit l'agent."""

    code = "DATA_ACCESS_ERROR"

    def __init__(self, message: str, *, source: str = "", detail: str = ""):
        super().__init__(message)
        self.message = message
        self.source = source
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message,
                "source": self.source, "detail": self.detail}




class InvalidFilter(DataAccessError):
    """Filtre sur un champ non exposé, ou valeur hors domaine.

    Levée plutôt qu'ignorée : un filtre silencieusement ignoré rend TOUS les
    enregistrements, et l'agent conclut sur un ensemble qu'il croyait restreint.
    """
    code = "INVALID_FILTER"




class SourceUnavailable(DataAccessError):
    """Source illisible : fichier réécrit pendant la lecture, hôte injoignable."""
    code = "SOURCE_UNAVAILABLE"


class Timeout(DataAccessError):
    """Budget de lecture dépassé. Jamais un résultat partiel muet."""
    code = "TIMEOUT"


class BoundaryViolation(DataAccessError):
    """Un chemin résout hors de la racine du store, ou suit un lien symbolique.

    Fail-closed, et distincte de `SourceUnavailable` : ce n'est pas une panne,
    c'est une tentative de sortie de périmètre, et elle doit se voir comme telle
    dans les traces.
    """
    code = "PATH_ESCAPE"
