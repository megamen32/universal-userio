// One read at a time; hidden tabs do not poll, and a cancelled tab cannot
// schedule another request when an earlier read finally completes.
export function startInboxPolling(refresh: () => Promise<unknown>, visible: () => boolean,
  schedule: (fn: () => void, ms: number) => ReturnType<typeof setTimeout> = setTimeout,
  cancel: (id: ReturnType<typeof setTimeout>) => void = clearTimeout,
  busy: {current: boolean} = {current: false}) {
  let stopped = false
  let timer: ReturnType<typeof setTimeout>
  const tick = async () => {
    if (!busy.current && visible()) {
      busy.current = true
      try { await refresh() } catch { /* next read retries, never a send */ }
      finally { busy.current = false }
    }
    if (!stopped) timer = schedule(() => { void tick() }, 5000)
  }
  timer = schedule(() => { void tick() }, 5000)
  return () => { stopped = true; cancel(timer) }
}
