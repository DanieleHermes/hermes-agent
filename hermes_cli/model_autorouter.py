"""Operator-visible model autorouter for session startup.

This module deliberately keeps routing deterministic and config-owned.  It does
not inspect prompts and it does not run per-call plugin hooks; it only chooses an
initial session route from an explicit ``model.autorouter`` config block when no
CLI/session override already picked a route.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


AUTOROUTER_PROVIDER = "openai-codex"
AUTOROUTER_MODELS = frozenset(
    {
        "gpt-5.6-terra-pro",
        "gpt-5.6-sol-pro",
        "gpt-5.6-luna-pro",
    }
)


@dataclass(frozen=True)
class AutorouterDecision:
    """Resolved autorouter route."""

    name: str
    provider: str
    model: str
    reason: str


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _string_set(values: Iterable[Any]) -> set[str]:
    return {str(v).strip().lower() for v in values if str(v).strip()}


def _route_items(routes: Any) -> list[tuple[str, Mapping[str, Any]]]:
    """Normalize list/dict route config into ``[(name, route), ...]``."""

    out: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(routes, Mapping):
        for name, route in routes.items():
            if isinstance(route, Mapping):
                out.append((str(name), route))
        return out
    if isinstance(routes, Sequence) and not isinstance(routes, (str, bytes, bytearray)):
        for idx, route in enumerate(routes):
            if not isinstance(route, Mapping):
                continue
            name = str(route.get("name") or f"route-{idx + 1}")
            out.append((name, route))
    return out


def _path_exists(cwd: Path, rel: Any) -> bool:
    try:
        rel_s = str(rel).strip()
    except Exception:
        return False
    if not rel_s:
        return False
    return (cwd / rel_s).exists()


def _matches_when(
    when: Any,
    *,
    cwd: Path,
    platform: str,
    enabled_toolsets: Iterable[str] | None,
) -> bool:
    """Return whether a route predicate matches the current session context."""

    if not when:
        return True
    if not isinstance(when, Mapping):
        return False

    platform_norm = (platform or "").strip().lower()
    if "platform" in when:
        allowed = _string_set(_as_list(when.get("platform")))
        if allowed and platform_norm not in allowed:
            return False

    toolsets = _string_set(enabled_toolsets or [])
    if "toolsets_any" in when:
        wanted = _string_set(_as_list(when.get("toolsets_any")))
        if wanted and not (toolsets & wanted):
            return False
    if "toolsets_all" in when:
        wanted = _string_set(_as_list(when.get("toolsets_all")))
        if wanted and not wanted.issubset(toolsets):
            return False

    cwd_has = _as_list(when.get("cwd_has"))
    if cwd_has and not all(_path_exists(cwd, item) for item in cwd_has):
        return False

    cwd_has_any = _as_list(when.get("cwd_has_any"))
    if cwd_has_any and not any(_path_exists(cwd, item) for item in cwd_has_any):
        return False

    return True


def _route_decision(
    name: str,
    route: Mapping[str, Any],
    *,
    reason: str,
) -> AutorouterDecision | None:
    provider = str(route.get("provider") or AUTOROUTER_PROVIDER).strip()
    model = str(route.get("model") or route.get("default") or "").strip()
    if not model:
        return None
    if provider != AUTOROUTER_PROVIDER:
        raise ValueError(
            f"model.autorouter.routes.{name} uses provider {provider!r}; "
            f"autorouter routes are limited to {AUTOROUTER_PROVIDER!r}"
        )
    if model not in AUTOROUTER_MODELS:
        allowed = ", ".join(sorted(AUTOROUTER_MODELS))
        raise ValueError(
            f"model.autorouter.routes.{name} uses unsupported model {model!r}; "
            f"allowed models: {allowed}"
        )
    return AutorouterDecision(name=name, provider=provider, model=model, reason=reason)


def resolve_model_autorouter(
    config: Mapping[str, Any],
    *,
    explicit_model: bool = False,
    explicit_provider: bool = False,
    cwd: str | Path | None = None,
    platform: str = "",
    enabled_toolsets: Iterable[str] | None = None,
) -> AutorouterDecision | None:
    """Resolve the configured startup route, if autorouting should apply.

    ``model.autorouter`` schema::

        model:
          autorouter:
            enabled: true
            default_route: balanced
            routes:
              coding:
                provider: openai-codex
                model: gpt-5.6-terra-pro
                when:
                  cwd_has_any: [.git, pyproject.toml, package.json]
              balanced:
                provider: openai-codex
                model: gpt-5.6-sol-pro

    Routes are intentionally constrained to the OpenAI Codex 5.6 Pro family:
    ``gpt-5.6-terra-pro``, ``gpt-5.6-sol-pro``, and
    ``gpt-5.6-luna-pro``.

    Explicit CLI/session choices win.  That keeps the autorouter an
    operator-visible session-start default, not a hidden per-call override.
    """

    if explicit_model or explicit_provider:
        return None

    model_cfg = config.get("model") if isinstance(config, Mapping) else None
    if not isinstance(model_cfg, Mapping):
        return None
    router_cfg = model_cfg.get("autorouter")
    if not isinstance(router_cfg, Mapping) or not _as_bool(router_cfg.get("enabled")):
        return None

    cwd_path = Path(cwd or ".").expanduser()
    try:
        cwd_path = cwd_path.resolve()
    except OSError:
        cwd_path = cwd_path.absolute()

    routes = _route_items(router_cfg.get("routes"))
    if not routes:
        return None
    for name, route in routes:
        _route_decision(name, route, reason="validate model.autorouter route")

    default_route_name = str(router_cfg.get("default_route") or router_cfg.get("default") or "").strip()
    default_candidate: tuple[str, Mapping[str, Any]] | None = None

    for name, route in routes:
        if name == default_route_name:
            default_candidate = (name, route)
        if "when" not in route and default_candidate is None:
            default_candidate = (name, route)
        if "when" not in route:
            continue
        if _matches_when(route.get("when"), cwd=cwd_path, platform=platform, enabled_toolsets=enabled_toolsets):
            return _route_decision(
                name,
                route,
                reason=str(route.get("reason") or f"matched model.autorouter.routes.{name}"),
            )

    if default_candidate is None:
        return None
    name, route = default_candidate
    return _route_decision(
        name,
        route,
        reason=str(route.get("reason") or f"using model.autorouter default route {name}"),
    )
