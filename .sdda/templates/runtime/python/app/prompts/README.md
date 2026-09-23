# prompts/ — les prompts système, un fichier par agent

`{agent}.system.md` est l'EXÉCUTABLE d'un agent : c'est ce fichier, et lui
seul, que le code charge au démarrage et dont le hash est épinglé dans l'IR
(`agents[].promptHash`). Aucun texte système ne vit dans le code
(`[PROMPT_INLINE_FORBIDDEN]`).

Owner : `dev-prompt` (PHASE 4). `dev-agent` le lit pour vérifier le hash, ne
l'écrit jamais (`[PROMPT_OWNERSHIP_VIOLATION]`). Sections lues par le lint :
`## Compétences` et `## Règles` citent, entre backticks, les slugs déclarés au
roster — leurs fragments détaillés vivent dans `../skills/` et `../rules/`.
