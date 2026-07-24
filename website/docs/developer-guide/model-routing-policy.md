---
sidebar_position: 5
title: "Model Routing Policy"
description: "The supported boundaries for model switching, fallback chains, delegation, auxiliary routing, and plugin extension points"
---

# Model Routing Policy

Hermes supports model selection, model switching, and failover, but keeps the authority for runtime model choice in a small set of operator-controlled surfaces. This policy is intentionally simple: model routing should be predictable, auditable, and stable across CLI, gateway, cron, and delegated work.

## Policy summary

1. **The session route is the default authority.** A normal chat turn uses the model/provider selected for the current session by `hermes model`, `/model`, CLI flags, or `config.yaml`.
2. **Fallback is ordered and operator-configured.** When the active route fails, Hermes may walk the configured `fallback_providers` chain. The chain is static config, not a plugin- or prompt-selected per-call router.
3. **Auxiliary tasks may have task-scoped config.** Vision, compression, title generation, and similar host-owned auxiliary tasks may use `auxiliary.<task>.provider`, `auxiliary.<task>.model`, and `auxiliary.<task>.fallback_chain`.
4. **Delegated subagents are not selectable per task.** `delegate_task` children inherit the parent route unless the operator pins delegation globally with `delegation.provider` / `delegation.model` in config.
5. **Plugins may add context, not pick the runtime route for a turn.** Hooks such as `pre_llm_call` may inject bounded context. They must not override `provider`, `model`, `base_url`, credentials, system prompt, or fallback decisions for the current LLM call.

## Supported surfaces

| Surface | Supported route control | Notes |
|---|---|---|
| Main chat / CLI / TUI | `hermes model`, `/model`, CLI `--model` / `--provider`, `model.*` config | Per-session switches are explicit user/operator actions. |
| Gateway sessions | Session model override plus profile config | Keeps channel/session behavior predictable and resumable. |
| Fallback after provider failure | Top-level `fallback_providers` chain | Used for quota, auth, server, or transport failures according to the retry/fallback classifier. |
| Auxiliary calls | `auxiliary.<task>.*` config and task fallback chains | Host-owned side tasks can be cheaper/faster/safer without changing the main conversation route. |
| Cron jobs | Job-level model override plus profile fallback config | The scheduled job definition is the durable authority. |
| Delegation | `delegation.provider` / `delegation.model` config | No per-call or per-task model parameter in `delegate_task`. |
| Model-provider plugins | Provider registration and credential/runtime metadata | Plugins may define provider capabilities and defaults; they do not route each turn dynamically. |

## Non-goals

Hermes does **not** add these as core or plugin APIs:

- per-turn `resolve_route` hooks;
- `pre_llm_call` return values that override `model`, `provider`, `base_url`, credentials, `api_mode`, or system prompt;
- per-task `delegate_task(..., model=...)` routing;
- plugin-controlled failover redirects that choose an arbitrary fallback provider/model at failure time;
- complexity-, topic-, quota-, or cost-based model selection on every call as a hidden runtime layer.

These mechanisms are attractive for experimentation, but they make production behavior harder to reason about: transcripts no longer explain which authority picked the model, prompt caching becomes fragile, credential boundaries are harder to audit, and gateway/delegation behavior can drift between sessions.

## Why the boundary exists

Model routing affects reliability, cost, privacy, and user trust. Hermes therefore keeps route authority in places that are visible before the call starts:

- user/session selection;
- profile configuration;
- scheduled job configuration;
- operator-configured fallback order;
- explicit auxiliary/delegation config.

That gives maintainers and operators one place to inspect why a model was used, and it avoids letting third-party plugin code or model-generated context silently change which provider receives user data.

## Recommended pattern for smart fallback

For quota exhaustion or provider outage, use configured fallback chains instead of a plugin redirect hook:

```yaml
model:
  provider: openai-codex
  default: gpt-5.6

fallback_providers:
  - provider: anthropic
    model: claude-sonnet-4-20250514
  - provider: openrouter
    model: google/gemini-2.5-pro
```

Expected behavior:

1. The primary route handles the turn when healthy.
2. Hard usage-limit, quota, auth, or server failures are classified by the retry/fallback layer.
3. Hermes tries the configured fallback entries in order.
4. If fallback succeeds, the turn continues with preserved session context.
5. If fallback also fails, the error should describe the primary and fallback failures.

## Recommended pattern for auxiliary work

Use task-specific auxiliary config when the side task can safely use a different route:

```yaml
auxiliary:
  compression:
    provider: openrouter
    model: google/gemini-2.5-flash
    fallback_chain:
      - provider: openrouter
        model: anthropic/claude-haiku-4.5
```

Keep safety-sensitive tasks on capable models when cheap fallback would degrade correctness.

## Recommended pattern for delegation

If all subagents in a profile should use a different model, pin delegation globally:

```yaml
delegation:
  provider: anthropic
  model: claude-sonnet-4-20250514
```

Do not add per-task model selectors to the tool schema. If a task needs a different route, use a separate profile, a scheduled job with a model override, or a future operator-visible profile preset surface.

## Guidance for plugin authors

Plugins that want to influence model choice should use one of these safe patterns:

- register a model provider plugin so Hermes can resolve that provider through normal config;
- inject context explaining a recommendation to the user/operator;
- expose a command or settings UI that updates config through an explicit operator action;
- use `plugin_llm` for the plugin's own out-of-band calls, with host-owned credentials and audit metadata.

Plugins should not monkey-patch the agent loop or retry path to select models dynamically. If a plugin needs a new route-control surface, propose it as an operator-visible config or profile preset rather than a hidden per-call override.

## Related docs

- [Provider Runtime Resolution](./provider-runtime)
- [Agent Loop Internals](./agent-loop)
- [Plugin LLM Access](./plugin-llm-access)
- [Configuring Models](/user-guide/configuring-models)
