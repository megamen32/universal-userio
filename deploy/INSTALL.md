# Deployment

1. Install this checkout at `/opt/universal-userio`.
2. Create `/var/lib/universal-userio` and root-owned `/etc/universal-userio.env`
   (`0600`) from `.env.example`. Create a separate mode-`0600`
   `.env.owner-seed` with `USERIO_SEED_USERNAME` and
   `USERIO_SEED_PASSWORD`, or point `USERIO_OWNER_SEED_FILE` at it.
3. Install `deploy/universal-userio.service`, run `systemctl daemon-reload`, then `systemctl enable --now universal-userio`.
4. Configure Universal Inbox with the three `UNIVERSAL_USERIO_*` variables from its `deploy/universal-inbox.env.example` and restart only the Inbox watcher after validating UserIO's loopback API.

The service is loopback-only by default. Put an authenticated internal reverse
proxy in front of the dashboard if browser access is required. Do not copy
provider cookies, browser profiles, or raw NoticePlace credentials into UserIO.
If the proxy uses the dashboard trust header, configure
`USERIO_TRUSTED_PROXY_TOKEN` and make the proxy overwrite both
`X-UserIO-Authenticated` and `X-UserIO-Proxy-Token`; an unkeyed header is not
accepted.
The same HTTPS proxy may publish `/mcp` for ChatGPT; do not expose the
loopback listener directly. Give each connector its own user bearer token.
