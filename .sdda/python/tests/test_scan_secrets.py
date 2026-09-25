"""scan_secrets — le fichier de secrets désigné n'est pas une fuite ; une expression de code n'est pas une valeur."""
from __future__ import annotations

from pathlib import Path

from sdda_scripts import scan_secrets

APP = "workspace/src/SupportAssistant"
KEY = "sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4"


def _put(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def _classes(root: Path, *targets: str) -> list[tuple[str, str, str]]:
    report = scan_secrets.run(root, targets=list(targets))
    return [(f.severity, f.cls, f.location or "") for f in report.findings if f.cls == "SECRET_LEAK"]


def test_the_designated_env_file_is_skipped_but_a_shipped_example_is_scanned(project: Path) -> None:
    """`workspace/src/{App}/.env` est l'emplacement DÉSIGNÉ des valeurs (ARCHITECTURE §2.ter)."""
    _put(project, f"{APP}/.env", f"LLM_API_KEY={KEY}\n")
    _put(project, f"{APP}/.env.local", f"LLM_API_KEY={KEY}\n")
    _put(project, f"{APP}/.env.example", f"LLM_API_KEY={KEY}\n")
    found = _classes(project, "workspace/src")
    locations = {loc for _, _, loc in found}
    assert not any(loc.endswith("/.env") or loc.endswith("/.env.local") for loc in locations), found
    assert any(loc.endswith("/.env.example") for loc in locations), "un exemple livré part dans le dépôt : il reste scanné"
    assert scan_secrets.is_env_file(Path(".env")) and scan_secrets.is_env_file(Path(".env.production"))
    assert not scan_secrets.is_env_file(Path(".env.example")) and not scan_secrets.is_env_file(Path("settings.env"))


def test_a_code_expression_is_not_a_secret_but_a_quoted_literal_still_is(project: Path) -> None:
    """Les lignes du squelette généré (`app/models.py`, `app/tracing.py`) rougissaient quatre fois par projet."""
    _put(project, f"{APP}/app/models.py",
         'client = Anthropic(api_key=settings.secret("llmApiKey").get_secret_value())\n'
         "token = os.environ[\"OTEL_TOKEN\"]\n"
         "password = config.db.password\n")
    _put(project, f"{APP}/app/literal.py", 'api_key = "hunter2hunter2hunter2"\n')
    found = _classes(project, "workspace/src")
    assert not any(loc.endswith("models.py") for _, _, loc in found), found
    assert any(loc.endswith("literal.py") and sev == "warn" for sev, _, loc in found)
    assert scan_secrets.is_code_expression('settings.secret("llmApiKey").get_secret_value()')
    assert scan_secrets.is_code_expression("os.environ[")
    assert not scan_secrets.is_code_expression("hunter2hunter2hunter2")
