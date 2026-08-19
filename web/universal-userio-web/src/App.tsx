import { useCallback, useEffect, useMemo, useState } from "react"
import { Bot, Check, Inbox, Mail, MessageCircle, Send, Sparkles } from "lucide-react"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"

type Account = { id: string; provider: string; display_name: string; capabilities: string[] }
type Chat = { id: string; source: string; sender: string; identity_id?: string; preview?: string; unread_count: number }
type Conversation = { id: string; source: string; sender: string; identity_id?: string; messages: Message[]; drafts: Draft[] }
type Message = { source: string; message_id: string; sender: string; body: string; received_at: number; seen_at?: number }
type Draft = { id: string; body: string; status: string }

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } })
  if (!response.ok) throw new Error("Request failed")
  return response.json() as Promise<T>
}

const channelIcon = (source: string) => source === "email" || source.startsWith("gmail") ? <Mail className="size-4" /> : <MessageCircle className="size-4" />
const displayChannel = (source: string) => source === "email" || source.startsWith("gmail") ? "Email" : source[0].toUpperCase() + source.slice(1)
const initials = (value: string) => value.split(/[.@\s_-]/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase()

export function App() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [chats, setChats] = useState<Chat[]>([])
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [selectedAccount, setSelectedAccount] = useState<string>("all")
  const [selectedChannel, setSelectedChannel] = useState<string>("all")
  const [selectedChat, setSelectedChat] = useState<string>("")
  const [draft, setDraft] = useState("")
  const [search, setSearch] = useState("")

  const account = accounts.find((item) => item.id === selectedAccount)
  const activeChannel = selectedChannel !== "all" ? selectedChannel : account?.provider
  const channels = useMemo(() => Array.from(new Set([...accounts.map((item) => item.provider), ...chats.map((item) => item.source)])).sort(), [accounts, chats])

  const refreshChats = useCallback(async () => {
    const suffix = activeChannel ? `?source=${encodeURIComponent(activeChannel)}` : ""
    const data = await api<{ conversations: Chat[] }>(`/v1/conversations${suffix}`)
    setChats(data.conversations)
    setSelectedChat((current) => current && data.conversations.some((item) => item.id === current) ? current : data.conversations[0]?.id ?? "")
  }, [activeChannel])

  const loadConversation = useCallback(async (id: string) => setConversation(await api<Conversation>(`/v1/conversations/${id}`)), [])

  useEffect(() => { void api<{ accounts: Account[] }>("/v1/accounts").then((data) => setAccounts(data.accounts)) }, [])
  // The callback fetches external state before updating the view.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void refreshChats() }, [refreshChats])
  // The callback fetches external state before updating the view.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { if (selectedChat) void loadConversation(selectedChat) }, [loadConversation, selectedChat])

  const submitDraft = async () => {
    if (!conversation || !draft.trim()) return
    await api(`/v1/conversations/${conversation.id}/drafts`, { method: "POST", body: JSON.stringify({ body: draft }) })
    setDraft("")
    await loadConversation(conversation.id)
    await refreshChats()
  }
  const askAi = async () => { if (conversation) { await api(`/v1/conversations/${conversation.id}/ai-drafts`, { method: "POST", body: "{}" }); await loadConversation(conversation.id) } }
  const approve = async (id: string) => { await api(`/v1/drafts/${id}/approve`, { method: "POST", body: "{}" }); if (conversation) await loadConversation(conversation.id) }
  const markSeen = async () => { const message = conversation?.messages.at(-1); if (message) { await api("/v1/inbox/seen", { method: "POST", body: JSON.stringify({ source: message.source, message_id: message.message_id }) }); await refreshChats(); await loadConversation(conversation!.id) } }
  const visibleChats = chats.filter((chat) => `${chat.sender} ${chat.preview ?? ""}`.toLowerCase().includes(search.toLowerCase()))

  return <main className="grid h-svh min-h-0 overflow-hidden grid-cols-[72px_260px_340px_minmax(0,1fr)] bg-background text-foreground">
    <aside className="flex min-h-0 flex-col items-center gap-3 border-r bg-muted/30 py-4">
      <div className="grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground"><Inbox className="size-5" /></div>
      <Separator className="w-8" />
      <Button variant={selectedAccount === "all" ? "secondary" : "ghost"} size="icon" onClick={() => setSelectedAccount("all")} title="All accounts"><MessageCircle /></Button>
      {accounts.map((item) => <Button key={item.id} variant={selectedAccount === item.id ? "secondary" : "ghost"} size="icon" onClick={() => { setSelectedAccount(item.id); setSelectedChannel("all") }} title={item.display_name}><Avatar className="size-7"><AvatarFallback>{initials(item.display_name)}</AvatarFallback></Avatar></Button>)}
      <div className="mt-auto"><Button variant="ghost" size="icon" title="AI is opt-in"><Bot /></Button></div>
    </aside>

    <aside className="flex min-h-0 min-w-0 flex-col border-r bg-card">
      <header className="p-4"><p className="text-xs font-medium text-muted-foreground">ACCOUNTS</p><h1 className="mt-1 text-lg font-semibold">Universal UserIO</h1></header>
      <ScrollArea className="min-h-0 flex-1 px-2">
        <Button className="mb-1 w-full justify-start" variant={selectedChannel === "all" ? "secondary" : "ghost"} onClick={() => setSelectedChannel("all")}><Inbox /> All channels</Button>
        {channels.map((channel) => <Button key={channel} className="mb-1 w-full justify-start" variant={selectedChannel === channel ? "secondary" : "ghost"} onClick={() => { setSelectedChannel(channel); setSelectedAccount("all") }}>{channelIcon(channel)} {displayChannel(channel)}</Button>)}
      </ScrollArea>
      <div className="border-t p-3 text-xs text-muted-foreground">AI proposes only when you ask.</div>
    </aside>

    <section className="flex min-h-0 min-w-0 flex-col border-r bg-card">
      <header className="space-y-3 p-4"><div><p className="text-xs font-medium text-muted-foreground">CHATS</p><p className="text-sm text-muted-foreground">{activeChannel ? displayChannel(activeChannel) : "All conversations"}</p></div><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search chats" /></header>
      <ScrollArea className="min-h-0 flex-1 px-2 pb-3">
        {visibleChats.map((chat) => <button key={chat.id} onClick={() => setSelectedChat(chat.id)} className={`mb-1 flex w-full gap-3 rounded-lg p-3 text-left transition-colors ${selectedChat === chat.id ? "bg-accent" : "hover:bg-muted"}`}>
          <Avatar><AvatarFallback>{initials(chat.sender)}</AvatarFallback></Avatar><span className="min-w-0 flex-1"><span className="flex items-center justify-between gap-2"><b className="truncate text-sm">{chat.identity_id || chat.sender}</b>{chat.unread_count > 0 && <Badge className="rounded-full px-1.5">{chat.unread_count}</Badge>}</span><span className="mt-1 block truncate text-xs text-muted-foreground">{chat.preview || "No messages yet"}</span></span>
        </button>)}
        {!visibleChats.length && <p className="p-5 text-center text-sm text-muted-foreground">No chats in this channel.</p>}
      </ScrollArea>
    </section>

    <section className="flex min-h-0 min-w-0 flex-col bg-[linear-gradient(135deg,hsl(var(--background)),hsl(var(--muted)/.35))]">
      {conversation ? <>
        <header className="flex items-center gap-3 border-b bg-card/90 px-5 py-3 backdrop-blur"><Avatar><AvatarFallback>{initials(conversation.sender)}</AvatarFallback></Avatar><div className="min-w-0"><h2 className="truncate font-semibold">{conversation.identity_id || conversation.sender}</h2><p className="text-xs text-muted-foreground">{displayChannel(conversation.source)} · {conversation.sender}</p></div><Button className="ml-auto" variant="outline" size="sm" onClick={markSeen}><Check /> Mark seen</Button></header>
        <ScrollArea className="min-h-0 flex-1"><div className="mx-auto flex max-w-3xl flex-col gap-3 p-6">
          {conversation.messages.map((message) => <div key={`${message.source}:${message.message_id}`} className="max-w-[78%] rounded-2xl rounded-tl-sm bg-card px-4 py-3 text-sm shadow-sm"><p>{message.body}</p><p className="mt-1 text-[11px] text-muted-foreground">{new Date(message.received_at * 1000).toLocaleString()}</p></div>)}
          {conversation.drafts.map((item) => <div key={item.id} className="ml-auto max-w-[78%] rounded-2xl rounded-tr-sm bg-primary px-4 py-3 text-sm text-primary-foreground shadow-sm"><p>{item.body}</p><div className="mt-2 flex items-center justify-between gap-3"><span className="text-[11px] opacity-75">{item.status}</span>{item.status === "proposed" && <Button size="sm" variant="secondary" onClick={() => approve(item.id)}>Approve & send</Button>}</div></div>)}
        </div></ScrollArea>
        <footer className="border-t bg-card p-4"><div className="flex gap-2"><Input value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void submitDraft() }} placeholder="Write a draft reply…" /><Button onClick={() => void submitDraft()} size="icon" title="Create draft"><Send /></Button><Button variant="outline" onClick={() => void askAi()} title="Ask AI for variants"><Sparkles /></Button></div><p className="mt-2 text-xs text-muted-foreground">Create a draft, then explicitly approve it before delivery.</p></footer>
      </> : <div className="grid flex-1 place-items-center text-center"><div><div className="mx-auto grid size-12 place-items-center rounded-full bg-muted"><MessageCircle /></div><h2 className="mt-3 font-semibold">Choose a chat</h2><p className="mt-1 text-sm text-muted-foreground">Accounts, channels, and conversations stay separate.</p></div></div>}
    </section>
  </main>
}

export default App
