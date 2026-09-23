"""Configuration — le SEUL module qui lit l'environnement. GÉNÉRÉ, ne pas éditer.

Deux sources, et la frontière entre elles est la raison d'être du module :

    app_config.json   ce qui n'est pas secret et qu'on VERSIONNE — nom de
                      l'application, carte tier -> modèle, bornes par défaut,
                      table de tarifs, politique de trace. Un run n'est
                      reproductible que si ces valeurs sont dans le dépôt.
    variables d'env   les secrets, et eux seuls. Ils ne sont jamais écrits,
                      jamais journalisés, jamais rendus par `repr`. En
                      développement, le `.env` du livrable
                      (`workspace/src/{App}/.env`, gitignoré) les complète —
                      sans jamais écraser l'environnement réel.

**`os.environ` ne s'ouvre qu'ici.** Ailleurs dans `src/`, un `os.getenv("…")`
est `[SEC_ENV_VAR_FORBIDDEN]` (`lang/python.md §5.2`) — et le lint le cherche.
La raison n'est pas l'esthétique : une clé lue en trois endroits est une clé
qu'on ne peut ni rédiger dans les traces (on ne sait pas quelles valeurs
surveiller), ni faire manquer proprement au démarrage, ni remplacer en test.
Ici, `Settings.load(environ=…)` suffit à faire tourner tout le squelette sans
toucher au véritable environnement du processus.

**Le nom de la variable est une donnée, sa valeur est un secret.** `secretEnv`
associe un nom logique (`llmApiKey`) au NOM d'une variable
(`ANTHROPIC_API_KEY`). Le nom peut entrer dans une trace ou un message d'erreur
— c'est même ce qu'on veut y lire quand la clé manque ; la valeur ne le peut
jamais. C'est aussi ce qui rend `redact` capable de reconnaître un secret qui
aurait fui : `Settings.secret_values()` lui donne la liste exacte.

Aucune dépendance : `pydantic-settings` fera mieux quand il sera installé, mais
la configuration doit se charger avant lui — un `health` qui exige d'abord
`uv sync` ne sert à rien le jour où l'on cherche pourquoi rien ne démarre.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

#: Nom du fichier de configuration non secrète, écrit à côté du paquet par
#: `gen_app_skeleton.py`. Le chercher relativement à `__file__` et non au
#: répertoire courant : une application lancée depuis ailleurs (un runner
#: d'eval, un service, un `uv run` en CI) doit trouver SA configuration, pas
#: celle du dossier où quelqu'un se trouvait.
CONFIG_FILE = "app_config.json"

#: Les trois tiers. Le code manipule un tier, jamais un nom de modèle
#: (`[MODEL_NAME_HARDCODED]`) : la résolution appartient à `models.resolve`.
TIERS = ("fast", "balanced", "deep")


class ConfigError(Exception):
    """Configuration inexploitable. Porte une classe, pour le code de sortie 8.

    `cls` n'est pas décoratif : `serving/exit_codes.py` en dérive le code que
    lit la CI, et un appelant qui reçoit `8` sait que le problème est la
    configuration et non le modèle, sans lire le message.
    """

    def __init__(self, message: str, *, cls: str = "CONFIG_INVALID", fix: str = "") -> None:
        super().__init__(message)
        self.cls = cls
        self.fix = fix


class Secret:
    """Une valeur qui ne doit apparaître ni dans un log, ni dans un `repr`.

    L'équivalent local de `SecretStr` : `structlog`, `pytest` et un simple
    `print` de dataclass sérialisent ce qu'on leur donne. Une clé d'API dans un
    message d'erreur de démarrage se retrouve dans un ticket de support, puis
    dans une pièce jointe, puis dans un dépôt. Rendre la valeur accessible
    **uniquement** par un appel explicite (`get_secret_value()`) fait que la
    fuite devient une ligne de code qu'on peut chercher.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str = "") -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret(<set>)" if self._value else "Secret(<unset>)"

    def __str__(self) -> str:
        return "**********" if self._value else ""


@dataclass(frozen=True)
class Settings:
    """L'état de configuration, résolu une fois, immuable ensuite.

    Immuable parce qu'une configuration qui change en cours de run rend deux
    spans du même fichier incomparables : le premier appel LLM aurait été
    facturé sur un tarif et le second sur un autre, sans que la trace le dise.
    """

    app_name: str = "App"
    mission_id: str = ""
    provider: str = "none"
    default_tier: str = "balanced"
    tier_map: Mapping[str, str] = field(default_factory=dict)
    pricing: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    bounds: Mapping[str, Any] = field(default_factory=dict)
    deliverable_type: str = "cli-exe"
    serving_surface: str = "cli"
    streaming: bool = False
    trace_enabled: bool = True
    trace_redact: bool = True
    max_input_bytes: int = 262144
    tenant_id: str = ""
    workspace_root: Path = field(default_factory=Path)
    secret_env: Mapping[str, str] = field(default_factory=dict)
    _secrets: Mapping[str, Secret] = field(default_factory=dict, repr=False)

    # -- Chargement ---------------------------------------------------------
    @classmethod
    def load(cls, *, config_path: Path | None = None, environ: Mapping[str, str] | None = None,
             workspace_root: Path | None = None) -> "Settings":
        """Lit `app_config.json` puis l'environnement. `environ` est injectable.

        L'injection n'est pas une commodité de test : c'est ce qui permet à un
        runner d'eval de lancer deux configurations dans le même processus sans
        qu'elles se contaminent, et c'est la seule façon d'écrire un test qui
        prouve qu'une clé manquante échoue AVANT le premier appel au modèle.
        """
        path = Path(config_path) if config_path else Path(__file__).resolve().parent / CONFIG_FILE
        env = cls._environ_with_env_file(path) if environ is None else environ
        raw = cls._read(path)

        secret_env = {str(k): str(v) for k, v in (raw.get("secretEnv") or {}).items()}
        secrets = {name: Secret(str(env.get(var, "")).strip()) for name, var in secret_env.items()}

        root = workspace_root or cls._workspace_root(raw, env, path)
        tier_map = {str(k): str(v) for k, v in (raw.get("tierMap") or {}).items()}
        unknown = sorted(t for t in tier_map if t not in TIERS)
        if unknown:
            raise ConfigError(
                f"`tierMap` déclare un tier hors liste close : {unknown}",
                fix=f"les tiers sont {list(TIERS)} — un quatrième tier n'a pas de borne dans "
                    "`agent-bounds.yaml`, donc rien ne le plafonne")

        return cls(
            app_name=str(raw.get("appName") or "App"),
            mission_id=str(raw.get("missionId") or ""),
            provider=str(raw.get("provider") or "none"),
            default_tier=str(raw.get("defaultTier") or "balanced"),
            tier_map=tier_map,
            pricing={str(k): {str(rk): float(rv) for rk, rv in (v or {}).items()}
                     for k, v in (raw.get("pricing") or {}).items()},
            bounds=dict(raw.get("bounds") or {}),
            deliverable_type=str(raw.get("deliverableType") or "cli-exe"),
            serving_surface=str(raw.get("servingSurface") or "cli"),
            streaming=bool(raw.get("streaming", False)),
            trace_enabled=bool((raw.get("tracing") or {}).get("enabled", True)),
            trace_redact=bool((raw.get("tracing") or {}).get("redact", True)),
            max_input_bytes=int(raw.get("maxInputBytes") or 262144),
            # L'identité de l'appelant vient du transport (`--tenant`, en-tête,
            # variable). Un défaut en dur ferait filtrer les vues SQL et le
            # retrieval sur un locataire que personne n'a établi.
            tenant_id=str(env.get("SDDA_TENANT_ID", "")).strip(),
            workspace_root=root,
            secret_env=secret_env,
            _secrets=secrets,
        )

    @staticmethod
    def _environ_with_env_file(config_path: Path) -> Mapping[str, str]:
        """L'environnement du processus, complété par le `.env` du LIVRABLE — jamais écrasé par lui.

        Le fichier vit à la racine de l'application, `workspace/src/{App}/.env`
        (le répertoire même de ce module, layout plat), gitignoré : c'est l'application qui
        consomme la clé, et c'est de là qu'elle part en exécutable ou en
        conteneur. Le harnais de construction ne le lit jamais.

        L'environnement réel gagne toujours : en production, c'est lui qui
        fournit les valeurs, et un `.env` resté dans une image ne doit pas
        pouvoir remplacer ce que l'orchestrateur a injecté. Le fichier n'est
        lu que quand `environ` n'est pas injecté — un test qui injecte son
        environnement ne voit jamais les vraies clés du poste.
        """
        merged: dict[str, str] = dict(os.environ)
        env_file = config_path.resolve().parent / ".env"
        if not env_file.is_file():
            return merged
        try:
            lines = env_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return merged
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].lstrip()
            name, value = stripped.split("=", 1)
            name = name.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if name and name not in merged:
                merged[name] = value
        return merged

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise ConfigError(
                f"`{path.name}` introuvable ({path})",
                fix="régénérer : python .sdda/sdda.py gen-app-skeleton --write")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ConfigError(f"`{path.name}` illisible : {exc}",
                              fix="régénérer plutôt que réparer à la main") from exc
        if not isinstance(payload, dict):
            raise ConfigError(f"`{path.name}` : objet JSON attendu")
        return payload

    @staticmethod
    def _workspace_root(raw: dict[str, Any], env: Mapping[str, str], path: Path) -> Path:
        """`workspace/` — d'où l'on écrit les traces et où l'on lit les prompts.

        L'ordre compte : la variable d'environnement d'abord (un conteneur monte
        le workspace ailleurs), puis la valeur versionnée, puis la déduction
        depuis la position du fichier. Déduire en premier ferait qu'un paquet
        installé dans `site-packages` écrirait ses traces dans `site-packages`.
        """
        override = str(env.get("SDDA_WORKSPACE_ROOT", "")).strip()
        if override:
            return Path(override).resolve()
        declared = str(raw.get("workspaceRoot") or "").strip()
        if declared:
            return (path.parent / declared).resolve()
        # Layout plat : workspace/src/{AppName}/config.py -> parents[2] == workspace/
        parents = path.resolve().parents
        return parents[2] if len(parents) > 2 else path.resolve().parent

    # -- Accès --------------------------------------------------------------
    def secret(self, name: str, *, required: bool = True) -> Secret:
        """Le secret nommé. Absent et requis -> `ConfigError`, avant tout appel.

        Échouer au démarrage plutôt qu'au premier appel du modèle : sinon la
        panne se déclare au milieu d'un run facturé, dans une pile d'appels
        asynchrones, et ressemble à une erreur de fournisseur.
        """
        value = self._secrets.get(name, Secret())
        if required and not value:
            var = self.secret_env.get(name, f"<{name} non déclaré dans secretEnv>")
            raise ConfigError(
                f"secret `{name}` absent : la variable `{var}` n'est pas posée",
                cls="CONFIG_INVALID",
                fix=f"exporter `{var}`, ou lancer avec un client `StubClient` "
                    "(`models.stub_client()`) si le run ne doit appeler aucun modèle")
        return value

    def secret_values(self) -> tuple[str, ...]:
        """Les valeurs à rédiger partout ailleurs. Lues par `tracing.redact`.

        Une défense qui ne connaît pas ce qu'elle protège ne protège rien : sans
        cette liste, une clé recopiée par erreur dans un argument d'outil sort
        du processus en clair parce qu'elle ne ressemble à aucun motif connu.
        """
        return tuple(s.get_secret_value() for s in self._secrets.values() if s)

    def model_for(self, tier: str | None = None) -> str:
        """Tier -> identifiant de modèle, tel que déclaré. Jamais un nom en dur."""
        name = tier or self.default_tier
        model = self.tier_map.get(name, "")
        if not model:
            raise ConfigError(
                f"tier `{name}` sans modèle dans `tierMap` ({sorted(self.tier_map)})",
                fix="compléter `## Runtime Models` de STACK.md puis régénérer — un tier non "
                    "résolu ne peut pas être facturé, donc pas plafonné")
        return model

    def traces_dir(self) -> Path:
        return self.workspace_root / ".sys" / "traces" / "runs"

    def prompts_dir(self) -> Path:
        # Les prompts partent AVEC l'application : à côté de ce module, pas dans
        # un répertoire du dépôt qu'un exécutable ou un conteneur n'emporterait pas.
        return Path(__file__).resolve().parent / "prompts"

    def skills_dir(self) -> Path:
        return Path(__file__).resolve().parent / "skills"

    def rules_dir(self) -> Path:
        return Path(__file__).resolve().parent / "rules"
