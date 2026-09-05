import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { ArrowLeft, Check, ChevronDown, Expand, Image as ImageIcon, Inbox, LogOut, Mail, Menu, MessageCircle, MessagesSquare, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Phone, Plus, Send, Sparkles, Video, X } from "lucide-react"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"

type Account = { id: string; provider: string; display_name: string; capabilities: string[]; last_synced_at?: number }
type Chat = { id: string; source: string; sender: string; identity_id?: string; preview?: string; unread_count: number; last_at?: number; display_name?: string; account_last_at?: number }
type Conversation = { id: string; source: string; sender: string; identity_id?: string; display_name?: string; account_ref?: string; messages: Message[]; drafts: Draft[] }
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
const channelIcon = (source: string) => {
  const provider = providerForSource(source)
  if (provider === "gmail") return <Mail className="size-4" />
  if (provider === "telegram") return <Send className="size-4" />
  if (provider === "vk") return <MessagesSquare className="size-4" />
  if (provider === "whatsapp") return <Phone className="size-4" />
  return <MessageCircle className="size-4" />
}
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
const titleOf = (item: { identity_id?: string; display_name?: string; sender: string }) => item.display_name?.trim() || item.identity_id || prettySender(item.sender)
const previewText = (raw: string | undefined) => {
  const text = raw || "Нет сообщений"
  const media = mediaLabel(text)
  if (media) return `📎 ${media}`
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
const humanSize = (bytes: number) => {
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`
}
const initials = (value: string) => {
  if (/^\+?\d[\d\s()-]*$/.test(value)) {
    const digits = value.replace(/\D/g, "")
    if (digits.length >= 2) return digits.slice(-2)
    if (digits.length === 1) return digits
    return value[0]?.toUpperCase() ?? ""
  }
  const parts = value.split(/[.@\s_-]/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase() ?? "")
  return parts.join("") || value[0]?.toUpperCase() || ""
}

// Health dot for an account row. <5 min = green (alive), 5-60 min = amber
// (sluggish), >60 min or read-only = rose (silent or read-only).
const accountHealth = (account: Account, lastSyncedAt?: number) => {
  if (!account.capabilities.includes("read")) return "rose"
  if (!lastSyncedAt) return "rose"
  const minutes = (Date.now() / 1000 - lastSyncedAt) / 60
  if (minutes <= 5) return "emerald"
  if (minutes <= 60) return "amber"
  return "rose"
}
const HEALTH_CLASS: Record<string, string> = {
  emerald: "bg-emerald-500", amber: "bg-amber-500", rose: "bg-rose-500",
}
const HEALTH_TITLE: Record<string, string> = {
  emerald: "Аккаунт активен", amber: "Аккаунт молчит >5 мин", rose: "Аккаунт недоступен",
}

// Tiny Russian-aware query normalisation for the chat search box.
// Strips the most common Russian inflectional suffixes so "договор" finds
// "договору", "договором", "договора". ASCII queries pass through unchanged.
const RUSSIAN_SUFFIXES = ["ами", "ями", "ах", "ях", "ов", "ев", "ой", "ый", "ий", "ая", "ое", "ее", "ую", "юю", "ам", "ям", "а", "я", "у", "ю", "е", "и", "о", "ы", "ть"]
const stripRussianSuffix = (token: string) => {
  if (token.length <= 4 || !/[а-яё]/i.test(token)) return token
  for (const suffix of RUSSIAN_SUFFIXES) {
    if (token.length - suffix.length >= 3 && token.toLowerCase().endsWith(suffix)) return token.slice(0, -suffix.length)
  }
  return token
}
const searchStems = (query: string) =>
  query
    .toLowerCase()
    .split(/\s+/)
    .map((token) => token.trim())
    .filter((token) => token.length >= 2)
    .flatMap((token) => {
      const stem = stripRussianSuffix(token)
      return stem === token ? [token] : [token, stem]
    })

// Telegram-style timestamps: HH:MM in bubbles, "вчера"/"12 сент" in the chat list,
// «Сегодня»/«Вчера»/«12 августа» day separators in the feed.
const timeHM = (unix: number) => new Date(unix * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
const dayDiff = (unix: number) => {
  const date = new Date(unix * 1000), now = new Date()
  const dayStart = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  return Math.round((dayStart(now) - dayStart(date)) / 86400000)
}
const listTime = (unix?: number) => {
  if (!unix) return ""
  const days = dayDiff(unix)
  if (days <= 0) return timeHM(unix)
  if (days === 1) return "вчера"
  if (days < 7) return new Date(unix * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" })
  return new Date(unix * 1000).toLocaleDateString("ru-RU")
}
const dayLabel = (unix: number) => {
  const days = dayDiff(unix)
  if (days <= 0) return "Сегодня"
  if (days === 1) return "Вчера"
  const date = new Date(unix * 1000), now = new Date()
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "long", ...(date.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) })
}
const sameDay = (a: number, b: number) => {
  const x = new Date(a * 1000), y = new Date(b * 1000)
  return x.getFullYear() === y.getFullYear() && x.getMonth() === y.getMonth() && x.getDate() === y.getDate()
}

// Deterministic avatar colors so contacts are distinguishable at a glance.
const AVATAR_COLORS = ["bg-rose-500/80", "bg-orange-500/80", "bg-amber-600/80", "bg-lime-600/80", "bg-emerald-600/80", "bg-teal-500/80", "bg-sky-600/80", "bg-indigo-500/80", "bg-fuchsia-500/80"]
const avatarColor = (key: string) => AVATAR_COLORS[[...key].reduce((acc, ch) => (acc * 31 + ch.charCodeAt(0)) % 997, 7) % AVATAR_COLORS.length]

const MEDIA_PLACEHOLDER = /^\[\s*(?:WhatsApp|Telegram)?\s*(image|video|audio|voice|document|sticker|фото|видео|аудио|голосовое|файл)\s*\]$/i
const IMAGE_URL = /^https?:\/\/\S+\.(?:jpe?g|png|webp|gif)(?:\?\S*)?$/i
const MEDIA_LABELS: Record<string, string> = {
  image: "Фото", video: "Видео", audio: "Аудио", voice: "Голосовое сообщение",
  document: "Документ", sticker: "Стикер", фото: "Фото", видео: "Видео",
  аудио: "Аудио", голосовое: "Голосовое сообщение", файл: "Файл",
}
const mediaLabel = (body: string) => {
  const match = body.trim().match(MEDIA_PLACEHOLDER)
  return match ? MEDIA_LABELS[match[1].toLowerCase()] || body.trim() : ""
}

const MessageBody = ({ message, onAttachmentClick }: { message: Message; onAttachmentClick: (m: Message) => void }) => {
  if (message.attachment_url) return <img src={message.attachment_url} alt="attachment" loading="lazy" className="max-h-80 rounded-xl bg-muted" />
  const placeholder = message.body.trim().match(MEDIA_PLACEHOLDER)
  if (placeholder) {
    const kind = placeholder[1].toLowerCase()
    const Icon = /video|видео/.test(kind) ? Video : ImageIcon
    return (
      <button type="button" onClick={() => onAttachmentClick(message)}
        className="flex items-center gap-2 rounded-xl bg-muted px-3 py-2 text-left transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
        <Icon className="size-4" />
        <span className="underline-offset-2 group-hover:underline">{mediaLabel(message.body)}</span>
        <span className="ml-1 text-[11px] text-muted-foreground">↗</span>
      </button>
    )
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
  const [attachmentPreview, setAttachmentPreview] = useState<{ message: Message; meta: { kind: string | null; available: boolean; reason?: string; download_url?: string; filename?: string; content_type?: string; size?: number } | null; loading: boolean } | null>(null)
  const [hiddenAccounts, setHiddenAccounts] = useState<string[]>(() => JSON.parse(localStorage.getItem("userio-hidden-accounts") || "[]"))
  const [toast, setToast] = useState("")
  const [newChatOpen, setNewChatOpen] = useState(false)
  const [newChatPhone, setNewChatPhone] = useState("")
  const toastTimer = useRef<number | undefined>(undefined)
  const notify = (message: string) => {
    setToast(message)
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(""), 2800)
  }

  // Escape closes the mobile accounts drawer.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setDrawerOpen(false) }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

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
  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" })
  }, [conversation?.id, conversation?.messages.length, conversation?.drafts.length])

  useEffect(() => { void api<{ accounts: Account[] }>("/v1/accounts").then((data) => setAccounts(data.accounts)) }, [])
  // The callback fetches external state before updating the view.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void refreshChats() }, [refreshChats])
  // The callback fetches external state before updating the view.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { if (selectedChat) void loadConversation(selectedChat) }, [loadConversation, selectedChat])

  const openChat = (id: string) => { setSelectedChat(id); setMobilePane("chat"); setDraft("") }

  const openAttachment = async (message: Message) => {
    if (!conversation) return
    setAttachmentPreview({ message, meta: null, loading: true })
    try {
      const data = await api<{ kind: string | null; available: boolean; reason?: string; download_url?: string; filename?: string; content_type?: string; size?: number }>(
        `/v1/conversations/${conversation.id}/media/${encodeURIComponent(message.message_id)}`,
      )
      setAttachmentPreview({ message, meta: data, loading: false })
    } catch (error) {
      notify(`Не удалось открыть вложение: ${(error as Error).message}`)
      setAttachmentPreview(null)
    }
  }

  const downloadAttachment = async (url: string, filename: string, contentType: string) => {
    try {
      const response = await fetch(url, { credentials: "include" })
      if (!response.ok) {
        notify(`Скачивание не удалось: HTTP ${response.status}`)
        return
      }
      const blob = await response.blob()
      const objectUrl = URL.createObjectURL(new Blob([blob], { type: contentType }))
      const anchor = document.createElement("a")
      anchor.href = objectUrl
      anchor.download = filename
      document.body.appendChild(anchor)
      anchor.click()
      document.body.removeChild(anchor)
      URL.revokeObjectURL(objectUrl)
    } catch (error) {
      notify(`Скачивание не удалось: ${(error as Error).message}`)
    }
  }

  const submitDraft = async () => {
    if (!conversation || !canReply || !draft.trim()) return
    try {
      await api(`/v1/conversations/${conversation.id}/drafts`, { method: "POST", body: JSON.stringify({ body: draft }) })
      setDraft("")
      await loadConversation(conversation.id)
      await refreshChats()
      notify("Черновик создан — проверьте и нажмите «Отправить»")
    } catch (error) {
      notify(`Не удалось создать черновик: ${(error as Error).message}`)
    }
  }
  const createChat = async () => {
    const phone = newChatPhone.trim()
    if (!/^\+\d{8,15}$/.test(phone)) {
      notify("Номер в формате +79XXXXXXXXX")
      return
    }
    try {
      const data = await api<{ conversation: { id: string } }>("/v1/conversations", { method: "POST", body: JSON.stringify({ source: "sms", sender: phone }) })
      setNewChatOpen(false)
      setNewChatPhone("")
      await refreshChats()
      openChat(data.conversation.id)
      notify("Чат создан — напишите черновик и отправьте")
    } catch (error) {
      notify(`Не удалось создать чат: ${(error as Error).message}`)
    }
  }
  const setSenderAccount = async (accountId: string) => {
    if (!conversation) return
    try {
      await api("/v1/conversations/set-account", { method: "POST", body: JSON.stringify({ conversation_id: conversation.id, account_id: accountId }) })
      await loadConversation(conversation.id)
      notify(accountId ? "Аккаунт отправки переключён" : "Отправка: авто")
    } catch (error) {
      notify(`Не удалось переключить аккаунт: ${(error as Error).message}`)
    }
  }
  const askAi = async () => {
    if (!conversation || !canReply) return
    try {
      await api(`/v1/conversations/${conversation.id}/ai-drafts`, { method: "POST", body: "{}" })
      await loadConversation(conversation.id)
      notify("ИИ предложил варианты ответа")
    } catch (error) {
      notify(`ИИ недоступен: ${(error as Error).message}`)
    }
  }
  const approve = async (id: string) => {
    if (!canReply) return
    try {
      await api(`/v1/drafts/${id}/approve`, { method: "POST", body: "{}" })
      if (conversation) await loadConversation(conversation.id)
      notify("Отправляется…")
    } catch (error) {
      notify(`Не удалось отправить: ${(error as Error).message}`)
    }
  }
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
  const visibleChats = chats
    .filter((chat) => !accounts.some((item) => hiddenAccounts.includes(item.id) && chat.source === sourceForAccount(item)))
    .filter((chat) => {
      if (!search.trim()) return true
      const haystack = `${titleOf(chat)} ${chat.preview ?? ""}`.toLowerCase()
      return searchStems(search).every((stem) => haystack.includes(stem))
    })
    .sort((a, b) => (b.last_at ?? 0) - (a.last_at ?? 0))
  // A draft only belongs to the chat whose messages it was composed for.
  // Clearing on selectedChat guarantees we never leak text into the next reply.
  useEffect(() => { setDraft("") }, [selectedChat])
  const unreadByPlatform = useMemo(() => {
    const map: Record<string, number> = {}
    for (const chat of chats) {
      const platform = providerForSource(chat.source)
      map[platform] = (map[platform] || 0) + chat.unread_count
    }
    return map
  }, [chats])

  const sidebar = <>
    <header className="flex items-center gap-3 p-4"><div className="grid size-9 place-items-center rounded-xl bg-primary text-primary-foreground"><Inbox className="size-5" /></div><div><p className="text-xs font-medium text-muted-foreground">ПЛАТФОРМЫ</p><h1 className="text-lg font-semibold">Universal UserIO</h1></div><Button className="ml-auto md:hidden" variant="ghost" size="icon" onClick={() => setDrawerOpen(false)} title="Закрыть"><X /></Button></header>
    <ScrollArea className="min-h-0 flex-1 px-2">
      <Button className="mb-2 w-full justify-start" variant={selectedChannel === "all" && selectedAccount === "all" ? "secondary" : "ghost"} onClick={() => { setSelectedAccount("all"); setSelectedChannel("all"); setDrawerOpen(false) }}><Inbox /> Все чаты</Button>
      {platforms.map((platform) => <details key={platform} className="mb-2 rounded-lg border bg-muted/20" open={activeChannel === platform}><summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-semibold [&::-webkit-details-marker]:hidden">{channelIcon(platform)} {displayChannel(platform)}{unreadByPlatform[platform] > 0 && <Badge className="rounded-full bg-primary px-1.5 text-[10px] text-primary-foreground">{unreadByPlatform[platform]}</Badge>} <ChevronDown className="ml-auto size-4" /></summary><div className="border-t p-1"><Button className="w-full justify-start" variant={selectedChannel === platform && selectedAccount === "all" ? "secondary" : "ghost"} onClick={() => { setSelectedChannel(platform); setSelectedAccount("all"); setDrawerOpen(false) }}>Все: {displayChannel(platform)}</Button>{accounts.filter((item) => providerForSource(item.provider) === platform).map((item) => { const visible = !hiddenAccounts.includes(item.id); const last = chats.filter((chat) => chat.source === sourceForAccount(item)).reduce((acc, chat) => Math.max(acc, chat.account_last_at ?? 0), 0); const health = accountHealth(item, last || item.last_synced_at); return <div key={item.id} className="mt-1 flex items-center gap-1"><Button className="min-w-0 flex-1 justify-start" variant={selectedAccount === item.id ? "secondary" : "ghost"} onClick={() => { if (visible) { setSelectedAccount(item.id); setSelectedChannel("all"); setDrawerOpen(false) } }}><span className={`mr-1.5 inline-block size-2 shrink-0 rounded-full ${HEALTH_CLASS[health]}`} title={HEALTH_TITLE[health]} aria-label={HEALTH_TITLE[health]} /><Avatar className="size-6"><AvatarFallback>{initials(item.display_name)}</AvatarFallback></Avatar><span className="truncate">{item.display_name}</span>{item.capabilities.length === 0 && <span className="ml-auto text-[10px] text-muted-foreground">только чтение</span>}</Button><input aria-label={`Показать ${item.display_name}`} type="checkbox" checked={visible} onChange={(event) => toggleAccount(item.id, event.target.checked)} /><Button aria-label={`Удалить ${item.display_name}`} title="Удалить аккаунт" variant="ghost" size="icon" onClick={() => void removeAccount(item.id)}><X className="size-3" /></Button></div> })}{platform === "gmail" && <Button className="mt-1 w-full justify-start" variant="outline" onClick={() => window.location.assign("/gmail/connect/new")}><Plus /> Добавить Gmail</Button>}{platform === "telegram" && <Button className="mt-1 w-full justify-start" variant="outline" onClick={() => window.location.assign("/telegram-qr/new")}><Plus /> Добавить Telegram</Button>}{platform === "vk" && <Button className="mt-1 w-full justify-start" variant="outline" onClick={() => window.location.assign("/vk/connect/new")}><Plus /> Добавить VK</Button>}{platform === "whatsapp" && <Button className="mt-1 w-full justify-start" variant="outline" onClick={() => window.location.assign("/whatsapp-qr/new")}><Plus /> Добавить WhatsApp</Button>}</div></details>)}
    </ScrollArea>
    <div className="flex items-center gap-2 border-t p-3 text-xs text-muted-foreground"><form method="post" action="/auth/logout"><Button type="submit" variant="ghost" size="sm"><LogOut /> Выйти</Button></form><span>ИИ предлагает только по запросу.</span></div>
  </>

  return <main className="flex h-svh min-h-0 overflow-hidden bg-background text-foreground">
    {/* Desktop sidebar / mobile drawer */}
    <aside className={`min-h-0 w-[280px] shrink-0 flex-col overflow-hidden border-r bg-card ${sidebarOpen ? "md:flex" : "md:hidden"} ${drawerOpen ? "absolute inset-y-0 left-0 z-40 flex shadow-2xl" : "hidden"}`}>{sidebar}</aside>
    {drawerOpen && <button aria-label="Close menu" className="absolute inset-0 z-30 bg-black/50 md:hidden" onClick={() => setDrawerOpen(false)} />}

    {/* Chat list: default pane on mobile */}
    <section className={`min-h-0 min-w-0 flex-1 flex-col overflow-hidden border-r bg-card md:w-[340px] md:flex-none ${chatsOpen ? "md:flex" : "md:hidden"} ${mobilePane === "chats" ? "flex" : "hidden"}`}>
      <header className="space-y-3 p-4"><div className="flex items-start gap-2"><Button variant="ghost" size="icon" className="md:hidden" onClick={() => setDrawerOpen(true)} title="Аккаунты"><Menu /></Button><Button variant="ghost" size="icon" className="hidden md:inline-flex" onClick={() => setSidebarOpen((open) => !open)} title={sidebarOpen ? "Скрыть платформы" : "Показать платформы"}>{sidebarOpen ? <PanelLeftClose /> : <PanelLeftOpen />}</Button><div><p className="text-xs font-medium text-muted-foreground">ЧАТЫ</p><p className="text-sm text-muted-foreground">{activeChannel ? displayChannel(activeChannel) : "Все чаты"}</p></div><Button className="ml-auto" variant="ghost" size="icon" onClick={() => setNewChatOpen(true)} title="Новый SMS-чат"><Plus /></Button></div><div className="relative"><Input value={search} onChange={(event) => setSearch(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") { setSearch(""); event.currentTarget.blur() } if (event.key === "Enter") event.preventDefault() }} placeholder="Поиск" />{search && <button aria-label="Очистить поиск" className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground hover:text-foreground" onClick={() => setSearch("")}><X className="size-4" /></button>}</div></header>
      <ScrollArea className="min-h-0 flex-1 px-2 pb-3">
        {visibleChats.map((chat) => {
          const unread = chat.unread_count > 0
          return <button key={chat.id} onClick={() => openChat(chat.id)} className={`mb-1 flex w-full gap-3 rounded-lg p-3 text-left transition-colors ${selectedChat === chat.id ? "bg-accent" : "hover:bg-muted"}`}>
          <Avatar><AvatarFallback className={`${avatarColor(chat.sender)} font-medium text-white`}>{initials(titleOf(chat))}</AvatarFallback></Avatar><span className="min-w-0 flex-1"><span className="flex items-center justify-between gap-2"><span className={`min-w-0 truncate text-sm ${unread ? "font-semibold" : "font-medium"}`}>{titleOf(chat)}</span><span className="flex shrink-0 items-center gap-1.5">{chat.last_at && <span className="text-[11px] text-muted-foreground">{listTime(chat.last_at)}</span>}{unread && <Badge className="rounded-full bg-primary px-1.5 text-[11px] text-primary-foreground">{chat.unread_count}</Badge>}</span></span><span className={`mt-1 block truncate text-xs ${unread ? "text-foreground/80" : "text-muted-foreground"}`}>{previewText(chat.preview)}</span></span>
        </button>
        })}
        {!visibleChats.length && <p className="p-5 text-center text-sm text-muted-foreground">
          {search.trim()
            ? <>Ничего не найдено по запросу <span className="font-medium text-foreground">«{search.trim()}»</span></>
            : selectedChannel !== "all"
              ? <>В канале {displayChannel(selectedChannel)} чатов нет</>
              : "Ничего не найдено"}
        </p>}
      </ScrollArea>
    </section>

    {/* Conversation: fullscreen pane on mobile */}
    <section className={`min-h-0 min-w-0 flex-1 flex-col bg-[linear-gradient(135deg,hsl(var(--background)),hsl(var(--muted)/.35))] md:flex ${mobilePane === "chat" ? "flex" : "hidden"}`}>
      {conversation ? <>
        <header className="flex items-center gap-3 border-b bg-card/90 px-3 py-3 backdrop-blur md:px-5"><Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMobilePane("chats")} title="Назад"><ArrowLeft /></Button><Button variant="ghost" size="icon" className="hidden md:inline-flex" onClick={() => setChatsOpen((open) => !open)} title={chatsOpen ? "Скрыть список" : "Показать список"}>{chatsOpen ? <PanelRightClose /> : <PanelRightOpen />}</Button><Avatar><AvatarFallback className={`${avatarColor(conversation.sender)} font-medium text-white`}>{initials(titleOf(conversation))}</AvatarFallback></Avatar><div className="min-w-0"><h2 className="truncate font-semibold">{titleOf(conversation)}</h2><p className="truncate text-xs text-muted-foreground">{displayChannel(conversation.source)} · {conversation.sender}</p>{(() => { const platformAccounts = accounts.filter((item) => providerForSource(item.provider) === providerForSource(conversation.source)); const senderAccount = platformAccounts.find((item) => item.id === conversation.account_ref); if (platformAccounts.length === 0) return null; return <p className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">отправка от:{platformAccounts.length > 1 ? <select className="max-w-[180px] rounded border bg-transparent px-1 py-0.5 text-[11px] text-foreground" value={conversation.account_ref || ""} onChange={(event) => void setSenderAccount(event.target.value)}><option value="">авто</option>{platformAccounts.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select> : <span className="truncate text-foreground/80">{senderAccount ? senderAccount.display_name : platformAccounts[0].display_name}</span>}</p> })()}</div><Button className="ml-auto" variant="outline" size="sm" onClick={markSeen} title="Отметить прочитанным"><Check /> <span className="hidden sm:inline">Прочитано</span></Button></header>
        <ScrollArea className="min-h-0 flex-1"><div className="mx-auto flex max-w-3xl flex-col gap-3 p-4 md:p-6">
          {conversation.messages.map((message, index) => {
            const isHtmlEmail = /^\s*<(?:!doctype|html|body|table|div|p|span|h[1-6]|a\b)/i.test(message.body)
            const outgoing = message.sender !== conversation.sender
            const previous = conversation.messages[index - 1]
            const showDay = !previous || !sameDay(previous.received_at, message.received_at)
            return <Fragment key={`${message.source}:${message.message_id}`}>
              {showDay && <div className="my-2 text-center"><span className="rounded-full bg-muted px-3 py-1 text-[11px] font-medium text-muted-foreground">{dayLabel(message.received_at)}</span></div>}
              <div className={isHtmlEmail ? "w-full overflow-hidden bg-transparent text-sm" : `max-w-[85%] overflow-hidden rounded-2xl px-4 py-3 text-sm shadow-sm md:max-w-[78%] ${outgoing ? "ml-auto rounded-tr-sm bg-primary text-primary-foreground" : "rounded-tl-sm bg-card"}`}>{isHtmlEmail ? <><div className="flex items-center justify-end bg-background px-1 pb-2"><Button variant="outline" size="sm" onClick={() => setExpandedHtml({ body: message.body, title: titleOf(conversation) })}><Expand /> Развернуть</Button></div><iframe className="min-h-[360px] w-full border-0 bg-white" sandbox="" srcDoc={message.body} title={`Email ${message.message_id}`} /></> : <MessageBody message={message} onAttachmentClick={openAttachment} />}<p className={`mt-1 px-1 text-[11px] ${outgoing && !isHtmlEmail ? "text-primary-foreground/70" : "text-muted-foreground"}`}>{timeHM(message.received_at)}</p></div>
            </Fragment>
          })}
          {conversation.drafts.map((item) => <div key={item.id} className="ml-auto max-w-[85%] rounded-2xl rounded-tr-sm bg-primary/90 px-4 py-3 text-sm text-primary-foreground shadow-sm md:max-w-[78%]"><p className="whitespace-pre-wrap">{item.body}</p><div className="mt-2 flex items-center justify-between gap-3"><span className="text-[11px] opacity-75">{item.status === "proposed" ? "Черновик" : item.status === "approved" ? "Отправлено" : item.status === "rejected" ? "Отклонено" : item.status}</span>{item.status === "proposed" && <Button size="sm" variant="secondary" disabled={!canReply} title={canReply ? undefined : "Этот аккаунт только для чтения"} onClick={() => approve(item.id)}>Отправить</Button>}</div></div>)}
          <div ref={bottomRef} />
        </div></ScrollArea>
        <footer className="border-t bg-card p-3 md:p-4"><div className="flex gap-2"><Input value={draft} disabled={!canReply} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void submitDraft() }} placeholder={canReply ? "Написать ответ…" : "Ответы недоступны: аккаунт только для чтения"} /><Button disabled={!canReply} onClick={() => void submitDraft()} size="icon" title="Создать черновик (Enter)"><Send /></Button><Button disabled={!canReply} variant="outline" onClick={() => void askAi()} title="ИИ-варианты ответа"><Sparkles /></Button></div><p className="mt-2 text-xs text-muted-foreground">{canReply ? "Сначала черновик, затем явная отправка." : "Отправка не настроена для этого аккаунта."}</p></footer>
      </> : <div className="hidden flex-1 place-items-center text-center md:grid"><div><Button className="mb-4" variant="outline" size="sm" onClick={() => setChatsOpen((open) => !open)}>{chatsOpen ? <PanelRightClose /> : <PanelRightOpen />}{chatsOpen ? "Скрыть список" : "Показать список"}</Button><div className="mx-auto grid size-12 place-items-center rounded-full bg-muted"><MessageCircle /></div><h2 className="mt-3 font-semibold">Выберите чат</h2><p className="mt-1 text-sm text-muted-foreground">Аккаунты, платформы и переписки остаются раздельными.</p></div></div>}
      {toast && <div className="fixed inset-x-0 bottom-20 z-50 mx-auto w-fit max-w-[90%] rounded-full bg-foreground px-4 py-2 text-center text-sm text-background shadow-lg md:bottom-8">{toast}</div>}
      {newChatOpen && <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onClick={() => setNewChatOpen(false)}><div className="w-full max-w-sm rounded-2xl border bg-card p-5 shadow-xl" onClick={(event) => event.stopPropagation()}><h2 className="font-semibold">Новый SMS-чат</h2><p className="mt-1 text-xs text-muted-foreground">Отправка пойдёт с подключённого телефона-агента после вашего approve.</p><Input className="mt-3" value={newChatPhone} onChange={(event) => setNewChatPhone(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void createChat() }} placeholder="+79XXXXXXXXX" autoFocus /><div className="mt-4 flex justify-end gap-2"><Button variant="outline" size="sm" onClick={() => setNewChatOpen(false)}>Отмена</Button><Button size="sm" onClick={() => void createChat()}>Создать</Button></div></div></div>}
      {expandedHtml && <div className="fixed inset-0 z-50 flex flex-col bg-background"><header className="flex items-center gap-3 border-b px-5 py-3"><h2 className="truncate font-semibold">{expandedHtml.title}</h2><Button className="ml-auto" variant="outline" size="sm" onClick={() => setExpandedHtml(null)}><X /> Закрыть</Button></header><iframe className="min-h-0 flex-1 border-0 bg-white" sandbox="" srcDoc={expandedHtml.body} title="Письмо" /></div>}
      {attachmentPreview && <div role="dialog" aria-label="Вложение" className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setAttachmentPreview(null)}><div className="w-full max-w-md rounded-2xl bg-background p-5 shadow-2xl" onClick={(event) => event.stopPropagation()}><header className="flex items-center gap-3"><div className="grid size-10 place-items-center rounded-xl bg-muted">{attachmentPreview.message.body.trim().match(MEDIA_PLACEHOLDER)?.[1]?.toLowerCase().match(/video|видео/) ? <Video className="size-5" /> : <ImageIcon className="size-5" />}</div><div className="min-w-0"><h3 className="truncate font-semibold">{attachmentPreview.meta?.filename || mediaLabel(attachmentPreview.message.body) || "Вложение"}</h3><p className="truncate text-xs text-muted-foreground">Сообщение {attachmentPreview.message.message_id}{attachmentPreview.meta?.size ? ` · ${humanSize(attachmentPreview.meta.size)}` : ""}</p></div><Button className="ml-auto" variant="ghost" size="icon" onClick={() => setAttachmentPreview(null)} title="Закрыть"><X /></Button></header><div className="mt-4 space-y-3 text-sm">{attachmentPreview.loading && <p className="text-muted-foreground">Запрашиваю файл у провайдера…</p>}{!attachmentPreview.loading && attachmentPreview.meta?.available === false && (<p className="rounded-lg bg-muted px-3 py-2 text-muted-foreground">{attachmentPreview.meta.reason || "Вложение недоступно для скачивания."}</p>)}{!attachmentPreview.loading && attachmentPreview.meta?.available && (attachmentPreview.meta.content_type?.startsWith("image/") ? <img src={attachmentPreview.meta.download_url} alt={attachmentPreview.meta.filename || "preview"} className="max-h-72 w-full rounded-lg bg-muted object-contain" /> : attachmentPreview.meta.content_type === "application/pdf" ? <iframe title="PDF preview" src={attachmentPreview.meta.download_url} className="h-72 w-full rounded-lg border bg-white" /> : <p className="rounded-lg bg-muted px-3 py-2 text-muted-foreground">{attachmentPreview.meta.content_type || "Файл"} · {humanSize(attachmentPreview.meta.size ?? 0)} — превью недоступно, скачайте, чтобы открыть.</p>)}</div><div className="mt-5 flex items-center justify-end gap-2"><Button variant="outline" size="sm" onClick={() => setAttachmentPreview(null)}>Закрыть</Button>{attachmentPreview.meta?.available && attachmentPreview.meta?.download_url && <Button size="sm" onClick={() => void downloadAttachment(attachmentPreview.meta!.download_url!, attachmentPreview.meta!.filename || "attachment", attachmentPreview.meta!.content_type ?? "application/octet-stream")}><ImageIcon /> Скачать</Button>}</div></div></div>}
    </section>
  </main>
}

export default App
