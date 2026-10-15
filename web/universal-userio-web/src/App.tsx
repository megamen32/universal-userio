import { messageAuthor } from './message-author'
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Popover } from "radix-ui"
import { ArrowLeft, ChartLine, Check, Expand, Image as ImageIcon, Inbox, LogOut, Mail, Menu, MessageCircle, MessagesSquare, PanelLeftClose, PanelLeftOpen, Paperclip, Phone, Plus, Send, SlidersHorizontal, Sparkles, Video, X } from "lucide-react"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { canEditMaxText, providerPeerLabel } from "./max-message"
import { startInboxPolling } from "./inbox-poll"
import { mergePhoneHistory } from "./phone-history"
import { CallAudio, type CallRecording } from './call-audio'
import { ContextSettingsDialog } from "@/components/context-settings-dialog"

type Account = { id: string; provider: string; source?: string; display_name: string; capabilities: string[]; last_message_at?: number; enabled?: boolean }
type AnalysisPolicy = { conversation_id: string; conversation_kind: string; allowed: boolean; action: string }
type Chat = { id: string; source: string; sender: string; identity_id?: string; preview?: string; unread_count: number; last_at?: number; display_name?: string; account_last_at?: number; account_ref?: string; match_message_id?: string; match_body?: string; match_type?: "exact" | "fuzzy"; match_field?: "contact" | "message"; analysis_policy?: AnalysisPolicy }
type Conversation = { id: string; source: string; sender: string; identity_id?: string; display_name?: string; account_ref?: string; conversation_kind?: string; messages: Message[]; drafts: Draft[]; phone_history?: { oldest_id: string; has_more: boolean } }
type Message = { source: string; message_id: string; sender: string; sender_name?: string; body: string; direction?: "incoming" | "outgoing" | "system"; received_at: number; edited_at?: number; attachments?: unknown[]; seen_at?: number; provider_read?: boolean | number | null; attachment_url?: string; outbox_receipt?: string; call_audio?: CallRecording }
type Draft = { id: string; body: string; status: string; outbox_receipt?: string; created_at?: number; context_status?: "unknown" | "current" | "stale"; reply_to?: { source: string; message_id: string; body: string; received_at: number } | null }
type UserCapabilities = { read: boolean; subscribe: boolean; download: boolean; send: boolean }
type SecretaryTriage = { event_seq: number; status: string; request_id: string; triage?: { decision?: string; importance?: number; urgency?: string; confidence?: number; reason_ru?: string; action_summary?: string; action_required?: boolean } }
type SecretaryDeep = { job_id: string; event_seq: number; status: "accepted" | "running" | "completed" | "failed"; session_id?: string; session_url?: string; result?: string; error?: string }
type Secretary = { conversation: { id: string; event_seq?: number; message_id?: string; source: string; account_ref: string; peer_id: string }; triage: SecretaryTriage | null; summary: string; deep: SecretaryDeep | null; owner_messages_after_current?: {message_id: string; received_at: number}[] }
import { defineByokPresetPicker } from "./vendor/byok-ui"
import { defineByokRunsView, defineByokRunDetails, type ByokLedgerRecord, type ByokLedgerTotals } from "./vendor/byok-runs-ui"
import { beginContextSettingsLoad, completeContextSettingsLoad, confirmContextSettingsSave, contextSettingsPayload, contextSettingsValidationError, createContextSettingsSession, failContextSettingsLoad } from "./context-settings"
import { LatestRequest } from "./latest-request"
import { SecretaryRequests } from "./secretary-requests"

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { error?: string } | null
    throw new Error(payload?.error || "Request failed")
  }
  return response.json() as Promise<T>
}

const providerForSource = (source: string) => source === "email" || source.startsWith("email:") || source.startsWith("gmail") ? "gmail" : source.startsWith("matrix:") ? "matrix" : source.startsWith("chatgpt") ? "chatgpt" : source
const sourceForAccount = (account: Account) => {
  if (account.source) return account.source
  if (account.provider === "gmail") {
    const alias = account.id.match(/^gmail-(.+)$/i)?.[1]
    return alias ? `gmail:${alias}` : account.provider
  }
  if (account.provider === "chatgpt") {
    const slug = account.id.match(/^chatgpt:(.+)$/)?.[1]
    return slug ? `chatgpt:${slug}` : account.provider
  }
  return account.provider
}
const channelIcon = (source: string) => {
  const provider = providerForSource(source)
  if (provider === "gmail") return <Mail className="size-4" />
  if (provider === "telegram") return <Send className="size-4" />
  if (provider === "vk") return <MessagesSquare className="size-4" />
  if (provider === "whatsapp") return <Phone className="size-4" />
  return <MessageCircle className="size-4" />
}
const displayChannel = (source: string) => source === 'yandex' ? 'Яндекс · чаты' : source === 'phone' ? 'Звонки' : providerForSource(source) === "gmail" ? "Email" : source[0].toUpperCase() + source.slice(1)
const chatKindLabel = (kind?: string) => (Object.entries({ group: "Группа", channel: "Канал", direct: "Личный чат", telegram_bot: "Бот", unknown: "Тип не определён" }).find(([key]) => key === kind)?.[1] || "Тип не определён")

// A message is "mine" (right bubble) when it was authored by the sending
// account chosen for the conversation ("отправка от"; auto = first platform
// account). Switching the account therefore re-renders the whole chat from
// that person's perspective — e.g. a Telegram self-chat between two accounts.
const messageIsOutgoing = (conversation: Conversation, message: Message, allAccounts: Account[]) => {
  if (message.direction) return message.direction === "outgoing"
  const provider = providerForSource(conversation.source)
  const platformAccounts = allAccounts.filter((item) => providerForSource(item.provider) === provider)
  if (!platformAccounts.length) return message.sender !== conversation.sender
  const me = platformAccounts.find((item) => item.id === conversation.account_ref) || platformAccounts[0]
  if (message.sender === "self") return true
  const localId = me.id.slice(provider.length + 1)
  if (message.sender === me.id || message.sender === localId || message.sender === me.display_name) return true
  if (me.display_name && message.sender.trim().toLowerCase() === me.display_name.trim().toLowerCase()) return true
  return false
}

// Human-readable chat titles instead of raw provider JIDs.
const prettySender = (sender: string, source?: string) => {
  if (sender.endsWith("@s.whatsapp.net")) return "+" + sender.replace("@s.whatsapp.net", "")
  if (sender.endsWith("@lid")) {
    const id = sender.replace("@lid", "")
    return `wa:${id.length > 8 ? id.slice(0, 4) + "…" + id.slice(-4) : id}`
  }
  if (sender.endsWith("@g.us")) return "group " + sender.replace("@g.us", "").slice(-6)
  return providerPeerLabel(sender, source)
}
const titleOf = (item: { identity_id?: string; display_name?: string; sender: string; source?: string }) => item.display_name?.trim() || item.identity_id || prettySender(item.sender, item.source)
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

// Message freshness is not a provider-health check. Only a missing read
// capability means the account itself is unavailable to this user.
const accountActivity = (account: Account) => {
  if (!account.capabilities.includes("read")) return "blocked"
  if (!account.last_message_at) return "empty"
  return (Date.now() / 1000 - account.last_message_at) / 60 <= 60 ? "fresh" : "stale"
}
const HEALTH_CLASS: Record<string, string> = {
  fresh: "bg-emerald-500", stale: "bg-slate-400", empty: "bg-slate-300", blocked: "bg-rose-500",
}
const HEALTH_TITLE: Record<string, string> = {
  fresh: "Свежие сообщения", stale: "Нет новых сообщений", empty: "Нет сообщений", blocked: "Чтение недоступно",
}

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
  const attachment = message.attachments?.find(item => !!item && typeof item === "object") as {kind?: string; src?: string; filename?: string} | undefined
  if (attachment?.kind === "sticker") return <div className="space-y-1">
    {attachment.src && <img src={attachment.src} alt="Стикер MAX" loading="lazy" referrerPolicy="no-referrer" className="max-h-40 max-w-40 object-contain" />}
    <span className="block text-xs opacity-70">Стикер</span>
    {message.body.trim() && <p className="whitespace-pre-wrap">{message.body}</p>}
  </div>
  if (message.source === 'vk' && attachment) return <div className="space-y-2">
    {message.body.trim() && message.body!=="[Вложение VK]" && <p className="whitespace-pre-wrap">{message.body}</p>}
    <button type="button" title="Открыть вложение VK" onClick={()=>onAttachmentClick(message)} className="text-left underline">📎 {attachment.filename || "Вложение VK"}</button>
  </div>
  if (!message.body.trim() && attachment) return <button type="button" onClick={() => onAttachmentClick(message)} className="text-left underline">
    📎 {MEDIA_LABELS[attachment.kind || ""] || attachment.filename || "Вложение"}
  </button>
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
  const [secretary, setSecretary] = useState<Secretary | null>(null)
  const [secretaryBusy, setSecretaryBusy] = useState(false)
  const [secretaryError, setSecretaryError] = useState("")
  const [secretaryLoading, setSecretaryLoading] = useState(false)
  const [aiPaused, setAiPaused] = useState<boolean | null>(null)
  const [aiPauseBusy, setAiPauseBusy] = useState(false)
  const [aiPauseError, setAiPauseError] = useState("")
  const [analysisPolicy, setAnalysisPolicy] = useState<AnalysisPolicy | null>(null)
  const [analysisPolicyBusy, setAnalysisPolicyBusy] = useState(false)
  const [analysisPolicyError, setAnalysisPolicyError] = useState("")
  const analysisPolicyRequest = useRef(new LatestRequest())
  const currentSecretaryDeep = secretary?.deep?.event_seq === secretary?.conversation.event_seq ? secretary?.deep : null
  const [selectedAccount, setSelectedAccount] = useState<string>("all")
  const [selectedChannel, setSelectedChannel] = useState<string>("all")
  const [selectedChat, setSelectedChat] = useState<string>("")
  const [draft, setDraft] = useState("")
  const [selectedUpload,setSelectedUpload] = useState<{upload_id:string;filename:string}|null>(null)
  const [uploadBusy,setUploadBusy] = useState(false)
  const filePicker=useRef<HTMLInputElement>(null)
  useEffect(()=>{setSelectedUpload(null)},[selectedChat])
  const [search, setSearch] = useState("")
  const [searchResults, setSearchResults] = useState<Chat[]>([])
  const [searchError, setSearchError] = useState("")
  const [searchMore, setSearchMore] = useState({ exact: false, fuzzy: false })
  const searchGeneration = useRef(0)
  const [searching, setSearching] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [mobilePane, setMobilePane] = useState<"chats" | "chat">("chats")
  const [expandedHtml, setExpandedHtml] = useState<{ body: string; title: string } | null>(null)
  const [attachmentPreview, setAttachmentPreview] = useState<{ message: Message; meta: { kind: string | null; available: boolean; reason?: string; download_url?: string; filename?: string; content_type?: string; size?: number } | null; loading: boolean } | null>(null)
  const [hiddenAccounts, setHiddenAccounts] = useState<string[]>(() => JSON.parse(localStorage.getItem("userio-hidden-accounts") || "[]"))
  const [hiddenPlatforms, setHiddenPlatforms] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem("userio-hidden-platforms") || "[]") } catch { return [] } })
  const [toast, setToast] = useState("")
  const [newChatOpen, setNewChatOpen] = useState(false)
  const [newChatSource, setNewChatSource] = useState("sms")
  const [newChatPhone, setNewChatPhone] = useState("")
  const [sendBusy, setSendBusy] = useState(false)
  const [phoneHistoryBusy, setPhoneHistoryBusy] = useState(false)
  const phoneHistoryWindow = useRef('')
  const phoneFollowLatest = useRef(true)
  const transcriptRef = useRef<HTMLDivElement>(null)
  const phoneHistoryScroll = useRef<{id:string; messageId:string; top:number; restore:boolean} | null>(null)
  const sendInFlight = useRef(false)
  const sendIntent = useRef<{key: string; id: string} | null>(null)
  const [editingMessage, setEditingMessage] = useState<Message | null>(null)
  const [messageEditBody, setMessageEditBody] = useState("")
  const [messageEditBusy, setMessageEditBusy] = useState(false)
  const [approvingDraft, setApprovingDraft] = useState("")
  const approvalInFlight = useRef(false)
  const [editDraftId, setEditDraftId] = useState("")
  const [editDraftBody, setEditDraftBody] = useState("")
  const [byokOpen, setByokOpen] = useState(false)
  const [byokForm, setByokForm] = useState({ endpoint: "", model: "", token: "" })
  const [byokMine, setByokMine] = useState(false)
  const [contextOpen, setContextOpen] = useState(false)
  const [contextSession, setContextSession] = useState(createContextSettingsSession)
  const [contextSaving, setContextSaving] = useState(false)
  const contextLoadRequest = useRef(0)
  // Wait for the real per-user capability response before loading the large
  // conversation list. This keeps account metadata from being starved behind
  // duplicate initial list queries in React StrictMode.
  const [userCapabilities, setUserCapabilities] = useState<UserCapabilities>({ read: false, subscribe: false, download: false, send: false })
  const [autoRead, setAutoRead] = useState(false)
  const readAttempts = useRef(new Set<string>())
  const [runsOpen, setRunsOpen] = useState(false)
  const [aiRuns, setAiRuns] = useState<ByokLedgerRecord[]>([])
  const [aiTotals, setAiTotals] = useState<ByokLedgerTotals | null>(null)
  const [selectedRun, setSelectedRun] = useState<ByokLedgerRecord | null>(null)
  const runsHost = useRef<HTMLDivElement>(null)
  const runDetailsHost = useRef<HTMLDivElement>(null)
  const [byokPresets, setByokPresets] = useState<{ id: string; label: string; description: string; apiFormat: string; baseUrl: string; modelId: string; priceLabel: string }[]>([])
  const byokPickerRef = useRef<HTMLDivElement>(null)
  const toastTimer = useRef<number | undefined>(undefined)
  const chatsRequest = useRef(new LatestRequest())
  const conversationRequest = useRef(new LatestRequest())
  const activeConversation = useRef("")
  const secretaryRequest = useRef(new SecretaryRequests())
  const notify = (message: string) => {
    setToast(message)
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(""), 2800)
  }

  useEffect(() => {
    void api<{paused:boolean}>("/v1/preferences/ai").then(value => setAiPaused(value.paused))
      .catch(error => setAiPauseError((error as Error).message))
  }, [])
  const toggleAiPause = async () => {
    if (aiPaused === null || aiPauseBusy) return
    setAiPauseBusy(true)
    setAiPauseError("")
    try {
      const value = await api<{paused:boolean}>("/v1/preferences/ai", {
        method: "POST", body: JSON.stringify({paused: !aiPaused}),
      })
      setAiPaused(value.paused)
      notify(value.paused ? "Автоматический ИИ приостановлен. Сообщения продолжают поступать." : "Автоматический ИИ возобновлён. Уже прочитанное будет пропущено.")
    } catch (error) { setAiPauseError((error as Error).message) }
    finally { setAiPauseBusy(false) }
  }

  // Escape closes overlays first; otherwise it returns from an open conversation to the wide list.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // A popup consumes Escape to dismiss itself. Do not also close the chat
      // and erase the user's composer text when Radix handled that key.
      if (event.defaultPrevented) return
      if (event.key !== "Escape") return
      if (drawerOpen) { setDrawerOpen(false); return }
      if (byokOpen || contextOpen || newChatOpen || runsOpen || expandedHtml || attachmentPreview) return
      const target = event.target as HTMLElement | null
      if (target?.matches("input, textarea, select, [contenteditable='true']")) return
      if (selectedChat) { setSelectedChat(""); setConversation(null); setMobilePane("chats"); setDraft("") }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [attachmentPreview, byokOpen, contextOpen, drawerOpen, expandedHtml, newChatOpen, runsOpen, selectedChat])

  const account = accounts.find((item) => item.id === selectedAccount)
  const conversationAccount = conversation && (accounts.find(item => item.id === conversation.account_ref) || accounts.find((item) => item.id === (item.provider === "gmail" && conversation.source.startsWith("gmail:") ? conversation.source.replace(/^gmail:/, "gmail-") : conversation.source)))
  const sendEnabled = userCapabilities.send
  const canReply = sendEnabled && (!conversationAccount || conversationAccount.capabilities.includes("reply"))
  const canDownload = userCapabilities.download
  const canVkRich = !!conversation && conversation.source === "vk" && /^vk:\d+$/.test(conversation.account_ref || "") && canReply
  const activeChannel = selectedChannel !== "all" ? providerForSource(selectedChannel) : account ? providerForSource(account.provider) : undefined
  const platforms = useMemo(() => Array.from(new Set(["gmail", "telegram", "vk", "whatsapp", ...accounts.map((item) => providerForSource(item.provider)), ...chats.map((item) => providerForSource(item.source))])).sort(), [accounts, chats])

  const refreshChats = useCallback(async () => {
    const request = chatsRequest.current.begin()
    const sourceFilter = account ? sourceForAccount(account) : activeChannel
    const params = new URLSearchParams()
    if (sourceFilter) params.set("source", sourceFilter)
    if (account) params.set("exact_source", "1")
    if (account && ["telegram", "sms", "phone", "vk"].includes(account.provider)) params.set("account_ref", account.id)
    const suffix = params.size ? `?${params.toString()}` : ""
    const data = await api<{ conversations: Chat[] }>(`/v1/conversations${suffix}`)
    if (!chatsRequest.current.isCurrent(request)) return
    setChats(data.conversations)
    setSelectedChat((current) => current && data.conversations.some((item) => item.id === current) ? current : "")
  }, [account, activeChannel])

  useEffect(() => {
    const text = search.trim()
    if (!text || !userCapabilities.read) { setSearchResults([]); setSearching(false); return }
    const sourceFilter = account ? sourceForAccount(account) : activeChannel
    const generation = ++searchGeneration.current
    setSearchResults([])
    setSearchError("")
    setSearchMore({ exact: false, fuzzy: false })
    setSearching(true)
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ q: text, limit: "51" })
      if (sourceFilter) params.set("source", sourceFilter)
      if (account && ["telegram", "sms", "phone", "vk"].includes(account.provider)) params.set("account_ref", account.id)
      // Publish the first pass immediately; typo matching must not delay it.
      void (async () => {
        for (const kind of ["exact", "fuzzy"] as const) {
          const data = await api<{ results: Chat[] }>(`/v1/search?${params.toString()}&match_type=${kind}`)
          if (searchGeneration.current !== generation) return
          setSearchResults(previous => kind === "exact" ? data.results.slice(0, 50) : [...previous, ...data.results.slice(0, 50)])
          setSearchMore(previous => ({ ...previous, [kind]: data.results.length > 50 }))
        }
      })().catch((error) => { if (searchGeneration.current === generation) setSearchError(`Поиск не удался: ${(error as Error).message}`) })
        .finally(() => { if (searchGeneration.current === generation) setSearching(false) })
    }, 220)
    return () => { window.clearTimeout(timer); ++searchGeneration.current }
  }, [search, account, activeChannel, userCapabilities.read])

  const loadMoreSearch = async (kind: "exact" | "fuzzy") => {
    const generation = searchGeneration.current
    const params = new URLSearchParams({ q: search.trim(), limit: "51", match_type: kind,
      offset: String(searchResults.filter(chat => chat.match_type === kind).length) })
    const sourceFilter = account ? sourceForAccount(account) : activeChannel
    if (sourceFilter) params.set("source", sourceFilter)
    if (account && ["telegram", "sms", "phone", "vk"].includes(account.provider)) params.set("account_ref", account.id)
    setSearching(true)
    try {
      const data = await api<{ results: Chat[] }>(`/v1/search?${params.toString()}`)
      if (searchGeneration.current !== generation) return
      setSearchResults(previous => [...previous, ...data.results.slice(0, 50)])
      setSearchMore(previous => ({ ...previous, [kind]: data.results.length > 50 }))
    } catch (error) { if (searchGeneration.current === generation) setSearchError(`Поиск не удался: ${(error as Error).message}`) }
    finally { if (searchGeneration.current === generation) setSearching(false) }
  }

  const inboxPollingBusy = useRef(false)
  const loadConversation = useCallback(async (id: string) => {
    if (activeConversation.current !== id) return
    const request = conversationRequest.current.begin()
    const result = await api<Conversation>(`/v1/conversations/${encodeURIComponent(id)}`)
    if (conversationRequest.current.isCurrent(request) && activeConversation.current === id) {
      setConversation(previous => previous?.id === result.id && result.source === 'phone' && (phoneHistoryWindow.current === result.id || previous.messages.length > result.messages.length)
        ? { ...result, messages: mergePhoneHistory(previous.messages, result.messages), phone_history: previous.phone_history }
        : result)
      // A historical call link resolves to its person's retained canonical
      // dialogue. Keep the selected view and subsequent requests on that ID.
      if (result.id !== id) setSelectedChat(result.id)
    }
  }, [])
  const loadSecretary = useCallback(async (id: string) => {
    const gate = secretaryRequest.current
    const request = gate.beginRead()
    if (request === null) return
    setSecretaryLoading(true)
    try {
      const result = await api<Secretary>(`/v1/conversations/${encodeURIComponent(id)}/secretary`)
      if (gate.isCurrentRead(request)) {
        setSecretary(result)
        setSecretaryError("")
      }
    } catch (error) {
      if (gate.isCurrentRead(request)) setSecretaryError(`Не удалось загрузить разбор: ${(error as Error).message}`)
    } finally {
      if (gate.isCurrentRead(request)) setSecretaryLoading(false)
    }
  }, [])
  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const pending = phoneHistoryScroll.current
    const viewport = transcriptRef.current?.querySelector<HTMLElement>('[data-slot="scroll-area-viewport"]')
    if (pending && pending.id === conversation?.id) {
      if (pending.restore && viewport) {
        const anchor = [...viewport.querySelectorAll<HTMLElement>('[data-phone-message]')].find(element => element.dataset.phoneMessage === pending.messageId)
        if (anchor) viewport.scrollTop += anchor.getBoundingClientRect().top - pending.top
        phoneHistoryScroll.current = null
      }
      return
    }
    if (conversation?.source === 'phone' && phoneHistoryWindow.current === conversation.id && !phoneFollowLatest.current) return
    bottomRef.current?.scrollIntoView({ block: "end" })
  }, [conversation?.id, conversation?.messages.length, conversation?.drafts.length])

  const reloadAccounts = useCallback(async () => {
    const data = await api<{ accounts: Account[] }>("/v1/accounts")
    setAccounts(data.accounts.filter(item => item.enabled !== false))
  }, [])
  const loadCapabilities = useCallback(async () => {
    const reading = await api<{auto_read:boolean}>('/v1/preferences/reading')
    setAutoRead(reading.auto_read)
    const data = await api<{ capabilities: UserCapabilities }>("/v1/preferences/capabilities")
    setUserCapabilities(data.capabilities)
    return data.capabilities
  }, [])
  const setCapability = async (name: keyof UserCapabilities, enabled: boolean) => {
    try {
      const data = await api<{ capabilities: UserCapabilities }>("/v1/preferences/capabilities", {
        method: "POST", body: JSON.stringify({ capabilities: { [name]: enabled } }),
      })
      setUserCapabilities(data.capabilities)
      await reloadAccounts()
      if (name === "read" && !enabled) { setChats([]); setConversation(null); setSelectedChat("") }
      if (name === "read" && enabled) await refreshChats()
      if (conversation?.id && data.capabilities.read) await loadConversation(conversation.id)
      const labels: Record<keyof UserCapabilities, string> = { read: "Чтение", subscribe: "Push-подписки", download: "Скачивание", send: "Отправка" }
      notify(`${labels[name]}: ${enabled ? "разрешено" : "выключено"}`)
    } catch (error) {
      notify(`Не удалось изменить capability: ${(error as Error).message}`)
    }
  }
  useEffect(() => { void reloadAccounts() }, [reloadAccounts])
  useEffect(() => { void loadCapabilities() }, [loadCapabilities])
  useEffect(() => { void loadByok() }, [])
  // The callback fetches external state before updating the view.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { if (userCapabilities.read) void refreshChats(); else { chatsRequest.current.begin(); setChats([]); setSelectedChat(""); setConversation(null) } }, [refreshChats, userCapabilities.read])
  useEffect(() => {
    activeConversation.current = userCapabilities.read ? selectedChat : ""
    readAttempts.current.clear()
    phoneHistoryWindow.current = ''
    phoneFollowLatest.current = true
    phoneHistoryScroll.current = null
    conversationRequest.current.begin()
    return () => { activeConversation.current = ""; conversationRequest.current.begin() }
  }, [selectedChat, userCapabilities.read])
  // The callback fetches external state before updating the view.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { if (selectedChat && userCapabilities.read) void loadConversation(selectedChat) }, [loadConversation, selectedChat, userCapabilities.read])
  useEffect(() => {
    if (!userCapabilities.read || sendBusy || editingMessage) return
    return startInboxPolling(async () => {
      await Promise.allSettled([refreshChats(), ...(selectedChat ? [loadConversation(selectedChat)] : [])])
    }, () => document.visibilityState === "visible", setTimeout, clearTimeout, inboxPollingBusy)
  }, [editingMessage, loadConversation, refreshChats, selectedChat, sendBusy, userCapabilities.read])
  useEffect(() => {
    const requestGate = secretaryRequest.current
    requestGate.reset()
    setSecretary(null)
    setSecretaryBusy(false)
    setSecretaryError("")
    setSecretaryLoading(false)
    if (selectedChat && userCapabilities.read) void loadSecretary(selectedChat)
    return () => { requestGate.reset() }
  }, [loadSecretary, selectedChat, userCapabilities.read])
  useEffect(() => {
    const gate = analysisPolicyRequest.current
    const request = gate.begin()
    setAnalysisPolicy(null)
    setAnalysisPolicyError("")
    setAnalysisPolicyBusy(false)
    if (selectedChat && userCapabilities.read) {
      void api<{ analysis_policy: AnalysisPolicy }>(`/v1/conversations/${encodeURIComponent(selectedChat)}/analysis-policy`)
        .then(result => { if (gate.isCurrent(request)) setAnalysisPolicy(result.analysis_policy) })
        .catch(error => { if (gate.isCurrent(request)) setAnalysisPolicyError((error as Error).message) })
    }
    return () => { gate.begin() }
  }, [selectedChat, userCapabilities.read])
  const setChatAnalysis = async (enabled: boolean) => {
    if (!conversation || analysisPolicyBusy) return
    const id = conversation.id
    const gate = analysisPolicyRequest.current
    const request = gate.begin()
    setAnalysisPolicyBusy(true)
    setAnalysisPolicyError("")
    try {
      const result = await api<{ analysis_policy: AnalysisPolicy }>(`/v1/conversations/${encodeURIComponent(id)}/analysis-policy`, {
        method: "POST", body: JSON.stringify({ enabled }),
      })
      setChats(items => items.map(item => item.id === id ? { ...item, analysis_policy: result.analysis_policy } : item))
      if (gate.isCurrent(request)) setAnalysisPolicy(result.analysis_policy)
      notify(enabled ? "Автоматический разбор новых сообщений включён" : "Автоматический разбор выключен")
    } catch (error) {
      if (gate.isCurrent(request)) setAnalysisPolicyError((error as Error).message)
    } finally {
      if (gate.isCurrent(request)) setAnalysisPolicyBusy(false)
    }
  }
  useEffect(() => {
    if (!selectedChat || secretaryBusy || secretaryLoading || !secretary?.deep || !["accepted", "running"].includes(secretary.deep.status)) return
    const timer = window.setInterval(() => {
      void loadSecretary(selectedChat)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [loadSecretary, secretary?.deep, secretaryBusy, secretaryLoading, selectedChat])
  useEffect(() => {
    const host = runsHost.current
    if (!runsOpen || !host) return
    defineByokRunsView()
    host.innerHTML = ""
    const view = document.createElement("byok-runs-view")
    view.setAttribute("runs", JSON.stringify(aiRuns))
    if (aiTotals) view.setAttribute("totals", JSON.stringify(aiTotals))
    view.addEventListener("byok-run-open", ((event: Event) => {
      setSelectedRun((event as CustomEvent).detail as ByokLedgerRecord)
    }) as EventListener)
    host.appendChild(view)
  }, [runsOpen, aiRuns])
  useEffect(() => {
    const host = runDetailsHost.current
    if (!runsOpen || !host) return
    defineByokRunDetails()
    host.innerHTML = ""
    if (!selectedRun) return
    const details = document.createElement("byok-run-details")
    details.setAttribute("run", JSON.stringify(selectedRun))
    host.appendChild(details)
  }, [runsOpen, selectedRun])

  const loadOlderPhoneCalls = async () => {
    if (!conversation?.phone_history?.has_more || phoneHistoryBusy) return
    const id = conversation.id
    // Freeze the cursor's existing window before a poll can roll it forward.
    phoneHistoryWindow.current = id
    phoneFollowLatest.current = false
    const viewport = transcriptRef.current?.querySelector<HTMLElement>('[data-slot="scroll-area-viewport"]')
    const anchor = viewport && [...viewport.querySelectorAll<HTMLElement>('[data-phone-message]')].find(element => element.getBoundingClientRect().bottom >= viewport.getBoundingClientRect().top)
    if (anchor?.dataset.phoneMessage) phoneHistoryScroll.current = { id, messageId: anchor.dataset.phoneMessage, top: anchor.getBoundingClientRect().top, restore: false }
    setPhoneHistoryBusy(true)
    try {
      const page = await api<{ conversation_id: string; messages: Message[]; oldest_id: string; has_more: boolean }>(
        `/v1/conversations/${encodeURIComponent(id)}/phone-history?before=${encodeURIComponent(conversation.phone_history.oldest_id)}`,
      )
      if (activeConversation.current === id && page.conversation_id === id) {
        if (phoneHistoryScroll.current?.id === id) phoneHistoryScroll.current.restore = true
        setConversation(current => current?.id === id ? { ...current,
          messages: mergePhoneHistory(page.messages, current.messages),
          phone_history: { oldest_id: page.oldest_id, has_more: page.has_more },
        } : current)
      }
    } catch (error) {
      if (activeConversation.current === id) {
        phoneHistoryScroll.current = null
        notify(`Не удалось загрузить ранние звонки: ${(error as Error).message}`)
      }
    } finally { setPhoneHistoryBusy(false) }
  }

  const openChat = (id: string) => { setSelectedChat(id); setMobilePane("chat"); setDraft("") }
  const closeChat = () => { setSelectedChat(""); setConversation(null); setMobilePane("chats"); setDraft("") }

  const jumpToDraftSource = async (item: Draft) => {
    if (!item.reply_to || !conversation) return
    const id = conversation.id
    const locate = () => [...document.querySelectorAll<HTMLElement>("[data-read-message]")]
      .find(element => element.dataset.readMessage === item.reply_to?.message_id)
    let target = locate()
    if (!target) {
      try {
        // Reuse the maintained current-user message read. Fetch one canonical
        // source, without provider sync or a guessed history window.
        const result = await api<{ error?: { message?: string }; result?: { isError?: boolean; structuredContent?: { message?: Message & { conversation_id: string } } } }>("/mcp", {
          method: "POST", body: JSON.stringify({ jsonrpc: "2.0", id: "draft-source", method: "tools/call",
            params: { name: "userio.channels.read", arguments: {
              channel: ["phone", "chatgpt"].includes(providerForSource(item.reply_to.source)) ? undefined
                : providerForSource(item.reply_to.source) === "gmail" ? "mail" : providerForSource(item.reply_to.source),
              message_id: item.reply_to.message_id,
            } } }),
        })
        const message = result.result?.structuredContent?.message
        if (result.error || result.result?.isError || !message || message.conversation_id !== id || message.message_id !== item.reply_to.message_id) {
          throw new Error("Исходное сообщение удалено или недоступно")
        }
        if (activeConversation.current !== id) return
        setConversation(current => current?.id === id ? { ...current,
          messages: current.messages.some(entry => entry.message_id === message.message_id) ? current.messages
            : [...current.messages, message].sort((a, b) => a.received_at - b.received_at),
        } : current)
        await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
        target = locate()
      } catch (error) {
        notify((error as Error).message)
        return
      }
    }
    target?.scrollIntoView({ behavior: "smooth", block: "center" })
  }
  const openNewChat = () => {
    const preferredSource = account
      ? sourceForAccount(account)
      : activeChannel || (accounts.some((item) => ["sms", "phone"].includes(providerForSource(item.provider))) ? "sms" : "sms")
    setNewChatSource(preferredSource)
    setNewChatPhone("")
    setNewChatOpen(true)
  }

  const openAttachment = async (message: Message) => {
    if (!conversation) return
    if (!canDownload) { notify("Скачивание отключено в настройках пользователя"); return }
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

  const selectFile = async (file?:File) => {
    if (!file || !conversation || conversation.source!=="vk") return
    const cid=conversation.id
    if (!file.size || file.size>2*1024*1024) {notify("Файл должен быть не больше 2 МБ");return}
    setUploadBusy(true)
    try {
      const data=new Uint8Array(await file.arrayBuffer())
      const sha=[...new Uint8Array(await crypto.subtle.digest('SHA-256',data))].map(x=>x.toString(16).padStart(2,'0')).join('')
      let raw='';for(let i=0;i<data.length;i+=8192)raw+=String.fromCharCode(...data.subarray(i,i+8192))
      const uploaded=await api<{upload_id:string;filename:string}>(`/v1/conversations/${cid}/uploads`,{method:'POST',body:JSON.stringify({filename:file.name,mime_type:file.type||'application/octet-stream',sha256:sha,data_base64:btoa(raw),request_id:crypto.randomUUID()})})
      if(activeConversation.current===cid)setSelectedUpload(uploaded)
    } catch(error){notify(`Не удалось добавить файл: ${(error as Error).message}`)}
    finally{setUploadBusy(false);if(filePicker.current)filePicker.current.value=''}
  }
  const sendManual = async () => {
    if (!conversation || !canReply || (!draft.trim() && !selectedUpload) || uploadBusy || sendInFlight.current) return
    const id = conversation.id, text = draft.trim()
    const key = JSON.stringify([id, text, selectedUpload?.upload_id])
    if (sendIntent.current?.key !== key) sendIntent.current = { key, id: crypto.randomUUID() }
    sendInFlight.current = true
    setSendBusy(true)
    try {
      await api(`/v1/conversations/${id}/send`, { method: "POST", body: JSON.stringify({ body: text, request_id: sendIntent.current.id, upload_id:selectedUpload?.upload_id }) })
      setDraft(current => current.trim() === text ? "" : current)
      sendIntent.current = null
      setSelectedUpload(null)
      notify("Отправлено")
      // Delivery already succeeded. A later view refresh failure must never
      // turn it into a send failure or invite another copy of the message.
      await Promise.allSettled([loadConversation(id), refreshChats()])
    } catch (error) {
      notify(`Не удалось отправить: ${(error as Error).message}`)
    } finally {
      sendInFlight.current = false
      setSendBusy(false)
    }
  }
  const saveMessageEdit = async () => {
    if (!conversation || !editingMessage || !messageEditBody.trim() || messageEditBusy) return
    setMessageEditBusy(true)
    try {
      await api(`/v1/conversations/${conversation.id}/messages/${encodeURIComponent(editingMessage.message_id)}`, {
        method: "PATCH", body: JSON.stringify({body: messageEditBody, expected_body: editingMessage.body}),
      })
      setEditingMessage(null)
      notify("Сообщение изменено в "+displayChannel(conversation.source))
      await Promise.allSettled([loadConversation(conversation.id), refreshChats()])
    } catch (error) {
      notify(`Не удалось изменить: ${(error as Error).message}`)
    } finally { setMessageEditBusy(false) }
  }
  const createChat = async () => {
    const target = newChatPhone.trim()
    const normalizedTarget = newChatSource === "sms" || newChatSource === "phone"
      ? target.replace(/[()\s-]/g, "").replace(/^8(?=\d{10}$)/, "+7")
      : target
    if ((newChatSource === "sms" || newChatSource === "phone") && !/^\+\d{8,15}$/.test(normalizedTarget)) {
      notify("Номер в формате +79XXXXXXXXX")
      return
    }
    if (!normalizedTarget) {
      notify("Укажите получателя")
      return
    }
    try {
      const data = await api<{ conversation: { id: string } }>("/v1/conversations", { method: "POST", body: JSON.stringify({ source: newChatSource, sender: normalizedTarget, account_ref:account?.id }) })
      setNewChatOpen(false)
      setNewChatPhone("")
      await refreshChats()
      openChat(data.conversation.id)
      notify("Диалог создан — напишите сообщение")
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
  const saveDraftEdit = async (id: string) => {
    if (!editDraftBody.trim()) return
    try {
      await api(`/v1/drafts/${id}`, { method: "PATCH", body: JSON.stringify({ body: editDraftBody }) })
      setEditDraftId(""); setEditDraftBody("")
      if (conversation) await loadConversation(conversation.id)
      notify("Черновик обновлён")
    } catch (error) {
      notify(`Не удалось изменить черновик: ${(error as Error).message}`)
    }
  }
  const deleteDraft = async (id: string) => {
    try {
      await api(`/v1/drafts/${id}`, { method: "DELETE" })
      if (conversation) await loadConversation(conversation.id)
      notify("Черновик удалён")
    } catch (error) {
      notify(`Не удалось удалить черновик: ${(error as Error).message}`)
    }
  }
  const loadByok = async () => {
    try {
      const data = await api<{ endpoint: string; model: string; has_key: boolean }>("/v1/ai-settings", { method: "POST", body: "{}" })
      setByokMine(data.has_key)
      setByokForm({ endpoint: data.endpoint, model: data.model, token: "" })
    } catch { /* server default is fine */ }
  }
  const loadContextSettings = async (requestId: number) => {
    try {
      const data = await api<{ context?: unknown }>("/v1/preferences/context")
      if (contextLoadRequest.current !== requestId) return
      setContextSession((current) => completeContextSettingsLoad(current, data.context))
    } catch (error) {
      if (contextLoadRequest.current !== requestId) return
      setContextSession((current) => failContextSettingsLoad(current, (error as Error).message))
    }
  }
  const requestContextSettings = () => {
    const requestId = ++contextLoadRequest.current
    setContextSession((current) => beginContextSettingsLoad(current))
    void loadContextSettings(requestId)
  }
  const openContextSettings = () => {
    setContextOpen(true)
    requestContextSettings()
  }
  const closeContextSettings = () => {
    contextLoadRequest.current += 1
    setContextOpen(false)
  }
  const saveContextSettings = async () => {
    if (contextSession.status !== "ready" || contextSaving || contextSettingsValidationError(contextSession.draft)) return
    const requestId = contextLoadRequest.current
    setContextSaving(true)
    try {
      const data = await api<{ context?: unknown }>("/v1/preferences/context", {
        method: "POST", body: JSON.stringify(contextSettingsPayload(contextSession.draft)),
      })
      if (contextLoadRequest.current !== requestId) return
      setContextSession((current) => confirmContextSettingsSave(current, data.context ?? current.draft))
      setContextOpen(false)
      notify("Настройки контекста сохранены")
    } catch (error) {
      notify(`Не удалось сохранить настройки контекста: ${(error as Error).message}`)
    } finally {
      setContextSaving(false)
    }
  }
  useEffect(() => {
    const host = byokPickerRef.current
    if (!byokOpen || !host || byokPresets.length === 0) return
    defineByokPresetPicker()
    host.innerHTML = ""
    const picker = document.createElement("byok-preset-picker")
    picker.setAttribute("presets", JSON.stringify(byokPresets))
    picker.style.setProperty("--byok-border", "hsl(217 33% 28%)")
    picker.style.setProperty("--byok-accent", "hsl(232 82% 74%)")
    picker.style.setProperty("--byok-selected-bg", "hsl(232 82% 74% / .15)")
    picker.addEventListener("byok-select", ((event: Event) => {
      const preset = (event as CustomEvent).detail as { baseUrl: string; modelId: string }
      setByokForm((current) => ({ ...current, endpoint: preset.baseUrl, model: preset.modelId }))
    }) as EventListener)
    host.appendChild(picker)
  }, [byokOpen, byokPresets])

  const loadAiRuns = async () => {
    try {
      const data = await api<{ runs: ByokLedgerRecord[]; totals: ByokLedgerTotals | null }>("/v1/ai-runs", { method: "POST", body: "{}" })
      setAiRuns(data.runs || [])
      setAiTotals(data.totals)
    } catch { /* runs are optional */ }
  }
  const loadPresets = async () => {
    try {
      const data = await api<{ presets: typeof byokPresets }>("/v1/ai-presets", { method: "POST", body: "{}" })
      setByokPresets(data.presets || [])
    } catch { /* presets are optional */ }
  }
  const saveByok = async () => {
    try {
      await api("/v1/ai-settings/save", { method: "POST", body: JSON.stringify(byokForm) })
      setByokOpen(false)
      await loadByok()
      notify("Свой ИИ сохранён — кнопка «ИИ» работает через ваш ключ")
    } catch (error) {
      notify(`Не удалось сохранить: ${(error as Error).message}`)
    }
  }
  const resetByok = async () => {
    try {
      await api("/v1/ai-settings/reset", { method: "POST", body: "{}" })
      setByokOpen(false)
      await loadByok()
      notify("Вернулись к серверному ИИ")
    } catch (error) {
      notify(`Не удалось сбросить: ${(error as Error).message}`)
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
  const analyseWithSecretary = async () => {
    if (!conversation || secretaryBusy) return
    const request = secretaryRequest.current.beginAction()
    if (request === null) return
    setSecretaryLoading(false)
    setSecretaryBusy(true)
    try {
      const result = await api<Secretary>(`/v1/conversations/${encodeURIComponent(conversation.id)}/secretary/triage`, {
        method: "POST", body: "{}",
      })
      if (secretaryRequest.current.isCurrentAction(request)) {
        setSecretary(result)
        setSecretaryError("")
        notify("Секретарь разобрал последнее сообщение")
      }
    } catch (error) {
      if (secretaryRequest.current.isCurrentAction(request)) notify(`Разбор не удался: ${(error as Error).message}`)
    } finally {
      if (secretaryRequest.current.finishAction(request)) setSecretaryBusy(false)
    }
  }
  const startDeepSecretaryAnalysis = async () => {
    if (!conversation || secretaryBusy) return
    const request = secretaryRequest.current.beginAction()
    if (request === null) return
    setSecretaryLoading(false)
    setSecretaryBusy(true)
    try {
      const result = await api<Secretary>(`/v1/conversations/${encodeURIComponent(conversation.id)}/secretary/deep`, {
        method: "POST", body: JSON.stringify(currentSecretaryDeep?.status === "failed" ? { retry_of_job_id: currentSecretaryDeep.job_id } : {}),
      })
      if (secretaryRequest.current.isCurrentAction(request)) {
        setSecretary(result)
        setSecretaryError("")
        notify(result.deep?.status === "completed" ? "Глубокий разбор уже готов" : result.deep?.status === "failed" ? "Попытка завершилась ошибкой; можно повторить её ещё раз" : "Глубокий разбор принят")
      }
    } catch (error) {
      if (secretaryRequest.current.isCurrentAction(request)) notify(`Не удалось запустить глубокий разбор: ${(error as Error).message}`)
    } finally {
      if (secretaryRequest.current.finishAction(request)) setSecretaryBusy(false)
    }
  }
  const approve = async (id: string) => {
    if (!canReply || approvalInFlight.current) return
    approvalInFlight.current = true
    setApprovingDraft(id)
    try {
      await api(`/v1/drafts/${id}/approve`, { method: "POST", body: "{}" })
      notify("Отправлено")
      if (conversation) await Promise.allSettled([loadConversation(conversation.id)])
    } catch (error) {
      notify(`Не удалось отправить: ${(error as Error).message}`)
    } finally { approvalInFlight.current = false; setApprovingDraft("") }
  }
  const markSeen = async () => {
    const message = conversation?.messages.filter(m => m.direction !== 'outgoing').at(-1)
    if (!message || !conversation) return
    try { await api(`/v1/conversations/${encodeURIComponent(conversation.id)}/read`,{method:'POST',body:JSON.stringify({message_id:message.message_id,automatic:false})}); await refreshChats(); await loadConversation(conversation.id) }
    catch(error){ notify(`Не удалось отметить прочитанным: ${(error as Error).message}`) }
  }
  const setStealth = async (enabled:boolean) => {
    try { const result=await api<{auto_read:boolean}>('/v1/preferences/reading',{method:'POST',body:JSON.stringify({auto_read:!enabled})});setAutoRead(result.auto_read);readAttempts.current.clear() }
    catch(error){notify(`Не удалось сохранить настройку: ${(error as Error).message}`)}
  }
  useEffect(() => {
    if(!autoRead||!userCapabilities.read||!conversation) return
    const id=conversation.id,viewport=transcriptRef.current?.querySelector<HTMLElement>('[data-slot="scroll-area-viewport"]')
    if(!viewport) return
    let timer:ReturnType<typeof setTimeout>|undefined,disposed=false
    const scan=()=>{
      if(document.visibilityState!=='visible'||viewport.getBoundingClientRect().height<=0) return
      const bounds=viewport.getBoundingClientRect()
      const visible=[...viewport.querySelectorAll<HTMLElement>('[data-read-message]')].filter(el=>{const r=el.getBoundingClientRect();return r.bottom>bounds.top&&r.top<bounds.bottom&&r.width>0})
      const message=visible.map(el=>conversation.messages.find(m=>m.message_id===el.dataset.readMessage)).filter((m):m is Message=>!!m&&m.direction!=='outgoing'&&(!m.seen_at||(conversation.source==='telegram'&&(m.provider_read===false||m.provider_read===0)))).at(-1)
      if(!message) return
      const key=id+'|'+message.message_id
      if(readAttempts.current.has(key))return
      readAttempts.current.add(key)
      void api(`/v1/conversations/${encodeURIComponent(id)}/read`,{method:'POST',body:JSON.stringify({message_id:message.message_id,automatic:true})}).then(()=>{if(!disposed)void Promise.allSettled([refreshChats(),loadConversation(id)])}).catch(()=>{ /* No background retry loop; explicit view can retry. */ })
    }
    const schedule=()=>{if(timer)clearTimeout(timer);timer=setTimeout(scan,500)}
    const observer=new IntersectionObserver(schedule,{root:viewport,threshold:0.2})
    viewport.querySelectorAll('[data-read-message]').forEach(el=>observer.observe(el))
    viewport.addEventListener('scroll',schedule);document.addEventListener('visibilitychange',schedule);schedule()
    return ()=>{disposed=true;if(timer)clearTimeout(timer);observer.disconnect();viewport.removeEventListener('scroll',schedule);document.removeEventListener('visibilitychange',schedule)}
  },[autoRead,conversation,loadConversation,refreshChats,userCapabilities.read])
  const toggleAccount = (id: string, visible: boolean) => {
    const next = visible ? hiddenAccounts.filter((item) => item !== id) : Array.from(new Set([...hiddenAccounts, id]))
    setHiddenAccounts(next)
    localStorage.setItem("userio-hidden-accounts", JSON.stringify(next))
    if (!visible && selectedAccount === id) setSelectedAccount("all")
  }

  // View-only platform switch: unchecking a platform (e.g. ChatGPT) hides all
  // its chats from the unified feed. Explicit channel/account views win.
  const togglePlatformVisible = (platform: string, visible: boolean) => {
    const next = visible ? hiddenPlatforms.filter((item) => item !== platform) : Array.from(new Set([...hiddenPlatforms, platform]))
    setHiddenPlatforms(next)
    localStorage.setItem("userio-hidden-platforms", JSON.stringify(next))
    if (!visible) {
      if (selectedChannel === platform) setSelectedChannel("all")
      const selected = accounts.find((item) => item.id === selectedAccount)
      if (selected && providerForSource(selected.provider) === platform) setSelectedAccount("all")
    }
  }
  const unifiedFeed = selectedAccount === "all" && selectedChannel === "all"
  const platformVisibleInFeed = (source: string) => !unifiedFeed || !hiddenPlatforms.includes(providerForSource(source))
  // Remount the list (with staggered row animation) whenever the view changes.
  const viewKey = `${selectedAccount}|${selectedChannel}|${hiddenPlatforms.join(",")}|${hiddenAccounts.join(",")}|${search.trim() ? "search" : "list"}`

  const visibleChats = (search.trim() ? searchResults : chats)
    .filter((chat) => platformVisibleInFeed(chat.source))
    .filter((chat) => !accounts.some((item) => hiddenAccounts.includes(item.id) &&
      (["telegram", "sms", "phone", "vk"].includes(item.provider) ? chat.account_ref === item.id : chat.source === sourceForAccount(item))))
    .sort((a, b) => (search.trim() ? Number(b.match_field === "contact") - Number(a.match_field === "contact") : 0) || (b.last_at ?? 0) - (a.last_at ?? 0))
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
    <header className="flex items-center gap-3 px-4 pb-3 pt-4"><div className="grid size-9 place-items-center rounded-xl bg-[#2f80ed] text-white"><Inbox className="size-5" /></div><div className="min-w-0"><p className="text-xs font-medium text-muted-foreground">UNIVERSAL USERIO</p><h1 className="truncate text-base font-semibold">Все сообщения</h1></div><Button className="ml-auto md:hidden" variant="ghost" size="icon" onClick={() => setDrawerOpen(false)} title="Закрыть"><X /></Button></header>
    <div className="px-3 pb-3">
      <Button className="w-full justify-start" variant={aiPaused ? "secondary" : "outline"}
        disabled={aiPaused === null || aiPauseBusy} onClick={() => void toggleAiPause()}
        aria-pressed={aiPaused === true} title="Пауза автоматических разборов и уведомлений по всем чатам. Приём и чтение сообщений продолжаются.">
        <Sparkles className="mr-2 size-4" />{aiPauseBusy ? "Сохраняю…" : aiPaused ? "Возобновить ИИ" : "Приостановить ИИ"}
      </Button>
      {aiPaused && <p className="mt-1 text-xs text-muted-foreground" role="status">Автоматический ИИ на паузе</p>}
      {aiPauseError && <p className="mt-1 text-xs text-destructive" role="alert">{aiPauseError}</p>}
    </div>
    <ScrollArea className="min-h-0 flex-1 px-2">
      <Button className="mb-2 h-11 w-full justify-start rounded-xl" variant={selectedChannel === "all" && selectedAccount === "all" ? "secondary" : "ghost"} onClick={() => { setSelectedAccount("all"); setSelectedChannel("all"); setDrawerOpen(false) }}><span className="grid size-7 place-items-center rounded-lg bg-[#2f80ed]/10 text-[#2f80ed]"><Inbox className="size-4" /></span><span className="ml-2 text-left"><span className="block text-sm font-medium">Все аккаунты</span><span className="block text-[10px] text-muted-foreground">единая лента и поиск</span></span></Button>
      <Button className="mb-3 h-10 w-full justify-start rounded-xl border border-dashed" variant="outline" onClick={() => window.location.assign("/accounts")}><Plus className="mr-2 size-4" /> Аккаунты · подключить и настроить</Button>
      {platforms.map((platform) => {
        const platformAccounts = accounts.filter((item) => providerForSource(item.provider) === platform)
        const platformUnread = unreadByPlatform[platform] || 0
        return <div key={platform} className="mb-4">
          <div role="button" tabIndex={0} onClick={() => { setSelectedChannel(platform); setSelectedAccount("all"); setDrawerOpen(false) }} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { setSelectedChannel(platform); setSelectedAccount("all"); setDrawerOpen(false) } }} title={`Показать все: ${displayChannel(platform)}`} className={`group mb-1 flex h-9 cursor-pointer select-none items-center gap-2 rounded-lg px-2 text-sm font-medium ${selectedChannel === platform && selectedAccount === "all" ? "bg-secondary text-secondary-foreground" : "text-foreground/80 hover:bg-muted/70"} ${hiddenPlatforms.includes(platform) ? "opacity-50" : ""}`}><span className="text-foreground/70">{channelIcon(platform)}</span>{displayChannel(platform)}{platformUnread > 0 && <Badge className="ml-auto rounded-full bg-[#2f80ed] px-1.5 text-[10px] text-white">{platformUnread}</Badge>}<input className="size-3.5 shrink-0 opacity-40 hover:opacity-100 group-hover:opacity-90" aria-label={`Показывать ${displayChannel(platform)} в общей ленте`} title="Показывать платформу в общей ленте" type="checkbox" checked={!hiddenPlatforms.includes(platform)} onClick={(event) => event.stopPropagation()} onChange={(event) => togglePlatformVisible(platform, event.target.checked)} /></div>
          {platformAccounts.map((item) => {
            const visible = !hiddenAccounts.includes(item.id)
            const health = accountActivity(item)
            return <div key={item.id} className="group mb-1 flex items-center gap-1">
              <Button data-account-id={item.id} className="min-w-0 flex-1 justify-start rounded-lg px-2" variant={selectedAccount === item.id ? "secondary" : "ghost"} onClick={() => { if (visible) { setSelectedAccount(item.id); setSelectedChannel("all"); setDrawerOpen(false) } }}>
                <Avatar className="size-7"><AvatarFallback className="text-[10px]">{initials(item.display_name)}</AvatarFallback></Avatar>
                <span className="ml-2 min-w-0 flex-1 text-left"><span className="block truncate text-sm">{item.display_name}</span><span className="flex items-center gap-1 text-[10px] text-muted-foreground"><span className={`inline-block size-1.5 rounded-full ${HEALTH_CLASS[health]}`} />{HEALTH_TITLE[health]}</span></span>
              </Button>
              <input className="size-3.5 opacity-50 group-hover:opacity-100" aria-label={`Показать ${item.display_name}`} title="Показывать в общей ленте" type="checkbox" checked={visible} onChange={(event) => toggleAccount(item.id, event.target.checked)} />
            </div>
          })}

        </div>
      })}
    </ScrollArea>
    <div className="space-y-1 border-t p-2">
      <Button className="w-full justify-start" variant="ghost" size="sm" onClick={() => { void loadAiRuns(); setSelectedRun(null); setRunsOpen(true) }}><ChartLine className="size-4" /> Прогон / кошелёк</Button>
      <Button className="w-full justify-start" variant="ghost" size="sm" onClick={() => { void loadByok(); void loadPresets(); setByokOpen(true) }}><Sparkles className="size-4" /> Свой ИИ {byokMine ? "· активен" : ""}</Button>
      <Button className="w-full justify-start" variant="ghost" size="sm" onClick={openContextSettings}><SlidersHorizontal className="size-4" /> Контекст ИИ</Button>
      <details className="rounded-lg px-2 py-1 text-xs text-muted-foreground"><summary className="cursor-pointer py-1 font-medium text-foreground">Настройки чтения</summary><label className="flex cursor-pointer items-center justify-between gap-2 py-2"><span>Режим скрытности</span><input aria-label="Режим скрытности" type="checkbox" checked={!autoRead} onChange={event => void setStealth(event.target.checked)} /></label><p className="pb-2">В режиме скрытности просмотр не отмечает сообщения прочитанными. Без него открытый диалог отмечается прочитанным в UserIO и, для Telegram, у собеседника.</p></details><details className="rounded-lg px-2 py-1 text-xs text-muted-foreground"><summary className="cursor-pointer py-1 font-medium text-foreground">Права пользователя</summary><div className="space-y-1 py-1">{([["read", "Чтение"], ["subscribe", "Push"], ["download", "Вложения"], ["send", "Отправка"]] as [keyof UserCapabilities, string][]).map(([name, label]) => <label key={name} className="flex cursor-pointer items-center justify-between gap-2 py-1"><span>{label}</span><input type="checkbox" checked={userCapabilities[name]} onChange={(event) => void setCapability(name, event.target.checked)} /></label>)}</div></details>
      <nav aria-label="О сервисе и исходный код" className="grid grid-cols-2 gap-1 border-t pt-1">
        <a href="/welcome" className="rounded-md px-3 py-2 text-sm font-medium hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring">О сервисе</a>
        <a href="https://github.com/megamen32/universal-userio" target="_blank" rel="noopener noreferrer" className="rounded-md px-3 py-2 text-sm font-medium hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring">GitHub ↗</a>
      </nav>
      <form method="post" action="/auth/logout"><Button type="submit" className="w-full justify-start" variant="ghost" size="sm"><LogOut /> Выйти</Button></form>
    </div>
  </>

  return <main className="flex h-svh min-h-0 overflow-hidden bg-background text-foreground">
    {/* Desktop sidebar / mobile drawer */}
    <aside className={`min-h-0 w-[280px] shrink-0 flex-col overflow-hidden border-r bg-card ${sidebarOpen ? "md:flex" : "md:hidden"} ${drawerOpen ? "absolute inset-y-0 left-0 z-40 flex shadow-2xl" : "hidden"}`}>{sidebar}</aside>
    {drawerOpen && <button aria-label="Close menu" className="absolute inset-0 z-30 bg-black/50 md:hidden" onClick={() => setDrawerOpen(false)} />}

    {/* Chat list: default pane on mobile */}
    <section className={`min-h-0 min-w-0 flex-1 flex-col overflow-hidden border-r bg-card transition-[width,flex-basis] duration-200 ease-out ${selectedChat ? "md:w-[340px] md:flex-none" : "md:flex-1"} ${mobilePane === "chats" || !selectedChat ? "flex" : "hidden"} md:flex`}>
      <header className="space-y-3 p-4"><div className="flex items-start gap-2"><Button variant="ghost" size="icon" className="md:hidden" onClick={() => setDrawerOpen(true)} title="Аккаунты"><Menu /></Button><Button variant="ghost" size="icon" className="hidden md:inline-flex" onClick={() => setSidebarOpen((open) => !open)} title={sidebarOpen ? "Скрыть платформы" : "Показать платформы"}>{sidebarOpen ? <PanelLeftClose /> : <PanelLeftOpen />}</Button><div className="min-w-0"><p className="text-xs font-medium text-muted-foreground">ДИАЛОГИ</p><p className="truncate text-sm font-medium">{account ? account.display_name : activeChannel ? `Все ${displayChannel(activeChannel)}` : "Все аккаунты"}</p></div><Button className="ml-auto" variant="ghost" size="icon" onClick={openNewChat} aria-label="Новое сообщение" title="Новое сообщение"><Plus /></Button></div><div className="relative"><Input value={search} onChange={(event) => { setSearch(event.target.value); setSearching(Boolean(event.target.value.trim())); setSearchResults([]); setSearchError("") }} onKeyDown={(event) => { if (event.key === "Escape") { setSearch(""); event.currentTarget.blur() } if (event.key === "Enter") event.preventDefault() }} placeholder={selectedAccount === "all" && selectedChannel === "all" ? "Поиск по всем сообщениям" : "Поиск в выбранном аккаунте"} />{searching && <span className="absolute right-9 top-1/2 -translate-y-1/2 text-[10px] text-muted-foreground">ищу…</span>}{search && <button aria-label="Очистить поиск" className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground hover:text-foreground" onClick={() => setSearch("")}><X className="size-4" /></button>}</div></header>
      {/* Radix's table content wrapper otherwise expands to the longest preview. */}
      <ScrollArea className="min-h-0 min-w-0 flex-1 px-2 pb-3 [&_[data-slot=scroll-area-viewport]>div]:!block">
        <div key={viewKey}>
        {(!search.trim() || visibleChats.length > 0) && (search.trim() ? ["exact", "fuzzy"] as const : ["all"] as const).map(kind => {
          const rows = kind === "all" ? visibleChats : visibleChats.filter(chat => chat.match_type === kind)
          const content = <>{rows.map((chat, chatIndex) => {
          const unread = chat.unread_count > 0
          return <button key={chat.id} data-conversation-id={chat.id} onClick={() => openChat(chat.id)} style={{ animationDelay: `${selectedAccount === "all" ? Math.min(chatIndex * 4, 48) : 0}ms` }} className={`userio-row-in mb-1 flex w-full gap-3 rounded-xl border border-transparent p-3 text-left transition-all ${selectedChat === chat.id ? "border-[#2f80ed]/20 bg-[#2f80ed]/8 shadow-sm" : "hover:bg-muted/70"}`}>
          <Avatar><AvatarFallback className={`${avatarColor(chat.sender)} font-medium text-white`}>{initials(titleOf(chat))}</AvatarFallback></Avatar><span className="min-w-0 flex-1"><span className="flex items-center justify-between gap-2"><span className={`min-w-0 truncate text-sm ${unread ? "font-semibold" : "font-medium"}`}>{titleOf(chat)}</span><span className="flex shrink-0 items-center gap-1.5">{chat.analysis_policy && <span title={`${chatKindLabel(chat.analysis_policy.conversation_kind)} · Разбор ${chat.analysis_policy.allowed ? "включён" : "выключен"}`} aria-label={`Разбор ${chat.analysis_policy.allowed ? "включён" : "выключен"}`}><Sparkles className={`size-3 ${chat.analysis_policy.allowed ? "text-[#2f80ed]" : "text-muted-foreground/50"}`} /></span>}{chat.last_at && <span className="text-[11px] text-muted-foreground">{listTime(chat.last_at)}</span>}{unread && <Badge className="rounded-full bg-primary px-1.5 text-[11px] text-primary-foreground">{chat.unread_count}</Badge>}</span></span><span className="mt-1 flex min-w-0 items-center gap-1.5"><span className="shrink-0 text-muted-foreground">{channelIcon(chat.source)}</span><span className={`min-w-0 truncate text-xs ${unread ? "text-foreground/80" : "text-muted-foreground"}`}>{previewText(chat.preview)}</span>{chat.match_message_id && <span className="ml-auto shrink-0 rounded bg-[#2f80ed]/10 px-1.5 py-0.5 text-[9px] font-medium text-[#2f80ed]">{chat.match_field === "contact" ? "контакт" : "текст"}</span>}</span></span>
        </button>
        })}{kind !== "all" && searchMore[kind] && <Button variant="ghost" className="my-2 w-full" disabled={searching} onClick={() => void loadMoreSearch(kind)}>Показать ещё</Button>}</>
          return kind === "all" ? <div key={kind}>{content}</div> : <details key={`${search.trim()}-${kind}`} open={kind === "exact" || !visibleChats.some(chat => chat.match_type === "exact")} className="mb-2" aria-label={kind === "exact" ? "Точные совпадения" : "Возможные совпадения"}>
            <summary className="cursor-pointer rounded-lg px-3 py-2 text-sm font-medium hover:bg-muted">{kind === "exact" ? "Точные совпадения" : "Возможные совпадения · с опечатками"} <span className="text-muted-foreground">({rows.length}{searchMore[kind] ? "+" : ""})</span></summary>{content}
          </details>
        })}
        </div>
        {!visibleChats.length && <p className="p-5 text-center text-sm text-muted-foreground">
          {searchError || (searching && search.trim() ? "Ищу…" : search.trim()
            ? <>Ничего не найдено по запросу <span className="font-medium text-foreground">«{search.trim()}»</span></>
            : selectedChannel !== "all"
              ? <>В канале {displayChannel(selectedChannel)} чатов нет</>
              : "Ничего не найдено")}
        </p>}
      </ScrollArea>
    </section>

    {/* Conversation: fullscreen pane on mobile */}
    <section className={`min-h-0 min-w-0 flex-1 flex-col bg-[#eef2f6] dark:bg-[#0e1621] ${mobilePane === "chat" ? "flex" : "hidden"} ${selectedChat ? "md:flex" : "md:hidden"}`}>
      {conversation && conversation.id === selectedChat ? <>
        <header data-slot="chat-header" className="flex min-h-14 shrink-0 items-center gap-2 border-b bg-card/90 px-3 py-2 backdrop-blur md:px-4">
          <Button variant="ghost" size="icon" className="size-8 md:hidden" onClick={() => setMobilePane("chats")} title="Назад"><ArrowLeft /></Button>
          <Avatar className="size-8 shrink-0"><AvatarFallback className={`${avatarColor(conversation.sender)} font-medium text-white`}>{initials(titleOf(conversation))}</AvatarFallback></Avatar>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold" title={prettySender(conversation.sender, conversation.source)}>{titleOf(conversation)}</h2>
            <div className="flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
              <span className="shrink-0">{displayChannel(conversation.source)}</span>
              <span className="hidden shrink-0 sm:inline">· {chatKindLabel(analysisPolicy?.conversation_kind || conversation.conversation_kind)}</span>
              {(() => {
                const platformAccounts = accounts.filter(item => providerForSource(item.provider) === providerForSource(conversation.source))
                const senderAccount = platformAccounts.find(item => item.id === conversation.account_ref)
                if (!platformAccounts.length) return null
                return <><span aria-hidden="true">·</span>{platformAccounts.length > 1
                  ? <select aria-label="Аккаунт отправки" className="min-w-0 max-w-36 rounded bg-transparent text-[11px] text-foreground" value={conversation.account_ref || ""} onChange={event => void setSenderAccount(event.target.value)}><option value="">авто</option>{platformAccounts.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select>
                  : <span className="truncate">{senderAccount?.display_name || platformAccounts[0].display_name}</span>}</>
              })()}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <label className="flex h-8 cursor-pointer items-center gap-1.5 rounded-md px-2 text-xs" title={analysisPolicyError || "Автоматический разбор новых сообщений и уведомления. Сообщения остаются в диалоге при выключении."}>
              <input type="checkbox" aria-label="Разбирать секретарём" checked={!!analysisPolicy?.allowed} disabled={!analysisPolicy || analysisPolicyBusy} onChange={event => void setChatAnalysis(event.target.checked)} className="size-4 accent-[#2f80ed]" />
              <span>{analysisPolicyBusy ? "Сохраняю…" : analysisPolicy?.allowed ? "ИИ: включён" : "ИИ: выключен"}</span><Sparkles className={`size-3.5 ${analysisPolicy?.allowed ? "text-[#2f80ed]" : "text-muted-foreground"}`} />
            </label>{analysisPolicyError && <span role="alert" className="text-xs text-destructive">Настройка не сохранена: {analysisPolicyError}</span>}
            <Popover.Root key={conversation.id}>
              <Popover.Trigger asChild><Button variant="ghost" size="sm" className="h-8 px-2" title="Открыть секретаря: оценка важности, подробный разбор и результаты" aria-label="Открыть секретаря"><Sparkles className="size-4 text-[#2f80ed]" /><span className="hidden sm:inline">Секретарь</span>{(secretaryError || currentSecretaryDeep?.status === "failed") && <span className="text-destructive">!</span>}{currentSecretaryDeep?.status === "running" && <span className="size-1.5 rounded-full bg-[#2f80ed]" />}</Button></Popover.Trigger>
              <Popover.Portal><Popover.Content align="end" sideOffset={8} collisionPadding={12} aria-label="Секретарь" className="z-50 flex max-h-[min(70vh,var(--radix-popover-content-available-height))] w-[min(420px,calc(100vw-24px))] flex-col gap-3 overflow-y-auto rounded-xl border bg-card p-3 text-foreground shadow-xl">
            <div className="flex shrink-0 flex-wrap gap-2" title="Оценить важность — приоритет и нужен ли ответ. Подробный анализ — отдельная сессия агента с разбором и планом.">{secretaryError && <Button variant="outline" size="sm" disabled={secretaryBusy || secretaryLoading} onClick={() => void loadSecretary(conversation.id)}>Повторить загрузку</Button>}<Button variant="outline" size="sm" disabled={secretaryBusy || !secretary?.conversation.event_seq} onClick={() => void analyseWithSecretary()}>{secretaryBusy ? "Подождите…" : "Оценить важность"}</Button><Button size="sm" disabled={secretaryBusy || !secretary?.conversation.event_seq || ["accepted", "running", "completed"].includes(currentSecretaryDeep?.status || "")} onClick={() => void startDeepSecretaryAnalysis()}>{currentSecretaryDeep?.status === "completed" ? "Готово" : currentSecretaryDeep?.status === "failed" ? "Повторить подробный анализ" : "Подробный анализ"}</Button>{secretary?.deep?.session_url && <Button asChild variant="outline" size="sm"><a href={secretary.deep.session_url} target="_blank" rel="noreferrer">Agent Herder ↗</a></Button>}</div>
            <div className="min-w-0 flex-1">
              <p className="mb-2 text-xs text-muted-foreground">{chatKindLabel(analysisPolicy?.conversation_kind || conversation.conversation_kind)} · {analysisPolicy ? analysisPolicy.allowed ? "Автоматический разбор включён" : "Автоматический разбор выключен" : "Загружаю настройку разбора…"}</p>
              {analysisPolicyError && <p className="mb-2 text-xs text-destructive" role="alert">Не удалось сохранить настройку: {analysisPolicyError}</p>}
              <div className="flex flex-wrap items-center gap-2"><span className="inline-flex items-center gap-1 text-sm font-semibold"><Sparkles className="size-4 text-[#2f80ed]" /> Секретарь</span>{secretary?.triage?.triage && <Badge variant="secondary">{(() => { const score = secretary.triage?.triage?.importance ?? 0; return score >= .75 ? "Высокий приоритет" : score >= .45 ? "Средний приоритет" : "Низкий приоритет" })()}</Badge>}{secretary?.deep && <Badge variant="outline">{secretary.deep.status === "accepted" ? "Принято" : secretary.deep.status === "running" ? "Глубокий разбор идёт" : secretary.deep.status === "completed" ? "Готово" : "Ошибка"}</Badge>}</div>
              {secretaryError && <p className="mt-1 text-xs text-destructive" role="alert">{secretaryError}</p>}
              {secretaryLoading && !secretary && <p className="mt-1 text-xs text-muted-foreground">Загружаю состояние секретаря…</p>}
              {secretary?.triage && secretary.triage.event_seq !== secretary.conversation.event_seq && <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">Показан разбор предыдущего сообщения. Запустите новый разбор для последнего входящего.</p>}
              {secretary?.triage?.triage ? <><p className="mt-1 text-sm">{secretary.triage.triage.reason_ru || "Сообщение разобрано."}</p>{secretary.triage.triage.action_summary && <p className="mt-1 text-xs text-muted-foreground">{secretary.triage.triage.action_summary}</p>}</> : <p className="mt-1 text-xs text-muted-foreground">Разбор последнего входящего сообщения ещё не запускался.</p>}
              {secretary?.deep && secretary.deep.event_seq !== secretary.conversation.event_seq && <p className="mt-1 text-[11px] text-muted-foreground">Глубокий разбор относится к предыдущему входящему сообщению.</p>}
              {!!secretary?.owner_messages_after_current?.length && <p className="mt-1 text-xs text-muted-foreground">После этого входящего вы уже отвечали в этом диалоге. Секретарь учитывает отправленные ответы.</p>}
              <p className="mt-1 text-[11px] text-muted-foreground">Оценить важность: приоритет и нужен ли ответ. Подробный анализ: отдельная сессия с разбором и планом.</p>
              {secretary?.deep?.result && <p className="mt-2 whitespace-pre-wrap rounded-lg bg-muted px-3 py-2 text-sm">{secretary.deep.result}</p>}
              {secretary?.deep?.error && <p className="mt-2 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{secretary.deep.error}</p>}
            </div>
              </Popover.Content></Popover.Portal>
            </Popover.Root>
            <Button variant="ghost" size="icon" className="size-8" onClick={markSeen} title="Отметить просмотренным в UserIO" aria-label="Отметить просмотренным"><Check className="size-4" /></Button>
            <Button className="hidden size-8 md:inline-flex" variant="ghost" size="icon" onClick={closeChat} title="Закрыть диалог"><X className="size-4" /></Button>
          </div>
        </header>
        <ScrollArea ref={transcriptRef} onScrollCapture={event => {
          const viewport = event.target as HTMLElement
          if (conversation.source === 'phone' && viewport.dataset.slot === 'scroll-area-viewport') phoneFollowLatest.current = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 96
        }} className="min-h-0 flex-1"><div className="mx-auto flex w-full max-w-[920px] flex-col gap-1.5 px-3 py-5 md:px-6 md:py-7">
          {conversation.phone_history?.has_more && <Button variant="outline" size="sm" className="self-center" disabled={phoneHistoryBusy} onClick={() => void loadOlderPhoneCalls()}>{phoneHistoryBusy ? 'Загружаю…' : 'Показать более ранние звонки'}</Button>}
          {conversation.messages.map((message, index) => {
            const isHtmlEmail = /^\s*<(?:!doctype|html|body|table|div|p|span|h[1-6]|a\b)/i.test(message.body)
            const system = message.direction === "system"
            const outgoing = messageIsOutgoing(conversation, message, accounts)
            const author = messageAuthor(message, conversation.conversation_kind)
            const previous = conversation.messages[index - 1]
            const showDay = !previous || !sameDay(previous.received_at, message.received_at)
            return <Fragment key={`${message.source}:${message.message_id}`}>
              {showDay && <div className="my-2 text-center"><span className="rounded-full bg-muted px-3 py-1 text-[11px] font-medium text-muted-foreground">{dayLabel(message.received_at)}</span></div>}
              {system
                ? <div data-read-message={message.message_id} data-phone-message={conversation.source === 'phone' ? message.message_id : undefined} className="my-1 text-center"><span className="inline-flex max-w-[88%] items-center gap-2 rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground"><MessageBody message={message} onAttachmentClick={openAttachment} /><span className="text-[10px] opacity-70">{timeHM(message.received_at)}</span></span></div>
                : <div data-read-message={message.message_id} data-phone-message={conversation.source === 'phone' ? message.message_id : undefined} className={isHtmlEmail ? "w-full overflow-hidden bg-transparent text-sm" : `self-start w-fit max-w-[88%] overflow-hidden rounded-[18px] px-3.5 py-2.5 text-sm shadow-sm md:max-w-[72%] ${outgoing ? "ml-auto rounded-br-[6px] bg-[#2f80ed] text-white" : "rounded-bl-[6px] border border-black/5 bg-white text-slate-900 dark:border-white/5 dark:bg-[#182533] dark:text-slate-100"}`}>{author && <p data-message-author={message.message_id} className={`mb-1 text-xs font-semibold ${outgoing ? "text-white/90" : "text-blue-700 dark:text-blue-300"}`}>{author}</p>}{isHtmlEmail ? <><div className="flex items-center justify-end bg-background px-1 pb-2"><Button variant="outline" size="sm" onClick={() => setExpandedHtml({ body: message.body, title: titleOf(conversation) })}><Expand /> Развернуть</Button></div><iframe className="min-h-[360px] w-full border-0 bg-white" sandbox="" srcDoc={message.body} title={`Email ${message.message_id}`} /></> : editingMessage?.message_id === message.message_id ? <div className="space-y-2"><textarea aria-label="Изменить сообщение" className="min-h-16 w-full rounded bg-background p-2 text-foreground" value={messageEditBody} onChange={event => setMessageEditBody(event.target.value)} /><div className="flex gap-2"><Button variant="secondary" size="sm" disabled={messageEditBusy} onClick={() => setEditingMessage(null)}>Отмена</Button><Button variant="secondary" size="sm" disabled={messageEditBusy || !messageEditBody.trim()} onClick={() => void saveMessageEdit()}>Сохранить изменение</Button></div></div> : <MessageBody message={message} onAttachmentClick={openAttachment} />}{(conversation.source === "max" || canVkRich) && outgoing && canReply && (canVkRich || canEditMaxText(message.attachments)) && !!message.body.trim() && editingMessage?.message_id !== message.message_id && <button className="mt-1 block text-xs underline opacity-80" onClick={() => { setEditingMessage(message); setMessageEditBody(message.body) }}>Изменить сообщение</button>}<p className={`mt-1 px-1 text-[11px] ${outgoing && !isHtmlEmail ? "text-white/65" : "text-muted-foreground"}`}>{timeHM(message.received_at)}{message.edited_at ? " · изменено" : ""}{conversation.source === "telegram" && message.provider_read != null ? (message.provider_read ? (outgoing ? " · Прочитано собеседником" : " · Прочитано в Telegram") : (outgoing ? " · Отправлено" : " · Не прочитано в Telegram")) : conversation.source === "vk" && outgoing ? " · Отправлено в VK" : ""}</p></div>}
              {message.call_audio && <CallAudio recording={message.call_audio} />}
            </Fragment>
          })}
          {conversation.drafts.filter(item => !(item.status === "approved" && conversation.messages.some(message =>
            message.message_id === item.outbox_receipt || (conversation.source === "sms" && message.outbox_receipt === item.outbox_receipt) || (conversation.source === "max" && message.message_id.endsWith(`-${item.outbox_receipt}`)) || (["vk", "yandex"].includes(conversation.source) && !!item.outbox_receipt && message.direction === "outgoing" && message.message_id.startsWith(`${conversation.account_ref}|`) && message.message_id.endsWith(`:${item.outbox_receipt}`))))).map((item) => {
            const editing = editDraftId === item.id
            return <div key={item.id} className="ml-auto max-w-[85%] rounded-2xl rounded-tr-sm bg-primary/90 px-4 py-3 text-sm text-primary-foreground shadow-sm md:max-w-[78%]">
              <p className="mb-2 text-[11px] opacity-75">{item.created_at ? <time dateTime={new Date(item.created_at * 1000).toISOString()}>Создано {new Date(item.created_at * 1000).toLocaleString("ru-RU")}</time> : "Дата создания не сохранена"}</p>
              {item.reply_to ? <button type="button" data-draft-source={item.id} className="mb-2 block w-full rounded border-l-2 border-current bg-black/10 px-2 py-1 text-left text-xs" onClick={() => void jumpToDraftSource(item)}><span className="font-semibold">В ответ на сообщение · {timeHM(item.reply_to.received_at)}</span><span className="mt-0.5 block line-clamp-2 whitespace-pre-wrap opacity-80">{item.reply_to.body || "Вложение"}</span></button> : item.status === "proposed" && !item.id.startsWith("draft_web_") && <p className="mb-2 text-xs opacity-75">Исходное сообщение не сохранено для этого черновика</p>}
              {item.context_status === "stale" && item.status === "proposed" && <p className="mb-2 text-xs font-medium">Переписка изменилась после создания: проверьте актуальность ответа.</p>}
              {editing
                ? <div className="space-y-2"><textarea className="min-h-16 w-full rounded-lg bg-primary-foreground/10 p-2 text-foreground" value={editDraftBody} onChange={(event) => setEditDraftBody(event.target.value)} rows={3} /><div className="flex justify-end gap-2"><Button size="sm" variant="secondary" onClick={() => { setEditDraftId(""); setEditDraftBody("") }}>Отмена</Button><Button size="sm" onClick={() => void saveDraftEdit(item.id)}>Сохранить</Button></div></div>
                : <><p className="whitespace-pre-wrap">{item.body}</p><div className="mt-2 flex items-center justify-between gap-3"><span className="text-[11px] opacity-75">{item.status === "proposed" ? (item.id.startsWith("draft_web_") ? "Не отправлено" : "Черновик") : item.status === "approved" ? "Отправлено" : item.status === "rejected" ? "Отклонено" : item.status === "sending" ? "Результат отправки уточняется — не отправляйте повторно" : item.status}</span>{item.status === "proposed" && <span className="flex items-center gap-1"><Button size="sm" variant="ghost" className="h-7 px-2 text-primary-foreground/80 hover:text-primary-foreground" title="Изменить текст черновика" onClick={() => { setEditDraftId(item.id); setEditDraftBody(item.body) }}>Изменить</Button><Button size="sm" variant="ghost" className="h-7 px-2 text-primary-foreground/80 hover:text-primary-foreground" title="Удалить черновик" onClick={() => void deleteDraft(item.id)}><X className="size-3.5" /></Button><Button size="sm" variant="secondary" disabled={!canReply || !!approvingDraft} title={canReply ? "Отправить это сообщение" : "Этот аккаунт только для чтения"} onClick={() => approve(item.id)}>Отправить</Button></span>}</div></>}
            </div>
          })}
          <div ref={bottomRef} />
        </div></ScrollArea>
        <footer className="bg-transparent px-3 pb-3 pt-2 md:px-6 md:pb-5"><div className="mx-auto max-w-[920px]">{selectedUpload && <div className="mb-2 flex items-center gap-2 text-xs" data-selected-upload={selectedUpload.upload_id}><Paperclip className="size-3.5" />{selectedUpload.filename}<button title="Убрать вложение" onClick={()=>setSelectedUpload(null)} disabled={sendBusy}>×</button></div>}<div className="flex items-center gap-2 rounded-2xl border bg-card p-2 shadow-lg shadow-black/5">{canVkRich && <><input ref={filePicker} type="file" className="hidden" aria-label="Вложение VK" onChange={event=>void selectFile(event.target.files?.[0])} /><Button variant="ghost" size="icon" title="Добавить фото или файл" disabled={!canReply||sendBusy||uploadBusy} onClick={()=>filePicker.current?.click()}><Paperclip className="size-4" /></Button></>}<Input className="border-0 bg-transparent shadow-none focus-visible:ring-0" value={draft} disabled={!canReply || sendBusy} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); void sendManual() } }} placeholder={canReply ? "Сообщение…" : "Ответы недоступны для этого аккаунта"} /><Button disabled={!canReply} variant="ghost" size="icon" onClick={() => void askAi()} title="Предложить ответ"><Sparkles className="size-4" /></Button><Button className="rounded-xl bg-[#2f80ed] text-white hover:bg-[#2774d8]" disabled={!canReply || sendBusy || uploadBusy || (!draft.trim() && !selectedUpload)} onClick={() => void sendManual()} size="icon" title="Отправить сообщение"><Send /></Button></div><p className="mt-1.5 px-2 text-[10px] text-muted-foreground">{canReply ? "Ваш текст отправляется сразу. Предложения ИИ — после вашего подтверждения." : "Только чтение."}</p></div></footer>
      </> : <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">Открываю диалог…</div>}
      {toast && <div className="fixed inset-x-0 bottom-20 z-50 mx-auto w-fit max-w-[90%] rounded-full bg-foreground px-4 py-2 text-center text-sm text-background shadow-lg md:bottom-8">{toast}</div>}
      {byokOpen && <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onClick={() => setByokOpen(false)}><div className="w-full max-w-md rounded-2xl border bg-card p-5 shadow-xl" onClick={(event) => event.stopPropagation()}>
          <h2 className="font-semibold">Свой ИИ (BYOK)</h2>
          <div ref={byokPickerRef} className="mt-3 max-h-44 overflow-y-auto" />
            <p className="mt-1 text-xs text-muted-foreground">Кнопка «ИИ» будет работать через ваш ключ. Подойдёт любой OpenAI-совместимый API (MiniMax, OpenAI, routerai…). Ключ хранится на сервере и не показывается обратно.</p>
          <div className="mt-3 space-y-2">
            <Input value={byokForm.endpoint} onChange={(event) => setByokForm({ ...byokForm, endpoint: event.target.value })} placeholder="https://api.minimax.io/v1" />
            <Input value={byokForm.model} onChange={(event) => setByokForm({ ...byokForm, model: event.target.value })} placeholder="MiniMax-M2.7" />
            <Input type="password" value={byokForm.token} onChange={(event) => setByokForm({ ...byokForm, token: event.target.value })} placeholder="API-ключ" />
          </div>
          <div className="mt-4 flex items-center justify-between gap-2">
            <Button variant="outline" size="sm" onClick={() => void resetByok()}>Сбросить к серверному</Button>
            <span className="flex gap-2"><Button variant="outline" size="sm" onClick={() => setByokOpen(false)}>Отмена</Button><Button size="sm" onClick={() => void saveByok()}>Сохранить</Button></span>
          </div>
        </div></div>}
      {runsOpen && <div className="fixed inset-0 z-50 flex flex-col bg-background"><header className="flex items-center gap-3 border-b px-5 py-3"><h2 className="truncate font-semibold">Прогон/Кошелёк · затраты ИИ</h2>{aiTotals && <span className="text-xs text-muted-foreground">{aiTotals.calls} прогонов · ${aiTotals.costUsd.toFixed(4)} / {aiTotals.costRub.toFixed(2)}₽{aiTotals.fxRate ? ` · курс ${aiTotals.fxRate}` : ""}</span>}<Button className="ml-auto" variant="outline" size="sm" onClick={() => setRunsOpen(false)}><X /> Закрыть</Button></header><div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6"><div className="mx-auto max-w-4xl"><div ref={runsHost} /><div ref={runDetailsHost} /></div></div></div>}
      {expandedHtml && <div className="fixed inset-0 z-50 flex flex-col bg-background"><header className="flex items-center gap-3 border-b px-5 py-3"><h2 className="truncate font-semibold">{expandedHtml.title}</h2><Button className="ml-auto" variant="outline" size="sm" onClick={() => setExpandedHtml(null)}><X /> Закрыть</Button></header><iframe className="min-h-0 flex-1 border-0 bg-white" sandbox="" srcDoc={expandedHtml.body} title="Письмо" /></div>}
      {attachmentPreview && <div role="dialog" aria-label="Вложение" className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setAttachmentPreview(null)}><div className="w-full max-w-md rounded-2xl bg-background p-5 shadow-2xl" onClick={(event) => event.stopPropagation()}><header className="flex items-center gap-3"><div className="grid size-10 place-items-center rounded-xl bg-muted">{attachmentPreview.message.body.trim().match(MEDIA_PLACEHOLDER)?.[1]?.toLowerCase().match(/video|видео/) ? <Video className="size-5" /> : <ImageIcon className="size-5" />}</div><div className="min-w-0"><h3 className="truncate font-semibold">{attachmentPreview.meta?.filename || mediaLabel(attachmentPreview.message.body) || "Вложение"}</h3><p className="truncate text-xs text-muted-foreground">Сообщение {attachmentPreview.message.message_id}{attachmentPreview.meta?.size ? ` · ${humanSize(attachmentPreview.meta.size)}` : ""}</p></div><Button className="ml-auto" variant="ghost" size="icon" onClick={() => setAttachmentPreview(null)} title="Закрыть"><X /></Button></header><div className="mt-4 space-y-3 text-sm">{attachmentPreview.loading && <p className="text-muted-foreground">Запрашиваю файл у провайдера…</p>}{!attachmentPreview.loading && attachmentPreview.meta?.available === false && (<p className="rounded-lg bg-muted px-3 py-2 text-muted-foreground">{attachmentPreview.meta.reason || "Вложение недоступно для скачивания."}</p>)}{!attachmentPreview.loading && attachmentPreview.meta?.available && (attachmentPreview.meta.content_type?.startsWith("image/") ? <img src={attachmentPreview.meta.download_url} alt={attachmentPreview.meta.filename || "preview"} className="max-h-72 w-full rounded-lg bg-muted object-contain" /> : attachmentPreview.meta.content_type === "application/pdf" ? <iframe title="PDF preview" src={attachmentPreview.meta.download_url} className="h-72 w-full rounded-lg border bg-white" /> : <p className="rounded-lg bg-muted px-3 py-2 text-muted-foreground">{attachmentPreview.meta.content_type || "Файл"} · {humanSize(attachmentPreview.meta.size ?? 0)} — превью недоступно, скачайте, чтобы открыть.</p>)}</div><div className="mt-5 flex items-center justify-end gap-2"><Button variant="outline" size="sm" onClick={() => setAttachmentPreview(null)}>Закрыть</Button>{attachmentPreview.meta?.available && attachmentPreview.meta?.download_url && <Button size="sm" onClick={() => void downloadAttachment(attachmentPreview.meta!.download_url!, attachmentPreview.meta!.filename || "attachment", attachmentPreview.meta!.content_type ?? "application/octet-stream")}><ImageIcon /> Скачать</Button>}</div></div></div>}
    </section>
    {contextOpen && <ContextSettingsDialog settings={contextSession.draft} loading={contextSession.status === "loading"} ready={contextSession.status === "ready"} loadError={contextSession.loadError} saving={contextSaving} onChange={(draft) => setContextSession((current) => ({ ...current, draft }))} onClose={closeContextSettings} onRetry={requestContextSettings} onSave={() => void saveContextSettings()} />}
    {newChatOpen && <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onClick={() => setNewChatOpen(false)}><div className="w-full max-w-sm rounded-2xl border bg-card p-5 shadow-xl" onClick={(event) => event.stopPropagation()}><h2 className="font-semibold">Новое сообщение</h2><p className="mt-1 text-xs text-muted-foreground">Канал: {displayChannel(newChatSource)}. Ваш текст отправляется сразу кнопкой отправки.</p><Input className="mt-3" value={newChatPhone} onChange={(event) => setNewChatPhone(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void createChat() }} placeholder={newChatSource === "sms" || newChatSource === "phone" ? "+79XXXXXXXXX" : "Получатель или идентификатор"} autoFocus /><div className="mt-4 flex justify-end gap-2"><Button variant="outline" size="sm" onClick={() => setNewChatOpen(false)}>Отмена</Button><Button size="sm" onClick={() => void createChat()}>Создать</Button></div></div></div>}
  </main>
}

export default App
