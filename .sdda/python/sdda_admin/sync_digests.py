#!/usr/bin/env python3
"""
Génère les digests de taxonomie par agent.

Chaque agent lit `.sdda/digests/error-classification.{agent}.md` en STEP
contexte : la tranche des classes qu'il peut réellement émettre, quelques
centaines d'octets au lieu des ~40 Ko de la taxonomie complète.

Le digest est dérivé de la fiche de l'agent elle-même : on extrait les `[CLASS]`
qu'elle mentionne. Une classe qu'un agent n'émet pas n'a rien à faire dans son
contexte, et une classe qu'il émet sans l'avoir dans son digest est une classe
qu'il aura inventée.

Usage :
    python .sdda/python/sdda_admin/sync_digests.py
    python .sdda/python/sdda_admin/sync_digests.py --check   # CI
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

SDDA = Path(__file__).resolve().parents[2]
AGENTS = SDDA / "agents"
DIGESTS = SDDA / "digests"
TAXONOMY = SDDA / "rules" / "error-classification.md"

# Cf. sync_error_registry.NOT_A_CLASS — préfixes de sortie chat, pas des classes.
NOT_A_CLASS = {
    "CLASS", "FAMILLE_SUJET_PROBLEME", "REDACTED", "AGENT", "ANALYSIS", "BOOTSTRAP",
    "BUILD", "CAPS", "EVAL", "MEMORY", "MISSION", "ORCHESTRATION", "PROMPT",
    "RETRIEVAL", "REVIEW", "SAFETY", "STATUS", "TESTS", "TOOL", "TOOLS",
    "TOPOLOGY", "TRACE",
}

# Classes que TOUT agent peut émettre, quel que soit son rôle.
UNIVERSAL = ["INVALID_ARG", "PACK_UNUSABLE", "STACK_MISSING"]

HEADER = """<!-- GÉNÉRÉ par sdda_admin/sync_digests.py — ne pas éditer à la main.
     Source : la fiche .sdda/agents/{agent}.md + rules/error-classification.md
     Régénérer : python .sdda/python/sdda_admin/sync_digests.py -->

# Digest — classes d'erreur de `{agent}`

Tranche de `@.sdda/rules/error-classification.md` réduite à ce que cet agent
peut émettre. **Aucun bloc ERROR sans préfixe `[CLASS]`.**

Format obligatoire (cf. `rules/output-protocol.md` §3) :

```
ERROR: <qui> — <quoi>
CAUSE: [CLASS] <le fait constaté, avec ses valeurs>
FIX: <l'action précise qui débloque>
```

## Classes propres à cet agent

{specific}

## Classes universelles

{universal}

---

Une classe absente de cette liste ne doit pas être inventée : elle ne serait
parsée par personne, donc ne déclencherait aucune reprise et n'apparaîtrait dans
aucun tableau de bord. Émettre avec la classe la plus proche et signaler le trou.
"""


def classes_of(agent_file: Path) -> list[str]:
    text = agent_file.read_text(encoding="utf-8")
    found = set(re.findall(r"\[([A-Z][A-Z0-9_]{2,})\]", text))
    return sorted(found - NOT_A_CLASS - set(UNIVERSAL))


def render(agent: str, specific: list[str]) -> str:
    def bullets(items: list[str]) -> str:
        if not items:
            return "_(aucune)_"
        return "\n".join(f"- `[{c}]`" for c in items)

    return HEADER.format(
        agent=agent,
        specific=bullets(specific),
        universal=bullets(UNIVERSAL),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Génère les digests de taxonomie par agent.")
    parser.add_argument("--check", action="store_true", help="CI : ne rien écrire, échouer si dérive")
    args = parser.parse_args()

    if not AGENTS.is_dir():
        print(f"ERROR: sync_digests — {AGENTS} absent")
        return 1
    if not TAXONOMY.is_file():
        print(f"ERROR: sync_digests — taxonomie absente : {TAXONOMY}")
        return 1

    DIGESTS.mkdir(exist_ok=True)

    drift: list[str] = []
    written = 0
    empty: list[str] = []

    for agent_file in sorted(AGENTS.glob("*.md")):
        agent = agent_file.stem
        specific = classes_of(agent_file)
        if not specific:
            empty.append(agent)
        content = render(agent, specific)
        target = DIGESTS / f"error-classification.{agent}.md"

        if args.check:
            current = target.read_text(encoding="utf-8") if target.is_file() else ""
            if current != content:
                drift.append(agent)
        else:
            target.write_text(content, encoding="utf-8")
            written += 1

    # Digests orphelins : un agent retiré laisse son digest derrière lui.
    known = {p.stem for p in AGENTS.glob("*.md")}
    orphans = [
        p for p in DIGESTS.glob("error-classification.*.md")
        if p.name[len("error-classification."):-3] not in known
    ]

    if args.check:
        if drift or orphans:
            print("ERROR: sync_digests — les digests ont dérivé")
            print(f"CAUSE: [DIGEST_DRIFT] {len(drift)} digest(s) périmé(s), "
                  f"{len(orphans)} orphelin(s)")
            if drift:
                print(f"       périmés   : {', '.join(drift[:10])}")
            if orphans:
                print(f"       orphelins : {', '.join(p.name for p in orphans[:10])}")
            print("FIX: python .sdda/python/sdda_admin/sync_digests.py")
            return 1
        print(f"  ok — {len(known)} digests à jour")
        return 0

    for path in orphans:
        path.unlink()

    print(f"  {written} digest(s) générés" + (f", {len(orphans)} orphelin(s) supprimé(s)" if orphans else ""))
    if empty:
        print(f"  note — aucune classe détectée pour : {', '.join(empty)}")
        print("         (un agent qui n'émet aucune erreur ne peut rien bloquer — à vérifier)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
