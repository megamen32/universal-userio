# UserIO Adapter SDK

`userio_adapter_sdk` is the stable dependency boundary for provider adapters.

Provider implementations import only DTOs, protocols, capabilities and adapter errors from this package. They must not import UserIO store, service, HTTP, MCP or UI modules. `universal_userio.channels.core` remains a compatibility re-export for older integrations.

Runtime split:

- `userio_adapter_sdk`: stable provider-neutral contracts.
- `universal_userio.channels.*`: provider libraries and low-level sidecar clients.
- `*_ingress.py`: thin processes that normalize provider events and deliver them to canonical UserIO ingress.
- `Universal UserIO`: canonical users/accounts/conversations/messages/files/policies/MCP.
- `NoticePlace`: system/agent notifications only; it is not a human-message reply transport.

Embedding applications use `Omnichannel` as the multi-provider, multi-account
composition root. Register concrete `Channel` adapters once and select them by
`platform` plus `account_id`; provider SDK clients never cross that boundary.

```python
from userio_adapter_sdk import Omnichannel
from universal_userio.channels.sms import AndroidSmsChannel

channels = Omnichannel()
channels.register(AndroidSmsChannel.from_env(), account_id="sales-phone")
await channels.send_message("sms", "+15551234567", "Hello", account_id="sales-phone")
```

A provider should expose capabilities explicitly. Read-only providers simply omit `reply`; per-user `send_enabled` is an additional UserIO policy gate above provider capabilities.
