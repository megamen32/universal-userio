import { useEffect, useState, type ReactNode } from 'react'
import { Popover, Dialog } from 'radix-ui'
import { Check, ChevronRight, CirclePlus, Folder, FolderPlus, Folders, Inbox, Infinity as InfinityIcon, MessageCircle, Moon, Pencil, Plus, Search, Settings, Sun, Trash2, UserRound, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useTheme } from '@/components/theme-provider'
import { FOLDERS_KEY, readInboxFolders, type InboxFolder } from '../inbox-folders'

export type NavigationAccount = { id: string; provider: string; display_name: string; enabled?: boolean }
export type FolderChoice = { id: string; name: string; source: string }
const STANDARD = [{ id: 'all', name: 'Все сообщения', short: 'Все', Icon: Inbox }, { id: 'unread', name: 'Непрочитанные', short: 'Непрочитанные', Icon: MessageCircle }, { id: 'personal', name: 'Личные', short: 'Личные', Icon: UserRound }]

export function useInboxFolders() {
  const [folders, setFolders] = useState<InboxFolder[]>(() => {
    try { return readInboxFolders(localStorage.getItem(FOLDERS_KEY)) } catch { return [] }
  })
  const [active, select] = useState('all')
  const [error, setError] = useState('')
  const change = (next: InboxFolder[]) => {
    try { localStorage.setItem(FOLDERS_KEY, JSON.stringify(next)); setFolders(next); setError(''); return true }
    catch { setError('Не удалось сохранить папку. Проверьте, разрешено ли сайту хранить данные.'); return false }
  }
  const save = (folder: InboxFolder) => {
    if (folder.conversations.length > 100) { setError('В одну папку можно добавить до 100 переписок.'); return false }
    if (folders.length >= 64 && !folders.some(row => row.id === folder.id)) { setError('Можно создать до 64 папок.'); return false }
    const next = folders.some(row => row.id === folder.id) ? folders.map(row => row.id === folder.id ? folder : row) : [...folders, folder]
    if (!change(next)) return false
    select(folder.id); return true
  }
  const remove = (id: string) => {
    if (!change(folders.filter(row => row.id !== id))) return false
    if (active === id) select('all')
    return true
  }
  return { folders, active, select, save, remove, error }
}
export type FolderState = ReturnType<typeof useInboxFolders>

type NavigationProps = {
  accounts: NavigationAccount[]; platforms: string[]; selectedAccount: string; selectedChannel: string
  provider: (source: string) => string; label: (source: string) => string; icon: (source: string) => ReactNode
  unread: Record<string, number>; onSelect: (channel: string, account: string) => void
  expanded: boolean; onExpand: () => void; mobileChat: boolean; onSettings: () => void
  folders: FolderState; folderCounts: Record<string, number>; onFolder: (id: string) => void
  onEditFolder: (folder?: InboxFolder) => void
}

export function InboxNavigation(p: NavigationProps) {
  const { theme, setTheme } = useTheme()
  const [openPlatform, setOpenPlatform] = useState<string | null>(null)
  const activeAccount = p.accounts.find(a => a.id === p.selectedAccount)
  const activePlatform = activeAccount ? p.provider(activeAccount.provider) : p.selectedChannel
  const activeFolder = p.folders.folders.find(f => f.id === p.folders.active)
  const entries = [...STANDARD, ...p.folders.folders.map(f => ({ ...f, short: f.name, Icon: Folder }))]
  return <>
    <nav aria-label="Каналы и аккаунты" className={`userio-rail ${p.mobileChat ? 'hidden md:flex' : 'flex'}`}>
      <a href="/welcome" className="userio-logo" aria-label="User.io — о сервисе">U</a>
      <div className="userio-rail-scroll">
        <button type="button" className="userio-service" aria-label="Все каналы" aria-pressed={activePlatform === 'all'} onClick={() => p.onSelect('all', 'all')}><InfinityIcon /><span>Все</span></button>
        {p.platforms.map(platform => {
          const accounts = p.accounts.filter(a => p.provider(a.provider) === platform)
          const title = p.label(platform)
          const short = ({ telegram: 'TG', whatsapp: 'WA', gmail: 'Почта', yandex: 'Яндекс', chatgpt: 'GPT', matrix: 'Matrix', phone: 'Звонки', telemost: 'Встречи' } as Record<string, string>)[platform] || title
          return <Popover.Root key={platform} open={openPlatform === platform} onOpenChange={value => setOpenPlatform(value ? platform : null)}>
            <Popover.Trigger asChild><button type="button" className="userio-service" data-platform={platform} aria-label={`${title}: выбрать аккаунт`} aria-pressed={activePlatform === platform} title={title}>
              {p.icon(platform)}<span>{short}</span>{(p.unread[platform] || 0) > 0 && <span className="userio-channel-dot" aria-label="Есть непрочитанные" />}
              {activePlatform === platform && activeAccount && accounts.length > 1 && <span className="userio-account-mark" aria-label={activeAccount.display_name}>{accounts.findIndex(a => a.id === activeAccount.id) + 1}</span>}
            </button></Popover.Trigger>
            <Popover.Portal><Popover.Content className="userio-account-picker" side="right" align="start" sideOffset={9} collisionPadding={12} aria-label={`Аккаунты ${title}`}>
              <header><span>{title}</span><Popover.Close asChild><Button variant="ghost" size="icon" aria-label="Закрыть выбор аккаунта"><X /></Button></Popover.Close></header>
              <button className="userio-account-option" data-account-id="all" aria-pressed={activePlatform === platform && !activeAccount} onClick={() => { p.onSelect(platform, 'all'); setOpenPlatform(null) }}><span className="userio-account-avatar"><InfinityIcon /></span><span className="min-w-0 flex-1">Все аккаунты<div className="text-xs text-muted-foreground">Общая лента {title}</div></span></button>
              <div className="userio-account-options">{accounts.map(a => <button key={a.id} data-account-id={a.id} className="userio-account-option" aria-pressed={p.selectedAccount === a.id} onClick={() => { p.onSelect('all', a.id); setOpenPlatform(null) }}>
                <span className="userio-account-avatar">{a.display_name.trim().slice(0, 2).toUpperCase()}</span><span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{a.display_name}{a.enabled === false && <span className="block text-xs text-muted-foreground">Отключён</span>}</span>{p.selectedAccount === a.id && <Check className="size-4 shrink-0" />}
              </button>)}</div>
              <a href="/accounts" className="userio-connect"><Plus className="size-4" />Подключить аккаунт</a>
            </Popover.Content></Popover.Portal>
          </Popover.Root>
        })}
      </div>
      <button type="button" className="userio-service" aria-label="Папки" aria-expanded={p.expanded} aria-pressed={p.expanded} onClick={p.onExpand}><Folders /><span>Папки</span></button>
      <a className="userio-service" href="/accounts" aria-label="Подключить аккаунт"><Plus /><span>Добавить</span></a>
      <button type="button" className="userio-service" aria-label={theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? <Sun /> : <Moon />}</button>
      <button type="button" className="userio-service" aria-label="Настройки" onClick={p.onSettings}><Settings /></button>
    </nav>
    {p.expanded && <button className="absolute inset-0 z-20 bg-black/40 md:hidden" aria-label="Закрыть папки" onClick={p.onExpand} />}
    <aside aria-label="Папки сообщений" className={`userio-folder-panel ${p.expanded ? 'is-open' : ''}`} inert={!p.expanded}>
      <div className="userio-folder-panel-inner">
        <header><h2>Папки</h2><Button variant="ghost" size="icon" onClick={p.onExpand} aria-label="Свернуть папки"><ChevronRight className="rotate-180" /></Button></header>
        <div className="userio-folder-rows">{entries.map(({ id, name, Icon }) => <div className="userio-folder-row" key={id}>
          <button type="button" data-folder={id} aria-pressed={p.folders.active === id} onClick={() => p.onFolder(id)}><Icon className="size-4 shrink-0" /><span>{name}</span><small>{p.folderCounts[id] || 0}</small></button>
          {!STANDARD.some(f => f.id === id) && <Button variant="ghost" size="icon" aria-label={`Изменить папку ${name}`} onClick={() => p.onEditFolder(p.folders.folders.find(f => f.id === id))}><Pencil className="size-3.5" /></Button>}
        </div>)}</div>
        <button type="button" className="userio-new-folder" onClick={() => p.onEditFolder()}><FolderPlus className="size-4" />Создать папку</button>
        {activeFolder && <p className="px-3 text-xs text-muted-foreground">Переписки из разных каналов в одной папке</p>}
      </div>
    </aside>
  </>
}

export function InboxFolderTabs({ state, onSelect, onEdit }: { state: FolderState; onSelect: (id: string) => void; onEdit: (folder?: InboxFolder) => void }) {
  const custom = state.folders.find(f => f.id === state.active)
  return <div className="userio-folder-tabs" aria-label="Фильтры сообщений">
    {STANDARD.map(f => <button type="button" key={f.id} data-folder={f.id} aria-label={f.name} aria-pressed={state.active === f.id} onClick={() => onSelect(f.id)}>{f.short}</button>)}
    {custom && <button type="button" aria-pressed="true" className="max-w-full" title={custom.name} onClick={() => onEdit(custom)}><Folder className="size-3.5 shrink-0" /><span className="min-w-0 truncate">{custom.name}</span></button>}
    <button type="button" aria-label="Создать папку" onClick={() => onEdit()}><Plus className="size-4" /></button>
  </div>
}

export function InboxFolderEditor({ open, onOpenChange, folder, state, loadChats }: { open: boolean; onOpenChange: (open: boolean) => void; folder?: InboxFolder; state: FolderState; loadChats: (query: string, selected: string[]) => Promise<FolderChoice[]> }) {
  const [name, setName] = useState(''), [selected, setSelected] = useState<string[]>([]), [choices, setChoices] = useState<FolderChoice[]>([])
  const [filter, setFilter] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('')
  useEffect(() => {
    if (!open) return
    setName(folder?.name || ''); setSelected(folder?.conversations || []); setFilter(''); setError('')
  }, [open, folder])
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setBusy(true); setError('')
    const timer = window.setTimeout(() => {
      void loadChats(filter.trim(), folder?.conversations || []).then(items => { if (!cancelled) setChoices(items) }).catch(() => { if (!cancelled) setError('Не удалось загрузить переписки. Попробуйте ещё раз.') }).finally(() => { if (!cancelled) setBusy(false) })
    }, filter.trim() ? 250 : 0)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [open, folder, filter, loadChats])
  const save = () => {
    if (!name.trim()) { setError('Введите название папки.'); return }
    if (state.save({ id: folder?.id || crypto.randomUUID(), name: name.trim(), conversations: selected })) onOpenChange(false)
  }
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-50 bg-black/55 backdrop-blur-sm" /><Dialog.Content className="userio-folder-dialog">
    <header><div><Dialog.Title>{folder ? 'Изменить папку' : 'Новая папка'}</Dialog.Title><Dialog.Description>Выберите переписки из любых аккаунтов. Папка сохранится в этом браузере.</Dialog.Description></div><Dialog.Close asChild><Button variant="ghost" size="icon" aria-label="Закрыть папку"><X /></Button></Dialog.Close></header>
    <form onSubmit={e => { e.preventDefault(); save() }} className="flex min-h-0 flex-1 flex-col gap-4">
      <label className="space-y-2 text-sm"><span>Название</span><Input aria-label="Название папки" value={name} maxLength={60} onChange={e => setName(e.target.value)} placeholder="Например, Клиенты" autoFocus /></label>
      <label className="relative"><Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input className="pl-9" aria-label="Найти переписку для папки" placeholder="Найти переписку" value={filter} onChange={e => setFilter(e.target.value)} /></label>
      <p className="text-xs text-muted-foreground" role="status">{busy ? 'Загружаю переписки…' : `Выбрано: ${selected.length} из 100`}</p>
      <div className="userio-folder-choices">{!busy && choices.filter(c => `${c.name} ${c.source}`.toLocaleLowerCase().includes(filter.toLocaleLowerCase())).map(c => <label key={c.id}><input type="checkbox" value={c.id} checked={selected.includes(c.id)} disabled={!selected.includes(c.id) && selected.length >= 100} onChange={e => setSelected(ids => e.target.checked ? [...ids, c.id] : ids.filter(id => id !== c.id))} /><span><span className="block [overflow-wrap:anywhere]">{c.name}</span><span className="text-xs text-muted-foreground">{c.source}</span></span></label>)}{!busy && !choices.length && <p className="py-3 text-sm text-muted-foreground">Переписок пока нет. Можно сохранить пустую папку.</p>}</div>
      {(error || state.error) && <p className="text-sm text-destructive" role="alert">{error || state.error}</p>}
      <footer>{folder && <Button type="button" variant="ghost" className="mr-auto text-destructive" onClick={() => { if (state.remove(folder.id)) onOpenChange(false) }}><Trash2 />Удалить папку</Button>}<Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Отмена</Button><Button type="submit" disabled={busy}><CirclePlus />Сохранить</Button></footer>
    </form>
  </Dialog.Content></Dialog.Portal></Dialog.Root>
}
