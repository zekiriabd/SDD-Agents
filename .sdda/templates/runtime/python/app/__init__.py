"""{AppName} — squelette applicatif GÉNÉRÉ par `gen_app_skeleton.py`, ne pas éditer.

Ce paquet porte les sept pièces qu'une application agentic a **toujours**, quel
que soit le framework, la topologie ou le livrable :

    config.py          d'où viennent les valeurs, et le seul lecteur de secrets
    models.py          tier -> client, et les tokens dont on recalcule le coût
    bounds.py          les cinq bornes, en code plutôt qu'en intention
    trust.py           ce qui vient d'un tiers, balisé avant d'entrer dans un prompt
    tracing.py         un span par décision, au format que le framework relit
    orchestration/     la boucle bornée, le routeur, la séquence, et leur manifeste
    run_service.py     le point d'entrée unique de toutes les surfaces
    serving/cli.py     la console — le livrable par défaut, et la surface des evals
    evals/executor.py  ce que `eval-runner --executor` charge pour mesurer

Pourquoi ce squelette existe, alors que six fiches de stack le décrivaient déjà
en prose : six agents `dev-*` lisaient ces fiches et en tiraient six
architectures différentes. La prose dit *quoi* ; elle ne peut pas empêcher deux
runs de câbler `bounds` à deux endroits, ni un `run` de la CLI d'avoir une
sémantique que le runner d'eval n'a pas. Ici la réponse est sur le disque, elle
est la même à chaque génération, et `gen_app_skeleton.py --check` dit quand
quelqu'un l'a retouchée à la main.

**Aucun import de framework au chargement.** Tout ce paquet est importable — et
testable — sans `langchain`, sans `langgraph`, sans SDK de fournisseur et sans
clé d'API. Ce qui dépend d'une librairie tierce est importé *dans* la fonction
qui s'en sert. C'est ce qui permet au smoke, aux tests L0/L1 et à la CI de
tourner sur un clone nu : une plomberie qu'on ne peut pas exécuter à vide est
une plomberie qu'on ne teste pas.
"""
from __future__ import annotations

__all__ = ["__version__"]

#: Version du **squelette**, pas de l'application. Elle change quand la forme
#: change (un champ de span, un code de sortie, la signature de l'exécuteur) :
#: c'est ce que lit un rapport d'eval pour savoir si deux runs sont comparables.
__version__ = "1.0.0"
