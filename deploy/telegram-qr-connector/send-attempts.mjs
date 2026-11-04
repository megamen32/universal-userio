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

import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, unlinkSync, writeSync } from "node:fs";
import { randomUUID } from "node:crypto";
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
    // Persist the epoch immediately. It must survive every restart of the
    // connector: it is the boundary that decides whether the absence of an
    // entry proves anything about an older attempt.
    this.save();
  }

  load() {
    if (!existsSync(this.path)) return;
    // A corrupt journal must stop the connector, not erase prior delivery identities.
    const raw = JSON.parse(readFileSync(this.path, "utf8"));
    if (!raw || !Array.isArray(raw.entries) || !Number.isSafeInteger(raw.started_at)) {
      throw new Error("Telegram send journal is corrupt; refusing to lose provider identities");
    }
    this.startedAt = raw.started_at;
    for (const row of raw.entries) {
      if (!row || typeof row.attempt_id !== "string" || !row.attempt_id) {
        throw new Error("Telegram send journal has an invalid entry");
      }
      if (this.entries.has(row.attempt_id)) throw new Error("Duplicate journal attempt ID");
      this.entries.set(row.attempt_id, row);
    }
  }

  save() {
    // The provider call must happen only after the full attempt is durable.
    mkdirSync(dirname(this.path), { recursive: true });
    const temp = this.path + ".tmp-" + randomUUID();
    let fd = null, parent = null;
    try {
      fd = openSync(temp, "wx", 0o600);
      writeSync(fd, JSON.stringify({started_at: this.startedAt, entries: [...this.entries.values()]}, null, 2));
      fsyncSync(fd);
      closeSync(fd); fd = null;
      renameSync(temp, this.path);
      parent = openSync(dirname(this.path), "r");
      fsyncSync(parent);
    } finally {
      if (fd !== null) closeSync(fd);
      if (parent !== null) closeSync(parent);
      if (existsSync(temp)) unlinkSync(temp);
    }
  }

  get(attemptId) {
    if (!attemptId) return null;
    return this.entries.get(attemptId) || null;
  }

  record(attemptId, patch) {
    if (!attemptId) return null;
    const current = this.entries.get(attemptId) || { attempt_id: attemptId, created_at: now() };
    // A late exception must never downgrade a concurrently recovered receipt.
    if (current.state === "sent" && patch.state && patch.state !== "sent") return current;
    if (current.state === "sent" && patch.state === "sent"
        && current.message_id && patch.message_id && current.message_id !== patch.message_id) {
      throw new Error("provider receipt identity conflict");
    }
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
    // Keep every in-flight attempt and every MTProto nonce permanently; losing
    // these identifiers would make later updates impossible to correlate.
    const drop = rows.filter(row => !["pending", "unknown"].includes(row.state) && !row.random_id)
      .slice(0, Math.max(0, rows.length - this.limit));
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
