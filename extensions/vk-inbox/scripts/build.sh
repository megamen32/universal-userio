#!/usr/bin/env bash
# Package the VK Inbox sidecar extension into the zip served by UserIO HTTP API.
#
# The tree is manifest_version 2 (MV2 background until lib/* is ported to ES
# modules). One package is built and published under BOTH static URLs so
# historical links keep working:
#   vk-userio-extension.zip       - current MV2 package
#   vk-userio-extension-mv3.zip   - same bytes (legacy alias)
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

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
PKG="${WORK}/pkg"
mkdir -p "${PKG}"
cp manifest.json background.js popup.html popup.css popup.js options.html options.js README.md "${PKG}/"
cp -r lib content "${PKG}/"

( cd "${PKG}" && zip -qr "${STATIC_DIR}/vk-userio-extension.zip" . )
cp "${STATIC_DIR}/vk-userio-extension.zip" "${STATIC_DIR}/vk-userio-extension-mv3.zip"
echo "Built ${STATIC_DIR}/vk-userio-extension.zip (+ mv3 alias)"
