"""Le manifeste d'invariants est branché sur la réalité du disque.

Enforcer de la note d'en-tête d'`INVARIANTS.yml` : « si un enforcer disparaît
sans que l'invariant soit explicitement retiré, le smoke test de ce manifeste
échoue ». Sans ces tests, la phrase est une intention, et le manifeste devient
le premier endroit où le doc-theater s'installe — celui qui prétend justement
l'empêcher.

Quatre contrôles, du plus grave au plus formel :

1. **Tout enforcer `sdda_hooks/*` est câblé.** C'est le contrôle qui manquait :
   cinq hooks ont existé, testés et déclarés, sans qu'aucun chemin d'exécution
   ne les atteigne. Un enforcer présent mais non câblé rassure sans protéger.
2. **Tout enforcer non-hook existe, ou est déclaré planifié.** En phase de
   conception, une dette annoncée est légitime ; une dette muette ne l'est pas.
3. **Le compte déclaré est le compte réel.**
4. **Les identifiants sont uniques et kebab-case**, puisque les hooks et les
   rapports s'y réfèrent par chaîne.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SDDA = Path(__file__).resolve().parents[2]
ROOT = SDDA.parent
MANIFEST = SDDA / "INVARIANTS.yml"

ID_RE = re.compile(r"^\s*- id:\s*(\S+)\s*$", re.M)
ENFORCER_RE = re.compile(r"^\s+- (\.sdda/[\w\-./*{}]+\.py)\s*$", re.M)
KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@pytest.fixture(scope="module")
def manifest() -> str:
    assert MANIFEST.is_file(), "INVARIANTS.yml absent — le manifeste EST le contrat"
    return MANIFEST.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def enforcers(manifest: str) -> list[str]:
    found = sorted(set(ENFORCER_RE.findall(manifest)))
    assert found, "aucun enforcer déclaré — un manifeste sans enforcer est une liste de vœux"
    return found


def _planned_scripts() -> set[str]:
    """Les scripts que `docs/PLANNED-SCRIPTS.md` annonce comme à écrire.

    On lit l'inventaire généré plutôt que de re-scanner : deux implémentations
    d'un même comptage divergent, et c'est celle qui ne bloque pas qui survit.
    """
    doc = SDDA / "docs" / "PLANNED-SCRIPTS.md"
    if not doc.is_file():
        return set()
    return set(re.findall(r"`([\w\-]+\.py)`", doc.read_text(encoding="utf-8")))


def test_every_hook_enforcer_is_wired(enforcers: list[str]) -> None:
    """Un hook déclaré enforcer s'exécute réellement, ou l'invariant ment.

    Le contrôle porte sur la façade du harnais de référence : c'est le seul
    endroit où un hook devient exécutable au moment de l'action. Sur les autres
    harnais, l'invariant bascule en CI — `harness-impact.md` le dit — et ce
    déplacement est documenté, donc acceptable.
    """
    settings_path = ROOT / ".claude" / "settings.json"
    assert settings_path.is_file(), (
        ".claude/settings.json absent — aucun hook ne s'exécute. "
        "Lancer `python .sdda/python/sdda_admin/harness_build.py`"
    )
    settings = settings_path.read_text(encoding="utf-8")

    hooks = [e for e in enforcers if "/sdda_hooks/" in e]
    assert hooks, "aucun hook parmi les enforcers — attendu au moins un au niveau A"

    unwired = [h for h in hooks if h not in settings and Path(ROOT / h).is_file()]
    assert not unwired, (
        f"{len(unwired)} enforcer(s) de type hook présent(s) sur disque mais absent(s) de "
        f".claude/settings.json : {unwired}. Un enforcer non câblé rassure sans protéger — "
        "c'est exactement le doc-theater que ce manifeste existe pour empêcher. "
        "Corriger : déclarer son `WIRING` dans le module, puis `harness_build.py`."
    )


def test_every_wired_hook_declares_a_wiring() -> None:
    """Réciproque : un hook sur le disque sans `WIRING` n'est câblé nulle part.

    Sans ce test, ajouter un hook et oublier sa déclaration produit un fichier
    qui a l'air d'une protection, passe la revue, et ne s'exécute jamais.
    """
    hooks_dir = SDDA / "python" / "sdda_hooks"
    modules = sorted(p for p in hooks_dir.glob("*.py") if not p.stem.startswith("_"))
    assert modules, "aucun hook sur le disque"

    undeclared = [p.name for p in modules
                  if not re.search(r"^WIRING\s*=\s*\{", p.read_text(encoding="utf-8"), re.M)]
    assert not undeclared, (
        f"{len(undeclared)} hook(s) sans `WIRING` : {undeclared}. "
        "Déclarer `WIRING = {'event': …, 'matcher': …, 'applies_to': …}` "
        "(cf. sdda_hooks/_hook.py), sinon `harness_build.py` ne peut pas les câbler."
    )


def test_missing_enforcers_are_declared_planned(manifest: str, enforcers: list[str]) -> None:
    """Une dette d'enforcer est légitime si elle est annoncée, jamais si elle est muette."""
    status = re.search(r"^status:\s*(\S+)", manifest, re.M)
    design_phase = bool(status and "design" in status.group(1))

    missing = [e for e in enforcers if not (ROOT / e).is_file()]
    if not missing:
        return

    assert design_phase, (
        f"{len(missing)} enforcer(s) absent(s) hors phase de conception : {missing}. "
        "Un invariant sans enforcer n'est pas appliqué — le retirer du manifeste "
        "ou l'écrire, mais ne pas le laisser prétendre."
    )

    planned = _planned_scripts()
    undeclared = [e for e in missing if Path(e).name not in planned]
    assert not undeclared, (
        f"{len(undeclared)} enforcer(s) absent(s) ET absent(s) de docs/PLANNED-SCRIPTS.md : "
        f"{undeclared}. Régénérer l'inventaire : "
        "`python .sdda/python/sdda_admin/planned_scripts.py`"
    )


def test_declared_total_matches_reality(manifest: str) -> None:
    ids = ID_RE.findall(manifest)
    declared = re.search(r"^total:\s*(\d+)", manifest, re.M)
    assert declared, "`total:` absent — le compte doit être vérifiable, pas estimé"
    assert int(declared.group(1)) == len(ids), (
        f"`total: {declared.group(1)}` mais {len(ids)} invariants listés. "
        "Un compte faux dans le manifeste anti-pourrissement est le premier symptôme."
    )


def test_ids_are_unique_and_kebab_case(manifest: str) -> None:
    ids = ID_RE.findall(manifest)
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates, f"identifiants dupliqués : {duplicates} — les rapports s'y réfèrent par chaîne"

    malformed = [i for i in ids if not KEBAB_RE.match(i)]
    assert not malformed, f"identifiants hors kebab-case : {malformed}"


def test_every_invariant_names_a_principle(manifest: str) -> None:
    """Un invariant sans principe est une règle dont personne ne sait pourquoi elle existe.

    C'est ce qui la rend impossible à arbitrer le jour où elle gêne : sans le
    `pourquoi`, la seule question possible est « est-ce qu'on peut la couper ? ».
    """
    blocks = re.split(r"\n  - id: ", manifest)[1:]
    orphans = [b.split("\n")[0].strip() for b in blocks if not re.search(r"^\s+principle:\s*\S+", b, re.M)]
    assert not orphans, f"invariant(s) sans `principle:` : {orphans} — renvoyer vers PHILOSOPHY.md"
