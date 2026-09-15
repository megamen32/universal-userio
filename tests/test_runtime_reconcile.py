from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.runtime_reconcile import (
    ReconciliationError,
    audit_runtime,
    parse_porcelain_v1_z,
    write_report,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(root: Path, files: dict[str, str]) -> None:
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "runtime-reconcile@example.invalid")
    _git(root, "config", "user.name", "Runtime Reconcile Test")
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    _git(root, "add", "--", *files)
    _git(root, "commit", "-q", "-m", "fixture")


def test_parse_porcelain_z_keeps_spaces_and_rename_as_one_record() -> None:
    records = parse_porcelain_v1_z(b" M ordinary file.py\0R  new name.py\0old name.py\0")

    assert [(row.status, row.path, row.original_path) for row in records] == [
        (" M", "ordinary file.py", None),
        ("R ", "new name.py", "old name.py"),
    ]


def test_audit_reconciles_categories_and_never_exposes_secret_path_or_content(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    runtime = tmp_path / "runtime"
    base = {
        "src/app.py": "canonical\n",
        "pyproject.toml": "[project]\nname='fixture'\n",
    }
    _repo(canonical, base)
    _repo(runtime, base)
    (runtime / "src/app.py").write_text("runtime drift\n", encoding="utf-8")
    (runtime / "node_modules/pkg/index.js").parent.mkdir(parents=True)
    (runtime / "node_modules/pkg/index.js").write_text("generated\n", encoding="utf-8")
    (runtime / "src/app.py.bak.20260915").write_text("backup\n", encoding="utf-8")
    (runtime / ".tmp").mkdir()
    secret_path = runtime / ".tmp/super-secret-token.txt"
    secret_path.write_text("NEVER-PRINT-ME\n", encoding="utf-8")
    policy = {
        "schema_version": 1,
        "decisions": {
            "src/app.py": {
                "disposition": "preserve_canonical",
                "reason": "canonical source is authoritative",
            }
        },
    }

    before = _git(runtime, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    report = audit_runtime(canonical, runtime, policy=policy)
    after = _git(runtime, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    encoded = json.dumps(report, sort_keys=True)

    assert before == after
    assert report["total_entries"] == 4
    assert sum(report["category_counts"].values()) == report["total_entries"]
    assert sum(report["disposition_counts"].values()) == report["total_entries"]
    assert report["category_counts"] == {
        "backup": 1,
        "generated_dependency": 1,
        "secret_session": 1,
        "source": 1,
    }
    assert report["disposition_counts"]["preserve_canonical"] == 1
    secret = next(row for row in report["entries"] if row["category"] == "secret_session")
    assert set(secret) == {
        "category",
        "disposition",
        "path_id",
        "reason",
        "runtime_mode",
        "runtime_present",
        "status",
    }
    assert "super-secret-token.txt" not in encoded
    assert "NEVER-PRINT-ME" not in encoded
    assert "runtime_sha256" not in secret


def test_unknown_and_unreviewed_safe_delta_are_unresolved(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    runtime = tmp_path / "runtime"
    _repo(canonical, {"README.md": "canonical\n"})
    _repo(runtime, {"README.md": "canonical\n"})
    (runtime / "README.md").write_text("runtime\n", encoding="utf-8")
    (runtime / "mystery.bin").write_bytes(b"mystery")

    report = audit_runtime(canonical, runtime, policy={"schema_version": 1, "decisions": {}})

    assert report["unresolved_count"] == 2
    assert {row["category"] for row in report["entries"]} == {"source", "unknown"}


def test_escape_symlink_is_rejected_without_following_it(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    runtime = tmp_path / "runtime"
    outside = tmp_path / "outside.txt"
    outside.write_text("private\n", encoding="utf-8")
    _repo(canonical, {"README.md": "ok\n"})
    _repo(runtime, {"README.md": "ok\n"})
    os.symlink(outside, runtime / "escape.py")

    with pytest.raises(ReconciliationError, match="symlink escapes runtime root"):
        audit_runtime(canonical, runtime, policy={"schema_version": 1, "decisions": {}})


def test_write_report_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    write_report(output, {"schema_version": 1, "total_entries": 0})

    assert json.loads(output.read_text(encoding="utf-8"))["total_entries"] == 0
    assert list(tmp_path.glob(f".{output.name}.*.tmp")) == []

