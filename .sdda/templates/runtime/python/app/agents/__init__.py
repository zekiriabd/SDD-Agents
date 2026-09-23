"""agents/ — un paquet par agent du produit (`agents/{agent}/`), écrit par `dev-agent` en PHASE 4.

Chaque paquet expose `build(deps, bounds)` et lit SON prompt depuis
`../prompts/{agent}.system.md` (hash vérifié contre l'IR), ses outils depuis le
registre (moindre privilège : ceux que ses CAPs exigent, pas un de plus).
"""
