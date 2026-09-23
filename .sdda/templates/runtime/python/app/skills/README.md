# skills/ — ce que chaque agent SAIT FAIRE

Un fichier Markdown par compétence déclarée au roster (`{skill}.md`, slug en
kebab-case) : quand elle s'applique, ce qu'elle produit, ce qui prouve qu'elle
a joué. C'est la MATIÈRE du prompt : `dev-prompt` la compose, puis cite le slug
sous `## Compétences` de `../prompts/{agent}.system.md`.

Une skill n'a ni schéma ni effet de bord : ce n'est pas un outil (`../tools/`,
ce que l'agent a le DROIT D'APPELER). `lint_prompts` vérifie la symétrie
contrat ↔ prompt et signale un slug cité sans fragment.
