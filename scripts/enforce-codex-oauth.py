#!/usr/bin/env python3
"""Audit or enforce Codex-OAuth-only OpenAI authentication across the fleet."""

from __future__ import annotations

import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

import argparse
import json
import os
import tempfile
from pathlib import Path


ROOT = Path("/home/aaron/.openclaw")


def _atomic_text(path: Path, text: str, mode: int | None = None) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix="." + path.name + ".", suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.chmod(temporary, mode if mode is not None else (path.stat().st_mode & 0o777))
    os.replace(temporary, path)


def _profile_files(root: Path) -> tuple[list[Path], list[Path]]:
    existing = sorted((root / "agents").glob("*/agent/auth-profiles.json"))
    try:
        config = json.loads((root / "openclaw.json").read_text())
        configured = [
            root / "agents" / str(row["id"]) / "agent/auth-profiles.json"
            for row in config.get("agents", {}).get("list", []) if row.get("id")
        ]
    except (OSError, json.JSONDecodeError, AttributeError):
        configured = []
    # Resolve existing symlinks (jerry -> main) so the same store is not edited
    # twice, while retaining missing configured paths so --apply can create them.
    active_by_key: dict[str, Path] = {}
    for path in [*existing, *configured]:
        key = str(path.resolve()) if path.exists() else str(path)
        active_by_key.setdefault(key, path)
    active = sorted(active_by_key.values(), key=str)
    backups = sorted((root / "credentials/token-backups").glob("auth-profiles.*.json"))
    return active, backups


def _source_codex_profile(paths: list[Path]) -> tuple[str, dict]:
    for path in paths:
        try:
            profiles = json.loads(path.read_text()).get("profiles") or {}
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
        for profile_id, profile in profiles.items():
            if (
                isinstance(profile, dict)
                and profile.get("provider") == "openai-codex"
                and profile.get("type") == "oauth"
            ):
                return str(profile_id), dict(profile)
    raise RuntimeError("no openai-codex OAuth profile is available to seed the fleet")


def enforce(root: Path, *, apply: bool) -> dict:
    active, backups = _profile_files(root)
    source_id, source = _source_codex_profile(active + backups)
    direct_removed = seeded = files_changed = 0
    for path in active + backups:
        missing = path in active and not path.exists()
        if missing:
            payload = {"version": 1, "profiles": {}}
        else:
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"invalid auth profile store {path}: {exc}") from exc
        profiles = payload.setdefault("profiles", {})
        forbidden = [
            key for key, value in profiles.items()
            if key == "openai:default"
            or (isinstance(value, dict) and value.get("provider") == "openai")
        ]
        for key in forbidden:
            profiles.pop(key, None)
            direct_removed += 1
        has_codex = any(
            isinstance(value, dict)
            and value.get("provider") == "openai-codex"
            and value.get("type") == "oauth"
            for value in profiles.values()
        )
        if path in active and not has_codex:
            profiles[source_id] = dict(source)
            seeded += 1
        changed = bool(missing or forbidden or (path in active and not has_codex))
        if changed:
            files_changed += 1
            if apply:
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_text(
                    path, json.dumps(payload, indent=2, sort_keys=True) + "\n", 0o600,
                )

    env_path = root / "credentials/openclaw-gateway.env"
    env_lines = env_path.read_text().splitlines()
    filtered = [line for line in env_lines if not line.startswith("OPENAI_API_KEY=")]
    env_removed = len(env_lines) - len(filtered)
    if env_removed and apply:
        _atomic_text(env_path, "\n".join(filtered) + "\n")
    return {
        "applied": apply,
        "active_stores": len(active),
        "backup_stores": len(backups),
        "files_changed": files_changed,
        "direct_openai_profiles_removed": direct_removed,
        "active_stores_seeded_with_codex_oauth": seeded,
        "gateway_openai_api_key_removed": env_removed,
        "clean": not (files_changed or env_removed),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = enforce(ROOT, apply=args.apply)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0 if args.apply or result["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
