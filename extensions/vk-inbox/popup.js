// Popup UI: tabs (chats, search, compose). Communicates with the background SW.

(function () {
  const U = self.UserIO;
  const $ = (id) => document.getElementById(id);
  const tabs = document.querySelectorAll("nav button");
  const sections = {
    chats: $("tab-chats"),
    search: $("tab-search"),
    compose: $("tab-compose"),
  };

  tabs.forEach((b) => {
    b.addEventListener("click", () => {
      tabs.forEach((x) => x.classList.toggle("active", x === b));
      Object.entries(sections).forEach(([k, el]) => el.classList.toggle("active", k === b.dataset.tab));
      if (b.dataset.tab === "chats") refreshChats();
      if (b.dataset.tab === "search") $("searchInput").focus();
    });
  });

  $("openOptions").addEventListener("click", () => chrome.runtime.openOptionsPage());

  function fmtTime(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    return sameDay
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString();
  }

  async function callSW(msg) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(msg, (r) => {
          if (chrome.runtime.lastError) {
            resolve({ ok: false, error: String(chrome.runtime.lastError.message) });
          } else {
            resolve(r);
          }
        });
      } catch (e) {
        resolve({ ok: false, error: String(e.message) });
      }
    });
  }

  async function refreshChats(attempt = 0) {
    const list = $("chatList");
    list.innerHTML = '<div class="empty">Загрузка…</div>';
    const res = await callSW({ kind: "listChats" });
    if (!res || !res.ok) {
      list.innerHTML = `<div class="empty">Ошибка: ${(res && res.error) || "нет ответа"}</div>`;
      return;
    }
    const chats = res.chats || [];
    if (!chats.length && attempt < 2) {
      // SW may not have indexed yet — retry once after a short delay.
      await new Promise((r) => setTimeout(r, 600));
      return refreshChats(attempt + 1);
    }
    if (!chats.length) {
      list.innerHTML = '<div class="empty">Нет данных. Откройте VK Web и побудьте там минуту.</div>';
      return;
    }
    list.innerHTML = "";
    for (const c of chats) {
      const el = document.createElement("div");
      el.className = "chat";
      el.innerHTML = `
        <div class="row"><span class="name"></span><span class="time"></span></div>
        <div class="row"><span class="preview"></span><span class="unread" hidden></span></div>
      `;
      el.querySelector(".name").textContent = c.name || c.peer_id;
      el.querySelector(".time").textContent = fmtTime(c.last_message_at);
      el.querySelector(".preview").textContent = c.last_preview || "";
      if (c.unread) {
        const u = el.querySelector(".unread");
        u.textContent = String(c.unread);
        u.hidden = false;
      }
      el.addEventListener("click", () => {
        $("composePeer").value = c.peer_id;
        tabs.forEach((x) => x.classList.toggle("active", x.dataset.tab === "compose"));
        Object.entries(sections).forEach(([k, el2]) => el2.classList.toggle("active", k === "compose"));
        $("composeBody").focus();
      });
      list.appendChild(el);
    }
  }

  async function refreshStats(attempt = 0) {
    const res = await callSW({ kind: "stats" });
    if (res && res.ok) {
      $("stats").textContent = `чатов: ${res.stats.chats} · сообщений: ${res.stats.messages} · черновиков: ${res.stats.drafts}`;
    } else if (attempt < 2) {
      await new Promise((r) => setTimeout(r, 600));
      return refreshStats(attempt + 1);
    }
  }

  let searchTimer = null;
  $("searchInput").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    const q = e.target.value;
    searchTimer = setTimeout(async () => {
      const res = await callSW({ kind: "search", query: q });
      const out = $("searchResults");
      if (!q.trim()) {
        out.innerHTML = '<div class="empty">Введите запрос.</div>';
        return;
      }
      if (!res || !res.ok) {
        out.innerHTML = `<div class="empty">Ошибка: ${(res && res.error) || ""}</div>`;
        return;
      }
      const hits = res.results || [];
      if (!hits.length) {
        out.innerHTML = '<div class="empty">Ничего не найдено.</div>';
        return;
      }
      out.innerHTML = "";
      for (const m of hits) {
        const el = document.createElement("div");
        el.className = "msg";
        const dir = m.direction === "out" ? "исх." : "вх.";
        el.innerHTML = `
          <div class="body"></div>
          <div class="meta">
            <span class="peer"></span> · <span class="time"></span> · <span class="dir"></span>
          </div>
        `;
        el.querySelector(".body").textContent = m.body;
        el.querySelector(".peer").textContent = m.peer_id;
        el.querySelector(".time").textContent = fmtTime(m.ts);
        el.querySelector(".dir").textContent = dir;
        el.querySelector(".dir").className = m.direction === "out" ? "dir-out" : "dir-in";
        el.addEventListener("click", () => {
          $("composePeer").value = m.peer_id;
          $("composeBody").value = "";
          tabs.forEach((x) => x.classList.toggle("active", x.dataset.tab === "compose"));
          Object.entries(sections).forEach(([k, el2]) => el2.classList.toggle("active", k === "compose"));
          $("composeBody").focus();
        });
        out.appendChild(el);
      }
    }, 150);
  });

  async function send() {
    const peer = $("composePeer").value.trim();
    const body = $("composeBody").value;
    const status = $("composeStatus");
    if (!peer || !body) {
      status.textContent = "Укажите peer и текст.";
      status.className = "status err";
      return;
    }
    status.textContent = "Отправляю…";
    status.className = "status";
    const res = await callSW({ kind: "send", peer_id: peer, body });
    if (res && res.ok) {
      status.textContent = `Отправлено (msg_id: ${res.msg_id}).`;
      status.className = "status ok";
      $("composeBody").value = "";
      refreshStats();
      refreshChats();
    } else {
      status.textContent = `Ошибка: ${(res && res.error) || "неизвестно"}`;
      status.className = "status err";
    }
  }

  $("composeSend").addEventListener("click", send);
  $("composeBody").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") send();
  });

  $("purge").addEventListener("click", async () => {
    if (!confirm("Очистить локальный кеш расширения? Это не удалит сообщения в VK.")) return;
    await callSW({ kind: "purge" });
    refreshChats();
    refreshStats();
  });

  chrome.runtime.onMessage.addListener((m) => {
    if (m && m.kind === "captured") {
      refreshStats();
      if (sections.chats.classList.contains("active")) refreshChats();
    }
  });

  refreshChats();
  refreshStats();
})();
