#!/bin/sh
set -eu
umask 077
chrome="$HOME/.agent-browser/browsers/chrome-153.0.8010.36/chrome"
profile="$HOME/.config/agent-browser-userio-chrome"
extension="$HOME/agents-projects/universal-userio/extensions/vk-inbox"
set -- --disable-extensions
if [ "${USERIO_CHROME_EXTENSION_ENABLED:-1}" = 1 ]; then
    set -- "--disable-extensions-except=$extension" "--load-extension=$extension"
fi
exec "$chrome" \
  "--user-data-dir=$profile" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9226 \
  --no-first-run --no-default-browser-check \
  --disable-session-crashed-bubble \
  --disable-background-networking --disable-component-update \
  "$@" about:blank
