#!/usr/bin/env bash
# Package the VK Inbox sidecar extension into zips served by UserIO HTTP API.
#
# Usage:
#   ./scripts/build.sh                       # build into ../../src/universal_userio/static/
#   USERIO_STATIC_DIR=/path ./scripts/build.sh
#
# Outputs (under USERIO_STATIC_DIR):
#   vk-userio-extension-mv3.zip   — manifest_version 3 package (Chrome / Chromium)
#   vk-userio-extension.zip       — legacy manifest_version 2 alias for older clients
#                                   (no content/ scripts — kept only for the static URL)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATIC_DIR="${USERIO_STATIC_DIR:-${ROOT}/../../src/universal_userio/static}"

if [[ ! -d "${STATIC_DIR}" ]]; then
    echo "Static dir not found: ${STATIC_DIR}" >&2
    echo "Set USERIO_STATIC_DIR to override." >&2
    exit 1
fi

if ! command -v zip >/dev/null; then
    echo "zip not found — install with apt: apt-get install -y zip" >&2
    exit 1
fi

if ! command -v python3 >/dev/null; then
    echo "python3 not found" >&2
    exit 1
fi

cd "${ROOT}"

python3 -c "import json; json.load(open('manifest.json'))" >/dev/null || { echo "manifest.json invalid" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# --- MV3 bundle --------------------------------------------------------------
# Copy files at the root of the zip so chrome://extensions can "Load unpacked".
MV3="${WORK}/mv3"
mkdir -p "${MV3}"
cp "${ROOT}/manifest.json" "${MV3}/manifest.json"
cp "${ROOT}/background.js" "${MV3}/background.js"
cp "${ROOT}/popup.html"   "${MV3}/popup.html"
cp "${ROOT}/popup.css"    "${MV3}/popup.css"
cp "${ROOT}/popup.js"     "${MV3}/popup.js"
cp "${ROOT}/options.html" "${MV3}/options.html"
cp "${ROOT}/options.js"   "${MV3}/options.js"
cp -r "${ROOT}/lib"        "${MV3}/lib"
cp -r "${ROOT}/content"    "${MV3}/content"
cp "${ROOT}/README.md"     "${MV3}/README.md"

( cd "${MV3}" && zip -qr "${STATIC_DIR}/vk-userio-extension-mv3.zip" . )
echo "Built ${STATIC_DIR}/vk-userio-extension-mv3.zip"

# --- Legacy v2 alias ----------------------------------------------------------
# Rewrite manifest v3 -> v2 in a fresh work dir using only the files needed for MV2
# (background.js + popup + options, no content/ subfolder).
LEGACY="${WORK}/legacy"
mkdir -p "${LEGACY}"
cp "${ROOT}/manifest.json" "${LEGACY}/manifest.json"
cp "${ROOT}/background.js" "${LEGACY}/background.js"
cp "${ROOT}/popup.html"    "${LEGACY}/popup.html"
cp "${ROOT}/popup.css"     "${LEGACY}/popup.css"
cp "${ROOT}/popup.js"      "${LEGACY}/popup.js"
cp "${ROOT}/options.html"  "${LEGACY}/options.html"
cp "${ROOT}/options.js"    "${LEGACY}/options.js"
cp -r "${ROOT}/lib"         "${LEGACY}/lib"
cp "${ROOT}/README.md"      "${LEGACY}/README.md"

# Mutate the legacy manifest to v2 in-place via Python (passes paths explicitly
# so nothing depends on shell variable expansion inside a heredoc).
python3 - "${LEGACY}/manifest.json" <<'PYEOF'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
m = json.loads(p.read_text())
m["manifest_version"] = 2
if "action" in m:
    m["browser_action"] = m.pop("action")
if "background" in m and "service_worker" in m.get("background", {}):
    m["background"] = {"scripts": [m["background"]["service_worker"]]}
m["content_scripts"] = []  # MV2 alias ships without content scripts
host = m.pop("host_permissions", [])
if host:
    m["permissions"] = list(dict.fromkeys(list(m.get("permissions", [])) + host))
p.write_text(json.dumps(m, indent=2, ensure_ascii=False))
PYEOF

( cd "${LEGACY}" && zip -qr "${STATIC_DIR}/vk-userio-extension.zip" . )
echo "Built ${STATIC_DIR}/vk-userio-extension.zip"
