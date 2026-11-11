#!/usr/bin/env bash
# Package the VK Inbox sidecar extension (MV3 service worker) into the zip
# served by UserIO HTTP API. Published under BOTH static URLs so historical
# links keep working:
#   vk-userio-extension.zip       - current MV3 package
#   vk-userio-extension-mv3.zip   - same bytes (legacy alias)
#
# Public package defaults (baked into lib/config.js) so a fresh install
# knows the global UserIO host without including deployment credentials:
#   USERIO_PUBLIC_ENDPOINT   defaults to https://msg.bezrabotnyi.com
# USERIO_AGENT_ID is retained only as the migration fallback for older profiles.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATIC_DIR="${USERIO_STATIC_DIR:-${ROOT}/../../src/universal_userio/static}"

if [[ ! -d "${STATIC_DIR}" ]]; then
    echo "Static dir not found: ${STATIC_DIR}" >&2
    exit 1
fi
if ! command -v python3 >/dev/null; then echo "python3 not found" >&2; exit 1; fi

cd "${ROOT}"
python3 -c "import json; json.load(open('manifest.json'))" >/dev/null || { echo "manifest.json invalid" >&2; exit 1; }
if [[ -n "${USERIO_API_TOKEN:-}" ]]; then
    echo "Refusing to package USERIO_API_TOKEN; users authenticate through UserIO OAuth after installation." >&2
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
PKG="${WORK}/pkg"
mkdir -p "${PKG}"
cp manifest.json background.js popup.html popup.css popup.js options.html options.js README.md "${PKG}/"
cp -r lib content "${PKG}/"

# Bake public configuration into the package only. The shared ZIP must never
# contain a deployment token; users connect their account through OAuth after installation.
python3 - "${PKG}/lib/config.js" "${USERIO_PUBLIC_ENDPOINT:-https://msg.bezrabotnyi.com}" "${USERIO_AGENT_ID:-}" <<'PY'
import json, sys
path, endpoint, legacy_agent_id = sys.argv[1:4]
cfg = {"endpoint": endpoint, "token": "", "agentId": legacy_agent_id}
body = (
    "// Build-time configuration baked by scripts/build.sh; storage overrides.\n"
    "(function (root) {\n"
    f"  root.USERIO_CONFIG = {json.dumps(cfg)};\n"
    "})(typeof self !== \"undefined\" ? self : this);\n"
)
open(path, "w", encoding="utf-8").write(body)
PY

python3 - "${PKG}" "${STATIC_DIR}/vk-userio-extension.zip" <<'PY'
from pathlib import Path
import sys
import zipfile

root, target = map(Path, sys.argv[1:])
# Recreate the archive so removed files cannot survive a previous build.
# Stable order, timestamps and modes make identical inputs byte-identical.
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
    for source in sorted(root.rglob("*")):
        if not source.is_file():
            continue
        entry = zipfile.ZipInfo(source.relative_to(root).as_posix(), (1980, 1, 1, 0, 0, 0))
        entry.create_system = 3
        entry.external_attr = 0o100644 << 16
        entry.compress_type = zipfile.ZIP_DEFLATED
        package.writestr(entry, source.read_bytes(), compresslevel=9)
PY
cp "${STATIC_DIR}/vk-userio-extension.zip" "${STATIC_DIR}/vk-userio-extension-mv3.zip"
echo "Built ${STATIC_DIR}/vk-userio-extension.zip (+ mv3 alias)"
