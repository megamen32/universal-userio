#!/usr/bin/env python3
"""Audit a drifted Universal UserIO runtime without reading private state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


class ReconciliationError(RuntimeError):
    """The runtime cannot be audited safely or completely."""


@dataclass(frozen=True, slots=True)
class StatusRecord:
    status: str
    path: str
    original_path: str | None = None


_BACKUP_RE = re.compile(r"(?:^|/)(?:\.backup[^/]*|backup[^/]*)(?:/|$)|\.bak(?:\.|$)|\.(?:old|orig|save|swp)$", re.I)
_SECRET_NAME_RE = re.compile(r"(?:^|[-_.])(secret|credential|credentials|cookie|cookies|token|tokens|session)(?:[-_.]|$)", re.I)
_SOURCE_SUFFIXES = {".cjs", ".js", ".jsonl", ".md", ".mjs", ".py", ".ts", ".tsx"}
_CONFIG_NAMES = {
    ".env.example",
    ".gitignore",
    "mcp.json",
    "package-lock.json",
    "package.json",
    "plugin.json",
    "pyproject.toml",
    "uv.lock",
}
_EXCLUDED_CATEGORIES = {
    "backup",
    "diagnostic_state",
    "generated_build",
    "generated_dependency",
}
_POLICY_DISPOSITIONS = {
    "exclude_runtime_artifact",
    "preserve_canonical",
    "preserve_runtime",
}


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReconciliationError(f"git command failed for {root}: {args[0]}") from error


def _git_head(root: Path) -> str:
    head = _git(root, "rev-parse", "HEAD").decode("ascii", "strict").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ReconciliationError(f"invalid git head for {root}")
    return head


def parse_porcelain_v1_z(payload: bytes) -> list[StatusRecord]:
    """Parse `git status --porcelain=v1 -z` without treating paths as syntax."""

    chunks = payload.split(b"\0")
    if chunks and chunks[-1] == b"":
        chunks.pop()
    records: list[StatusRecord] = []
    index = 0
    while index < len(chunks):
        raw = chunks[index]
        if len(raw) < 4 or raw[2:3] != b" ":
            raise ReconciliationError("invalid porcelain v1 -z record")
        status_code = raw[:2].decode("ascii", "strict")
        path = os.fsdecode(raw[3:])
        original_path = None
        if "R" in status_code or "C" in status_code:
            index += 1
            if index >= len(chunks):
                raise ReconciliationError("rename/copy record lacks original path")
            original_path = os.fsdecode(chunks[index])
        records.append(StatusRecord(status_code, path, original_path))
        index += 1
    return records


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ReconciliationError("unsafe relative path in git status")
    return path


def _classify(path: str, status_code: str) -> str:
    relative = _safe_relative(path)
    lowered = path.lower()
    parts = tuple(part.lower() for part in relative.parts)
    basename = parts[-1]
    untracked = status_code == "??"

    if basename.startswith(".env") and basename != ".env.example":
        return "secret_session"
    if ".agents" in parts and "shared-session" in parts:
        return "secret_session"
    if untracked and (_SECRET_NAME_RE.search(basename) or basename.endswith((".tok", ".session"))):
        return "secret_session"
    if any(
        part in {".mypy_cache", ".pytest_cache", ".venv", "__pycache__", "build", "coverage", "dist", "node_modules"}
        or part.endswith(".egg-info")
        for part in parts
    ):
        return "generated_dependency"
    if _BACKUP_RE.search(lowered):
        return "backup"
    if parts[0] in {".tmp", "tmp"} or any(part in {"logs", "screenshots", "test-results"} for part in parts):
        return "diagnostic_state"
    if basename.endswith((".db", ".log", ".pid", ".sock", ".sqlite", ".sqlite3")):
        return "diagnostic_state"
    if lowered.startswith("src/universal_userio/static/assets/") or (
        lowered.startswith("src/universal_userio/static/") and basename.endswith((".crx", ".zip"))
    ):
        return "generated_build"
    if parts[0] in {"config", "deploy"} or basename in _CONFIG_NAMES:
        return "config"
    if parts[0] in {".agents", "docs", "extensions", "scripts", "src", "tests"}:
        return "source"
    if Path(basename).suffix.lower() in _SOURCE_SUFFIXES:
        return "source"
    return "unknown"


def _metadata(root: Path, relative: str, *, read_content: bool = False) -> dict[str, Any]:
    target = root / _safe_relative(relative)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return {"present": False, "mode": None, "sha256": None}
    if stat.S_ISLNK(info.st_mode):
        try:
            resolved = target.resolve(strict=True)
        except (FileNotFoundError, RuntimeError) as error:
            raise ReconciliationError(f"invalid symlink in {root.name} root") from error
        try:
            resolved.relative_to(root.resolve(strict=True))
        except ValueError as error:
            raise ReconciliationError(f"symlink escapes {root.name} root") from error
        return {"present": True, "mode": f"{stat.S_IMODE(info.st_mode):04o}", "sha256": None}
    digest = None
    if read_content and stat.S_ISREG(info.st_mode):
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "present": True,
        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
        "sha256": digest,
    }


def _load_policy(policy: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    if policy is None:
        return {}
    if policy.get("schema_version") != 1 or not isinstance(policy.get("decisions", {}), dict):
        raise ReconciliationError("reconciliation policy must use schema_version 1 and decisions")
    decisions: dict[str, dict[str, str]] = {}
    for raw_path, raw_decision in policy.get("decisions", {}).items():
        if not isinstance(raw_path, str) or not isinstance(raw_decision, dict):
            raise ReconciliationError("policy decisions must map paths to objects")
        normalized = _safe_relative(raw_path).as_posix()
        disposition = raw_decision.get("disposition")
        reason = raw_decision.get("reason")
        if disposition not in _POLICY_DISPOSITIONS or not isinstance(reason, str) or not reason.strip():
            raise ReconciliationError(f"invalid policy decision for {normalized}")
        decisions[normalized] = {"disposition": disposition, "reason": reason.strip()}
    return decisions


def _redacted_path_id(path: str, canonical_head: str, runtime_head: str) -> str:
    domain = f"userio-runtime-reconcile-v1\0{canonical_head}\0{runtime_head}\0{path}"
    return hashlib.sha256(domain.encode("utf-8", "surrogateescape")).hexdigest()[:24]


def audit_runtime(
    canonical: str | Path,
    runtime: str | Path,
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    canonical_root = Path(canonical).resolve(strict=True)
    runtime_root = Path(runtime).resolve(strict=True)
    canonical_head = _git_head(canonical_root)
    runtime_head = _git_head(runtime_root)
    records = parse_porcelain_v1_z(
        _git(runtime_root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    )
    decisions = _load_policy(policy)
    used_decisions: set[str] = set()
    entries: list[dict[str, Any]] = []

    for record in records:
        normalized = _safe_relative(record.path).as_posix()
        category = _classify(normalized, record.status)
        read_content = category in {"source", "config"}
        runtime_meta = _metadata(runtime_root, normalized, read_content=read_content)
        if category == "secret_session":
            entries.append(
                {
                    "status": record.status,
                    "category": category,
                    "disposition": "exclude_secret",
                    "reason": "private runtime state is never inspected or promoted",
                    "path_id": _redacted_path_id(normalized, canonical_head, runtime_head),
                    "runtime_present": runtime_meta["present"],
                    "runtime_mode": runtime_meta["mode"],
                }
            )
            continue

        canonical_meta = _metadata(canonical_root, normalized, read_content=read_content)
        if category in _EXCLUDED_CATEGORIES:
            disposition = "exclude_runtime_artifact"
            reason = f"{category} is not canonical source"
        elif category == "unknown":
            disposition = "unresolved"
            reason = "path does not match an approved reconciliation category"
        elif normalized in decisions:
            decision = decisions[normalized]
            used_decisions.add(normalized)
            disposition = decision["disposition"]
            reason = decision["reason"]
            if disposition == "preserve_canonical" and not canonical_meta["present"]:
                disposition = "unresolved"
                reason = "policy requires canonical content, but the canonical path is absent"
            elif disposition == "preserve_runtime" and not runtime_meta["present"]:
                disposition = "unresolved"
                reason = "policy requires runtime content, but the runtime path is absent"
        elif (
            runtime_meta["present"]
            and canonical_meta["present"]
            and runtime_meta["sha256"] is not None
            and runtime_meta["sha256"] == canonical_meta["sha256"]
        ):
            disposition = "already_canonical"
            reason = "runtime content matches canonical source"
        else:
            disposition = "unresolved"
            reason = "safe source/config delta requires review"

        entry: dict[str, Any] = {
            "status": record.status,
            "path": normalized,
            "category": category,
            "disposition": disposition,
            "reason": reason,
            "runtime_present": runtime_meta["present"],
            "runtime_mode": runtime_meta["mode"],
            "canonical_present": canonical_meta["present"],
            "canonical_mode": canonical_meta["mode"],
        }
        if record.original_path is not None:
            original = _safe_relative(record.original_path).as_posix()
            if _classify(original, record.status) == "secret_session":
                entry["original_path_id"] = _redacted_path_id(
                    original, canonical_head, runtime_head
                )
            else:
                entry["original_path"] = original
        if category in {"source", "config"}:
            entry["runtime_sha256"] = runtime_meta["sha256"]
            entry["canonical_sha256"] = canonical_meta["sha256"]
        entries.append(entry)

    unused_decisions = sorted(set(decisions) - used_decisions)
    if unused_decisions:
        raise ReconciliationError(
            "policy contains paths absent from the current safe status set: "
            + ", ".join(unused_decisions)
        )
    entries.sort(key=lambda row: (str(row.get("path", "")), str(row.get("path_id", ""))))
    category_counts = dict(sorted(Counter(row["category"] for row in entries).items()))
    disposition_counts = dict(sorted(Counter(row["disposition"] for row in entries).items()))
    unresolved_count = disposition_counts.get("unresolved", 0)
    if sum(category_counts.values()) != len(entries) or sum(disposition_counts.values()) != len(entries):
        raise ReconciliationError("reconciliation totals do not match status entries")
    return {
        "schema_version": 1,
        "canonical_head": canonical_head,
        "runtime_head": runtime_head,
        "total_entries": len(entries),
        "category_counts": category_counts,
        "disposition_counts": disposition_counts,
        "unresolved_count": unresolved_count,
        "entries": entries,
    }


def write_report(output: str | Path, report: Mapping[str, Any]) -> None:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _policy_file(path: str | None) -> Mapping[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReconciliationError("cannot read reconciliation policy") from error
    if not isinstance(value, dict):
        raise ReconciliationError("reconciliation policy must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--policy")
    parser.add_argument("--output")
    parser.add_argument("--require-clean-resolution", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        report = audit_runtime(
            arguments.canonical,
            arguments.runtime,
            policy=_policy_file(arguments.policy),
        )
        if arguments.output:
            write_report(arguments.output, report)
        summary = {
            "canonical_head": report["canonical_head"],
            "runtime_head": report["runtime_head"],
            "total_entries": report["total_entries"],
            "category_counts": report["category_counts"],
            "disposition_counts": report["disposition_counts"],
            "unresolved_count": report["unresolved_count"],
        }
        print(json.dumps(summary, sort_keys=True))
        if arguments.require_clean_resolution and report["unresolved_count"]:
            return 2
        return 0
    except ReconciliationError as error:
        print(json.dumps({"error": str(error)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
