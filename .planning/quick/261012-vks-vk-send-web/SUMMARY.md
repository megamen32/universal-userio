---
type: quick
status: complete
date: 2026-10-12
---
# Personal VK text sending delivered

Existing authorized personal test account200000000106 now reads and sends text
through the ordinary UserIO conversation composer. The native sender binds the
current user, account and exact peer; a draft-derived random_id and durable
claim prevent ambiguous retries. Actual provider message7 is mirrored back into
normal history. Personal account onboarding enables reply; VKID identity-only
and community connections are unchanged.

Real visible Chrome on the M1: choose VK/self, type a unique text, click Send
ONCE, observe cleared composer and outgoing provider7, reload/reopen. A separate
new process read messages.getById7 and confirmed exact owner/peer/body/outgoing.
The canary exposed a duplicated approved draft card; fixed, built and installed.
Final post-install reload shows one history bubble with “Отправлено в VK”, no
remaining approved draft card. The second body-wide text occurrence is the
sidebar preview; the overly broad verifier assertion is retained and corrected
against saved actual DOM and the screenshot, without replaying the send.

Independent simulated reader first identified unclear delivery status; final
screenshot review PASS after the explicit VK delivery caption. This is simulated
UI review, not human research. Original real native delivery remains the proof.

Source deliveries:8475c55 (VK send),6f5fd41 (MAX read repair),ca57284 (dedup/docs),
7d04cc6 (visible delivery status). Final runtime manifest verified, core2829089.
Tests:30 focused VK PASS8.24s; MAX stale-read RED2 failures then combined47PASS9.96s;
ordinary frontend builds and docs checks helper0/same-generation cleanup. All
ordinary runs stayed below180s. No extra model, phone, SMS, recipient or old
provider intent was replayed. VK edit/media/reactions/read ACK remain unsupported.

Own foreground M1 browser stopped before deadline; PID/CDP/own profile/auth/PNG
copies/control process absent. Peak1.61GB/12processes under2GiB/32, host reserve4GiB.
Existing foreign browser profiles/processes were not stopped or copied.
