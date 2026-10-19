import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { TelegramAdminJournal, executeTelegramAdmin, invokeTelegramAdminOnce, prepareTelegramAdmin } from "./telegram-admin.mjs";

class Request { constructor(args) { Object.assign(this, args); } }
const Api = { messages: { CreateChat: Request, AddChatUser: Request, DeleteChatUser: Request },
  channels: { CreateChannel: Request, InviteToChannel: Request, EditBanned: Request, DeleteChannel: Request },
  ChatBannedRights: Request };

function harness({ fail = false, channel = { className: "Channel", id: 700, title: "Existing", broadcast: true, megagroup: false, creator: true } } = {}) {
  const calls = [];
  const client = {
    async getMe() { return { className: "User", id: 1, firstName: "Owner" }; },
    async getEntity(id) {
      if (String(id) === "-100700") return channel;
      return { className: "User", id: Number(id), firstName: `User${id}` };
    },
    async getInputEntity(id) { return { className: "InputUser", userId: Number(id), accessHash: 55 }; },
    async invoke(request) {
      calls.push(request);
      if (fail) throw new Error("connection dropped after submit");
      if (request instanceof Api.messages.CreateChat) return { chats: [{ className: "Chat", id: 42 }], missingInvitees: [{ userId: 9 }] };
      if (request instanceof Api.channels.CreateChannel) return { chats: [{ className: "Channel", id: 701, title: request.title, broadcast: true }] };
      return { className: "Updates" };
    },
  };
  return { client, calls };
}

test("preflight distinguishes group types, refuses self-target and requires explicit supergroup ban", async () => {
  const { client } = harness();
  const group = { className: "Chat", id: 42, title: "Basic" };
  client.getEntity = async (id) => String(id) === "-42" ? group : { className: "User", id: Number(id), firstName: "Other" };
  await assert.rejects(prepareTelegramAdmin(client, Api, "group_remove", {
    peer_id: "-42", participant_id: "1", conversation_kind: "group",
  }), /cannot_target_own_account/);
  const supergroup = { className: "Channel", id: 42, title: "Super", broadcast: false, megagroup: true };
  client.getEntity = async (id) => String(id) === "-42" ? supergroup : { className: "User", id: Number(id), firstName: "Other" };
  await assert.rejects(prepareTelegramAdmin(client, Api, "group_remove", {
    peer_id: "-42", participant_id: "9", conversation_kind: "channel",
  }), /requires_explicit_flag/);
  const prepared = await prepareTelegramAdmin(client, Api, "group_remove", {
    peer_id: "-42", participant_id: "9", conversation_kind: "channel", ban_on_supergroup: true,
  });
  assert.equal(prepared.effect, "ban");
});

test("channel deletion is restricted to the exact privately owned broadcast peer", async () => {
  const { client } = harness();
  await assert.rejects(prepareTelegramAdmin(client,Api,"channel_delete",{
    peer_id:"-100700",confirmation_phrase:"DELETE CHANNEL -100701",
  }),/channel_delete_phrase_mismatch/);
  const wrongOwner={className:"Channel",id:700,title:"Existing",broadcast:true,megagroup:false,creator:false};
  client.getEntity=async()=>wrongOwner;
  await assert.rejects(prepareTelegramAdmin(client,Api,"channel_delete",{
    peer_id:"-100700",confirmation_phrase:"DELETE CHANNEL -100700",
  }),/owned_broadcast_channel_required/);
});

test("group creation preserves partial invite result and replay returns the stored receipt", async () => {
  const dir = mkdtempSync(join(tmpdir(), "uio-admin-"));
  try {
    const journal = new TelegramAdminJournal(join(dir, "actions.jsonl"));
    const { client, calls } = harness();
    const payload = { title: "Group", participant_ids: ["9"], prepared: {
      operation: "group_create", actor_id:"1", conversation_kind: "group", participants: [{ id: "9", name: "User9" }],
    } };
    const requestId = "action_" + "a".repeat(64);
    const args = { accountId: "telegram:1", operation: "group_create", payload, requestId };
    const first = await executeTelegramAdmin(client, Api, journal, args);
    assert.equal(first.status, "partial");
    assert.deepEqual(first.missing_invitees, ["9"]);
    const second = await executeTelegramAdmin(client, Api, journal, args);
    assert.deepEqual(second, first);
    assert.equal(calls.length, 1);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("basic-group invite/removal use zero history forwarding and preserve old history", async () => {
  const dir=mkdtempSync(join(tmpdir(),"uio-admin-group-"));
  try {
    const journal=new TelegramAdminJournal(join(dir,"actions.jsonl"));
    const {client,calls}=harness();
    client.getEntity=async(id)=>String(id)==="-42"?{className:"Chat",id:42,title:"Basic"}:
      {className:"User",id:Number(id),firstName:"Member"};
    const invitePayload={peer_id:"-42",participant_id:"9",conversation_kind:"group",
      prepared:{operation:"group_invite",actor_id:"1",peer_id:"-42",conversation_kind:"group",
        participant:{id:"9",name:"Member"}}};
    const invite=await executeTelegramAdmin(client,Api,journal,{accountId:"telegram:1",operation:"group_invite",
      payload:invitePayload,requestId:"action_"+"c".repeat(64)});
    assert.equal(invite.status,"complete");
    assert.equal(calls[0].fwdLimit,0);
    const removePayload={...invitePayload,prepared:{...invitePayload.prepared,operation:"group_remove"}};
    const remove=await executeTelegramAdmin(client,Api,journal,{accountId:"telegram:1",operation:"group_remove",
      payload:removePayload,requestId:"action_"+"d".repeat(64)});
    assert.equal(remove.status,"complete");
    assert.equal(calls[1].revokeHistory,false);
  } finally { rmSync(dir,{recursive:true,force:true}); }
});

test("unknown provider outcome remains durable and is never replayed", async () => {
  const dir = mkdtempSync(join(tmpdir(), "uio-admin-"));
  try {
    const path = join(dir, "actions.jsonl");
    const journal = new TelegramAdminJournal(path);
    const { client, calls } = harness({ fail: true });
    const payload = { title: "Private", about: "", private: true, broadcast: true,
      prepared: { operation: "channel_create", actor_id:"1", conversation_kind: "channel", title: "Private", broadcast: true, megagroup: false, private: true } };
    const requestId = "action_" + "b".repeat(64);
    const args = { accountId: "telegram:1", operation: "channel_create", payload, requestId };
    const first = await executeTelegramAdmin(client, Api, journal, args);
    assert.equal(first.status, "unknown");
    const afterRestart = new TelegramAdminJournal(path);
    const replay = await executeTelegramAdmin(client, Api, afterRestart, args);
    assert.equal(replay.status, "unknown");
    assert.equal(calls.length, 1);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("admin TL invocation disables GramJS RPC retries only on a request-local view", async () => {
  const calls=[];const client={_requestRetries:5,calls,async invoke(){this.calls.push(this._requestRetries);assert.equal(this._requestRetries,1);throw new Error("RPC failure");}};
  await assert.rejects(invokeTelegramAdminOnce(client,{}),/RPC failure/);
  assert.equal(client._requestRetries,5);
  assert.deepEqual(calls,[1]);
});
