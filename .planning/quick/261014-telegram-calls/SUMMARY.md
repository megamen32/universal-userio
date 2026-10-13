# Current outcome — 14 October

Reserve44 recordings: COMPLETE. Automatic importer bdff3d6 installed and enabled.
Both completed historical calls imported with stable IDs; repeat passes create
no duplicates. The latest human-confirmed clean-voice call passed actual headed
UserIO acceptance: both tracks, transcripts, playback progress, reload and exact
WAV downloads. Browser owner published e0fe1d8; evidence and accepted summary in
the sibling quick261014-reserve44-recording-ui task. Earlier inaudible call's
FAILED acceptance is preserved; importing its archive does not change that.

Telegram: IMPLEMENTED/PUBLISHED/OWN SERVER PART INSTALLED; recipient acceptance
PENDING. UserIO6d9bbd7 supplies durable start/get/stop and authenticated two-track
playback. Gateway e99dd010 runs the fixed native worker inside its existing
budget. Offline ntgcalls3.0.3 install completed, peak263266304bytes, no swap/OOM,
native helper cleanup complete. No new provider sessions/credentials/accounts.

Actual MCP exposes all three tools and confirm:false refuses before provider
activity. Installed gateway worker rejects invalid recipient before any RPC or
synthesis, then closes its control stream. Source56PASS/1native opt-in skip,
actual GramJS constructor tests7PASS, gateway4PASS. Separately accepted paired
native48k test passed1.98s with both nonzero PCM directions and full cleanup.
Independent review found/fixed late dial after Stop and final-RPC teardown loss;
new regression tests cover both. No real Telegram call has yet been created.

Concrete dependency: existing Secretary relay call-capabilities endpoint still
returns HTTP404. Existing client seam owner must install his settled route;
UserIO will then use that route for exactly one authorized live dialogue and
verify the resulting recording/transcripts. No fallback client/login or replay.
Private install/probe receipts: .tmp/telegram-call-runtime-install-20261014/.
