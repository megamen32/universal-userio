#!/usr/bin/env python3
"""Build, install, verify, restore, and canary an exact UserIO release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


class ReleaseError(RuntimeError):
    """A release precondition or verification failed."""


_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SERVICE_PATHS = {
    "deploy/universal-userio.service",
    "deploy/userio-agentcall-ingress.service",
}
_REQUIRED_PATHS = {"pyproject.toml", *_SERVICE_PATHS}
_OBSOLETE_RUNTIME_PATHS = {"src/universal_userio/email.py"}


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseError(f"git command failed: {args[0]}") from error


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise ReleaseError("unsafe manifest path")
    return path


def _is_managed_source(path: str) -> bool:
    return (
        path == "pyproject.toml"
        or path in _SERVICE_PATHS
        or path.startswith("src/universal_userio/")
    )


def _source_identity(source: Path, remote_ref: str) -> str:
    if _git(source, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise ReleaseError("release requires a clean canonical worktree")
    branch = _git(source, "branch", "--show-current").decode().strip()
    if branch != "main":
        raise ReleaseError(f"canonical release branch must be main, got {branch or 'detached'}")
    head = _git(source, "rev-parse", "HEAD").decode("ascii").strip()
    remote = _git(source, "rev-parse", remote_ref).decode("ascii").strip()
    if not _COMMIT_RE.fullmatch(head) or head != remote:
        raise ReleaseError(f"canonical HEAD must equal {remote_ref}")
    return head


def _manifest_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": manifest.get("schema_version"),
        "commit": manifest.get("commit"),
        "entries": manifest.get("entries"),
    }


def _validate_manifest(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    if set(manifest) != {"schema_version", "commit", "entries", "manifest_sha256"}:
        raise ReleaseError("manifest has unexpected fields")
    if manifest.get("schema_version") != 1 or isinstance(manifest.get("schema_version"), bool):
        raise ReleaseError("manifest schema_version must be 1")
    commit = manifest.get("commit")
    digest = manifest.get("manifest_sha256")
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise ReleaseError("manifest commit is invalid")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ReleaseError("manifest digest is invalid")
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ReleaseError("manifest entries are required")
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict) or set(raw) != {"path", "mode", "sha256"}:
            raise ReleaseError("manifest entry has unexpected fields")
        path = raw.get("path")
        mode = raw.get("mode")
        sha = raw.get("sha256")
        if not isinstance(path, str) or not _is_managed_source(path):
            raise ReleaseError("manifest contains an unmanaged path")
        normalized = _safe_relative(path).as_posix()
        if normalized != path or path in seen:
            raise ReleaseError("manifest path is duplicated or not normalized")
        if mode not in {"0644", "0755"}:
            raise ReleaseError("manifest mode is unsafe or unsupported")
        if not isinstance(sha, str) or not _SHA256_RE.fullmatch(sha):
            raise ReleaseError("manifest entry digest is invalid")
        entries.append({"path": path, "mode": mode, "sha256": sha})
        seen.add(path)
    if not _REQUIRED_PATHS.issubset(seen):
        raise ReleaseError("manifest lacks required deployment files")
    if entries != sorted(entries, key=lambda entry: entry["path"]):
        raise ReleaseError("manifest entries must be sorted")
    calculated = _sha256(_canonical_json(_manifest_payload(manifest)))
    if calculated != digest:
        raise ReleaseError("manifest digest mismatch")
    return entries


def build_manifest(source: str | Path, remote_ref: str = "origin/main") -> dict[str, Any]:
    source_root = Path(source).resolve(strict=True)
    commit = _source_identity(source_root, remote_ref)
    raw = _git(
        source_root,
        "ls-tree",
        "-rz",
        "--full-tree",
        commit,
        "--",
        "pyproject.toml",
        "src/universal_userio",
        *_SERVICE_PATHS,
    )
    entries: list[dict[str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        if not separator:
            raise ReleaseError("git ls-tree returned an invalid record")
        try:
            mode, object_type, object_id = metadata.decode("ascii").split()
            path = os.fsdecode(raw_path)
        except (UnicodeError, ValueError) as error:
            raise ReleaseError("git ls-tree returned an invalid record") from error
        if object_type != "blob" or mode == "120000":
            raise ReleaseError(f"manifest refuses symlink or non-blob: {path}")
        if mode not in {"100644", "100755"} or not _is_managed_source(path):
            raise ReleaseError(f"manifest refuses unsupported tracked path: {path}")
        content = _git(source_root, "cat-file", "blob", object_id)
        entries.append(
            {
                "path": _safe_relative(path).as_posix(),
                "mode": "0755" if mode == "100755" else "0644",
                "sha256": _sha256(content),
            }
        )
    entries.sort(key=lambda entry: entry["path"])
    payload: dict[str, Any] = {
        "schema_version": 1,
        "commit": commit,
        "entries": entries,
    }
    payload["manifest_sha256"] = _sha256(_canonical_json(payload))
    _validate_manifest(payload)
    return payload


def _load_json(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseError(f"cannot read {label}") from error
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} must be a JSON object")
    return value


def _write_json(path: str | Path, payload: Mapping[str, Any], *, mode: int = 0o644) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _target(root: Path, relative: str) -> Path:
    root = root.resolve(strict=True)
    candidate = root / _safe_relative(relative)
    cursor = root
    for part in _safe_relative(relative).parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ReleaseError("target parent may not be a symlink")
    if candidate.is_symlink():
        raise ReleaseError("target may not be a symlink")
    return candidate


def _atomic_write(
    target: Path,
    content: bytes,
    mode: int,
    *,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, mode)
        if uid is not None and gid is not None and os.geteuid() == 0:
            os.chown(temporary_name, uid, gid)
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _rollback_targets(
    entries: list[dict[str, str]], runtime_root: Path, unit_root: Path
) -> list[tuple[str, str, Path]]:
    targets = [("runtime", entry["path"], _target(runtime_root, entry["path"])) for entry in entries]
    targets.append(("runtime", ".userio-release.json", _target(runtime_root, ".userio-release.json")))
    for obsolete in sorted(_OBSOLETE_RUNTIME_PATHS):
        targets.append(("runtime", obsolete, _target(runtime_root, obsolete)))
    for service_path in sorted(_SERVICE_PATHS):
        name = PurePosixPath(service_path).name
        targets.append(("units", name, _target(unit_root, name)))
    return targets


def prepare_rollback(
    manifest: Mapping[str, Any],
    runtime: str | Path,
    unit_root: str | Path,
    rollback_root: str | Path,
) -> dict[str, Any]:
    entries = _validate_manifest(manifest)
    runtime_root = Path(runtime).resolve(strict=True)
    units = Path(unit_root).resolve(strict=True)
    destination = Path(rollback_root)
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = destination / f"userio-{timestamp}-{str(manifest['commit'])[:12]}.tar"
    paths: list[dict[str, Any]] = []
    with tarfile.open(archive, "x") as bundle:
        for scope, relative, target in _rollback_targets(entries, runtime_root, units):
            present = target.exists()
            record: dict[str, Any] = {"scope": scope, "path": relative, "present": present}
            if present:
                info = target.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise ReleaseError(f"rollback target is not a regular file: {relative}")
                record.update(
                    {
                        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                        "uid": info.st_uid,
                        "gid": info.st_gid,
                    }
                )
                bundle.add(target, arcname=f"{scope}/{relative}", recursive=False)
            paths.append(record)
    receipt_path = archive.with_suffix(".json")
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "commit": manifest["commit"],
        "manifest_sha256": manifest["manifest_sha256"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "archive_path": str(archive.resolve()),
        "archive_sha256": _sha256(archive.read_bytes()),
        "receipt_path": str(receipt_path.resolve()),
        "paths": paths,
        "restore_argv": [
            sys.executable,
            str(Path(__file__).resolve()),
            "restore",
            "--receipt",
            str(receipt_path.resolve()),
            "--runtime",
            str(runtime_root),
            "--unit-root",
            str(units),
        ],
    }
    _write_json(receipt_path, receipt, mode=0o600)
    return receipt


def _validate_rollback(receipt: Mapping[str, Any], manifest: Mapping[str, Any]) -> Path:
    if receipt.get("schema_version") != 1:
        raise ReleaseError("rollback receipt schema is invalid")
    if receipt.get("commit") != manifest.get("commit") or receipt.get(
        "manifest_sha256"
    ) != manifest.get("manifest_sha256"):
        raise ReleaseError("rollback receipt does not match manifest")
    archive_value = receipt.get("archive_path")
    archive_sha = receipt.get("archive_sha256")
    if not isinstance(archive_value, str) or not isinstance(archive_sha, str):
        raise ReleaseError("rollback archive identity is missing")
    archive = Path(archive_value)
    if not archive.is_file() or _sha256(archive.read_bytes()) != archive_sha:
        raise ReleaseError("rollback archive is missing or corrupt")
    if not isinstance(receipt.get("paths"), list):
        raise ReleaseError("rollback path inventory is missing")
    return archive


def restore_release(
    receipt: Mapping[str, Any], runtime: str | Path, unit_root: str | Path
) -> dict[str, Any]:
    runtime_root = Path(runtime).resolve(strict=True)
    units = Path(unit_root).resolve(strict=True)
    archive_value = receipt.get("archive_path")
    archive_sha = receipt.get("archive_sha256")
    if not isinstance(archive_value, str) or not isinstance(archive_sha, str):
        raise ReleaseError("rollback receipt is incomplete")
    archive = Path(archive_value)
    if not archive.is_file() or _sha256(archive.read_bytes()) != archive_sha:
        raise ReleaseError("rollback archive is missing or corrupt")
    restored = 0
    with tarfile.open(archive, "r") as bundle:
        members = {member.name: member for member in bundle.getmembers()}
        for raw in receipt.get("paths", []):
            if not isinstance(raw, dict) or raw.get("scope") not in {"runtime", "units"}:
                raise ReleaseError("rollback path inventory is invalid")
            relative = raw.get("path")
            if not isinstance(relative, str):
                raise ReleaseError("rollback path inventory is invalid")
            root = runtime_root if raw["scope"] == "runtime" else units
            target = _target(root, relative)
            if target.exists():
                if not target.is_file() or target.is_symlink():
                    raise ReleaseError("refusing to replace non-regular rollback target")
                target.unlink()
            if raw.get("present"):
                member_name = f"{raw['scope']}/{relative}"
                member = members.get(member_name)
                if member is None or not member.isfile():
                    raise ReleaseError(f"rollback archive lacks {member_name}")
                extracted = bundle.extractfile(member)
                if extracted is None:
                    raise ReleaseError(f"rollback archive cannot read {member_name}")
                _atomic_write(
                    target,
                    extracted.read(),
                    int(str(raw.get("mode")), 8),
                    uid=int(raw.get("uid")),
                    gid=int(raw.get("gid")),
                )
                restored += 1
    return {"restored": True, "files": restored, "archive_sha256": archive_sha}


def install_release(
    source: str | Path,
    manifest: Mapping[str, Any],
    rollback_receipt: Mapping[str, Any],
    runtime: str | Path,
    unit_root: str | Path,
    *,
    remote_ref: str = "origin/main",
    operation_hook: Callable[[str, Path], None] | None = None,
) -> dict[str, Any]:
    entries = _validate_manifest(manifest)
    source_root = Path(source).resolve(strict=True)
    runtime_root = Path(runtime).resolve(strict=True)
    units = Path(unit_root).resolve(strict=True)
    if _source_identity(source_root, remote_ref) != manifest["commit"]:
        raise ReleaseError("source commit does not match manifest")
    _validate_rollback(rollback_receipt, manifest)
    writes = 0
    try:
        for entry in entries:
            path = entry["path"]
            content = _git(source_root, "show", f"{manifest['commit']}:{path}")
            if _sha256(content) != entry["sha256"]:
                raise ReleaseError(f"source digest mismatch for {path}")
            targets = [_target(runtime_root, path)]
            if path in _SERVICE_PATHS:
                targets.append(_target(units, PurePosixPath(path).name))
            for target in targets:
                _atomic_write(target, content, int(entry["mode"], 8))
                writes += 1
                if operation_hook is not None:
                    operation_hook("after_write", target)
        for obsolete in sorted(_OBSOLETE_RUNTIME_PATHS):
            target = _target(runtime_root, obsolete)
            if target.exists():
                if not target.is_file() or target.is_symlink():
                    raise ReleaseError(f"obsolete path is not a regular file: {obsolete}")
                target.unlink()
                if operation_hook is not None:
                    operation_hook("after_remove", target)
        identity = {
            "schema_version": 1,
            "commit": manifest["commit"],
            "manifest_sha256": manifest["manifest_sha256"],
        }
        release_file = _target(runtime_root, ".userio-release.json")
        _write_json(release_file, identity, mode=0o644)
        writes += 1
        if operation_hook is not None:
            operation_hook("after_write", release_file)
    except BaseException as error:
        try:
            restore_release(rollback_receipt, runtime_root, units)
        except BaseException as restore_error:
            raise ReleaseError(
                f"install failed and rollback also failed: {restore_error}"
            ) from error
        raise ReleaseError("install failed; restored rollback") from error
    return {
        "commit": manifest["commit"],
        "manifest_sha256": manifest["manifest_sha256"],
        "files_written": writes,
    }


def _verify_file(target: Path, expected_sha: str, expected_mode: str) -> None:
    try:
        info = target.lstat()
    except FileNotFoundError as error:
        raise ReleaseError(f"installed file is missing: {target.name}") from error
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != int(expected_mode, 8):
        raise ReleaseError(f"installed file mode/type mismatch: {target.name}")
    if _sha256(target.read_bytes()) != expected_sha:
        raise ReleaseError(f"installed file digest mismatch: {target.name}")


def _systemd_identity() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    expectations = {
        "universal-userio.service": "universal_userio.runtime",
        "userio-agentcall-ingress.service": "universal_userio.agentcall_ingress",
    }
    for unit, module in expectations.items():
        try:
            output = subprocess.run(
                [
                    "systemctl",
                    "show",
                    unit,
                    "-p",
                    "ActiveState",
                    "-p",
                    "SubState",
                    "-p",
                    "MainPID",
                    "-p",
                    "ExecStart",
                    "-p",
                    "WorkingDirectory",
                    "-p",
                    "FragmentPath",
                    "-p",
                    "NRestarts",
                    "--no-pager",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise ReleaseError(f"cannot inspect {unit}") from error
        values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        if values.get("ActiveState") != "active" or values.get("SubState") != "running":
            raise ReleaseError(f"{unit} is not active/running")
        if values.get("WorkingDirectory") != "/opt/universal-userio":
            raise ReleaseError(f"{unit} has unexpected working directory")
        if module not in values.get("ExecStart", "") or int(values.get("MainPID", "0")) <= 0:
            raise ReleaseError(f"{unit} has unexpected process identity")
        result[unit] = {
            "active_state": values["ActiveState"],
            "sub_state": values["SubState"],
            "main_pid": int(values["MainPID"]),
            "working_directory": values["WorkingDirectory"],
            "fragment_path": values.get("FragmentPath"),
            "restart_count": int(values.get("NRestarts", "0") or "0"),
        }
    return result


def verify_release(
    source: str | Path,
    manifest: Mapping[str, Any],
    runtime: str | Path,
    unit_root: str | Path,
    *,
    remote_ref: str = "origin/main",
    check_systemd: bool = False,
) -> dict[str, Any]:
    entries = _validate_manifest(manifest)
    source_root = Path(source).resolve(strict=True)
    runtime_root = Path(runtime).resolve(strict=True)
    units = Path(unit_root).resolve(strict=True)
    if _source_identity(source_root, remote_ref) != manifest["commit"]:
        raise ReleaseError("source commit does not match manifest")
    for entry in entries:
        _verify_file(_target(runtime_root, entry["path"]), entry["sha256"], entry["mode"])
        if entry["path"] in _SERVICE_PATHS:
            _verify_file(
                _target(units, PurePosixPath(entry["path"]).name),
                entry["sha256"],
                entry["mode"],
            )
    for obsolete in _OBSOLETE_RUNTIME_PATHS:
        if _target(runtime_root, obsolete).exists():
            raise ReleaseError(f"obsolete runtime source remains: {obsolete}")
    release_path = _target(runtime_root, ".userio-release.json")
    release = _load_json(release_path, "runtime release identity")
    expected = {
        "schema_version": 1,
        "commit": manifest["commit"],
        "manifest_sha256": manifest["manifest_sha256"],
    }
    if release != expected or stat.S_IMODE(release_path.lstat().st_mode) != 0o644:
        raise ReleaseError("runtime release identity does not match manifest")
    result: dict[str, Any] = {
        "commit": manifest["commit"],
        "manifest_sha256": manifest["manifest_sha256"],
        "verified": True,
    }
    if check_systemd:
        result["services"] = _systemd_identity()
    return result


def _default_runner(argv: list[str]) -> None:
    try:
        subprocess.run(argv, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseError(f"command failed: {argv[0]}") from error


def activate_services(*, runner: Callable[[list[str]], None] = _default_runner) -> None:
    runner(["systemctl", "daemon-reload"])
    runner(["systemctl", "restart", "universal-userio.service"])
    runner(["systemctl", "restart", "userio-agentcall-ingress.service"])


def _read_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    try:
        info = env_path.lstat()
    except OSError as error:
        raise ReleaseError("cannot read canary environment file") from error
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise ReleaseError("canary environment file must be a private regular file")
    values: dict[str, str] = {}
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    except (OSError, UnicodeError) as error:
        raise ReleaseError("cannot parse canary environment file") from error
    return values


def _http_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else _canonical_json(payload)
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15.0) as response:
            value = json.loads(response.read())
            if not isinstance(value, dict):
                raise ReleaseError("UserIO returned a non-object response")
            return response.status, value
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseError("UserIO HTTP canary failed") from error


def run_canary(
    env_file: str | Path,
    *,
    message_id: str | None = None,
) -> dict[str, Any]:
    environment = _read_env_file(env_file)
    token = environment.get("USERIO_API_TOKEN", "").strip()
    if not token:
        raise ReleaseError("USERIO_API_TOKEN is required for canary")
    configured = environment.get("USERIO_URL", "").strip()
    port = environment.get("USERIO_PORT", "18093").strip() or "18093"
    if not configured and (not port.isdigit() or not 1 <= int(port) <= 65_535):
        raise ReleaseError("USERIO_PORT is invalid")
    base_url = configured.rstrip("/") if configured else f"http://127.0.0.1:{port}"
    unique_id = message_id or f"phase7-canary-{int(time.time())}-{uuid.uuid4().hex[:12]}"
    body = f"UserIO Phase 7 runtime canary {unique_id}"
    status, identity = _http_json(base_url + "/v1/runtime", token)
    if status != 200 or identity.get("verified") is not True:
        raise ReleaseError("runtime identity is not verified")
    status, accepted = _http_json(
        base_url + "/v1/messages",
        token,
        method="POST",
        payload={
            "route_id": "phase7-no-delivery",
            "message": {
                "schema": "universal.inbox.message.v1",
                "source": "matrix",
                "message_id": unique_id,
                "sender": "phase7-release-canary",
                "sender_name": "Phase 7 release canary",
                "body": body,
            },
        },
    )
    if status != 202 or accepted.get("accepted") is not True or accepted.get("draft") is not None:
        raise ReleaseError("runtime canary ingress was not safely accepted")
    conversation_id = accepted.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ReleaseError("runtime canary lacks conversation id")
    status, inbox = _http_json(base_url + "/v1/inbox", token)
    messages = inbox.get("messages")
    if status != 200 or not isinstance(messages, list):
        raise ReleaseError("runtime canary inbox read failed")
    read_back = any(
        isinstance(row, dict)
        and row.get("message_id") == unique_id
        and row.get("body") == body
        and row.get("source") == "matrix"
        and row.get("conversation_id") == conversation_id
        for row in messages
    )
    if not read_back:
        raise ReleaseError("runtime canary message was not read back")
    return {
        "status": "passed",
        "source": "matrix",
        "canary_kind": "phase7-runtime",
        "route_id": "phase7-no-delivery",
        "message_id": unique_id,
        "conversation_id": conversation_id,
        "accepted": True,
        "draft": None,
        "read_back": True,
        "runtime_identity": identity,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def _print(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest_parser = subparsers.add_parser("manifest")
    manifest_parser.add_argument("--source", default=".")
    manifest_parser.add_argument("--remote-ref", default="origin/main")
    manifest_parser.add_argument("--output", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--manifest", required=True)
    prepare_parser.add_argument("--runtime", default="/opt/universal-userio")
    prepare_parser.add_argument("--unit-root", default="/etc/systemd/system")
    prepare_parser.add_argument("--rollback-dir", default="/var/backups/universal-userio")

    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--source", default=".")
    install_parser.add_argument("--manifest", required=True)
    install_parser.add_argument("--rollback-receipt", required=True)
    install_parser.add_argument("--runtime", default="/opt/universal-userio")
    install_parser.add_argument("--unit-root", default="/etc/systemd/system")
    install_parser.add_argument("--remote-ref", default="origin/main")

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--receipt", required=True)
    restore_parser.add_argument("--runtime", default="/opt/universal-userio")
    restore_parser.add_argument("--unit-root", default="/etc/systemd/system")

    subparsers.add_parser("activate")

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--source", default=".")
    verify_parser.add_argument("--manifest", required=True)
    verify_parser.add_argument("--runtime", default="/opt/universal-userio")
    verify_parser.add_argument("--unit-root", default="/etc/systemd/system")
    verify_parser.add_argument("--remote-ref", default="origin/main")
    verify_parser.add_argument("--check-systemd", action="store_true")

    canary_parser = subparsers.add_parser("canary")
    canary_parser.add_argument("--env-file", default="/etc/universal-userio.env")
    canary_parser.add_argument("--message-id")

    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "manifest":
            result = build_manifest(arguments.source, arguments.remote_ref)
            _write_json(arguments.output, result)
        elif arguments.command == "prepare":
            result = prepare_rollback(
                _load_json(arguments.manifest, "manifest"),
                arguments.runtime,
                arguments.unit_root,
                arguments.rollback_dir,
            )
        elif arguments.command == "install":
            result = install_release(
                arguments.source,
                _load_json(arguments.manifest, "manifest"),
                _load_json(arguments.rollback_receipt, "rollback receipt"),
                arguments.runtime,
                arguments.unit_root,
                remote_ref=arguments.remote_ref,
            )
        elif arguments.command == "restore":
            result = restore_release(
                _load_json(arguments.receipt, "rollback receipt"),
                arguments.runtime,
                arguments.unit_root,
            )
        elif arguments.command == "activate":
            activate_services()
            result = {"activated": True}
        elif arguments.command == "verify":
            result = verify_release(
                arguments.source,
                _load_json(arguments.manifest, "manifest"),
                arguments.runtime,
                arguments.unit_root,
                remote_ref=arguments.remote_ref,
                check_systemd=arguments.check_systemd,
            )
        else:
            result = run_canary(arguments.env_file, message_id=arguments.message_id)
        _print(result)
        return 0
    except ReleaseError as error:
        _print({"error": str(error)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
