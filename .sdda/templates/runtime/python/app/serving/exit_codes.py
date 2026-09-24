"""`[CLASS]` -> code de sortie, à UN seul endroit. GÉNÉRÉ, ne pas éditer.

Un code de sortie est une interface machine : un `Makefile`, une CI, un runner
d'eval et un ordonnanceur le lisent, et aucun d'eux ne parse du texte. Il doit
donc être **stable** et **dérivé**, jamais choisi à la main dans une commande.
Deux commandes qui traduisent la même classe en deux codes différents rendent la
CI inexploitable, et personne ne s'en aperçoit avant que la branche de
remédiation soit prise à tort.

Trois familles de codes, et la distinction n'est pas cosmétique :

    0, 7           le run a produit une réponse (7 : dégradée, ANNONCÉE)
    3, 4, 5, 10    le run s'est arrêté comme prévu — ce sont des RÉSULTATS
    1, 2, 6, 8, 9  quelque chose n'a pas marché

Les codes du second groupe sont ceux qu'on oublie le plus souvent de distinguer.
Un refus de guardrail (`4`) est le résultat ATTENDU d'une suite L8 : le traiter
comme un échec ferait échouer la SAFETY GATE sur les cas où elle réussit.

Les codes restent sous 128 (POSIX laisse 128+ aux signaux) et la classe n'est
jamais « encodée » dans le code : la classe voyage dans l'événement `error`, le
code dit seulement quoi faire.
"""
from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Table close, documentée dans `--help`, testée en L1."""

    OK = 0
    INTERNAL = 1
    USAGE = 2                 # réservé par les parseurs d'arguments
    BOUND = 3                 # borne atteinte, politique `fail-explicit`
    REFUSED = 4               # guardrail ou politique de refus — résultat attendu en L8
    BUDGET = 5                # plafond de coût du run dépassé
    TOOL = 6                  # échec d'outil non déclaré, ou connectivité
    DEGRADED = 7              # réponse rendue, `degraded: true` — jaune pour le runner
    CONFIG = 8                # configuration, prompt absent, IR périmé
    OUTPUT_INVALID = 9        # sortie du modèle non conforme au schéma
    INTERRUPTED = 10          # attend une décision humaine ; reprendre avec `resume`
    RESUME_FAILED = 11        # thread inconnu, checkpoint absent
    SIGINT = 130              # Ctrl-C — traces vidées avant la sortie


#: Classe -> code. Exhaustive pour les classes que le squelette émet ; une
#: classe inconnue tombe sur `INTERNAL` plutôt que sur `OK`, parce qu'un succès
#: annoncé à tort est la seule erreur qu'aucun script ne rattrapera.
CLASS_TO_EXIT: dict[str, ExitCode] = {
    "CLI_USAGE": ExitCode.USAGE,
    "BUDGET_BOUND_EXCEEDED": ExitCode.BOUND,
    "UNBOUNDED_LOOP": ExitCode.BOUND,
    "SAFETY_GUARDRAIL_TRIPPED": ExitCode.REFUSED,
    "AGENT_REFUSED": ExitCode.REFUSED,
    "BUDGET_EXCEEDED_MEASURED": ExitCode.BUDGET,
    "TOOL_CONTRACT_FAILED": ExitCode.TOOL,
    "TOOL_MCP_DISCONNECTED": ExitCode.TOOL,
    "TOOL_NOT_REGISTERED": ExitCode.TOOL,
    "TOOL_FIXTURE_MISSING": ExitCode.TOOL,   # L4 : un appel qu'aucune fixture ne couvre
    "CONFIG_INVALID": ExitCode.CONFIG,
    "PROMPT_MISSING": ExitCode.CONFIG,
    "TOOL_SCHEMA_DRIFT": ExitCode.CONFIG,
    "IR_STALE": ExitCode.CONFIG,
    "AGENT_OUTPUT_INVALID": ExitCode.OUTPUT_INVALID,
    "AGENT_INTERRUPTED": ExitCode.INTERRUPTED,
    "RESUME_FAILED": ExitCode.RESUME_FAILED,
    "INTERNAL_ERROR": ExitCode.INTERNAL,
}

#: Statut de run -> code, quand aucune classe n'est en jeu. `budget_usd` est le
#: seul dépassement qui ne rend pas `3` : G6 le lit séparément, parce qu'un run
#: trop cher et un run trop long ne se corrigent pas de la même façon.
STATUS_TO_EXIT: dict[str, ExitCode] = {
    "ok": ExitCode.OK,
    "degraded": ExitCode.DEGRADED,
    "interrupted": ExitCode.INTERRUPTED,
    "failed": ExitCode.INTERNAL,
}


def resolve_exit_code(*, status: str = "ok", error_class: str = "",
                      bound_exceeded: str = "") -> ExitCode:
    """Le code d'un run. Dérivé, jamais choisi.

    L'ordre de résolution est ce qui rend la table utile : le statut d'abord
    (une réponse dégradée reste une réponse, même si une borne est tombée),
    puis la borne, puis la classe. L'inverse ferait qu'un `degrade` sur budget
    rendrait `5` — et le runner d'eval, qui compte `5` comme un budget crevé,
    verrait rouge là où la politique déclarée disait jaune.
    """
    if status in ("ok", "degraded", "interrupted"):
        return STATUS_TO_EXIT[status]
    if bound_exceeded == "budget_usd":
        return ExitCode.BUDGET
    if bound_exceeded:
        return ExitCode.BOUND
    return CLASS_TO_EXIT.get(error_class, ExitCode.INTERNAL)
