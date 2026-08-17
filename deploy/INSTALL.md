# Deployment

1. Install this checkout at `/opt/universal-userio`.
2. Create `/var/lib/universal-userio` and root-owned `/etc/universal-userio.env` (`0600`) from `.env.example`.
3. Install `deploy/universal-userio.service`, run `systemctl daemon-reload`, then `systemctl enable --now universal-userio`.
4. Configure Universal Inbox with the three `UNIVERSAL_USERIO_*` variables from its `deploy/universal-inbox.env.example` and restart only the Inbox watcher after validating UserIO's loopback API.

The service is loopback-only by default. Put an authenticated internal reverse
proxy in front of the dashboard if browser access is required. Do not copy
provider cookies, browser profiles, or raw NoticePlace credentials into UserIO.
