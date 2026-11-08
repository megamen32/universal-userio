#!/usr/bin/env python3
"""Strip build-time UserIO credentials from distributable Chrome extension zips."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

_PUBLIC_ENDPOINT = "https://msg.bezrabotnyi.com"
_CONFIG_PATTERN = re.compile(r"(root\.USERIO_CONFIG\s*=\s*)(\{[^;]+\})(;)")


def sanitize(path: Path) -> None:
    with ZipFile(path, "r") as src:
        infos = src.infolist()
        existing = {info.filename: src.read(info.filename) for info in infos}
        config = existing["lib/config.js"].decode("utf-8")
        match = _CONFIG_PATTERN.search(config)
        if not match:
            raise ValueError("extension config format not recognized")
        data = json.loads(match.group(2))
        if not isinstance(data, dict):
            raise ValueError("extension configuration is invalid")
        data.update(endpoint=_PUBLIC_ENDPOINT, token="", agentId="vk-browser")
        config = config[:match.start(2)] + json.dumps(data, ensure_ascii=False) + config[match.end(2):]
        existing["lib/config.js"] = config.encode()
        options = existing["options.js"].decode()
        options = options.replace(
            'endpoint: "http://127.0.0.1:18093"',
            f'endpoint: "{_PUBLIC_ENDPOINT}"',
        )
        existing["options.js"] = options.encode()
        if any(re.search(rb'USERIO_CONFIG\s*=\s*\{[^;]*?"token"\s*:\s*"[^"]+', value)
               for value in existing.values()):
            raise ValueError("nonempty built-in token remains")
        descriptor, tempfile_path = tempfile.mkstemp(prefix=".vk-safe-", suffix=".zip", dir=path.parent)
        os.close(descriptor)
        try:
            with ZipFile(tempfile_path, "w") as dst:
                for info in infos:
                    dst.writestr(info, existing[info.filename])
            with ZipFile(tempfile_path) as check:
                if check.testzip() is not None:
                    raise ValueError("zip CRC check failed")
                baked = json.loads(_CONFIG_PATTERN.search(check.read("lib/config.js").decode()).group(2))
                if baked["token"]:
                    raise ValueError("token still bundled")
            os.chmod(tempfile_path, path.stat().st_mode & 0o777)
            os.replace(tempfile_path, path)
        finally:
            try:
                os.unlink(tempfile_path)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: sanitize_vk_extension.py <zip> [...]")
    for name in sys.argv[1:]:
        sanitize(Path(name))
        print("sanitized", Path(name).name)
