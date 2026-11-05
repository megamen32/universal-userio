import { useCallback, useEffect, useState } from 'react'

type Share = {id:string; account_ref:string}
type Chat = {id:string; title:string; last_message_snippet:string}
type Message = {text:string; at:number; source:{message_id:string; direction:string}}
type Action = {id:string; hash:string; status:string; envelope?:{payload?:{text?:string}}}
type Pending = {state:'preparing'|'prepared'|'sending'|'unknown'; action?:Action}

type Props = {onClose:()=>void}
const key = (share:string, chat:string) => `userio-shared-reply:${share}:${chat}`
async function request<T>(path:string, payload?:unknown):Promise<T> {
  const response = await fetch(path, payload === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
  const body = await response.json() as T & {error?:string}
  if (!response.ok) throw new Error(body.error || 'Запрос не выполнен')
  return body
}

export function SharedChats({onClose}:Props) {
  const [shares,setShares]=useState<Share[]>([])
  const [share,setShare]=useState<Share|null>(null)
  const [chats,setChats]=useState<Chat[]>([])
  const [chat,setChat]=useState<Chat|null>(null)
  const [messages,setMessages]=useState<Message[]>([])
  const [text,setText]=useState('')
  const [pending,setPending]=useState<Pending|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const call = useCallback(<T,>(s:Share, operation:string, args:object) => request<T>('/v1/profile-shares/call',{share_id:s.id,operation,arguments:args}),[])
  useEffect(()=>{let active=true; request<{shares:Share[]}>('/v1/profile-shares').then(r=>{if(active)setShares(r.shares)}).catch(()=>{if(active)setError('Не удалось загрузить общие чаты')}); return ()=>{active=false}},[])
  async function chooseShare(s:Share) {
    setShare(s);setChat(null);setMessages([]);setChats([]);setError('');setText('');setPending(null)
    try { const result=await call<{chats:Chat[]}>(s,'userio.channels.list',{});setChats(result.chats) }
    catch {setError('Нет доступа к общим чатам или папка недоступна')}
  }
  async function openChat(c:Chat) {
    if(!share)return
    setChat(c);setMessages([]);setText('');setError('')
    try { const raw=localStorage.getItem(key(share.id,c.id)); const saved=raw ? JSON.parse(raw) as Pending : null
      setPending(saved && (saved.state==='preparing'||saved.state==='sending') ? {...saved,state:'unknown'} : saved)
    } catch {setPending({state:'unknown'});setError('Не удалось проверить состояние предыдущего ответа')}
    try {const result=await call<{messages:Message[]}>(share,'userio.messenger.history',{account_ref:share.account_ref,conversation_id:c.id,limit:100});setMessages(result.messages)}
    catch {setError('Чат недоступен или доступ отозван')}
  }
  function persist(value:Pending|null) {
    if(!share||!chat)throw Error('chat_required')
    if(value)localStorage.setItem(key(share.id,chat.id),JSON.stringify(value));else localStorage.removeItem(key(share.id,chat.id))
    setPending(value)
  }
  async function prepare() {
    if (!share || !chat || !navigator.locks) { setError('Блокировка отправки недоступна'); return }
    await navigator.locks.request(key(share.id,chat.id), {ifAvailable:true}, async lock => {
      if (!lock) { setError('Этот ответ уже обрабатывается в другой вкладке'); return }
      if (localStorage.getItem(key(share.id,chat.id))) { setPending({state:'unknown'}); setError('Уже есть незавершённый ответ'); return }
      await prepareLocked()
    })
  }
  async function prepareLocked() {
    if(!share||!chat||!text.trim()||pending||busy)return
    const source=[...messages].reverse().find(m=>m.source.direction==='incoming')
    if(!source){setError('Нет входящего сообщения для ответа');return}
    setBusy(true);setError('')
    try {persist({state:'preparing'})} catch {setError('Не удалось сохранить состояние ответа; отправка заблокирована');setBusy(false);return}
    try {
      const result=await call<{action:Action}>(share,'userio.messenger.propose',{account_ref:share.account_ref,conversation_id:chat.id,source_message_id:source.source.message_id,action:'reply',payload:{text:text.trim()},request_id:`web-share-${crypto.randomUUID()}`})
      persist({state:'prepared',action:result.action})
    } catch {persist({state:'unknown'});setError('Результат подготовки неизвестен. Повторная подготовка заблокирована.')}
    finally {setBusy(false)}
  }
  async function approve() {
    if (!share || !chat || !navigator.locks) { setError('Блокировка отправки недоступна'); return }
    await navigator.locks.request(key(share.id,chat.id), {ifAvailable:true}, async lock => {
      if (!lock) { setError('Этот ответ уже обрабатывается в другой вкладке'); return }
      const saved = localStorage.getItem(key(share.id,chat.id))
      if (!saved || (JSON.parse(saved) as Pending).state !== 'prepared') { setPending({state:'unknown'}); setError('Состояние ответа изменилось; повтор заблокирован'); return }
      await approveLocked()
    })
  }
  async function approveLocked() {
    if(!share||!pending?.action||pending.state!=='prepared'||busy)return
    const action=pending.action;setBusy(true);setError('')
    try {persist({state:'sending',action})} catch {setError('Не удалось сохранить состояние; отправка заблокирована');setBusy(false);return}
    try {
      const result=await call<{action:Action}>(share,'userio.messenger.approve',{action_id:action.id,expected_hash:action.hash,confirm:true})
      if(result.action.status==='accepted'){persist(null);setText('');if(chat)await openChat(chat)}
      else {persist({state:'unknown',action:result.action});setError('Результат отправки неизвестен. Проверьте состояние ответа.')}
    } catch {persist({state:'unknown',action});setError('Результат отправки неизвестен. Не отправляйте повторно; проверьте состояние.')}
    finally {setBusy(false)}
  }
  async function check() {
    if(!share||!pending?.action||busy)return
    setBusy(true)
    try {const result=await call<{action:Action}>(share,'userio.messenger.get',{action_id:pending.action.id})
      if(result.action.status==='accepted'){persist(null);setText('');setError('Ответ отправлен');if(chat)await openChat(chat)}
      else {persist({state:'unknown',action:result.action});setError(`Состояние ответа: ${result.action.status}. Повторная отправка заблокирована.`)}
    } catch {setError('Не удалось проверить состояние ответа; повторная отправка заблокирована.')}
    finally {setBusy(false)}
  }
  return <section aria-label="Общие чаты" className="absolute inset-0 z-[60] flex flex-col bg-background p-4 text-foreground">
    <header className="flex items-center gap-3"><button disabled={busy} onClick={onClose}>← Назад</button><h1 className="text-lg font-semibold">Общие чаты</h1></header>
    {error && <p role="alert" className="my-2 text-sm text-red-700">{error}</p>}
    <div className="flex min-h-0 flex-1 gap-4 pt-4">
      <nav aria-label="Доступные общие чаты" className="w-1/3 min-w-32 overflow-auto">
        {shares.length===0 && <p>Нет предоставленных чатов</p>}
        {shares.map(s=><button className="block w-full p-2 text-left font-semibold" key={s.id} disabled={busy} onClick={()=>void chooseShare(s)}>Общий доступ · {s.account_ref}</button>)}
        {chats.map(c=><button className="block w-full rounded p-2 text-left hover:bg-muted" key={c.id} disabled={busy} onClick={()=>void openChat(c)}>{c.title}<small className="block truncate">{c.last_message_snippet}</small></button>)}
      </nav>
      <div className="flex min-w-0 flex-1 flex-col">{chat && <><h2 className="font-semibold">{chat.title}</h2><div className="flex-1 overflow-auto" aria-label="История чата">{messages.map(m=><p className="my-2 whitespace-pre-wrap" key={m.source.message_id}><strong>{m.source.direction==='outgoing'?'Вы':'Собеседник'}: </strong>{m.text || '[Вложение]'}</p>)}</div>
      <p className="text-xs text-muted-foreground">Доступны чтение и ответ. Вложения, реакции и другие действия здесь недоступны.</p>
      <textarea aria-label="Текст ответа" className="my-2 min-h-20 w-full rounded border p-2" value={text} disabled={!!pending||busy} onChange={e=>setText(e.target.value)}/>
      {!pending && <button disabled={busy||!text.trim()} onClick={()=>void prepare()}>Подготовить ответ</button>}
      {pending?.state==='prepared' && <><p className="whitespace-pre-wrap">Текст к отправке: {pending.action?.envelope?.payload?.text}</p><button disabled={busy || !pending.action?.envelope?.payload?.text} onClick={()=>void approve()}>Подтвердить и отправить</button></>}
      {pending?.state==='unknown' && <><p>Исход предыдущего действия неизвестен; повтор заблокирован.</p>{pending.action && <button disabled={busy} onClick={()=>void check()}>Проверить состояние</button>}</>}
      </>}</div>
    </div>
  </section>
}
