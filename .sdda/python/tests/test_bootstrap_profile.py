"""`bootstrap.py --profile` : le profil est écrit, et `poc` reçoit vraiment ses défauts.

Le profil est une couche de DÉFAUTS sous la couche projet ; `profiles/poc.yml`
demandait à l'utilisateur de retirer lui-même les `*SetMinItems` que le gabarit
écrit sous `## Active Eval Stack`, sans quoi un poc exigeait 50 items en
déclarant 15. Le bootstrap ignorait `Profile` : il ne pouvait donc ni l'écrire
ni omettre ces lignes.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bootstrap as bs  # noqa: E402
from sdda_lib.layered_config import read_layered_config  # noqa: E402

SCHEMA = ROOT / ".sdda" / "templates" / "project-config.schema.json"
MIN_ITEMS_RE = re.compile(r"^(Golden|Holdout|Adversarial|Calibration)SetMinItems:", re.M)


def _project(tmp_path: Path, text: str) -> Path:
    stack = tmp_path / "workspace" / "stack"
    stack.mkdir(parents=True)
    (stack / "STACK.md").write_text(text, encoding="utf-8")
    return tmp_path


def test_the_profiles_are_the_schema_enum() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert list(bs.PROFILES) == schema["properties"]["Profile"]["enum"]
    assert bs.DEFAULT_PROFILE == schema["properties"]["Profile"]["default"]


def test_the_default_is_standard_with_the_template_minimums(tmp_path: Path) -> None:
    text = bs.build_stack_md("Demo", bs.COMBOS["c1"], {})
    assert "Profile: standard" in text and "Profile: poc" not in text
    assert MIN_ITEMS_RE.search(text), "le gabarit standard garde ses minimums explicites"
    assert read_layered_config(_project(tmp_path, text)).get("GoldenSetMinItems") == 50


def test_poc_writes_the_profile_and_omits_the_minimums_so_the_profile_applies(tmp_path: Path) -> None:
    text = bs.build_stack_md("Demo", bs.COMBOS["c1"], {}, profile="poc")
    assert "Profile: poc" in text and "Profile: standard" not in text
    assert not MIN_ITEMS_RE.search(text), "une valeur écrite dans STACK.md l'emporterait sur profiles/poc.yml"
    cfg = read_layered_config(_project(tmp_path, text))
    assert cfg.get("Profile") == "poc"
    assert cfg.get("GoldenSetMinItems") == 15 and cfg.sources["GoldenSetMinItems"] == "profile"
    assert not any(f"{{{{{p}}}}}" in text for p in ("AppName", "Language"))


def test_poc_only_touches_the_eval_stack_section() -> None:
    standard = bs.build_stack_md("Demo", bs.COMBOS["c1"], {}).splitlines()
    poc = bs.build_stack_md("Demo", bs.COMBOS["c1"], {}, profile="poc").splitlines()
    removed = sorted(set(standard) - set(poc))
    assert removed == ["AdversarialSetMinItems: 25", "GoldenSetMinItems: 50", "HoldoutSetMinItems: 30",
                       "Profile: standard                   # poc | standard | production — `poc` : 2 agents de build au lieu de 8,"]


def test_an_unknown_profile_is_refused() -> None:
    with pytest.raises(SystemExit):
        bs.build_stack_md("Demo", bs.COMBOS["c1"], {}, profile="prod")


def test_the_cli_exposes_the_flag() -> None:
    source = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
    assert '"--profile"' in source and "SDDA_PROFILE" in source
