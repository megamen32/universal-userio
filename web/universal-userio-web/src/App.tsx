import { useCallback, useEffect, useMemo, useState } from "react"
import { ArrowLeft, Check, ChevronDown, Expand, Image as ImageIcon, Inbox, LogOut, Mail, Menu, MessageCircle, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Plus, Send, Sparkles, Video, X } from "lucide-react"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"

type Account = { id: string; provider: string; display_name: string; capabilities: string[] }
type Chat = { id: string; source: string; sender: string; identity_id?: string; preview?: string; unread_count: number }
type Conversation = { id: string; source: string; sender: string; identity_id?: string; messages: Message[]; drafts: Draft[] }
type Message = { source: string; message_id: string; sender: string; body: string; received_at: number; seen_at?: number; attachment_url?: string }
type Draft = { id: string; body: string; status: string }

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { error?: string } | null
    throw new Error(payload?.error || "Request failed")
  }
  return response.json() as Promise<T>
}

const providerForSource = (source: string) => source === "email" || source.startsWith("gmail") ? "gmail" : source
const sourceForAccount = (account: Account) => {
  if (account.provider !== "gmail") return account.provider
  const alias = account.id.match(/^gmail-(.+)$/i)?.[1]
  return alias ? `gmail:${alias}` : account.provider
}
const channelIcon = (source: string) => providerForSource(source) === "gmail" ? <Mail className="size-4" /> : <MessageCircle className="size-4" />
const displayChannel = (source: string) => providerForSource(source) === "gmail" ? "Email" : source[0].toUpperCase() + source.slice(1)

// Human-readable chat titles instead of raw provider JIDs.
const prettySender = (sender: string) => {
  if (sender.endsWith("@s.whatsapp.net")) return "+" + sender.replace("@s.whatsapp.net", "")
  if (sender.endsWith("@lid")) {
    const id = sender.replace("@lid", "")
    return `wa:${id.length > 8 ? id.slice(0, 4) + "…" + id.slice(-4) : id}`
  }
  if (sender.endsWith("@g.us")) return "group " + sender.replace("@g.us", "").slice(-6)
  if (/^-?\d+$/.test(sender)) return `tg:${sender}`
  return sender
}
const chatTitle = (identityId: string | undefined, sender: string) => identityId || prettySender(sender)
const previewText = (raw: string | undefined) => {
  const text = raw || "No messages yet"
  if (!/^\s*<(!doctype|html|body|div|p|h[1-6]|table|br|img|a\s)/i.test(text)) return text
  return text
    .replace(/<(style|script)[\s\S]*?<\/\1>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&(amp|lt|gt|quot|#39);/g, (_, entity: string) => ({ amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" })[entity] ?? " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 140) || "HTML email"
}
const initials = (value: string) => value.split(/[.@\s_-]/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase()

const MEDIA_PLACEHOLDER = /^\[\s*(?:WhatsApp|Telegram)?\s*(image|video|audio|voice|document|sticker|фото|видео|аудио|голосовое|файл)\s*\]$/i
const IMAGE_URL = /^https?:\/\/\S+\.(?:jpe?g|png|webp|gif)(?:\?\S*)?$/i

const MessageBody = ({ message }: { message: Message }) => {
  if (message.attachment_url) return <img src={message.attachment_url} alt="attachment" loading="lazy" className="max-h-80 rounded-xl bg-muted" />
  const placeholder = message.body.trim().match(MEDIA_PLACEHOLDER)
  if (placeholder) {
    const kind = placeholder[1].toLowerCase()
    return <span className="flex items-center gap-2 rounded-xl bg-muted px-3 py-2">{/video|видео/.test(kind) ? <Video className="size-4" /> : <ImageIcon className="size-4" />}{message.body.trim()}</span>
  }
  if (IMAGE_URL.test(message.body.trim())) return <img src={message.body.trim()} alt="attachment" loading="lazy" className="max-h-80 rounded-xl bg-muted" />
  return <p className="whitespace-pre-wrap">{message.body}</p>
}

export function App() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [chats, setChats] = useState<Chat[]>([])
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [selectedAccount, setSelectedAccount] = useState<string>("all")
  const [selectedChannel, setSelectedChannel] = useState<string>("all")
  const [selectedChat, setSelectedChat] = useState<string>("")
  const [draft, setDraft] = useState("")
  const [search, setSearch] = useState("")
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [chatsOpen, setChatsOpen] = useState(true)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [mobilePane, setMobilePane] = useState<"chats" | "chat">("chats")
  const [expandedHtml, setExpandedHtml] = useState<{ body: string; title: string } | null>(null)
  const [hiddenAccounts, setHiddenAccounts] = useState<string[]>(() => JSON.parse(localStorage.getItem("userio-hidden-accounts") || "[]"))

  const account = accounts.find((item) => item.id === selectedAccount)
  const conversationAccount = conversation && accounts.find((item) => item.id === (item.provider === "gmail" && conversation.source.startsWith("gmail:") ? conversation.source.replace(/^gmail:/, "gmail-") : conversation.source))
  const canReply = !conversationAccount || conversationAccount.capabilities.includes("reply")
  const activeChannel = selectedChannel !== "all" ? providerForSource(selectedChannel) : account ? providerForSource(account.provider) : undefined
  const platforms = useMemo(() => Array.from(new Set(["gmail", "telegram", "vk", "whatsapp", ...accounts.map((item) => providerForSource(item.provider)), ...chats.map((item) => providerForSource(item.source))])).sort(), [accounts, chats])

  const refreshChats = useCallback(async () => {
    const sourceFilter = account ? sourceForAccount(account) : activeChannel
    const suffix = sourceFilter ? `?source=${encodeURIComponent(sourceFilter)}` : ""
    const data = await api<{ conversations: Chat[] }>(`/v1/conversations${suffix}`)
    setChats(data.conversations)
    setSelectedChat((current) => current && data.conversations.some((item) => item.id === current) ? current : data.conversations[0]?.id ?? "")
  }, [account, activeChannel])

  const loadConversation = useCallback(async (id: string) => setConversation(await api<Conversation>(`/v1/conversations/${id}`)), [])

  useEffect(() => { void api<{ accounts: Account[] }>("/v1/accounts").then((data) => setAccounts(data.accounts)) }, [])
  // The callback fetches external state before updating the view.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void refreshChats() }, [refreshChats])
  // The callback fetches external state before updating the view.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { if (selectedChat) void loadConversation(selectedChat) }, [loadConversation, selectedChat])

  const openChat = (id: string) => { setSelectedChat(id); setMobilePane("chat") }

  const submitDraft = async () => {
    if (!conversation || !canReply || !draft.trim()) return
    await api(`/v1/conversations/${conversation.id}/drafts`, { method: "POST", body: JSON.stringify({ body: draft }) })
    setDraft("")
    await loadConversation(conversation.id)
    await refreshChats()
  }
  const askAi = async () => { if (conversation && canReply) { await api(`/v1/conversations/${conversation.id}/ai-drafts`, { method: "POST", body: "{}" }); await loadConversation(conversation.id) } }
  const approve = async (id: string) => { if (canReply) { await api(`/v1/drafts/${id}/approve`, { method: "POST", body: "{}" }); if (conversation) await loadConversation(conversation.id) } }
  const markSeen = async () => { const message = conversation?.messages.at(-1); if (message) { await api("/v1/inbox/seen", { method: "POST", body: JSON.stringify({ source: message.source, message_id: message.message_id }) }); await refreshChats(); await loadConversation(conversation!.id) } }
  const toggleAccount = (id: string, visible: boolean) => {
    const next = visible ? hiddenAccounts.filter((item) => item !== id) : Array.from(new Set([...hiddenAccounts, id]))
    setHiddenAccounts(next)
    localStorage.setItem("userio-hidden-accounts", JSON.stringify(next))
    if (!visible && selectedAccount === id) setSelectedAccount("all")
  }
  const removeAccount = async (id: string) => {
    if (!window.confirm("Удалить аккаунт из UserIO? Данные у провайдера не удаляются.")) return
    await api(`/v1/accounts/${encodeURIComponent(id)}`, { method: "DELETE" })
    setAccounts((current) => current.filter((item) => item.id !== id))
    if (selectedAccount === id) setSelectedAccount("all")
  }
  const visibleChats = chats.filter((chat) => !accounts.some((item) => hiddenAccounts.includes(item.id) && chat.source === sourceForAccount(item)))
    .filter((chat) => `${chat.sender} ${chat.preview ?? ""}`.toLowerCase().includes(search.toLowerCase()))

  const sidebar = <>
    <header className="flex items-center gap-3 p-4"><div className="grid size-9 place-items-center rounded-xl bg-primary text-primary-foreground"><Inbox className="size-5" /></div><div><p className="text-xs font-medium text-muted-foreground">PLATFORMS</p><h1 className="text-lg font-semibold">Universal UserIO</h1></div><Button className="ml-auto md:hidden" variant="ghost" size="icon" onClick={() => setDrawerOpen(false)}><X /></Button></header>
    <ScrollArea className="min-h-0 flex-1 px-2">
      <Button className="mb-2 w-full justify-start" variant={selectedChannel === "all" && selectedAccount === "all" ? "secondary" : "ghost"} onClick={() => { setSelectedAccount("all"); setSelectedChannel("all"); setDrawerOpen(false) }}><Inbox /> All conversations</Button>
      {platforms.map((platform) => <details key={platform} className="mb-2 rounded-lg border bg-muted/20" open={activeChannel === platform}><summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-semibold [&::-webkit-details-marker]:hidden">{channelIcon(platform)} {displayChannel(platform)} <ChevronDown className="ml-auto size-4" /></summary><div className="border-t p-1"><Button className="w-full justify-start" variant={selectedChannel === platform && selectedAccount === "all" ? "secondary" : "ghost"} onClick={() => { setSelectedChannel(platform); setSelectedAccount("all"); setDrawerOpen(false) }}>All {displayChannel(platform)}</Button>{accounts.filter((item) => providerForSource(item.provider) === platform).map((item) => { const visible = !hiddenAccounts.includes(item.id); return <div key={item.id} className="mt-1 flex items-center gap-1"><Button className="min-w-0 flex-1 justify-start" variant={selectedAccount === item.id ? "secondary" : "ghost"} onClick={() => { if (visible) { setSelectedAccount(item.id); setSelectedChannel("all"); setDrawerOpen(false) } }}><Avatar className="size-6"><AvatarFallback>{initials(item.display_name)}</AvatarFallback></Avatar><span className="truncate">{item.display_name}</span>{item.capabilities.length === 0 && <span className="ml-auto text-[10px] text-muted-foreground">ID only</span>}</Button><input aria-label={`Show ${item.display_name}`} type="checkbox" checked={visible} onChange={(event) => toggleAccount(item.id, event.target.checked)} /><Button aria-label={`Remove ${item.display_name}`} title="Remove account" variant="ghost" size="icon" onClick={() => void removeAccount(item.id)}><X className="size-3" /></Button></div> })}{platform === "gmail" && <Button className="mt-1 w-full justify-start" variant="outline" onClick={() => window.location.assign("/gmail/connect/new")}><Plus /> Add Gmail account</Button>}{platform === "telegram" && <Button className="mt-1 w-full justify-start" variant="outline" onClick={() => window.location.assign("/telegram-qr/new")}><Plus /> Add Telegram account</Button>}{platform === "vk" && <Button className="mt-1 w-full justify-start" variant="outline" onClick={() => window.location.assign("/vk/connect/new")}><Plus /> Add VK account</Button>}{platform === "whatsapp" && <Button className="mt-1 w-full justify-start" variant="outline" onClick={() => window.location.assign("/whatsapp-qr/new")}><Plus /> Add WhatsApp account</Button>}</div></details>)}
    </ScrollArea>
    <div className="flex items-center gap-2 border-t p-3 text-xs text-muted-foreground"><form method="post" action="/auth/logout"><Button type="submit" variant="ghost" size="sm"><LogOut /> Выйти</Button></form><span>AI proposes only when you ask.</span></div>
  </>

  return <main className="flex h-svh min-h-0 overflow-hidden bg-background text-foreground">
    {/* Desktop sidebar / mobile drawer */}
    <aside className={`min-h-0 w-[280px] shrink-0 flex-col overflow-hidden border-r bg-card ${sidebarOpen ? "md:flex" : "md:hidden"} ${drawerOpen ? "absolute inset-y-0 left-0 z-40 flex shadow-2xl" : "hidden"}`}>{sidebar}</aside>
    {drawerOpen && <button aria-label="Close menu" className="absolute inset-0 z-30 bg-black/50 md:hidden" onClick={() => setDrawerOpen(false)} />}

    {/* Chat list: default pane on mobile */}
    <section className={`min-h-0 min-w-0 flex-1 flex-col overflow-hidden border-r bg-card md:w-[340px] md:flex-none ${chatsOpen ? "md:flex" : "md:hidden"} ${mobilePane === "chats" ? "flex" : "hidden"}`}>
      <header className="space-y-3 p-4"><div className="flex items-start gap-2"><Button variant="ghost" size="icon" className="md:hidden" onClick={() => setDrawerOpen(true)} title="Accounts"><Menu /></Button><Button variant="ghost" size="icon" className="hidden md:inline-flex" onClick={() => setSidebarOpen((open) => !open)} title={sidebarOpen ? "Hide platforms" : "Show platforms"}>{sidebarOpen ? <PanelLeftClose /> : <PanelLeftOpen />}</Button><div><p className="text-xs font-medium text-muted-foreground">CHATS</p><p className="text-sm text-muted-foreground">{activeChannel ? displayChannel(activeChannel) : "All conversations"}</p></div></div><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search chats" /></header>
      <ScrollArea className="min-h-0 flex-1 px-2 pb-3">
        {visibleChats.map((chat) => <button key={chat.id} onClick={() => openChat(chat.id)} className={`mb-1 flex w-full gap-3 rounded-lg p-3 text-left transition-colors ${selectedChat === chat.id ? "bg-accent" : "hover:bg-muted"}`}>
          <Avatar><AvatarFallback>{initials(chatTitle(chat.identity_id, chat.sender))}</AvatarFallback></Avatar><span className="min-w-0 flex-1"><span className="flex items-center justify-between gap-2"><b className="truncate text-sm">{chatTitle(chat.identity_id, chat.sender)}</b>{chat.unread_count > 0 && <Badge className="rounded-full px-1.5">{chat.unread_count}</Badge>}</span><span className="mt-1 block truncate text-xs text-muted-foreground">{previewText(chat.preview)}</span></span>
        </button>)}
        {!visibleChats.length && <p className="p-5 text-center text-sm text-muted-foreground">No chats in this channel.</p>}
      </ScrollArea>
    </section>

    {/* Conversation: fullscreen pane on mobile */}
    <section className={`min-h-0 min-w-0 flex-1 flex-col bg-[linear-gradient(135deg,hsl(var(--background)),hsl(var(--muted)/.35))] md:flex ${mobilePane === "chat" ? "flex" : "hidden"}`}>
      {conversation ? <>
        <header className="flex items-center gap-3 border-b bg-card/90 px-3 py-3 backdrop-blur md:px-5"><Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMobilePane("chats")} title="Back"><ArrowLeft /></Button><Button variant="ghost" size="icon" className="hidden md:inline-flex" onClick={() => setChatsOpen((open) => !open)} title={chatsOpen ? "Hide chats" : "Show chats"}>{chatsOpen ? <PanelRightClose /> : <PanelRightOpen />}</Button><Avatar><AvatarFallback>{initials(conversation.sender)}</AvatarFallback></Avatar><div className="min-w-0"><h2 className="truncate font-semibold">{chatTitle(conversation.identity_id, conversation.sender)}</h2><p className="text-xs text-muted-foreground">{displayChannel(conversation.source)} · {conversation.sender}</p></div><Button className="ml-auto" variant="outline" size="sm" onClick={markSeen}><Check /> <span className="hidden sm:inline">Mark seen</span></Button></header>
        <ScrollArea className="min-h-0 flex-1"><div className="mx-auto flex max-w-3xl flex-col gap-3 p-4 md:p-6">
          {conversation.messages.map((message) => {
            const isHtmlEmail = message.source.startsWith("gmail") && /^\s*<(?:!doctype|html|body|table|div|p|span|h[1-6]|a\b)/i.test(message.body)
            const outgoing = message.sender !== conversation.sender
            return <div key={`${message.source}:${message.message_id}`} className={isHtmlEmail ? "w-full overflow-hidden bg-transparent text-sm" : `max-w-[85%] overflow-hidden rounded-2xl px-4 py-3 text-sm shadow-sm md:max-w-[78%] ${outgoing ? "ml-auto rounded-tr-sm bg-primary text-primary-foreground" : "rounded-tl-sm bg-card"}`}>{isHtmlEmail ? <><div className="flex items-center justify-end bg-background px-1 pb-2"><Button variant="outline" size="sm" onClick={() => setExpandedHtml({ body: message.body, title: conversation.sender })}><Expand /> Expand</Button></div><iframe className="min-h-[360px] w-full border-0 bg-white" sandbox="" srcDoc={message.body} title={`Email ${message.message_id}`} /></> : <MessageBody message={message} />}<p className={`mt-1 px-1 text-[11px] ${outgoing && !isHtmlEmail ? "text-primary-foreground/70" : "text-muted-foreground"}`}>{new Date(message.received_at * 1000).toLocaleString()}</p></div>
          })}
          {conversation.drafts.map((item) => <div key={item.id} className="ml-auto max-w-[85%] rounded-2xl rounded-tr-sm bg-primary px-4 py-3 text-sm text-primary-foreground shadow-sm md:max-w-[78%]"><p>{item.body}</p><div className="mt-2 flex items-center justify-between gap-3"><span className="text-[11px] opacity-75">{item.status}</span>{item.status === "proposed" && <Button size="sm" variant="secondary" disabled={!canReply} title={canReply ? undefined : "This account is read-only"} onClick={() => approve(item.id)}>Approve & send</Button>}</div></div>)}
        </div></ScrollArea>
        <footer className="border-t bg-card p-3 md:p-4"><div className="flex gap-2"><Input value={draft} disabled={!canReply} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void submitDraft() }} placeholder={canReply ? "Write a draft reply…" : "Replies are unavailable for this read-only account"} /><Button disabled={!canReply} onClick={() => void submitDraft()} size="icon" title="Create draft"><Send /></Button><Button disabled={!canReply} variant="outline" onClick={() => void askAi()} title="Ask AI for variants"><Sparkles /></Button></div><p className="mt-2 hidden text-xs text-muted-foreground md:block">{canReply ? "Create a draft, then explicitly approve it before delivery." : "This account is read-only; delivery is not configured."}</p></footer>
      </> : <div className="hidden flex-1 place-items-center text-center md:grid"><div><Button className="mb-4" variant="outline" size="sm" onClick={() => setChatsOpen((open) => !open)}>{chatsOpen ? <PanelRightClose /> : <PanelRightOpen />}{chatsOpen ? "Hide chats" : "Show chats"}</Button><div className="mx-auto grid size-12 place-items-center rounded-full bg-muted"><MessageCircle /></div><h2 className="mt-3 font-semibold">Choose a chat</h2><p className="mt-1 text-sm text-muted-foreground">Accounts, channels, and conversations stay separate.</p></div></div>}
      {expandedHtml && <div className="fixed inset-0 z-50 flex flex-col bg-background"><header className="flex items-center gap-3 border-b px-5 py-3"><h2 className="truncate font-semibold">{expandedHtml.title}</h2><Button className="ml-auto" variant="outline" size="sm" onClick={() => setExpandedHtml(null)}><X /> Close</Button></header><iframe className="min-h-0 flex-1 border-0 bg-white" sandbox="" srcDoc={expandedHtml.body} title="Expanded email" /></div>}
    </section>
  </main>
}

export default App
