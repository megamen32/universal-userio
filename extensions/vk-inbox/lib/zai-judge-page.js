// Bundled fixed Z.AI page operation from Agent Herder; no dynamic source evaluation.
// All site scripts execute only in MAIN world on https://chat.z.ai.
(function(root){
/** Serialized only into an already owned page. It has no module-scope dependencies. */
async function pageOperation(p) {
    const page = globalThis, doc = page.document;
    const visible = (e) => !!e && e.getClientRects().length > 0;
    const ownerName = "agent-herder-zai-judge:" + p.owner;
    if (page.location.origin !== "https://chat.z.ai")
        return { ok: false, reason: "origin_changed" };
    const url = new URL(page.location.href);
    const rootIdentity = url.searchParams.get("agent-herder-zai-judge") === p.owner
        || p.acknowledgedNewPage === true && url.search === "" && url.hash === "";
    if (p.step === "claim") {
        if (page.location.pathname !== "/" || !rootIdentity)
            return { ok: false, reason: "new_page_identity_unverified" };
        if (page.name && page.name !== ownerName)
            return { ok: false, reason: "page_already_owned" };
        // Z.AI removes query parameters while hydrating a newly allocated page.
        // A successful exact-ID receipt is ownership evidence, but never permission
        // to claim a page that has acquired a conversation, draft, or generation.
        const blankEditor = doc.querySelector('textarea[placeholder="Send a Message"]');
        const blankSend = doc.querySelector("#send-message-button");
        const active = [...doc.querySelectorAll("button")].some((button) => visible(button)
            && !!button.querySelector(":scope > span.block.size-3.rounded-xs.bg-text-inverted"));
        if (!visible(blankEditor) || !visible(blankSend) || blankEditor.value || active)
            return { ok: false, reason: "new_page_not_blank" };
        page.name = ownerName;
    }
    if (page.name !== ownerName)
        return { ok: false, reason: "page_owner_changed" };
    if (page.location.pathname !== p.path || p.path === "/" && !rootIdentity)
        return { ok: false, reason: "target_changed", path: page.location.pathname };
    const controls = () => [...doc.querySelectorAll("button")].filter(visible);
    const generating = () => controls().some((button) => !!button.querySelector(":scope > span.block.size-3.rounded-xs.bg-text-inverted"));
    const editor = () => doc.querySelector('textarea[placeholder="Send a Message"]');
    const send = () => doc.querySelector("#send-message-button");
    const picker = () => [...doc.querySelectorAll('button[aria-label="Select a model"]')].filter(visible);
    const security = [...doc.querySelectorAll("body *")].some((e) => visible(e) && e.children.length === 0
        && /please complete security verification|please drag the slider to complete the puzzle/i.test(e.textContent || ""));
    if (security)
        return { ok: false, reason: "security_verification" };
    const alerts = [...doc.querySelectorAll('[role="alert"],[class*="border-red-600"],[class*="bg-red-600"]')]
        .filter(visible).map((e) => e.innerText || e.textContent || "").join("\n");
    const errorKind = /usage (?:cap|limit)|quota (?:exceeded|exhausted)|insufficient balance|лимит.{0,25}исчерпан/i.test(alerts) ? "quota"
        : /current concurrent conversation limit has been reached|too many requests|rate limit|currently (?:busy|overloaded)|high demand|overloaded/i.test(alerts) ? "busy" : undefined;
    const ready = () => visible(editor()) && visible(send());
    const safe = () => page.name === ownerName && page.location.origin === "https://chat.z.ai"
        && page.location.pathname === p.path && ready() && !generating();
    if (p.step === "claim" || p.step === "state")
        return { ok: true, path: page.location.pathname, phase: generating() ? "generating" : ready() ? "idle" : "unknown",
            draft: !!editor()?.value, ...(errorKind ? { kind: errorKind } : {}) };
    if (p.step === "model") {
        if (!safe() || editor().value)
            return { ok: false, kind: "busy", reason: "target_not_idle" };
        if (errorKind)
            return { ok: false, kind: errorKind };
        const buttons = picker();
        if (buttons.length !== 1)
            return { ok: false, reason: "picker_unavailable" };
        const button = buttons[0];
        const wasOpen = button.getAttribute("data-state") === "open";
        if (!wasOpen)
            button.click();
        try {
            await new Promise(resolve => page.setTimeout(resolve, 160));
            if (page.name !== ownerName || page.location.origin !== "https://chat.z.ai" || page.location.pathname !== p.path)
                return { ok: false, reason: "target_changed" };
            if (!safe() || editor().value)
                return { ok: false, kind: "busy", reason: "target_not_idle" };
            const matches = [...doc.querySelectorAll('[role="menu"][data-state="open"] button')].filter(visible)
                .filter((item) => (item.textContent || "").trim() === p.model);
            if (matches.length !== 1)
                return { ok: false, kind: "model_unavailable" };
            if ((button.textContent || "").trim() !== p.model)
                matches[0].click();
            await new Promise(resolve => page.setTimeout(resolve, 180));
            return safe() && !editor().value && (button.textContent || "").trim() === p.model
                ? { ok: true } : { ok: false, reason: "model_readback_unconfirmed" };
        }
        finally {
            if (!wasOpen && page.name === ownerName && page.location.pathname === p.path
                && button.getAttribute("data-state") === "open")
                button.click();
        }
    }
    if (p.step === "clear") {
        if (!safe() || editor().value !== p.prompt)
            return { ok: false, reason: "draft_changed" };
        const setter = Object.getOwnPropertyDescriptor(page.HTMLTextAreaElement.prototype, "value")?.set;
        if (!setter)
            return { ok: false, reason: "composer_unavailable" };
        setter.call(editor(), "");
        editor().dispatchEvent(new page.Event("input", { bubbles: true }));
        return { ok: editor().value === "" };
    }
    if (p.step === "fill") {
        if (!safe() || editor().value)
            return { ok: false, kind: "busy", reason: "target_not_idle" };
        if (picker().length !== 1 || (picker()[0].textContent || "").trim() !== p.model)
            return { ok: false, reason: "model_changed" };
        const setter = Object.getOwnPropertyDescriptor(page.HTMLTextAreaElement.prototype, "value")?.set;
        if (!setter || typeof p.prompt !== "string")
            return { ok: false, reason: "composer_unavailable" };
        setter.call(editor(), p.prompt);
        editor().dispatchEvent(new page.Event("input", { bubbles: true }));
        editor().dispatchEvent(new page.Event("change", { bubbles: true }));
        return { ok: editor().value === p.prompt };
    }
    if (p.step === "click") {
        if (editor()?.value !== p.prompt)
            return { ok: false, reason: "draft_changed" };
        if (picker().length !== 1 || (picker()[0].textContent || "").trim() !== p.model)
            return { ok: false, reason: "model_changed" };
        if (!safe() || send().disabled)
            return { ok: false, kind: "busy", reason: "pre_click_busy" };
        // All target/model/draft/Stop checks and the sole Send click share one task.
        send().click();
        return { ok: true };
    }
    if (p.step !== "read")
        return { ok: false, reason: "unsupported_step" };
    // Visible post-click alerts can belong to a sidebar/history request.
    // They never prove that this inference was rejected; read its native run.
    const chatId = p.path.match(/^\/c\/([a-f0-9-]{12,80})$/i)?.[1];
    if (!chatId)
        return { ok: false, reason: "route_pending" };
    const controller = new page.AbortController(), timer = page.setTimeout(() => controller.abort(), 10_000);
    try {
        const response = await page.fetch("/api/v1/chats/" + encodeURIComponent(chatId), { credentials: "same-origin", signal: controller.signal });
        if (!response.ok)
            return { ok: false, reason: "history_unavailable" };
        const chat = await response.json();
        if (chat.id !== chatId || !chat.user_id)
            return { ok: false, reason: "history_identity_mismatch" };
        const digest = async (text) => [...new Uint8Array(await page.crypto.subtle.digest("SHA-256", new page.TextEncoder().encode(text)))]
            .map((n) => n.toString(16).padStart(2, "0")).join("");
        if (await digest(String(chat.user_id)) !== p.scope)
            return { ok: false, reason: "account_changed" };
        const mapping = chat.chat?.history?.messages;
        if (!mapping || typeof mapping !== "object" || Array.isArray(mapping))
            return { ok: false, reason: "history_pending" };
        const ids = Object.keys(mapping);
        if (!ids.length)
            return { ok: false, reason: "history_pending" };
        if (ids.length > 32)
            return { ok: false, reason: "unexpected_conversation_history" };
        const batch = await page.fetch("/api/v1/chats/" + encodeURIComponent(chatId) + "/messages/batch", {
            method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
            body: JSON.stringify({ ids }), signal: controller.signal,
        });
        if (!batch.ok)
            return { ok: false, reason: "history_unavailable" };
        const result = await batch.json();
        if (result.chat_id !== chatId || !result.data || ids.some(id => result.data[id]?.id !== id))
            return { ok: false, reason: "history_identity_mismatch" };
        const all = ids.map(id => result.data[id]);
        const text = (m) => typeof m.content === "string" ? m.content : (Array.isArray(m.content_blocks) ? m.content_blocks : [])
            .filter((block) => block.type === "text" || block.type === "markdown")
            .map((block) => typeof block.content === "string" ? block.content : "").join("\n");
        const users = all.filter(m => m.role === "user");
        if (users.length !== 1 || await digest(text(users[0])) !== p.promptHash)
            return { ok: false, reason: "prompt_correlation_mismatch" };
        const assistants = all.filter(m => m.role === "assistant");
        const run = chat.last_run;
        const runStatus = typeof run?.status === "string" ? run.status : undefined;
        // This newly created chat contains exactly our one unique prompt. A typed
        // failed native run is distinct from an unrelated visible rate-limit toast.
        if (["failed", "error", "rejected"].includes(runStatus ?? "")) {
            if (!safe())
                return { ok: false, reason: "response_pending", userMessageId: users[0].id };
            const code = typeof run?.error?.code === "string" || typeof run?.error?.code === "number" ? String(run.error.code) : "";
            if (["1113", "1308", "1310", "1316", "1317", "1318", "1319", "1320", "1321", "quota_exceeded", "insufficient_quota"].includes(code))
                return { ok: false, kind: "quota", userMessageId: users[0].id };
            if (["1302", "1305", "rate_limit_exceeded", "overloaded", "concurrent_conversation_limit"].includes(code))
                return { ok: false, kind: "busy", userMessageId: users[0].id };
            if (["model_not_found", "model_not_supported", "unsupported_model", "invalid_model"].includes(code))
                return { ok: false, kind: "model_unavailable", userMessageId: users[0].id };
            return { ok: false, reason: "native_run_rejection_unclassified", userMessageId: users[0].id };
        }
        if (assistants.length !== 1)
            return { ok: false, reason: assistants.length ? "ambiguous_assistant" : "response_pending", userMessageId: users[0].id };
        const assistant = assistants[0], answer = text(assistant);
        const parent = assistant.parentId ?? assistant.parent_id ?? mapping[assistant.id]?.parentId ?? mapping[assistant.id]?.parent_id;
        if (parent && parent !== users[0].id)
            return { ok: false, reason: "assistant_parent_mismatch" };
        if (typeof assistant.model === "string" && assistant.model.toLowerCase() !== p.model.toLowerCase())
            return { ok: false, reason: "assistant_model_mismatch" };
        const completed = assistant.done === true || ["completed", "done", "success", "succeeded"].includes(runStatus ?? "");
        if (!safe() || !completed || assistant.done === false
            || runStatus !== undefined && !["completed", "done", "success", "succeeded"].includes(runStatus))
            return { ok: false, reason: "response_pending", userMessageId: users[0].id };
        if (!answer.trim())
            return { ok: false, reason: "response_pending", userMessageId: users[0].id };
        if (new page.TextEncoder().encode(answer).length > 64 * 1024)
            return { ok: false, reason: "answer_too_large" };
        return { ok: true, userMessageId: users[0].id, assistantMessageId: assistant.id, text: answer };
    }
    finally {
        page.clearTimeout(timer);
    }
}

root.UserIOZaiJudgePageOperation=pageOperation;
})(typeof self!=="undefined"?self:this);
