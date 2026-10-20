# UserIO communications boundary

Read the parent source-ownership rules and
[docs/COMMUNICATIONS-ARCHITECTURE.md](docs/COMMUNICATIONS-ARCHITECTURE.md) before
changing communications, calls, channel documentation or runtime placement.

UserIO is the universal gateway for accounts, messages, chats, files, history,
calls and meetings. Telegram, Matrix/MatrixRTC, Telemost and cellular/SIM are
separate account-scoped channels. Never reduce the product to Android calling.

Notice Place owns the agent/operator notification and question API/MCP, contact
policy, escalation, deduplication and canonical answers. It delegates actual
communications to UserIO. Ordinary UserIO reading and messaging remain available
directly. Calling primitives are not permission to bypass the owner's contact policy.

UserIO Voice / AgentCall is an internal media/AI implementation, not a third
public calling product. Asterisk and GSM2SIP belong to the cellular transport,
not every internet channel. Keep live ownership distinct from old service names.
Do not restart/remove a working voice runtime to simplify a diagram or migrate
an adapter without checking active calls, authorization, verified replacement
and rollback. Never expose credentials, phone numbers or private transcripts.

Discover exact installed API/MCP schemas and capabilities before use. Accepted,
queued, ringing, answered and explicitly confirmed decisions are different.
Unknown delivery is reconciled by the original request identity, never replayed.

Write operational instructions, code comments and agent coordination in English;
keep Russian user-facing guides/responses in Russian and preserve raw evidence.
Keep the canonical architecture in one place with links from guides and runbooks;
regenerate the documentation site with the existing builder after guide edits.
