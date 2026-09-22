"""`tracing.redact` — une trace ne peut pas fuir un secret, ni par clé ni par valeur.

Les traces sont partagées pour déboguer ; c'est ce qui en fait un canal
d'exfiltration. Ces tests fixent la règle du module : normalisation des noms de
clés, liste noire + suffixes, liste blanche des `*_key` légitimes, et rédaction
par valeur avec les MÊMES motifs que `scan_secrets` — importés, pas recopiés.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sdda_lib import tracing
from sdda_lib.tracing import REDACTED, redact
from sdda_scripts import scan_secrets


# ---------------------------------------------------------------------------
# Par clé — liste noire, normalisation, suffixes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", [
    "api_key", "apikey", "password", "secret", "token", "authorization", "credential",
    "private_key",                                            # déjà couverts avant
    "access_token", "refresh_token", "client_secret", "x_api_key", "x-api-key",
    "cookie", "set_cookie", "session",                        # nouveaux
])
def test_each_forbidden_key_is_redacted(key: str) -> None:
    assert redact({key: "valeur-en-clair"}) == {key: REDACTED}


@pytest.mark.parametrize("variant", [
    "API_KEY", "Api-Key", "apiKey", "ApiKey", "APIKey", "X-API-Key", "x-Api-Key",
    "accessToken", "AccessToken", "access-token", "ACCESS_TOKEN",
    "clientSecret", "Set-Cookie", "SET_COOKIE", "Authorization", "OAuthToken",
])
def test_key_matching_survives_case_dash_and_camel_case(variant: str) -> None:
    """`apiKey`, `Api-Key`, `API_KEY` désignent la même chose ; la casse d'un
    en-tête HTTP ou le style d'un SDK ne doivent pas ouvrir une brèche."""
    assert redact({variant: "v"}) == {variant: REDACTED}


@pytest.mark.parametrize("key", [
    "db_password", "smtp_passwd", "signing_key", "webhook_secret", "session_token",
    "sessionToken", "id_token", "hmac_key", "encryptionKey", "LlmApiKey",
])
def test_suffix_rule_catches_names_absent_from_the_black_list(key: str) -> None:
    assert redact({key: "v"}) == {key: REDACTED}


@pytest.mark.parametrize("key", [
    "primary_key", "primaryKey", "foreign_key", "partition_key", "sort_key", "by_key",
    "config_key", "configKey", "norm_key", "ir_key", "prompt_key", "contract_key",
    "metric_key", "item_key", "source_key", "stack_key", "requirement_key",
    "cache_key", "idempotency_key", "routing_key", "public_key",
])
def test_allow_listed_keys_are_left_untouched(key: str) -> None:
    """Une *clé* au sens identifiant n'est pas un secret : la rédiger casserait
    la lecture des traces sans protéger quoi que ce soit."""
    assert redact({key: "customer_id"}) == {key: "customer_id"}


@pytest.mark.parametrize("key", [
    "key_env", "keyEnv", "access_key_env", "secret_key_env",  # NOMS de variables d'env
    "topK", "top_k", "stack_keys", "requirementKeys",         # ne finissent pas par `_key`
    "tokensIn", "tokensOut", "maxTokens", "token_count",      # des comptes, pas des jetons
    "session_id", "sessionId", "customer_id", "tool", "model",
])
def test_near_misses_are_not_redacted(key: str) -> None:
    assert redact({key: "CRM_API_KEY"}) == {key: "CRM_API_KEY"}


def test_every_allow_listed_name_would_otherwise_be_caught_by_the_suffix_rule() -> None:
    """Une entrée de `SAFE_KEYS` qui ne se termine par aucun suffixe surveillé
    est du bruit : elle laisse croire à une exception qui n'en est pas une."""
    for name in tracing.SAFE_KEYS:
        assert name.endswith(tracing.SECRET_KEY_SUFFIXES), name
        assert name not in tracing.FORBIDDEN_KEYS, name


def test_nested_dicts_and_lists_are_walked() -> None:
    payload = {
        "arguments": {"customer_id": "CUS-1", "headers": [{"Authorization": "Bearer x"},
                                                           {"X-Trace": "t-1"}]},
        "steps": [[{"password": "p"}], {"deep": {"refresh_token": "r", "ok": True}}],
    }
    assert redact(payload) == {
        "arguments": {"customer_id": "CUS-1", "headers": [{"Authorization": REDACTED},
                                                           {"X-Trace": "t-1"}]},
        "steps": [[{"password": REDACTED}], {"deep": {"refresh_token": REDACTED, "ok": True}}],
    }


# ---------------------------------------------------------------------------
# Par valeur — les motifs de scan_secrets, importés
# ---------------------------------------------------------------------------
#: Un échantillon par motif de `scan_secrets.PREFIXED`, avec la portion qui ne
#: doit plus apparaître. Le test de couverture ci-dessous refuse qu'un motif
#: ajouté au scan reste sans échantillon ici.
SAMPLES: dict[str, tuple[str, str]] = {
    "clé OpenAI": ("sk-abcdefghijklmnopqrstuvwx", "abcdefghijklmnopqrstuvwx"),
    "token Slack": ("xoxb-1234567890-abcdefghij", "1234567890-abcdefghij"),
    "token GitHub": ("ghp_0123456789abcdefghijklmnopqrstuvwx", "0123456789abcdefghij"),
    "PAT GitHub": ("github_pat_0123456789abcdefghijklmnopqrstuvwx", "0123456789abcdefghij"),
    "clé AWS": ("AKIAIOSFODNN7EXAMPLE", "IOSFODNN7EXAMPLE"),
    "clé Google": ("AIzaSyA1234567890abcdefghijklmnopqrstuv", "SyA1234567890abcdefghij"),
    "token GitLab": ("glpat-abcdefghijklmnopqrst", "abcdefghijklmnopqrst"),
    "JWT": ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij", "eyJzdWIiOiIxMjM0NTY3ODkwIn0"),
    "clé privée": ("-----BEGIN RSA PRIVATE KEY-----", "BEGIN RSA PRIVATE KEY"),
    "URL avec identifiants": ("postgres://app:s3cr3tp4ssw0rd@db.internal:5432/prod", "s3cr3tp4ssw0rd"),
}


def test_every_scan_secrets_pattern_has_a_sample_here() -> None:
    assert set(SAMPLES) == set(scan_secrets.PREFIXED)


@pytest.mark.parametrize("label", sorted(SAMPLES))
def test_each_imported_pattern_is_redacted_inside_a_longer_string(label: str) -> None:
    sample, core = SAMPLES[label]
    out = redact(f"appel refusé pour {sample} — réessayer")
    assert core not in out
    assert REDACTED in out
    assert out.startswith("appel refusé pour ") and out.endswith(" — réessayer")


def test_only_the_matched_span_is_replaced() -> None:
    out = redact("postgres://app:s3cr3tp4ssw0rd@db.internal:5432/prod")
    assert out == f"{REDACTED}db.internal:5432/prod"


def test_strings_are_redacted_at_any_depth_under_a_harmless_key() -> None:
    """La clé est innocente (`note`, `stdout`), la valeur ne l'est pas."""
    payload = {"note": "voir sk-abcdefghijklmnopqrstuvwx", "stdout": ["ok", "clé AKIAIOSFODNN7EXAMPLE"]}
    out = redact(payload)
    assert out == {"note": f"voir {REDACTED}", "stdout": ["ok", f"clé {REDACTED}"]}


def test_literal_assignment_in_a_string_is_redacted_but_placeholders_survive() -> None:
    assert redact("token=abcdefghijklmnop suite") == f"token={REDACTED} suite"
    assert redact('password: "s3cr3tp4ssw0rd!"') == f'password: "{REDACTED}"'
    assert redact("key_env: CRM_API_KEY") == "key_env: CRM_API_KEY"      # un NOM de variable
    assert redact("api_key: ${CRM_API_KEY}") == "api_key: ${CRM_API_KEY}"
    assert redact("password: <à compléter>") == "password: <à compléter>"


# ---------------------------------------------------------------------------
# Bearer / Basic
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("header", [
    "Bearer eyJhbGciOiJIUzI1NiJ9.abc.def",
    "Bearer sk_live_ABCDEFGHijklmnop",
    "bearer abcdef1234567890",
    "Basic YXBwOnMzY3IzdHA0c3N3MHJk",
    "Basic dXNlcjpwYXNz",
])
def test_bearer_and_basic_tokens_are_redacted_but_the_scheme_stays(header: str) -> None:
    scheme, token = header.split(" ", 1)
    out = redact(f"Authorization header sent: {header}.")
    assert token not in out
    assert out == f"Authorization header sent: {scheme} {REDACTED}."


@pytest.mark.parametrize("prose", [
    "Basic authentication failed",
    "Bearer token expired",
    "the Basic understanding of retrieval",
])
def test_bearer_and_basic_in_prose_are_left_alone(prose: str) -> None:
    assert redact(prose) == prose


# ---------------------------------------------------------------------------
# Pureté, types non textuels, écriture bout en bout
# ---------------------------------------------------------------------------
def test_non_string_values_are_untouched() -> None:
    payload = {"tokensIn": 900, "costUsd": 0.012, "ok": True, "n": None, "ratio": 1.5,
               "ids": [1, 2, 3], "pair": (4, "sk-abcdefghijklmnopqrstuvwx")}
    out = redact(payload)
    assert out["tokensIn"] == 900 and out["costUsd"] == 0.012 and out["ok"] is True
    assert out["n"] is None and out["ratio"] == 1.5 and out["ids"] == [1, 2, 3]
    assert out["pair"] == (4, REDACTED)


def test_redact_is_pure_and_deterministic() -> None:
    payload = {"api_key": "k", "note": "sk-abcdefghijklmnopqrstuvwx", "nested": [{"password": "p"}]}
    snapshot = json.dumps(payload, sort_keys=True)
    first, second = redact(payload), redact(payload)
    assert first == second
    assert json.dumps(payload, sort_keys=True) == snapshot          # l'entrée n'est pas mutée
    assert redact(first) == first                                    # idempotent


def test_a_clean_payload_comes_back_identical() -> None:
    payload = {"tool": "invoice_lookup", "sideEffectClass": "read-only", "ok": True,
               "arguments": {"customer_id": "CUS-1", "topK": 5, "key_env": "CRM_API_KEY"}}
    assert redact(payload) == payload


def test_trace_writer_writes_a_redacted_line(tmp_path: Path) -> None:
    """De bout en bout : ce qui touche le disque ne contient aucun des secrets."""
    writer = tracing.TraceWriter(tmp_path, "run-redact")
    writer.emit(
        "tool_call", "2026-09-22T10:00:00Z", tool="crm_lookup", sideEffectClass="read-only",
        ok=True,
        arguments={"accessToken": "tok-live-4f8a2b91", "customer_id": "CUS-1",
                   "headers": {"X-API-Key": "k-1234", "Authorization": "Bearer abc123def456"}},
        stderr="connexion à postgres://app:s3cr3tp4ssw0rd@db.internal/prod refusée ; "
               "retry avec sk-abcdefghijklmnopqrstuvwx",
    )
    raw = writer.path.read_text(encoding="utf-8")
    for secret in ("tok-live-4f8a2b91", "k-1234", "abc123def456", "s3cr3tp4ssw0rd",
                   "sk-abcdefghijklmnopqrstuvwx"):
        assert secret not in raw
    assert "CUS-1" in raw and "crm_lookup" in raw and REDACTED in raw

    (event,) = list(tracing.read_events(writer.path))
    assert event["arguments"]["customer_id"] == "CUS-1"
    assert event["arguments"]["accessToken"] == REDACTED
    assert event["arguments"]["headers"] == {"X-API-Key": REDACTED, "Authorization": REDACTED}
    assert event["stderr"] == (f"connexion à {REDACTED}db.internal/prod refusée ; retry avec {REDACTED}")

    # Et le scan de G7, sur cette trace, ne trouve rien : les deux reconnaissent les mêmes formes.
    assert not scan_secrets.run(tmp_path, targets=["workspace/traces"]).errors
