"""Pure validators for OpenClaw configuration invariants.

Keep these checks free of I/O so release gates, health sweeps, and unit tests
all exercise the same policy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


BOOTSTRAP_FILES = (
    "AGENTS.md",
    "SOUL.md",
    "TOOLS.md",
    "IDENTITY.md",
    "USER.md",
    "MEMORY.md",
)


def validate_model_policy(config: dict[str, Any], root: Path) -> list[str]:
    """Return violations of the fleet-wide model/runtime inheritance policy."""
    errors: list[str] = []
    agents = config.get("agents")
    if not isinstance(agents, dict):
        return ["agents must be an object"]

    defaults = agents.get("defaults")
    if not isinstance(defaults, dict):
        return ["agents.defaults must be an object"]
    model = defaults.get("model")
    if not isinstance(model, dict):
        errors.append("agents.defaults.model must include primary and fallbacks")
        primary = None
        fallbacks: list[Any] = []
    else:
        primary = model.get("primary")
        fallbacks = model.get("fallbacks")
        if not isinstance(primary, str) or not primary.strip():
            errors.append("agents.defaults.model.primary must be a non-empty string")
        if not isinstance(fallbacks, list) or not fallbacks:
            errors.append("agents.defaults.model.fallbacks must be a non-empty list")
            fallbacks = []
        elif any(not isinstance(item, str) or not item.strip() for item in fallbacks):
            errors.append("agents.defaults.model.fallbacks must contain only non-empty strings")
        if primary in fallbacks:
            errors.append("primary model cannot also be a fallback")

    registered = defaults.get("models")
    if not isinstance(registered, dict):
        errors.append("agents.defaults.models must register the fleet models")
        registered = {}
    for candidate in [primary, *fallbacks]:
        if isinstance(candidate, str) and candidate not in registered:
            errors.append(f"model {candidate!r} is not registered in agents.defaults.models")

    agent_list = agents.get("list")
    if not isinstance(agent_list, list) or not agent_list:
        return errors + ["agents.list must be a non-empty list"]
    seen: set[str] = set()
    for index, agent in enumerate(agent_list):
        if not isinstance(agent, dict):
            errors.append(f"agents.list[{index}] must be an object")
            continue
        agent_id = agent.get("id")
        label = str(agent_id or index)
        if not isinstance(agent_id, str) or not agent_id:
            errors.append(f"agents.list[{index}].id must be a non-empty string")
        elif agent_id in seen:
            errors.append(f"duplicate agent id {agent_id!r}")
        else:
            seen.add(agent_id)
        if "model" in agent:
            errors.append(
                f"agent {label!r} overrides model; inherit fleet primary+fallbacks instead"
            )
        if "agentRuntime" in agent:
            errors.append(
                f"agent {label!r} uses ignored agentRuntime; configure runtime on the provider"
            )
        workspace = agent.get("workspace")
        if not isinstance(workspace, str) or not workspace.startswith("/"):
            errors.append(f"agent {label!r} workspace must be an absolute path")

    runtime = (
        config.get("models", {})
        .get("providers", {})
        .get("openai", {})
        .get("agentRuntime", {})
    )
    if not isinstance(runtime, dict) or runtime.get("id") != "codex":
        errors.append("models.providers.openai.agentRuntime.id must be 'codex'")

    return errors


def validate_bootstrap_policy(config: dict[str, Any]) -> list[str]:
    """Require enough prompt budget to inject every registered agent's bootstrap."""
    errors: list[str] = []
    agents = config.get("agents", {})
    defaults = agents.get("defaults", {}) if isinstance(agents, dict) else {}
    per_file = defaults.get("bootstrapMaxChars")
    total = defaults.get("bootstrapTotalMaxChars")
    if not isinstance(per_file, int) or per_file <= 0:
        errors.append("agents.defaults.bootstrapMaxChars must be a positive integer")
        per_file = 0
    if not isinstance(total, int) or total <= 0:
        errors.append("agents.defaults.bootstrapTotalMaxChars must be a positive integer")
        total = 0
    agent_list = agents.get("list", []) if isinstance(agents, dict) else []
    for agent in agent_list if isinstance(agent_list, list) else []:
        if not isinstance(agent, dict):
            continue
        workspace = agent.get("workspace")
        if not isinstance(workspace, str):
            continue
        path = Path(workspace)
        if not path.is_dir():
            continue
        sizes = {
            name: len((path / name).read_text())
            for name in BOOTSTRAP_FILES
            if (path / name).is_file()
        }
        if not sizes:
            continue
        largest_name, largest_size = max(sizes.items(), key=lambda item: item[1])
        agent_id = agent.get("id", "unknown")
        if largest_size > per_file:
            errors.append(
                f"agent {agent_id!r} bootstrap {largest_name} has {largest_size} chars "
                f"> bootstrapMaxChars {per_file}"
            )
        required_total = sum(sizes.values())
        if required_total > total:
            errors.append(
                f"agent {agent_id!r} bootstrap needs {required_total} chars "
                f"> bootstrapTotalMaxChars {total}"
            )
    return errors


def validate_operator_policy(config: dict[str, Any]) -> list[str]:
    """Require privileged commands to have an explicit, authorized human owner."""
    commands = config.get("commands")
    if not isinstance(commands, dict):
        return ["commands must be an object"]
    owners = commands.get("ownerAllowFrom")
    if not isinstance(owners, list) or not owners:
        return ["commands.ownerAllowFrom must name at least one explicit owner"]
    allowed_telegram = commands.get("allowFrom", {}).get("telegram", [])
    errors: list[str] = []
    for owner in owners:
        if not isinstance(owner, str) or not owner.startswith("telegram:"):
            errors.append(f"unsupported command owner {owner!r}; expected telegram:<user-id>")
            continue
        user_id = owner.removeprefix("telegram:")
        if user_id not in allowed_telegram:
            errors.append(f"command owner {owner!r} is not in commands.allowFrom.telegram")
    return errors


def validate_reference_policy(config: dict[str, Any], root: Path) -> list[str]:
    """Reject dangling agents, channels, workspaces, credentials, and skills."""
    errors: list[str] = []
    agents = config.get("agents", {})
    agent_list = agents.get("list", []) if isinstance(agents, dict) else []
    agent_ids = {
        agent.get("id")
        for agent in agent_list
        if isinstance(agent, dict) and isinstance(agent.get("id"), str)
    }
    workspaces: list[Path] = []
    for agent in agent_list if isinstance(agent_list, list) else []:
        if not isinstance(agent, dict):
            continue
        workspace = agent.get("workspace")
        if not isinstance(workspace, str):
            continue
        path = Path(workspace)
        if not path.is_dir():
            errors.append(f"agent {agent.get('id')!r} workspace does not exist: {workspace}")
        else:
            workspaces.append(path)

    telegram = config.get("channels", {}).get("telegram", {})
    accounts = telegram.get("accounts", {}) if isinstance(telegram, dict) else {}
    if not isinstance(accounts, dict):
        errors.append("channels.telegram.accounts must be an object")
        accounts = {}
    for account_id, account in accounts.items():
        if not isinstance(account, dict) or account.get("enabled", True) is False:
            continue
        token_file = account.get("tokenFile")
        if not isinstance(token_file, str) or not Path(token_file).is_file():
            errors.append(
                f"enabled Telegram account {account_id!r} has no readable tokenFile"
            )

    for index, binding in enumerate(config.get("bindings", [])):
        if not isinstance(binding, dict):
            errors.append(f"bindings[{index}] must be an object")
            continue
        agent_id = binding.get("agentId")
        if agent_id not in agent_ids:
            errors.append(f"binding references unknown agent {agent_id!r}")
        match = binding.get("match", {})
        if match.get("channel") == "telegram" and match.get("accountId") not in accounts:
            errors.append(
                f"binding references unknown Telegram account {match.get('accountId')!r}"
            )

    groups = telegram.get("groups", {}) if isinstance(telegram, dict) else {}
    if isinstance(groups, dict):
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            if "agentId" in group and group.get("agentId") not in agent_ids:
                errors.append(f"Telegram group {group_id} references unknown agent {group.get('agentId')!r}")
            topics = group.get("topics", {})
            if not isinstance(topics, dict):
                continue
            for topic_id, topic in topics.items():
                if (
                    isinstance(topic, dict)
                    and "agentId" in topic
                    and topic.get("agentId") not in agent_ids
                ):
                    errors.append(
                        f"Telegram topic {group_id}/{topic_id} references unknown agent "
                        f"{topic.get('agentId')!r}"
                    )

    available_skills = {
        path.parent.name
        for base in [root / "workspace", *workspaces]
        for path in (base / "skills").glob("*/SKILL.md")
    }
    skill_refs: set[str] = set()
    defaults = agents.get("defaults", {}) if isinstance(agents, dict) else {}
    if isinstance(defaults, dict):
        skill_refs.update(defaults.get("skills", []))
    for agent in agent_list if isinstance(agent_list, list) else []:
        if isinstance(agent, dict):
            skill_refs.update(agent.get("skills", []))
    if isinstance(groups, dict):
        for group in groups.values():
            if not isinstance(group, dict):
                continue
            skill_refs.update(group.get("skills", []))
            topics = group.get("topics", {})
            if isinstance(topics, dict):
                for topic in topics.values():
                    if isinstance(topic, dict):
                        skill_refs.update(topic.get("skills", []))
    missing_skills = sorted(skill_refs - available_skills)
    if missing_skills:
        errors.append("configured skills do not exist: " + ", ".join(missing_skills))
    return errors
