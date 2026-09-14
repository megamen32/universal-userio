# UserIO contact and file operations

Started at 2026-09-14T03:21:06+03:00 (system clock; uptime 3 days, 2 hours, 24 minutes).
Estimate: 90-180 active minutes. Active time is not continuously measured.

Status: complete

- Wanted result: the stable omnichannel adapter SDK supports download/upload, contact CRUD, and adding/removing contacts from groups with truthful per-adapter capabilities.
- Shortest real canary: invoke every operation through a deployed `Omnichannel` binding, observe provider-neutral DTOs for supported operations, and an early `AdapterNotSupported` for an unsupported binding.
- Smallest vertical slice: SDK DTOs/protocol/capabilities, Omnichannel delegation, Telegram implementation, explicit unsupported behavior via capability gates, focused tests, docs, deployment and canary.
- Discard now: new provider-side address books, UI redesign, external destructive contact/group mutations, and claiming operations that an adapter cannot actually perform.

## Acceptance

- [x] `download` and `upload` are stable SDK operations.
- [x] `get_contact`, `add_contact`, `edit_contact`, `remove_contact` are stable SDK operations.
- [x] `add_contact_to_group`, `remove_contact_from_group` are stable SDK operations.
- [x] Adapter capabilities are named, discoverable, and enforced before delegation.
- [x] Telegram implements the supported operations without leaking Telethon objects.
- [x] Unsupported adapters fail with `AdapterNotSupported` and do not advertise false capabilities.
- [x] Focused and full tests pass; Graphify is refreshed.
- [x] Main is pushed, deployed SDK is current, and deployed canary passes.

Evidence before deployment:

- focused SDK/channel tests: 20 passed;
- full UserIO suite: 159 passed in 68.44s;
- AutoFindClient consumer compatibility: 3 passed;
- Graphify update: 5,888 nodes / 19,991 edges.

Deployment evidence:

- implementation commit `974aba4` pushed to `origin/main`;
- five task-owned SDK/channel files installed byte-for-byte under `/opt/universal-userio`;
- production import smoke passed;
- `universal-userio.service` active since 2026-09-14 03:47:40 MSK, PID 3441751, `NRestarts=0`;
- deployed `Omnichannel` canary passed all eight requested operations and the unsupported-adapter capability gate without contacting real people or mutating a real address book.
