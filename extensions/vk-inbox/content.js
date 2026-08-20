(() => {
  const sent = new Set();
  const text = (node) => (node?.innerText || node?.textContent || "").replace(/\s+/g, " ").trim();
  const currentPeer = () => {
    const title = document.querySelector('[data-testid="chat-header"], .im-page--header-main, .im-page--header')
      || document.querySelector('h1, h2');
    return text(title) || location.pathname.split("/").pop() || "vk-chat";
  };
  const collect = () => {
    const nodes = [...document.querySelectorAll('[data-msgid], [data-message-id], .im-mess, .im-mess--text')];
    return nodes.map((node, index) => {
      const body = text(node.querySelector('.im-mess--text, [data-testid="message-text"]') || node);
      const id = node.dataset.msgid || node.dataset.messageId || `${location.pathname}:${index}:${body}`;
      return {id: String(id), body, sender: currentPeer()};
    }).filter((item) => item.body && !sent.has(item.id));
  };
  window.userioVkCapture = () => {
    const items = collect();
    items.forEach((item) => sent.add(item.id));
    return {peer: currentPeer(), messages: items};
  };
  new MutationObserver(() => window.userioVkCapture()).observe(document.documentElement, {subtree: true, childList: true});
})();
