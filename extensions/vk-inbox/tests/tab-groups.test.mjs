import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";

function fixture() {
  const tabs = [
    { id: 1, windowId: 11, groupId: -1, url: "https://chatgpt.com/dots/room" },
    { id: 2, windowId: 12, groupId: -1, url: "https://chat.z.ai/" },
  ];
  const groups = [];
  let nextGroup = 1;
  let nextTab = 3;
  const stored = {};
  const chrome = {
    storage: { local: {
      async get(key) { return { [key]: stored[key] }; },
      async set(value) { Object.assign(stored, value); },
    } },
    tabs: {
      async get(id) { const tab = tabs.find(item => item.id === id); if (!tab) throw new Error("missing"); return { ...tab }; },
      async query() { return tabs.map(tab => ({ ...tab })); },
      async group({ tabIds, groupId }) {
        const gid = groupId ?? nextGroup++;
        if (!groupId) groups.push({ id: gid, windowId: tabs.find(tab => tab.id === tabIds[0]).windowId, title: "", color: "grey" });
        for (const id of tabIds) tabs.find(tab => tab.id === id).groupId = gid;
        return gid;
      },
      async ungroup(id) { tabs.find(tab => tab.id === id).groupId = -1; },
      async update(id, patch) { Object.assign(tabs.find(tab => tab.id === id), patch); return { ...tabs.find(tab => tab.id === id) }; },
      async create({ url, active }) { const tab = { id: nextTab++, windowId: 11, groupId: -1, url, active }; tabs.push(tab); return { ...tab }; },
    },
    tabGroups: {
      async get(id) { const group = groups.find(item => item.id === id); if (!group) throw new Error("missing"); return { ...group }; },
      async query() { return groups.map(group => ({ ...group })); },
      async update(id, patch) { Object.assign(groups.find(item => item.id === id), patch); return { ...groups.find(item => item.id === id) }; },
    },
  };
  const context = { chrome, self: {}, URL, console, setTimeout, clearTimeout };
  context.self = context;
  vm.runInNewContext(fs.readFileSync(new URL("../lib/tab-groups.js", import.meta.url), "utf8"), context);
  return { api: context.UserIOTabGroups, tabs, groups, stored };
}

test("creates one service group per browser window and restores by title", async () => {
  const f = fixture();
  const dots = await f.api.assign("dots", 1);
  const zai = await f.api.assign("zai", 2);
  assert.equal(dots, 1);
  assert.equal(zai, 2);
  assert.deepEqual(f.groups.map(group => [group.windowId, group.title]), [
    [11, "UserIO · Dots"],
    [12, "UserIO · Z.AI"],
  ]);
  f.stored["userio.tabGroups.v1"].groups["11:dots"] = 999;
  const recovered = await f.api.assign("dots", 1);
  assert.equal(recovered, 1);
  assert.equal(f.groups.filter(group => group.title === "UserIO · Dots").length, 1);
});

test("reuses a stable slot and refuses capacity instead of spawning unbounded tabs", async () => {
  const f = fixture();
  const first = await f.api.acquire("zai", {
    slot: "send",
    url: "https://chat.z.ai/?userio_slot=send",
    match: tab => String(tab.url || "").includes("userio_slot=send"),
  });
  const second = await f.api.acquire("zai", {
    slot: "send",
    url: "https://chat.z.ai/?userio_slot=send",
    match: tab => String(tab.url || "").includes("userio_slot=send"),
  });
  assert.equal(first.ok, true);
  assert.equal(second.ok, true);
  assert.equal(second.reused, true);
  assert.equal(f.tabs.filter(tab => tab.url.includes("userio_slot=send")).length, 1);
  const groups = await f.api.diagnostics();
  assert.equal(groups.groups.zai.length, 1);
});
