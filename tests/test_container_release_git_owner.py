from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "container_release.py"
spec = importlib.util.spec_from_file_location("userio_container_release_git_owner", SCRIPT)
assert spec and spec.loader
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_root_git_preflight_runs_as_documented_repository_owner():
    with patch.object(release.os, "geteuid", return_value=0):
        args = release.repo_git_argv(release.ROOT, "rev-parse", "origin/main")
    assert args == ["runuser", "-u", "roomhacker", "--", "git", "-C",
                    str(release.ROOT), "rev-parse", "origin/main"]


def test_repository_owner_executor_preserves_nonprivileged_reads():
    with patch.object(release.os, "geteuid", return_value=1000):
        args = release.repo_git_argv(
            release.ROOT, "show", "origin/main:src/universal_userio/service.py")
    assert args[:3] == ["git", "-C", str(release.ROOT)]
    assert args[-2:] == ["show", "origin/main:src/universal_userio/service.py"]


def test_owner_mismatch_fails_before_git_is_invoked():
    with patch.object(release.os, "geteuid", return_value=0), patch.object(
        release.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=99999)
    ):
        with pytest.raises(RuntimeError, match="repository owner"):
            release.repo_git_argv(release.ROOT, "rev-parse", "HEAD")


def test_unknown_or_mutating_git_operation_is_rejected():
    with patch.object(release.os, "geteuid", return_value=0):
        with pytest.raises(ValueError, match="read-only"):
            release.repo_git_argv(release.ROOT, "config", "--global", "safe.directory", "*")


def test_published_source_preflight_uses_owner_git_under_root():
    published = "dcb556581919fa1b05ed3e6f37d026fdc84a08a3"
    with patch.object(release.os, "geteuid", return_value=0), patch.object(
        release, "output", return_value=published
    ) as read:
        assert release._published_tip() == published
    read.assert_called_once_with([
        "runuser", "-u", "roomhacker", "--", "git", "-C",
        str(release.ROOT), "rev-parse", "origin/main",
    ])
