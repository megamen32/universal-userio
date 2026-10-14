---
mode: quick
status: in-progress
---
# Delayed unread-only automatic triage
1. Add durable arrival timestamps and atomic batches scoped to user/account/conversation. Fixed 300s window, up to100 events per bounded batch; preserve leased/completed/uncertain actions.
2. Before cache/image/model work check native/local read, owner reply, deletion, policy and stable automation identity. Pass eligible batch text together to existing native model; recheck before saving and notification delivery. Manual button remains immediate.
3. Focused regression (fast unit, expected40s/max400s; 768MiB RAM,256MiB swap,CPU2,tasks128,temporary files128MiB,IO10,noGPU), scoped main publication, rollback-first install, deployed HTTP queue/filter acceptance (focused integration expected360s/max3600s).

4. User extension: remove send-capability requirement from analysis toggle, make errors visible; named personal scoped_messenger token editor with account/platform grouping and read/write per chat, enforced at both MCP adapters before provider calls. Existing credentials change only after explicit save.

5. Direct user extension: persisted global automatic AI pause/resume button, queue preservation and live OFF/reload/ON verification.
