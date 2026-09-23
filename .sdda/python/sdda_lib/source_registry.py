"""Registre de sources — grammaire close, manifestes, stores, secrets par `.env`.

Une **source** est ce qu'un agent peut lire : un fichier, une réponse d'API, un
outil MCP. Un **store** est l'endroit d'où elle vient : un répertoire local, un
partage réseau, un bucket, un endpoint HTTP, un serveur MCP. La séparation n'est
pas cosmétique — c'est elle qui permet de dire « les identifiants vivent dans le
store, jamais dans la source », et donc de déclarer cent sources sans
multiplier les endroits où un secret peut fuir.

Trois principes tiennent tout le module :

1. **Grammaire close.** Une clé inconnue est une erreur, pas un champ ignoré.
   `pii` écrit `pii_fields` ne redige plus rien, et rien ne le signalerait.
2. **Les secrets sont des NOMS de variables.** Toute clé d'authentification se
   termine par `_env` et porte le nom d'une variable déclarée dans le `.env`
   de l'application (`workspace/src/{App}/.env`, avec le livrable — jamais à la
   racine du dépôt). Ce module lit les **noms** de ce fichier, jamais les
   valeurs : une valeur qui n'entre pas en mémoire n'entre pas dans un rapport.
3. **La déclaration vit dans STACK.md.** `Stores:` et `Sources:` s'écrivent
   inline dans `## Active Data Sources` — c'est la forme par défaut, et la
   seule que le gabarit propose : STACK.md est versionné, il ne porte que des
   noms de variables, il n'y a plus de raison d'éclater la surface de données
   ailleurs. `SourceManifests[]` subsiste comme porte optionnelle pour UN cas :
   importer une configuration MCP au format standard (`mcp.json`, celui que
   lisent Claude, Cursor ou VS Code) sans la retranscrire. Un manifeste hors
   de `workspace/stack/` est refusé.

Ce module ne lit **aucune** donnée métier, n'ouvre aucun enregistrement,
n'appelle aucun réseau. Il résout, fusionne et vérifie des déclarations.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sdda_lib import markdown_io, yaml_mini

#: Racine des manifestes optionnels : `workspace/stack/`, à côté de STACK.md.
#: Il n'y a plus de sous-répertoire `sources/` — la déclaration est inline, et
#: le seul fichier qui puisse encore vivre ici est un `mcp.json` standard.
DEFAULT_MANIFEST_ROOT = "workspace/stack"

# ---------------------------------------------------------------------------
# Grammaire close
# ---------------------------------------------------------------------------

#: D'où vient la donnée. Un store porte l'adresse et les identifiants.
STORE_KINDS: tuple[str, ...] = (
    "local",        # répertoire du poste ou du conteneur
    "smb",          # partage réseau Windows/CIFS — chemin UNC
    "nfs",          # partage réseau POSIX monté
    "s3",           # Amazon S3 ou compatible (MinIO, Ceph) via endpoint_url
    "azure-blob",
    "gcs",
    "http",         # API HTTP(S) — REST ou fichier servi
    "sftp",
    "mcp",          # serveur Model Context Protocol
)

#: Stores dont le contenu est vérifiable sur disque au preflight, sans réseau.
LOCAL_KINDS: frozenset[str] = frozenset({"local", "smb", "nfs"})

#: Stores qui sortent du poste : leur hôte doit être dans `SourceEgressAllowlist`.
REMOTE_KINDS: frozenset[str] = frozenset({"s3", "azure-blob", "gcs", "http", "sftp", "mcp"})

#: Comment une source est lue. Le connecteur choisit la grammaire admise.
CONNECTORS: tuple[str, ...] = ("file", "http-api", "mcp")

#: Connecteurs dont la sortie est écrite par un tiers : `untrusted` par défaut.
UNTRUSTED_BY_DEFAULT: frozenset[str] = frozenset({"http-api", "mcp"})

#: Formats de fichier. `object` / `array` / `jsonl` conservent la distinction
#: JSON d'origine ; les tabulaires s'y ajoutent sans changer le reste.
FILE_FORMATS: tuple[str, ...] = (
    "object", "array", "jsonl", "csv", "tsv", "xlsx", "parquet",
)

#: Formats qui exigent une dépendance hors stdlib, à épingler dans un `.libs.json`.
FORMATS_NEEDING_LIB: dict[str, str] = {
    "xlsx": "openpyxl",
    "parquet": "pyarrow",
}

#: Modes d'authentification admis. `none` est explicite : un store public se
#: déclare public, il ne s'obtient pas en oubliant la clé `auth`.
AUTH_MODES: tuple[str, ...] = (
    "none",
    "windows-integrated",      # SSO Kerberos/NTLM sur un partage SMB
    "api-key",
    "bearer",
    "basic",
    "oauth2-client-credentials",
    "aws-sigv4",
    "azure-ad",
    "gcp-service-account",
    "mtls",
)

#: Clés d'authentification admises par mode, et lesquelles sont obligatoires.
#: Toute clé se terminant par `_env` porte un NOM de variable, jamais une valeur.
AUTH_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    #                      requises                              optionnelles
    "none":                      (frozenset(), frozenset()),
    "windows-integrated":        (frozenset(), frozenset({"principal"})),
    "api-key":                   (frozenset({"key_env"}), frozenset({"header", "query_param"})),
    "bearer":                    (frozenset({"token_env"}), frozenset()),
    "basic":                     (frozenset({"user_env", "password_env"}), frozenset()),
    "oauth2-client-credentials": (frozenset({"client_id_env", "client_secret_env", "token_url"}),
                                  frozenset({"scopes", "audience"})),
    "aws-sigv4":                 (frozenset({"access_key_env", "secret_key_env"}),
                                  frozenset({"session_token_env", "profile"})),
    "azure-ad":                  (frozenset({"tenant_id_env", "client_id_env", "client_secret_env"}),
                                  frozenset({"scopes"})),
    "gcp-service-account":       (frozenset({"credentials_file_env"}), frozenset({"scopes"})),
    "mtls":                      (frozenset({"cert_file_env", "key_file_env"}), frozenset({"ca_file_env"})),
}

#: Clés admises dans une entrée de `Stores[]`, par `kind`.
STORE_COMMON_KEYS = frozenset({"id", "kind", "auth", "read_only", "description", "timeout_ms"})
STORE_KIND_KEYS: dict[str, frozenset[str]] = {
    "local":      frozenset({"root"}),
    "smb":        frozenset({"root"}),
    "nfs":        frozenset({"root"}),
    "s3":         frozenset({"bucket", "prefix", "region", "endpoint_url"}),
    "azure-blob": frozenset({"account", "container", "prefix"}),
    "gcs":        frozenset({"bucket", "prefix"}),
    "http":       frozenset({"base_url", "rate_limit_rpm", "verify_tls", "default_headers"}),
    "sftp":       frozenset({"host", "port", "root"}),
    "mcp":        frozenset({"server", "transport", "url", "command"}),
}
STORE_KIND_REQUIRED: dict[str, tuple[str, ...]] = {
    "local": ("root",), "smb": ("root",), "nfs": ("root",),
    "s3": ("bucket",), "azure-blob": ("account", "container"), "gcs": ("bucket",),
    "http": ("base_url",), "sftp": ("host", "root"), "mcp": ("server",),
}

#: Clés admises dans une entrée de `Sources[]`, communes puis par connecteur.
SOURCE_COMMON_KEYS = frozenset({
    "id", "connector", "store", "description", "key", "trust", "schema",
    "filters", "ranges", "required_filter", "pii", "free_text",
    "date_field", "max_staleness_hours",
})
SOURCE_CONNECTOR_KEYS: dict[str, frozenset[str]] = {
    "file":     frozenset({"glob", "format", "encoding", "delimiter", "sheet", "header_row",
                           "records_path"}),
    "http-api": frozenset({"path", "method", "query_params", "records_path", "page_param",
                           "page_size", "page_size_param", "cursor_path", "cache_ttl_s"}),
    "mcp":      frozenset({"tool", "arguments"}),
}
SOURCE_COMMON_REQUIRED: tuple[str, ...] = ("id", "connector", "store", "key", "description")
SOURCE_CONNECTOR_REQUIRED: dict[str, tuple[str, ...]] = {
    "file": ("glob", "format"),
    "http-api": ("path", "records_path"),
    "mcp": ("tool",),
}

#: Champs taggés : la valeur est une liste de noms de champs.
FIELD_TAGS: tuple[str, ...] = ("filters", "ranges", "required_filter", "pii", "free_text")

#: Formats de manifeste. `mcp-config` est le format standard `{"mcpServers": …}`
#: écrit par les clients MCP — on l'importe plutôt que de le retranscrire.
MANIFEST_KINDS: tuple[str, ...] = ("sdda-sources", "mcp-config")

#: Opérations que l'enveloppe doit interdire, quel que soit le connecteur.
REQUIRED_FORBIDDEN_OPS: tuple[str, ...] = (
    "WRITE", "DELETE", "EXEC", "SYMLINK_FOLLOW", "UNDECLARED_EGRESS",
)

#: Longueur minimale d'une description de source : c'est du prompt.
MIN_DESCRIPTION_CHARS = 200

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_ENV_REF_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")
_YAML_FENCE_RE = re.compile(r"```(?:ya?ml)?(?:\s+sdda-sources)?\s*\n(.*?)```", re.S)


# ---------------------------------------------------------------------------
# Problèmes — findings portables, sans dépendance au `Report` du validateur
# ---------------------------------------------------------------------------
@dataclass
class Problem:
    """Un finding, transportable vers n'importe quel `Report`."""

    severity: str            # "error" | "warn"
    cls: str
    message: str
    fix: str = ""
    location: str = ""


@dataclass
class Registry:
    """Le résultat d'une résolution : ce qui est déclaré, et d'où ça vient."""

    stores: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    origin: dict[str, str] = field(default_factory=dict)      # "store:id" | "source:id" -> manifeste
    manifests: list[str] = field(default_factory=list)
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)

    def error(self, cls: str, message: str, fix: str = "", location: str = "") -> None:
        self.problems.append(Problem("error", cls, message, fix, location))

    def warn(self, cls: str, message: str, fix: str = "", location: str = "") -> None:
        self.problems.append(Problem("warn", cls, message, fix, location))


# ---------------------------------------------------------------------------
# Secrets : on lit des NOMS, jamais des valeurs
# ---------------------------------------------------------------------------
def env_names(path: Path) -> set[str]:
    """Noms de variables déclarés dans un fichier `.env`.

    Seule la partie gauche du `=` est retenue. La valeur n'est ni parsée, ni
    conservée, ni renvoyée : ce qui n'entre pas en mémoire ne peut pas fuir
    dans un rapport de gate, un log de CI ou un message d'erreur.
    """
    if not path.is_file():
        return set()
    out: set[str] = set()
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    for line in raw.replace("\r\n", "\n").split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _ENV_LINE_RE.match(line)
        if m:
            out.add(m.group(1))
    return out


def env_reference(value: Any) -> str | None:
    """`${CRM_TOKEN}` ou `$CRM_TOKEN` -> `CRM_TOKEN` ; sinon None."""
    if not isinstance(value, str):
        return None
    m = _ENV_REF_RE.match(value.strip())
    return m.group(1) if m else None


def looks_like_secret(value: Any) -> bool:
    """Heuristique : cette valeur ressemble-t-elle à un secret en clair ?

    Volontairement large sur le littéral et stricte sur la référence : un faux
    positif coûte une ligne de correction, un faux négatif coûte une clé
    d'API commitée. Une valeur qui est une référence d'environnement
    (`${VAR}`) n'est jamais un secret.
    """
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s or env_reference(s):
        return False
    # Les préfixes connus d'abord : un identifiant AWS (`AKIA…`) ressemble
    # exactement à un nom de variable, et l'heuristique suivante le laisserait
    # passer. Un secret qui se présente comme un nom est le pire des deux.
    if re.match(r"^(sk-|xox[baprs]-|ghp_|gho_|github_pat_|AKIA|ASIA|eyJ|AIza|glpat-)", s):
        return True
    if _ENV_NAME_RE.match(s) and s.upper() == s and len(s) <= 64:
        return False                                   # un NOM de variable
    return len(s) >= 20 and bool(re.search(r"[A-Za-z]", s)) and bool(re.search(r"\d", s)) and " " not in s


# ---------------------------------------------------------------------------
# Manifestes
# ---------------------------------------------------------------------------
def _parse_manifest_text(text: str, suffix: str) -> Any:
    """Contenu d'un manifeste -> structure Python, selon son extension.

    `.md` : on lit le premier bloc clôturé ```` ```yaml ```` du fichier. Un
    manifeste en Markdown existe pour être lu par un humain autant que par le
    validateur ; la prose autour du bloc est de la documentation, pas du bruit.
    """
    if suffix in (".json",):
        return json.loads(text)
    if suffix in (".md", ".markdown"):
        m = _YAML_FENCE_RE.search(text)
        if not m:
            raise ValueError("aucun bloc ```yaml``` dans le manifeste Markdown")
        return yaml_mini.parse_mapping(m.group(1))
    return yaml_mini.parse_mapping(text)


def _import_mcp_config(data: Any, label: str, registry: Registry) -> None:
    """Configuration MCP standard -> stores `kind: mcp` + serveurs connus.

    Format reconnu : `{"mcpServers": {...}}` (Claude, Cursor) et
    `{"servers": {...}}` (VS Code). Chaque serveur devient un store nommé
    d'après lui : les sources n'ont pas à connaître le transport, seulement le
    nom du store — et l'allowlist reste maîtresse de ce qui existe vraiment.
    """
    if not isinstance(data, dict):
        registry.error("DATA_MANIFEST_MALFORMED", f"manifeste MCP `{label}` : la racine n'est pas un objet",
                       fix="un fichier MCP déclare `{\"mcpServers\": {…}}`", location=label)
        return
    servers = data.get("mcpServers") or data.get("servers") or {}
    if not isinstance(servers, dict) or not servers:
        registry.error("DATA_MANIFEST_MALFORMED", f"manifeste MCP `{label}` : aucun serveur sous `mcpServers`",
                       fix="vérifier le fichier, ou retirer le manifeste de `SourceManifests`", location=label)
        return

    for name, conf in servers.items():
        if not isinstance(conf, dict):
            continue
        sid = re.sub(r"[^a-z0-9_]", "_", str(name).lower())
        transport = str(conf.get("type") or conf.get("transport") or ("http" if conf.get("url") else "stdio"))
        store: dict[str, Any] = {
            "id": sid,
            "kind": "mcp",
            "server": str(name),
            "transport": transport,
            "read_only": True,
            "auth": {"mode": "none"},
        }
        if conf.get("url"):
            store["url"] = str(conf["url"])
        if conf.get("command"):
            store["command"] = str(conf["command"])

        # Un `env` inline est le piège de ces fichiers : le format autorise une
        # valeur littérale, et une clé d'API s'y retrouve commitée sans que rien
        # ne l'empêche. Seule une référence `${VAR}` est acceptée ici.
        for var, val in (conf.get("env") or {}).items():
            ref = env_reference(val)
            if ref is None and str(val).strip():
                registry.error(
                    "DATA_SECRET_INLINE",
                    f"manifeste MCP `{label}`, serveur `{name}` : `env.{var}` porte une valeur littérale",
                    fix=f"écrire `\"{var}\": \"${{{var}}}\"` et déclarer `{var}` dans le fichier `.env`",
                    location=label,
                )
            elif ref:
                store.setdefault("_env_refs", []).append(ref)

        registry.mcp_servers[str(name)] = {"transport": transport, "origin": label}
        _register(registry, "store", sid, store, label)


def _register(registry: Registry, what: str, ident: str, entry: dict[str, Any], origin: str) -> None:
    """Ajoute une entrée, en refusant toute collision d'identifiant.

    Deux manifestes qui déclarent le même `id` ne se fusionnent pas : l'un
    gagnerait selon l'ordre de lecture, et la surface réelle de l'agent
    dépendrait de l'ordre des lignes de `SourceManifests`.
    """
    table = registry.stores if what == "store" else registry.sources
    kind_label = "store" if what == "store" else "source"
    if ident in table:
        registry.error(
            "DATA_MANIFEST_DUPLICATE_ID",
            f"{kind_label} `{ident}` déclaré deux fois — {registry.origin.get(f'{what}:{ident}', '?')} et {origin}",
            fix=f"un `id` unique par {kind_label} : il nomme l'outil généré, une collision change la surface "
                "de l'agent selon l'ordre de lecture des manifestes",
            location=origin,
        )
        return
    table[ident] = entry
    registry.origin[f"{what}:{ident}"] = origin


def _absorb(registry: Registry, data: Any, label: str) -> None:
    """Un manifeste `sdda-sources` -> stores et sources."""
    if not isinstance(data, dict):
        registry.error("DATA_MANIFEST_MALFORMED", f"manifeste `{label}` : la racine n'est pas un mapping",
                       fix="un manifeste déclare `Stores:` et/ou `Sources:` à la racine", location=label)
        return
    known = {"Stores", "Sources", "stores", "sources"}
    extra = sorted(set(data) - known)
    if extra:
        registry.warn(
            "DATA_MANIFEST_MALFORMED",
            f"manifeste `{label}` : clé(s) de premier niveau ignorée(s) — {extra}",
            fix="un manifeste ne porte que `Stores:` et `Sources:` ; l'enveloppe reste dans STACK.md, "
                "sinon chaque manifeste pourrait relâcher une borne",
            location=label,
        )
    for key, what in (("Stores", "store"), ("stores", "store"), ("Sources", "source"), ("sources", "source")):
        for entry in (data.get(key) or []):
            if not isinstance(entry, dict):
                registry.error("DATA_MANIFEST_MALFORMED", f"manifeste `{label}` : entrée `{key}` qui n'est pas un mapping",
                               fix="chaque entrée est `- id: …` suivi de ses clés", location=label)
                continue
            ident = str(entry.get("id") or "").strip()
            if not ident:
                registry.error(
                    "DATA_SOURCE_INCOMPLETE" if what == "source" else "DATA_STORE_INCOMPLETE",
                    f"manifeste `{label}` : une entrée `{key}` n'a pas d'`id`",
                    fix="l'`id` est le nom métier — il nomme l'outil que voit le modèle", location=label,
                )
                continue
            _register(registry, what, ident, entry, label)


def manifest_root(root: Path, section: dict[str, Any]) -> Path:
    declared = str(section.get("SourceManifestRoot") or DEFAULT_MANIFEST_ROOT).strip()
    candidate = Path(declared)
    return (candidate if candidate.is_absolute() else root / candidate)


def load_registry(root: Path, section: dict[str, Any]) -> Registry:
    """Résout `## Active Data Sources` : inline + manifestes -> un registre unique.

    L'ordre est fixé : les manifestes d'abord, dans l'ordre déclaré, puis les
    entrées inline de STACK.md. Aucun écrasement n'est permis (cf. `_register`),
    donc l'ordre ne change jamais le résultat — il ne change que le manifeste
    nommé en premier dans le message de collision.
    """
    registry = Registry()
    mroot = manifest_root(root, section)

    declared_manifests = section.get("SourceManifests") or []
    if not isinstance(declared_manifests, list):
        registry.error("DATA_MANIFEST_MALFORMED", "`SourceManifests` n'est pas une liste",
                       fix="`SourceManifests:` suivi d'entrées `- path: <fichier>`",
                       location="workspace/stack/STACK.md")
        declared_manifests = []

    for raw in declared_manifests:
        entry = {"path": raw} if isinstance(raw, str) else raw
        if not isinstance(entry, dict):
            registry.error("DATA_MANIFEST_MALFORMED", f"entrée de `SourceManifests` illisible : {raw!r}",
                           fix="`- path: fichier.yml` ou `- { path: fichier.json, kind: mcp-config }`",
                           location="workspace/stack/STACK.md")
            continue

        rel_path = str(entry.get("path") or "").strip()
        kind = str(entry.get("kind") or "sdda-sources").strip()
        if not rel_path:
            registry.error("DATA_MANIFEST_MALFORMED", "entrée de `SourceManifests` sans `path`",
                           fix="déclarer le chemin du manifeste", location="workspace/stack/STACK.md")
            continue
        if kind not in MANIFEST_KINDS:
            registry.error("DATA_MANIFEST_KIND_UNKNOWN", f"manifeste `{rel_path}` : `kind: {kind}` inconnu",
                           fix=f"choisir parmi {list(MANIFEST_KINDS)}", location="workspace/stack/STACK.md")
            continue

        candidate = Path(rel_path)
        path = candidate if candidate.is_absolute() else (mroot / candidate)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path

        # La racine des manifestes est une frontière : un manifeste est du
        # code de configuration, et `../../../autre-projet/sources.yml`
        # rattacherait la surface de données d'un agent à un fichier que
        # personne ne relit dans cette revue.
        try:
            inside = resolved.is_relative_to(mroot.resolve()) or resolved.is_relative_to(root.resolve())
        except (OSError, ValueError):
            inside = False
        if not inside:
            registry.error(
                "DATA_MANIFEST_OUTSIDE_ROOT",
                f"manifeste `{rel_path}` résout hors du projet ({resolved})",
                fix=f"placer le manifeste sous `{section.get('SourceManifestRoot') or DEFAULT_MANIFEST_ROOT}` — "
                    "la racine des manifestes est une frontière, pas une convention",
                location="workspace/stack/STACK.md",
            )
            continue
        if not resolved.is_file():
            registry.error(
                "DATA_MANIFEST_MISSING",
                f"manifeste `{rel_path}` introuvable ({resolved})",
                fix="créer le fichier, ou retirer la ligne de `SourceManifests`",
                location="workspace/stack/STACK.md",
            )
            continue

        label = rel_path
        registry.manifests.append(label)
        try:
            data = _parse_manifest_text(markdown_io.read_text(resolved), resolved.suffix.lower())
        except (ValueError, yaml_mini.YamlMiniError, OSError) as exc:
            registry.error("DATA_MANIFEST_MALFORMED", f"manifeste `{label}` illisible : {exc}",
                           fix="corriger la syntaxe du fichier", location=label)
            continue

        if kind == "mcp-config":
            _import_mcp_config(data, label, registry)
        else:
            _absorb(registry, data, label)

    inline = {"Stores": section.get("Stores") or [], "Sources": section.get("Sources") or []}
    _absorb(registry, inline, "workspace/stack/STACK.md")
    return registry


# ---------------------------------------------------------------------------
# Helpers de lecture
# ---------------------------------------------------------------------------
def store_host(store: dict[str, Any]) -> str | None:
    """L'hôte réseau d'un store, pour la confrontation à l'egress allowlist."""
    kind = str(store.get("kind") or "").strip()
    if kind == "http":
        return _host_of(str(store.get("base_url") or ""))
    if kind == "s3":
        endpoint = str(store.get("endpoint_url") or "")
        if endpoint:
            return _host_of(endpoint)
        region = str(store.get("region") or "").strip()
        return f"s3.{region}.amazonaws.com" if region else "s3.amazonaws.com"
    if kind == "azure-blob":
        account = str(store.get("account") or "").strip()
        return f"{account}.blob.core.windows.net" if account else None
    if kind == "gcs":
        return "storage.googleapis.com"
    if kind == "sftp":
        return str(store.get("host") or "").strip() or None
    if kind == "mcp":
        url = str(store.get("url") or "")
        return _host_of(url) if url else None      # stdio : pas d'hôte, pas d'egress réseau
    return None


def _host_of(url: str) -> str | None:
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://([^/:@]+(?::\d+)?)", url.strip())
    if not m:
        return None
    return m.group(1).split(":", 1)[0].lower()


def store_scheme(store: dict[str, Any]) -> str | None:
    for key in ("base_url", "url", "endpoint_url"):
        value = str(store.get(key) or "").strip()
        if value:
            m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.\-]*)://", value)
            if m:
                return m.group(1).lower()
    return None


def auth_env_refs(store: dict[str, Any]) -> list[str]:
    """Noms de variables d'environnement référencés par un store."""
    out: list[str] = []
    auth = store.get("auth")
    if isinstance(auth, dict):
        for key, value in auth.items():
            if key.endswith("_env") and isinstance(value, str) and value.strip():
                out.append(value.strip())
    out.extend(str(r) for r in (store.get("_env_refs") or []))
    return sorted(set(out))


def source_trust(source: dict[str, Any]) -> str:
    """La confiance effective d'une source, déclarée ou déduite.

    Déduite `untrusted` dès qu'un tiers écrit le contenu — une API, un serveur
    MCP, ou un champ `free_text` d'un fichier. C'est le défaut sûr : un
    libellé transporteur est du texte hostile même quand il vient d'un CSV.
    """
    declared = str(source.get("trust") or "").strip().lower()
    if declared in ("trusted", "untrusted"):
        return declared
    connector = str(source.get("connector") or "").strip()
    if connector in UNTRUSTED_BY_DEFAULT or source.get("free_text"):
        return "untrusted"
    return "trusted"


def is_valid_id(ident: str) -> bool:
    return bool(_ID_RE.match(ident))


def allowed_keys(source: dict[str, Any]) -> frozenset[str]:
    connector = str(source.get("connector") or "").strip()
    return SOURCE_COMMON_KEYS | SOURCE_CONNECTOR_KEYS.get(connector, frozenset())


def schema_rel_path(source_id: str, app_name: str) -> str:
    """Le schéma figé d'une source : `workspace/src/{App}/data/schemas/{id}.schema.json`.

    Il vivait sous `feats/contracts/dataaccess/schemas/`. Or ce n'est ni une
    spécification ni du Markdown : c'est le fichier contre lequel l'application
    valide sa donnée au démarrage (`schema_guard.py`, `[DATA_SOURCE_SCHEMA_DRIFT]`).
    Un actif d'exécution qui reste dans la zone des specs ne part pas avec le
    code — l'application livrée cherchait son schéma dans un répertoire resté
    dans le dépôt. Même raison que pour les prompts. Il est écrit par
    `gen_source_tools --infer`, relu par l'humain, jamais par un `dev-*`.
    """
    return f"workspace/src/{app_name}/data/schemas/{source_id}.schema.json"   # layout plat (paths.app_src_root)
