// Durable record of every approved Telegram send attempt.
//
// The connector owns every Telegram session, so it is the only process that
// can say whether an approved message reached the provider. UserIO sends the
// attempt id (its draft id) with every approved send; this journal binds that
// exact attempt to the provider message id, to a refusal that happened before
// any provider IO, or to the honest "in flight" state. It stores ids and
// timestamps only, never a message body.
//
// The journal also makes an approved send idempotent: a repeated attempt id
// never produces a second message.

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

function now() {
  return Math.floor(Date.now() / 1000);
}

export class SendAttemptLedger {
  constructor(path, options) {
    const settings = options || {};
    this.path = path;
    this.limit = Number(settings.limit) || 500;
    this.entries = new Map();
    // The journal epoch is the moment this connector could first record an
    // attempt. An approval claimed before that epoch cannot be resolved by
    // the absence of an entry.
    this.startedAt = now();
    this.load();
  }

  load() {
    try {
      if (!existsSync(this.path)) return;
      const raw = JSON.parse(readFileSync(this.path, "utf8") || "{}");
      this.startedAt = Number(raw.started_at) || this.startedAt;
      const rows = Array.isArray(raw.entries) ? raw.entries : [];
      for (const row of rows) {
        if (row && typeof row.attempt_id === "string" && row.attempt_id) {
          this.entries.set(row.attempt_id, row);
        }
      }
    } catch (error) {
      this.entries = new Map();
    }
  }

  save() {
    try {
      mkdirSync(dirname(this.path), { recursive: true });
      const rows = [];
      for (const row of this.entries.values()) rows.push(row);
      const temp = `${this.path}.tmp`;
      writeFileSync(temp, JSON.stringify({ started_at: this.startedAt, entries: rows }, null, 2), { mode: 0o600 });
      renameSync(temp, this.path);
    } catch (error) {
      console.error("send attempt journal write failed:", (error && error.message) || error);
    }
  }

  get(attemptId) {
    if (!attemptId) return null;
    return this.entries.get(attemptId) || null;
  }

  record(attemptId, patch) {
    if (!attemptId) return null;
    const current = this.entries.get(attemptId) || { attempt_id: attemptId, created_at: now() };
    const next = Object.assign({}, current, patch, { attempt_id: attemptId, updated_at: now() });
    this.entries.set(attemptId, next);
    this.trim();
    this.save();
    return next;
  }

  trim() {
    const rows = Array.from(this.entries.values());
    if (rows.length <= this.limit) return;
    rows.sort((a, b) => Number(a.updated_at || 0) - Number(b.updated_at || 0));
    const drop = rows.slice(0, rows.length - this.limit);
    for (const row of drop) this.entries.delete(row.attempt_id);
  }
}

export function attemptState(entry) {
  if (!entry) return "absent";
  const state = String(entry.state || "");
  if (state === "sent" || state === "refused" || state === "unknown" || state === "pending") {
    return state;
  }
  return "unknown";
}
