"""L'identité de l'APPELANT, établie par le transport — jamais par le modèle. GÉNÉRÉ, ne pas éditer.

La CLI la reçoit par `--tenant`, une surface HTTP par son authentification :
dans les deux cas elle entre par `RunRequest.tenant_id`, et `RunService` la
pose ici pour la durée du run. Les couches qui filtrent à la source (vues SQL,
`data/envelope.py`, filtre d'index) la LISENT ici ; elle ne traverse jamais un
prompt ni un argument d'outil — un filtre d'identité qu'une phrase peut lever
n'est pas un filtre.

Avant ce module, `tenant_id` arrivait jusqu'à `RunService` et s'y arrêtait :
chaque source déclarant `required_filter` refusait alors TOUT appel
(identité absente), ou la composition devait la faire passer par un canal
que personne n'avait défini.

Un `ContextVar` et non un attribut : deux runs concurrents dans le même
processus (un serveur, un exécuteur d'eval parallèle) ne voient chacun que le
leur.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_TENANT: ContextVar[str] = ContextVar("sdda_caller_tenant", default="")


def current_tenant() -> str:
    """L'identité de l'appelant du run en cours ; `""` si le transport n'en a établi aucune."""
    return _TENANT.get()


@contextmanager
def caller(tenant_id: str) -> Iterator[None]:
    """Pose l'identité pour la durée d'un run, et la retire à la sortie."""
    token = _TENANT.set(str(tenant_id or "").strip())
    try:
        yield
    finally:
        _TENANT.reset(token)
