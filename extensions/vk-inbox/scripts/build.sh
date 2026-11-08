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
if ! command -v zip >/dev/null; then echo "zip not found" >&2; exit 1; fi
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
python3 - "${PKG}/lib/config.js" "${USERIO_PUBLIC_ENDPOINT:-https://msg.bezrabotnyi.com}" "${USERIO_AGENT_ID:-vk-browser}" <<'PY'
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

( cd "${PKG}" && zip -qr "${STATIC_DIR}/vk-userio-extension.zip" . )
cp "${STATIC_DIR}/vk-userio-extension.zip" "${STATIC_DIR}/vk-userio-extension-mv3.zip"
echo "Built ${STATIC_DIR}/vk-userio-extension.zip (+ mv3 alias)"
