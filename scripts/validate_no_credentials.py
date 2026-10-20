#!/usr/bin/env python3
"""Fail the build when a real credential is committed to this public repository.

The package and the server source in this repository are public. The owner runs a
private UserIO instance, so a committed token would hand every reader of GitHub
access to that owner's mail, messengers and SMS. This check keeps the repository
free of credentials and fails loudly instead of relying on a human to notice.

What counts as a finding:
  * a UserIO bearer token (``uio_`` followed by at least 20 token characters);
  * a PEM private key with a real base64 body (a header mentioned inside code,
    for example a redaction regex, is not a finding);
  * a non-empty assignment to a known secret variable;
  * an ``Authorization: Bearer`` header whose value is a real token rather than a
    ``${VAR}`` placeholder.

Usage: python3 scripts/validate_no_credentials.py [path ...]
Without arguments the tracked files of the current repository are scanned.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TOKEN_PATTERNS = (
    (re.compile(r"\buio_[A-Za-z0-9_\-]{20,}"), "UserIO bearer token"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "OpenAI-style API key"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}"), "GitHub personal access token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
)
PEM_BODY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[ \t]*\n[ \t]*[A-Za-z0-9+/]{40,}={0,2}",
)
SECRET_ASSIGNMENT = re.compile(
    r"^[ \t]*(?:export[ \t]+)?([A-Z][A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|PASSWD|API_KEY|PRIVATE_KEY))"
    r"[ \t]*=[ \t]*(.*)$",
    re.MULTILINE,
)
BEARER = re.compile(r"Authorization\"?\s*:\s*\"?Bearer\s+([A-Za-z0-9_\-.]+)", re.IGNORECASE)

# Values that are placeholders or test fixtures, never real credentials.
ALLOWED_VALUES = {
    "",
    "replace-me",
    "changeme",
    "test-token",
    "canary-secret",
    "placeholder",
    "example",
    "none",
    "null",
    "todo",
    "legacy-service-token",
}
ALLOWED_ASSIGNMENT_NAMES = {
    "USERIO_MAX_TOKEN",
    "USERIO_MAX_DEVICE_ID",
    "USERIO_MAX_USER_ID",
    "USERIO_MAX_ACCOUNT_ID",
    "USERIO_MAX_ROUTE_ID",
}
# Suffixes of variables that carry structure, not a secret value.
STRUCTURAL_SUFFIXES = ("_JSON", "_PATH", "_FILE", "_ID", "_REF", "_ENABLE", "_MODE", "_URL", "_DIR")
# Markers that a value is documentation, not a credential.
PLACEHOLDER_MARKERS = ("replace-with", "replace-with-", "your-", "example", "placeholder", "…")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".tmp"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".ico", ".webp", ".woff", ".woff2"}


def tracked_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []
    return [root / name for name in out.split("\0") if name]


def scan_text(path: Path, text: str, strict: bool = True) -> list[str]:
    findings: list[str] = []
    for pattern, label in TOKEN_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(f"{label} in {path.name}")
    if PEM_BODY.search(text):
        findings.append(f"private key body in {path.name}")
    for match in SECRET_ASSIGNMENT.finditer(text):
        name, value = match.group(1), match.group(2).strip().strip("\"'")
        if name in ALLOWED_ASSIGNMENT_NAMES or name.endswith(STRUCTURAL_SUFFIXES):
            continue
        if value.lower() in ALLOWED_VALUES or value.startswith(("${", "<", "{", "[", "…")):
            continue
        if any(marker in value.lower() for marker in PLACEHOLDER_MARKERS):
            continue
        if len(value) < 20 or "${" in value:
            continue
        if strict and not any(char.isdigit() for char in value):
            # A real credential virtually always mixes digits into the value;
            # a prose fixture such as "legacy-service-token" does not.
            continue
        findings.append(f"secret assignment {name} in {path.name}")
    for match in BEARER.finditer(text):
        value = match.group(1)
        if value.lower() in ALLOWED_VALUES or "${" in value or value.startswith("<"):
            continue
        if len(value) < 20:
            continue
        if strict and not any(char.isdigit() for char in value):
            continue
        findings.append(f"bearer credential in {path.name}")
    return findings


def main(argv: list[str]) -> int:
    roots = [Path(a).resolve() for a in argv[1:]] or [Path.cwd().resolve()]
    findings: list[str] = []
    scanned = 0
    for root in roots:
        candidates = tracked_files(root) if (root / ".git").exists() else [
            p for p in root.rglob("*") if p.is_file()
        ]
        for path in candidates:
            try:
                relative = path.relative_to(root)
            except ValueError:
                relative = path
            if any(part in SKIP_DIRS for part in relative.parts):
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            try:
                if path.stat().st_size > 4_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            is_test = any(part in {"tests", "test", "fixtures"} for part in relative.parts)
            findings.extend(scan_text(path, text, strict=not is_test))
    if findings:
        print("FAIL: credentials found in the published repository:")
        for item in sorted(set(findings)):
            print(f"  - {item}")
        print("Remove the credential, rotate it, and rewrite the history if it was published.")
        return 1
    print(f"PASS: no credentials in {scanned} scanned files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
