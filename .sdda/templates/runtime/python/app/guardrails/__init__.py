"""Guardrails du produit — branchés où le texte non maîtrisé ENTRE et où la sortie SORT. GÉNÉRÉ.

Trois fiches de stack, trois modules, une façade :

    injection.py   `guardrails/injection-detection.md` — couche « motifs », scorée
    pii.py         `guardrails/pii-redaction.md`       — formats vérifiables, jetons typés
    schema.py      `guardrails/schema-validation.md`   — sortie contre l'`outputSchema` de l'IR

Chaque guardrail n'est actif que si SA fiche est active dans STACK.md
(`## Active Guardrails`) : `gen_app_skeleton` le résout et l'écrit dans
`app_config.json` (`guardrails.active`), que `Settings` lit. Rien ne s'active par
défaut dans le code — un guardrail qu'on n'a pas déclaré serait un guardrail
qu'aucune eval n'a mesuré, donc un comportement inconnu en production.

Où ils sont appliqués (`RunService` et `BoundedLoop`) :

  - **entrée utilisateur** (injection DIRECTE) : score au-dessus du seuil et
    `OnGuardrailTrip: block-and-log` -> le run est refusé, classe
    `SAFETY_GUARDRAIL_TRIPPED` (code de sortie 4 : un refus est le résultat
    ATTENDU d'une suite L8) ; `sanitize-and-continue` -> passages neutralisés ;
  - **sortie d'outil `untrusted`** (injection INDIRECTE, la voie qui compte) :
    neutralisée et poursuivie — bloquer la requête punirait l'utilisateur pour
    la faute d'un tiers (§4 de la fiche) ;
  - **PII** : rédigées avant que le texte n'atteigne le modèle — donc la trace
    et le fournisseur —, entrée comme sorties d'outils, de confiance ou non
    (une ligne de base est « de confiance » et porte quand même des PII) ;
  - **sortie finale** : validée contre son schéma ; non conforme ->
    `AGENT_OUTPUT_INVALID` (code 9).

Chaque déclenchement écrit un span `sdda.guardrail {nom}` : règles touchées,
score, action — **jamais le texte de l'attaque**, qui est lui-même hostile et
ne doit être réinjecté dans aucun contexte de modèle.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from .injection import InjectionDetector, Verdict
from .pii import PiiRedactor
from .schema import coerce_output, validate

INJECTION = "injection-detection"
PII = "pii-redaction"
SCHEMA = "schema-validation"

#: Politiques de `OnGuardrailTrip` (fiche injection-detection §4).
BLOCKING_POLICIES = ("block-and-log", "escalate-human")
POLICIES = (*BLOCKING_POLICIES, "sanitize-and-continue")


class GuardrailTripped(Exception):
    """Une entrée a déclenché un guardrail bloquant. Porte la classe que lit `exit_codes`."""

    cls = "SAFETY_GUARDRAIL_TRIPPED"

    def __init__(self, guardrail: str, verdict: Verdict) -> None:
        super().__init__(f"guardrail `{guardrail}` déclenché : score {verdict.score:.2f} >= "
                         f"{verdict.threshold:.2f} (règles {list(verdict.rules)})")
        self.guardrail = guardrail
        self.verdict = verdict


@dataclass
class Guardrails:
    """La configuration résolue des guardrails. `for_run()` en rend l'instance d'UN run (état PII neuf)."""

    active: tuple[str, ...] = ()
    input_guards: tuple[str, ...] = ()
    on_trip: str = "block-and-log"
    injection: InjectionDetector | None = None
    pii: PiiRedactor | None = None
    output_schema: Mapping[str, Any] | None = None
    output_schemas: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, raw: Mapping[str, Any] | None) -> "Guardrails":
        config = dict(raw or {})
        active = tuple(str(a) for a in config.get("active") or ())
        on_trip = str(config.get("onTrip") or "block-and-log")
        if on_trip not in POLICIES:
            raise ValueError(f"`OnGuardrailTrip: {on_trip}` hors liste {list(POLICIES)}")
        injection_cfg = config.get("injection")
        pii_cfg = config.get("pii")
        schemas = config.get("outputSchemas") or {}
        final = config.get("outputSchema")
        injection = None
        if INJECTION in active:
            injection = InjectionDetector.from_config(
                injection_cfg if isinstance(injection_cfg, Mapping) else None)
        pii = None
        if PII in active:
            pii = PiiRedactor.from_config(pii_cfg if isinstance(pii_cfg, Mapping) else None)
        validating = SCHEMA in active
        declared = {str(k): v for k, v in schemas.items() if isinstance(v, Mapping)}
        return cls(
            active=active,
            input_guards=tuple(str(g) for g in config.get("input") or ()),
            on_trip=on_trip,
            injection=injection,
            pii=pii,
            output_schema=final if validating and isinstance(final, Mapping) else None,
            output_schemas=declared if validating else {},
        )

    @classmethod
    def from_settings(cls, settings: Any) -> "Guardrails":
        return cls.from_config(getattr(settings, "guardrails", None))

    def for_run(self) -> "Guardrails":
        """Une copie pour UN run, avec une table de jetons PII neuve.

        La configuration est partagée, l'état ne l'est pas : `PiiRedactor`
        garde la correspondance jeton -> valeur, et une instance réutilisée d'un
        run à l'autre (d'un appelant à l'autre) accumulait les valeurs de tous.
        """
        pii = PiiRedactor(self.pii.categories) if self.pii is not None else None
        return replace(self, pii=pii)

    # -- Entrée ---------------------------------------------------------------
    def check_input(self, text: str, *, tracer: Any = None) -> tuple[str, Verdict | None]:
        """L'entrée utilisateur, après injection directe et PII. `GuardrailTripped` si bloquant."""
        verdict: Verdict | None = None
        if self.injection is not None and (not self.input_guards or INJECTION in self.input_guards):
            verdict = self.injection.scan(text)
            if verdict.hits:
                action = ("blocked" if verdict.tripped and self.on_trip in BLOCKING_POLICIES
                          else "sanitized" if verdict.tripped else "logged")
                _span(tracer, INJECTION,
                      {"sdda.guardrail.point": "user_input", "sdda.guardrail.action": action,
                       **_verdict_attrs(verdict)},
                      error=GuardrailTripped.cls if action == "blocked" else "")
                if action == "blocked":
                    raise GuardrailTripped(INJECTION, verdict)
                if action == "sanitized":
                    text = self.injection.neutralize(text, verdict)
        return self.redact(text, point="user_input", tracer=tracer), verdict

    def screen_untrusted(self, text: str, *, source: str, tracer: Any = None) -> str:
        """Texte d'un TIERS (outil, document) : neutralisé s'il instruit, rédigé s'il porte des PII.

        Jamais bloquant : un document suspect est la faute de son auteur, pas
        de l'utilisateur. Le passage est rendu inerte et le run continue.
        """
        if self.injection is not None:
            verdict = self.injection.scan(text)
            if verdict.hits:
                action = "sanitized" if verdict.tripped else "logged"
                _span(tracer, INJECTION,
                      {"sdda.guardrail.point": "untrusted_content", "sdda.guardrail.source": source,
                       "sdda.guardrail.action": action, **_verdict_attrs(verdict)})
                if verdict.tripped:
                    text = self.injection.neutralize(text, verdict)
        return self.redact(text, point=source, tracer=tracer)

    def redact(self, text: str, *, point: str = "prompt", tracer: Any = None) -> str:
        if self.pii is None:
            return text
        redacted, findings = self.pii.redact(text)
        if findings:
            _span(tracer, PII, {"sdda.guardrail.point": point, "sdda.guardrail.action": "redacted",
                                "sdda.guardrail.pii.categories": sorted({f.category for f in findings}),
                                "sdda.guardrail.pii.count": len(findings)})
        return redacted

    # -- Sortie ---------------------------------------------------------------
    def schema_for(self, agent_id: str = "") -> Mapping[str, Any] | None:
        return self.output_schemas.get(agent_id) or self.output_schema

    def check_output(self, output: Any, *, agent_id: str = "", tracer: Any = None) -> tuple[Any, list[str]]:
        """(sortie décodée, violations). Sans schéma déclaré : la sortie telle quelle, aucune violation."""
        schema = self.schema_for(agent_id)
        if schema is None:
            return output, []
        value = coerce_output(output)
        violations = validate(value, schema)
        if violations:
            _span(tracer, SCHEMA,
                  {"sdda.guardrail.point": "final_output", "sdda.guardrail.action": "blocked",
                   "sdda.guardrail.violations": violations[:10]},
                  error="AGENT_OUTPUT_INVALID")
        return value, violations


def _verdict_attrs(verdict: Verdict) -> dict[str, Any]:
    data = verdict.to_dict()
    return {"sdda.guardrail.score": data["score"], "sdda.guardrail.threshold": data["threshold"],
            "sdda.guardrail.rules": data["rules"], "sdda.guardrail.categories": data["categories"]}


def _span(tracer: Any, name: str, attributes: Mapping[str, Any], *, error: str = "") -> None:
    if tracer is None:
        return
    with tracer.span(f"sdda.guardrail {name}", attributes=dict(attributes)) as span:
        if error:
            span.error(error)


__all__: Sequence[str] = ("GuardrailTripped", "Guardrails", "InjectionDetector", "PiiRedactor", "Verdict",
                          "coerce_output", "validate")
