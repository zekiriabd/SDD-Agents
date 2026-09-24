#!/usr/bin/env python3
"""TOOL GATE (G3) — part `contracts` : le contrat, le schéma, la sûreté, le code.

Ce que cette gate défend : *un outil se câble à un agent quand son contrat est
tenu, pas quand il existe.* Un outil dont la description ment se présente en
aval comme « le superviseur route mal » ; un outil non idempotent avec des
retries crée trois tickets pour une seule demande, et le symptôme apparaît chez
le client, pas dans les tests.

G3 est composite (`gate_reports.GATE_PARTS`). Ce script écrit la part
`contracts` — tout ce qui se vérifie **sans exécuter l'outil** :

    1. schémas d'entrée/sortie conformes au méta-schéma `tool-schema.schema.json`,
       `required` ⊆ `properties`, descriptions de paramètres présentes
    2. suite de contrat L2 déclarée ET présente sur disque (`contractTestsRef`)
    3. `sideEffectClass` identique entre le contrat Markdown, l'IR et le code
    4. stratégie de sûreté déclarée et cohérente pour tout outil non read-only
    5. enveloppe DB présente et bornée pour chaque `dataAccess[]`
    6. `toolSchemaHash` calculé et épinglé dans le rapport (P10)

La part `suites` — les tests L2 réellement exécutés — est écrite par
`eval_runner.py`. La séparation n'est pas cosmétique : ce script ne lance rien,
donc il tourne partout, tout le temps, y compris avant que le code existe.
Fusionner les deux rendrait la moitié statique otage d'un environnement
exécutable, et une gate qu'on ne peut pas jouer est une gate qu'on saute.

Usage :
    python .sdda/sdda.py validate-tool-contract --mission 1 --json
    python .sdda/sdda.py validate-tool-contract --mission 1 --require-code     # après génération
    python .sdda/sdda.py validate-tool-contract --mission 1 --tool 1-invoice-lookup
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdda_lib import hashing, markdown_io, paths  # noqa: E402
from sdda_lib.errors import Report  # noqa: E402
from sdda_lib.gate_reports import append_bypass_audit, write_gate_report  # noqa: E402
from sdda_lib.jsonschema_mini import SchemaValidator  # noqa: E402
from sdda_lib.layered_config import read_project_section  # noqa: E402
from sdda_scripts import ir_compiler  # noqa: E402
from sdda_scripts._common import add_common_args, ensure_utf8_stdout, finish, load_config, resolve_root  # noqa: E402

BYPASS_ENV = "SDDA_BYPASS_TOOL_GATE"

#: Ce que le bypass ne couvre JAMAIS. Un outil destructif sans stratégie ne se
#: câble pas, quelle que soit l'urgence : c'est la seule règle de cette gate
#: qu'aucune variable d'environnement ne desserre.
BYPASS_NEVER = ("SIDE_EFFECT_UNDECLARED", "SAFETY_STRATEGY_MISSING", "TOOL_RETRY_UNSAFE")

#: Effets de bord ordonnés du plus inoffensif au plus dangereux.
SIDE_EFFECTS = ("read-only", "idempotent-write", "external-side-effect", "write-destructive")

#: Dans le code généré, la classe d'effet de bord se déclare par l'un de ces
#: marqueurs. Plusieurs formes acceptées : le générateur n'est pas encore écrit,
#: et figer une seule syntaxe maintenant reviendrait à la décider ici.
_CODE_SIDE_EFFECT_RE = re.compile(
    r"""(?:side[_-]?effect(?:[_-]?class)?)\s*[:=]\s*["']?([a-z][a-z-]+)["']?""", re.I)


def meta_schema_path(root: Path) -> Path:
    local = root / ".sdda" / "templates" / "tool-schema.schema.json"
    return local if local.is_file() else paths.FRAMEWORK_SDDA_DIR / "templates" / "tool-schema.schema.json"


# ---------------------------------------------------------------------------
# Contrôles par outil
# ---------------------------------------------------------------------------
@dataclass
class ToolCheck:
    tool_id: str
    name: str
    side_effect: str
    verdict: str = "green"
    schema_hash: str = ""
    contract_tests: str = ""
    code_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "toolId": self.tool_id, "name": self.name, "sideEffectClass": self.side_effect,
            "verdict": self.verdict, "toolSchemaHash": self.schema_hash,
            "contractTestsRef": self.contract_tests, "codeFiles": self.code_files, "notes": self.notes,
        }

    def render_line(self) -> str:
        glyph = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[self.verdict]
        return f"{glyph} {self.tool_id} ({self.side_effect}) — {'; '.join(self.notes) if self.notes else 'contrat tenu'}"


def check_schemas(tool: dict[str, Any], validator: SchemaValidator, report: Report, loc: str) -> None:
    """Méta-schéma, `required` ⊆ `properties`, et descriptions de paramètres."""
    tid = str(tool.get("id"))
    for key in ("inputSchema", "outputSchema"):
        schema = tool.get(key)
        if not isinstance(schema, dict):
            report.error("TOOL_SCHEMA_INVALID", f"outil `{tid}` : `{key}` absent", "écrire les deux schémas dans `## 2. Schémas` du contrat", loc)
            continue
        for violation in validator.validate(schema):
            report.error("TOOL_SCHEMA_INVALID", f"outil `{tid}` : {key} — {violation}",
                         "corriger le schéma dans le contrat puis recompiler l'IR", loc)
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in properties:
                report.error("TOOL_SCHEMA_INVALID", f"outil `{tid}` : {key} exige `{name}`, qui n'est pas dans `properties`",
                             "un `required` fantôme ne se voit qu'au premier appel réel", loc)
        if key == "inputSchema":
            vague = sorted(n for n, spec in properties.items()
                           if not isinstance(spec, dict) or len(str(spec.get("description", ""))) < 10)
            if vague:
                report.warn("TOOL_DESCRIPTION_VAGUE", f"outil `{tid}` : paramètre(s) sans description exploitable — {vague}",
                            "le modèle remplit ces champs d'après leur description ; sans elle, il remplit au jugé", loc)


def check_contract_tests(root: Path, tool: dict[str, Any], ir: dict[str, Any], report: Report, loc: str,
                         *, static: bool = False) -> str:
    """La suite L2 est-elle déclarée, présente, et rattachée à une suite de l'IR ?

    En mode `static` (PHASE 2, juste après les architectes), la suite est
    exigée DÉCLARÉE mais pas PRÉSENTE : `workspace/pipeline/suites/**` appartient
    à `qa-evals` et `qa-tests`, qui n'écrivent qu'en PHASE 6. Exiger le fichier
    ici rendait le post-step de `/sdda-topology` rouge sur tout contrat
    correct — 21 fois sur 21 au premier run réel — et apprenait au lecteur que
    ce rouge-là n'est pas grave.
    """
    tid = str(tool.get("id"))
    ref = str(tool.get("contractTestsRef") or "")
    if not ref:
        report.error("TOOL_CONTRACT_FAILED", f"outil `{tid}` : aucune suite de contrat déclarée (`contractTestsRef`)",
                     "écrire `## 8. Tests de contrat (L2)` dans le contrat : happy path, chaque erreur déclarée, timeout, auth KO", loc)
        return ""
    if not (root / ref).is_file():
        if static:
            return ref     # attendue en PHASE 6 ; G3 (sur l'IR, sans --static) l'exigera
        report.error("TOOL_CONTRACT_FAILED", f"outil `{tid}` : suite `{ref}` déclarée mais absente du disque",
                     "produire la suite de contrat, ou corriger la référence du contrat", ref)
        return ref
    suites = [s for s in (ir.get("evaluation") or {}).get("suites") or [] if str(s.get("level")) == "L2"]
    if suites and not any(ref in str(s.get("dataset", "")) or tid in str(s.get("id", "")) for s in suites):
        report.warn("TOOL_CONTRACT_INCONSISTENT", f"outil `{tid}` : aucune suite L2 de l'IR ne le désigne — ses tests existent mais ne seront pas joués par la gate",
                    "déclarer la suite L2 dans les évaluations de la MISSION (part `suites` de G3)", ref)
    return ref


def check_safety(tool: dict[str, Any], report: Report, loc: str) -> None:
    """Stratégie de sûreté d'un outil non read-only — déclarée ET cohérente.

    `validate_ir.py` vérifie déjà la PRÉSENCE de `safetyStrategy`. Ce qui se
    joue ici est sa cohérence interne : une clé d'idempotence absente avec des
    retries, une confirmation `never` sur un outil destructif, un plafond
    manquant là où l'outil consomme un quota.
    """
    tid = str(tool.get("id"))
    cls = str(tool.get("sideEffectClass") or "")
    if cls == "read-only":
        return
    strategy = tool.get("safetyStrategy") or {}
    if not strategy:
        report.error("SAFETY_STRATEGY_MISSING", f"outil `{tid}` ({cls}) : `## 3. Stratégie de sûreté` vide ou non renseignée",
                     "déclarer idempotence, dry-run, confirmation, plafond, allowlist — un effet de bord non borné est un incident en attente", loc)
        return

    idempotency = str(strategy.get("idempotency") or "").strip().lower()
    retry = str(tool.get("retryPolicy") or "").strip().lower()
    if idempotency in ("", "none") and retry not in ("", "none"):
        report.error("TOOL_RETRY_UNSAFE", f"outil `{tid}` : `retryPolicy: {retry}` sans clé d'idempotence",
                     "un retry sur un outil non idempotent crée trois tickets pour une demande : `retry_policy: none`, ou déclarer une clé naturelle", loc)
    confirmation = str(strategy.get("confirmation") or "").strip().lower()
    if cls == "write-destructive" and confirmation in ("", "never"):
        report.error("SAFETY_STRATEGY_MISSING", f"outil `{tid}` (write-destructive) : `Confirmation: {confirmation or '<absente>'}`",
                     "un outil destructif demande confirmation (`always` ou `required-above:{seuil}`) — c'est la borne qui rend l'erreur récupérable", loc)
    if cls in ("external-side-effect", "write-destructive") and not strategy.get("dryRunSupported"):
        report.warn("SAFETY_STRATEGY_MISSING", f"outil `{tid}` ({cls}) : pas de dry-run — la revue adversariale ne pourra pas l'observer sans effet réel",
                    "supporter un mode dry-run, ou assumer que l'étage C testera en produisant de vrais effets", loc)


def check_code(root: Path, tool: dict[str, Any], report: Report, loc: str, *, require_code: bool) -> list[str]:
    """La classe d'effet de bord du CODE correspond-elle au contrat ?

    Le contrat peut dire `read-only` et le code écrire : c'est cet écart-là qui
    rend une revue de sécurité décorative, et il ne se voit qu'en confrontant
    les deux.
    """
    tid, name = str(tool.get("id")), str(tool.get("name") or "")
    declared = str(tool.get("sideEffectClass") or "")
    # Le code des outils vit dans le paquet de l'application (`workspace/src/{App}/tools/`
    # et `data/tools/` pour les outils générés depuis les sources), pas dans un
    # `workspace/src/tools/` à plat qu'aucun générateur n'a jamais écrit.
    app = str(read_project_section(root).get("AppName") or "").strip()
    directory = paths.app_src_root(root, app) if app else paths.workspace(root) / "src"
    if not directory.is_dir():
        if require_code:
            report.error("TOOL_CONTRACT_INCONSISTENT", f"outil `{tid}` : aucun code sous {paths.rel(root, directory)}/",
                         "générer le socle (/sdda-build {n} --layer socle) avant de rejouer G3 en --require-code", loc)
        return []

    files = [p for p in sorted(directory.rglob("*.py"))
             if name and "tools" in p.relative_to(directory).parts and "tests" not in p.parts
             and name in markdown_io.read_text(p)]
    if not files:
        if require_code:
            report.error("TOOL_CONTRACT_INCONSISTENT", f"outil `{tid}` : aucun fichier d'outil sous `{paths.rel(root, directory)}/` ne mentionne `{name}`",
                         "l'outil du contrat n'a pas d'implémentation : générer le socle, ou retirer le contrat", loc)
        return []

    rels = [paths.rel(root, p) for p in files]
    found: set[str] = set()
    for path in files:
        for match in _CODE_SIDE_EFFECT_RE.findall(markdown_io.read_text(path)):
            if match.lower() in SIDE_EFFECTS:
                found.add(match.lower())
    if not found:
        report.warn("TOOL_CONTRACT_INCONSISTENT", f"outil `{tid}` : le code ne déclare aucune classe d'effet de bord — rien ne peut être confronté au contrat",
                    "déclarer `side_effect_class = \"…\"` près de l'outil généré", rels[0])
    elif declared and declared not in found:
        report.error("TOOL_CONTRACT_INCONSISTENT", f"outil `{tid}` : contrat `{declared}`, code {sorted(found)}",
                     "le contrat fait foi : corriger le code, ou corriger le contrat en connaissance de cause (P8)", rels[0])
    return rels


def check_envelopes(root: Path, ir: dict[str, Any], report: Report, loc: str) -> list[dict[str, Any]]:
    """Enveloppe DB de chaque `dataAccess[]` — l'invariant `db-safety-envelope-present`."""
    out: list[dict[str, Any]] = []
    for entry in ir.get("dataAccess") or []:
        did = str(entry.get("id"))
        envelope = entry.get("envelope") or {}
        missing = [k for k in ("role", "statementTimeoutMs", "maxRows", "schemas") if not envelope.get(k)]
        if missing:
            report.error("DB_ENVELOPE_MISSING", f"accès données `{did}` : enveloppe incomplète — {missing} absent(s)",
                         "déclarer rôle, timeout, plafond de lignes et schémas autorisés : une requête sans bornes finit par ramener la table entière", loc)
        role = str(envelope.get("role") or "")
        if role == "full":
            report.error("DATA_ACCESS_ADR_REQUIRED", f"accès données `{did}` : `role: full` — un agent avec les pleins droits SQL n'est pas une stratégie d'accès",
                         "passer en `readonly` ou `scoped-write`, ou porter un ADR explicite qui assume le risque", loc)
        elif role == "scoped-write" and not envelope.get("forbidden"):
            report.warn("DB_ENVELOPE_MISSING", f"accès données `{did}` : `scoped-write` sans liste d'instructions interdites",
                        "lister au minimum DROP, TRUNCATE, ALTER : ce qui n'est pas interdit sera un jour émis", loc)
        out.append({"id": did, "role": role, "strategy": (entry.get("binding") or {}).get("strategy")})
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def tool_schema_hash(tool: dict[str, Any]) -> str:
    """Empreinte des schémas d'un outil — dimension du tuple d'épinglage (P10)."""
    return hashing.sha256_struct({"input": tool.get("inputSchema"), "output": tool.get("outputSchema")})


def contract_path(root: Path, tool_id: str) -> Path:
    return paths.contracts_dir(root, "tools") / f"{tool_id}.tool.md"


def validate_tools(
    root: Path,
    ir: dict[str, Any],
    *,
    report: Report,
    only: set[str] | None = None,
    require_code: bool = False,
    static: bool = False,
) -> list[ToolCheck]:
    validator = SchemaValidator(json.loads(markdown_io.read_text(meta_schema_path(root))))
    checks: list[ToolCheck] = []

    for tool in ir.get("tools") or []:
        tid = str(tool.get("id"))
        if only and tid not in only:
            continue
        path = contract_path(root, tid)
        loc = paths.rel(root, path) if path.is_file() else tid
        before = len(report.findings)

        check_schemas(tool, validator, report, loc)
        tests_ref = check_contract_tests(root, tool, ir, report, loc, static=static)
        check_safety(tool, report, loc)
        code_files = check_code(root, tool, report, loc, require_code=require_code)

        new = report.findings[before:]
        verdict = "red" if any(f.severity == "error" for f in new) else ("yellow" if new else "green")
        checks.append(ToolCheck(
            tool_id=tid, name=str(tool.get("name") or ""), side_effect=str(tool.get("sideEffectClass") or ""),
            verdict=verdict, schema_hash=tool_schema_hash(tool), contract_tests=tests_ref,
            code_files=code_files, notes=[f.message.split(" : ", 1)[-1] for f in new],
        ))
    return checks


def pinned_hashes(root: Path, ir: dict[str, Any], check: ToolCheck) -> dict[str, str]:
    """Ce qui rend ce rapport périmé : le schéma de l'outil, son contrat, l'IR."""
    pins = {"toolSchema": check.schema_hash, "ir": ir_compiler.ir_identity_hash(ir)}
    path = contract_path(root, check.tool_id)
    if path.is_file():
        pins["contract"] = hashing.sha256_file(path)
    return dict(sorted(pins.items()))


def run(
    root: Path,
    ir: dict[str, Any],
    *,
    report: Report,
    only: set[str] | None = None,
    require_code: bool = False,
    write_report: bool = True,
    static: bool = False,
) -> dict[str, Any]:
    mid = str(ir.get("missionId") or "")
    checks = validate_tools(root, ir, report=report, only=only, require_code=require_code, static=static)
    envelopes = check_envelopes(root, ir, report, mid or str(root))

    if not (ir.get("tools") or []):
        # Une MISSION sans outil est une MISSION sans TOOL GATE à franchir — et
        # le dire vaut mieux qu'un vert qui laisserait croire qu'on a vérifié.
        report.data["applicable"] = False

    verdict = "green"
    for check in checks:
        if check.verdict == "red":
            verdict = "red"
        elif check.verdict == "yellow" and verdict == "green":
            verdict = "yellow"
    if report.errors:
        verdict = "red"

    bypassed = False
    import os

    if os.environ.get(BYPASS_ENV, "") == "1" and report.errors:
        blocking = [f for f in report.errors if f.cls in BYPASS_NEVER]
        bypassed = True
        reason = os.environ.get("SDDA_BYPASS_REASON", "")
        append_bypass_audit(root, "G3", reason or f"{BYPASS_ENV}=1 sans raison déclarée")
        for finding in report.errors:
            if finding.cls not in BYPASS_NEVER:
                finding.severity = "warn"
        if blocking:
            report.warn("BYPASS_REASON_MISSING" if not reason else "SAFETY_STRATEGY_MISSING",
                        f"{BYPASS_ENV}=1 ne couvre pas {sorted({f.cls for f in blocking})} : un outil destructif sans stratégie ne se câble pas",
                        "corriger le contrat — cette part de G3 n'a pas de bypass", mid)

    payload = {
        "missionId": mid,
        "part": "contracts",
        "verdict": verdict,
        "bypassed": bypassed,
        "tools": [c.to_dict() for c in checks],
        "dataAccess": envelopes,
        "findings": {"errors": [f.to_dict() for f in report.errors], "warnings": [f.to_dict() for f in report.warnings]},
    }
    if write_report and mid:
        # UN rapport PAR OUTIL : `compute_status` lit `G3-{outil}` (LIFECYCLE).
        # Un rapport unique par MISSION serait vert dans un fichier que la
        # machine à états n'ouvre jamais.
        written: dict[str, str] = {}
        for check in checks:
            sub = Report(name="G3.contracts", target=check.tool_id, data={"verdict": check.verdict, "notes": check.notes})
            for finding in report.findings:
                if finding.location and check.tool_id in finding.location:
                    sub.findings.append(finding)
            path = write_gate_report(root, "G3", check.tool_id, sub, pinned_hashes(root, ir, check), part="contracts")
            written[check.tool_id] = paths.rel(root, path)
        payload["written"] = written
    report.data.update({"verdict": verdict, "tools": len(checks), "bypassed": bypassed,
                        "lines": [c.render_line() for c in checks]})
    return payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="TOOL GATE (G3), part `contracts` : schémas, sûreté, cohérence code/contrat — 0 exécution, 0 token")
    p.add_argument("--mission", type=int, default=None, help="numéro de mission ; défaut : l'unique IR compilé")
    p.add_argument("--ir", type=Path, default=None, help="fichier IR explicite")
    p.add_argument("--tool", action="append", default=None, help="identifiant(s) d'outil, répétable ou séparés par des virgules")
    p.add_argument("--require-code", action="store_true", help="exiger que le code de l'outil existe (après génération du socle)")
    p.add_argument("--static", action="store_true",
                   help="valider les contrats Markdown SANS IR compilé : schémas, sûreté, effets de bord. "
                        "C'est l'état de la PHASE 2, juste après les architectes et avant la compilation")
    add_common_args(p)
    return p


def static_ir(root: Path, mission: int | None, report: Report) -> dict[str, Any] | None:
    """Un IR partiel, compilé en mémoire depuis les seuls contrats d'outils.

    `/sdda-topology` veut refuser un contrat d'outil fautif AVANT de compiler
    l'IR — « on ne compile pas un IR depuis des contrats qu'on sait invalides ».
    Mais ce script travaille sur l'IR, donc exigeait ce qui n'existe pas encore :
    l'option que la commande passait depuis toujours n'était pas implémentée, et
    le post-step rendait une erreur d'argument au lieu d'un verdict. Un contrôle
    qui échoue sur sa propre ligne de commande ne protège rien, et il apprend à
    l'agent qui le lit que cette sortie-là n'est pas grave.

    On réutilise `ir_compiler.compile_tool` plutôt que de reparser : deux
    lectures du même Markdown divergeraient, et c'est celle que personne ne
    relit qui gouvernerait le verdict.
    """
    numbers = [mission] if mission is not None else sorted(
        {int(m.group(1)) for p in paths.contracts_dir(root, "tools").glob("*.tool.md")
         if (m := re.match(r"^(\d+)-", p.name))})
    tools: list[dict[str, Any]] = []
    compile_report = Report(name="G3.static", target=str(root))
    for n in numbers:
        ctx = ir_compiler.CompileContext(root=root, number=n, report=compile_report, config=None)
        for path in sorted(paths.contracts_dir(root, "tools").glob(f"{n}-*.tool.md")):
            try:
                tools.append(ir_compiler.compile_tool(ctx, path))
            except ir_compiler.CompileError:
                pass
    report.extend(compile_report)
    if not tools and not compile_report.errors:
        report.warn("TOOL_CONTRACT_MISSING", "aucun contrat d'outil à valider",
                    "une MISSION sans outil est légitime ; sinon, architect-tools n'a rien écrit",
                    paths.rel(root, paths.contracts_dir(root, "tools")))
    return {"missionId": f"{numbers[0]}" if numbers else "", "tools": tools, "agents": [], "evaluation": {"suites": []}}


def _split(raw: list[str] | None) -> set[str]:
    out: set[str] = set()
    for chunk in raw or []:
        out.update(t.strip() for t in chunk.split(",") if t.strip())
    return out


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    root = resolve_root(args)
    report = Report(name="G3", target=str(root))
    load_config(root, report)

    if args.static:
        ir = static_ir(root, args.mission, report)
        # Aucun rapport de gate en mode statique : G3 se prononce sur l'IR, et
        # un rapport écrit depuis des contrats non compilés ferait croire la
        # gate franchie avant que le graphe n'ait été vérifié.
        payload = run(root, ir or {}, report=report, only=_split(args.tool),
                      require_code=False, write_report=False, static=True)
        report.data["payload"] = payload
        if not args.json:
            for line in report.data.get("lines", []):
                print(line)
        return finish(report, args)

    if args.ir:
        ir_file = args.ir if args.ir.is_absolute() else root / args.ir
    elif args.mission is not None:
        ir_file = paths.ir_path(root, args.mission)
    else:
        candidates = sorted(paths.ir_dir(root).glob("*-system.ir.json"))
        if len(candidates) != 1:
            report.error("IR_NOT_FOUND", f"{len(candidates)} IR compilé(s) dans workspace/.sys/.ir/ : préciser --mission",
                         "python .sdda/sdda.py ir-compiler --mission {n}", str(paths.ir_dir(root)))
            return finish(report, args)
        ir_file = candidates[0]
    if not ir_file.is_file():
        report.error("IR_NOT_FOUND", f"IR `{paths.rel(root, ir_file)}` introuvable",
                     "compiler : python .sdda/sdda.py ir-compiler --mission {n}", paths.rel(root, ir_file))
        return finish(report, args)

    ir = ir_compiler.load_ir(ir_file)
    payload = run(root, ir, report=report, only=_split(args.tool), require_code=args.require_code,
                  write_report=not args.no_report)
    report.data["payload"] = payload
    if not args.json:
        for line in report.data.get("lines", []):
            print(line)
    return finish(report, args)


if __name__ == "__main__":
    sys.exit(main())
