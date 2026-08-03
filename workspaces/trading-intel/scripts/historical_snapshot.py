#!/usr/bin/env python3
"""Create and verify immutable inputs for historical AutoTrade replays."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/home/aaron/.openclaw")
DEFAULT_SOURCE_DB = ROOT / "state/features.sqlite"
DEFAULT_CACHE_DIR = ROOT / "state/market-data-cache"
DEFAULT_OUTPUT = ROOT / "state/research-snapshots/purged_walkforward_v1"
SCHEMA_VERSION = 1
SQLITE_TRANSIENT_SUFFIXES = ("-wal", "-shm", "-journal")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_digest(files: list[dict]) -> str:
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _cached_price_path(cache_dir: Path, symbol: str) -> Path | None:
    escaped = glob.escape(symbol.lower())
    wide = sorted(cache_dir.glob(f"massive_{escaped}_1d_2015-01-01_*.json"), reverse=True)
    candidates = wide or sorted(cache_dir.glob(f"massive_{escaped}_1d_*.json"), reverse=True)
    for path in candidates:
        try:
            bars = json.loads(path.read_text()).get("bars") or []
        except (OSError, json.JSONDecodeError):
            continue
        if any(
            bar.get("t") and bar.get("c") is not None and float(bar["c"]) > 0
            for bar in bars
        ):
            return path
    return None


def _copy_stable(source: Path, target: Path) -> None:
    before = source.stat()
    shutil.copy2(source, target)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"source changed while snapshotting: {source}")
    if _sha256_file(source) != _sha256_file(target):
        raise RuntimeError(f"snapshot copy digest mismatch: {source}")


def _write_manifest(snapshot_dir: Path, *, missing_symbols: list[str]) -> dict:
    files = []
    for path in sorted(
        p for p in snapshot_dir.rglob("*")
        if p.is_file()
        and p.name != "manifest.json"
        and not p.name.endswith(SQLITE_TRANSIENT_SUFFIXES)
    ):
        rel = path.relative_to(snapshot_dir).as_posix()
        files.append({
            "path": rel,
            "size": path.stat().st_size,
            "sha256": _sha256_file(path),
        })
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "file_count": len(files),
        "total_bytes": sum(row["size"] for row in files),
        "snapshot_sha256": _manifest_digest(files),
        "missing_price_symbols": sorted(missing_symbols),
        "files": files,
    }
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return manifest


def validate_snapshot(snapshot_dir: Path) -> dict:
    root = snapshot_dir.resolve()
    for suffix in SQLITE_TRANSIENT_SUFFIXES:
        if Path(str(root / "features.sqlite") + suffix).exists():
            raise RuntimeError("historical snapshot contains transient SQLite state")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("unsupported historical snapshot schema")
    files = manifest.get("files") or []
    if any(str(row.get("path") or "").endswith(SQLITE_TRANSIENT_SUFFIXES) for row in files):
        raise RuntimeError("historical snapshot manifest contains transient SQLite state")
    if manifest.get("snapshot_sha256") != _manifest_digest(files):
        raise RuntimeError("historical snapshot manifest digest mismatch")
    if manifest.get("file_count") != len(files):
        raise RuntimeError("historical snapshot file count mismatch")
    total = 0
    for row in files:
        path = (root / str(row.get("path") or "")).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("historical snapshot contains path traversal") from exc
        if not path.is_file():
            raise RuntimeError(f"historical snapshot file missing: {row.get('path')}")
        size = path.stat().st_size
        if size != row.get("size") or _sha256_file(path) != row.get("sha256"):
            raise RuntimeError(f"historical snapshot file changed: {row.get('path')}")
        total += size
    if total != manifest.get("total_bytes"):
        raise RuntimeError("historical snapshot byte count mismatch")
    required = {"features.sqlite"}
    present = {row["path"] for row in files}
    if not required <= present or not any(path.startswith("cache/fred_") for path in present):
        raise RuntimeError("historical snapshot lacks the feature DB or FRED inputs")
    return {
        "sha256": manifest["snapshot_sha256"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "missing_price_symbols": manifest.get("missing_price_symbols") or [],
    }


def create_snapshot(
    output: Path,
    *,
    source_db: Path = DEFAULT_SOURCE_DB,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> dict:
    output = output.resolve()
    staging = output.with_name(output.name + ".building")
    if output.exists():
        raise RuntimeError(f"snapshot already exists: {output}")
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "cache").mkdir(parents=True)
    try:
        source = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True, timeout=60.0)
        target = sqlite3.connect(staging / "features.sqlite")
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
            target.commit()
        finally:
            target.close()
            source.close()

        frozen = sqlite3.connect(f"file:{staging / 'features.sqlite'}?mode=ro", uri=True)
        try:
            universe = sorted(
                row[0] for row in frozen.execute(
                    "SELECT DISTINCT ticker FROM features WHERE source='price'"
                )
            )
        finally:
            frozen.close()

        for suffix in SQLITE_TRANSIENT_SUFFIXES:
            transient = Path(str(staging / "features.sqlite") + suffix)
            if transient.exists():
                transient.unlink()

        missing = []
        for symbol in sorted(set(universe) | {"SPY"}):
            path = _cached_price_path(cache_dir, symbol)
            if path is None:
                missing.append(symbol)
                continue
            _copy_stable(path, staging / "cache" / path.name)
        for path in sorted(cache_dir.glob("fred_*.json")):
            _copy_stable(path, staging / "cache" / path.name)
        if "SPY" in missing:
            raise RuntimeError("snapshot cannot be built without frozen SPY bars")

        _write_manifest(staging, missing_symbols=missing)
        result = validate_snapshot(staging)
        output.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, output)
        for path in sorted(output.rglob("*"), reverse=True):
            path.chmod(0o555 if path.is_dir() else 0o444)
        output.chmod(0o555)
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    create.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    create.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    verify = sub.add_parser("verify")
    verify.add_argument("--snapshot", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.command == "create":
        result = create_snapshot(
            args.output, source_db=args.source_db, cache_dir=args.cache_dir,
        )
    else:
        result = validate_snapshot(args.snapshot)
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
