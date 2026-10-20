from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_no_credentials.py"
spec = importlib.util.spec_from_file_location("validate_no_credentials", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def scan(tmp_path: Path, name: str, body: str) -> list[str]:
    target = tmp_path / name
    target.write_text(body, encoding="utf-8")
    return module.scan_text(target, body)


def test_userio_token_is_reported(tmp_path) -> None:
    findings = scan(tmp_path, "leak.py", 'token = "uio_DSfwabcdefghijklmnopqrstuvwxyz0123456789"\n')
    assert any("UserIO bearer token" in item for item in findings)


def test_private_key_body_is_reported(tmp_path) -> None:
    body = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAy8Dbv8prpJ/0kKhlGeJYozo2t60EG8L0561g13R29LvMR5hy\n"
        "-----END RSA PRIVATE KEY-----\n"
    )
    findings = scan(tmp_path, "key.txt", body)
    assert any("private key body" in item for item in findings)


def test_bearer_credential_is_reported(tmp_path) -> None:
    body = 'headers = {"Authorization": "Bearer 8f31c0d9a7b6e5d4c3b2a1908f7e6d5c"}\n'
    findings = scan(tmp_path, "headers.py", body)
    assert any("bearer credential" in item for item in findings)


def test_redaction_regex_is_not_a_finding(tmp_path) -> None:
    body = 'const re = /-----BEGIN [A-Z ]*PRIVATE KEY-----[\\s\\S]*?-----END [A-Z ]*PRIVATE KEY-----/g;\n'
    assert scan(tmp_path, "redact.ts", body) == []


def test_empty_env_assignment_is_not_a_finding(tmp_path) -> None:
    body = "USERIO_API_TOKEN=\nUSERIO_TRUSTED_PROXY_TOKEN=\n"
    assert scan(tmp_path, ".env.example", body) == []


def test_placeholder_values_are_not_findings(tmp_path) -> None:
    body = "USERIO_SMS_GATEWAY_TOKEN=replace-with-android-sms-gateway-api-key\n"
    assert scan(tmp_path, ".env.example", body) == []


def test_documented_placeholder_is_not_a_finding(tmp_path) -> None:
    body = "USERIO_TELEGRAM_2FA_PASSWORD=… python -m universal_userio.channels.telegram_login\n"
    assert scan(tmp_path, "RUNTIME-REFERENCE.md", body) == []


def test_env_var_substitution_is_not_a_finding(tmp_path) -> None:
    body = '{"Authorization": "Bearer ${USERIO_MCP_TOKEN}"}'
    assert scan(tmp_path, "mcp.json", body) == []
