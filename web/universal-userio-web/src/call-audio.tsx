import { useRef, useState } from 'react'

export type CallRecording = { complete: boolean; duration: number; tracks: { name: string; label: string; url: string; duration: number; transcript?: string }[] }

export function CallAudio({ recording }: { recording: CallRecording }) {
  const players = useRef<(HTMLAudioElement | null)[]>([])
  const [error, setError] = useState('')
  const playTogether = async () => {
    const audio = players.current.filter((item): item is HTMLAudioElement => !!item)
    audio.forEach(item => { item.currentTime = 0 })
    try {
      await Promise.all(audio.map(item => item.play()))
      setError('')
    } catch {
      audio.forEach(item => item.pause())
      setError('Не удалось загрузить запись. Обновите страницу и попробуйте снова.')
    }
  }
  return <section aria-label="Запись звонка" className="mx-auto my-2 w-full max-w-md rounded-xl border bg-card p-3 text-left text-sm">
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
      <strong>Запись звонка · 2 дорожки</strong>
      <button type="button" className="text-xs underline" onClick={() => void playTogether()}>Слушать обе с начала</button>
    </div>
    {!recording.complete && <p className="mb-2 text-xs text-amber-600">Запись неполная — часть разговора могла не сохраниться.</p>}
    {recording.tracks.map((track, index) => <div key={track.name} className="mb-2 last:mb-0">
      <div className="mb-1 flex items-center justify-between text-xs"><span>{track.label}</span><a href={track.url} download={`${track.name}.wav`} className="underline">Скачать WAV</a></div>
      <audio ref={element => { players.current[index] = element }} aria-label={`Аудио: ${track.label}`} controls preload="none" src={track.url} className="h-9 w-full" onError={() => setError('Не удалось загрузить запись. Обновите страницу и попробуйте снова.')} />
      {track.transcript && <p aria-label={`Транскрипция: ${track.label}`} className="mt-2 whitespace-pre-wrap text-sm">{track.transcript}</p>}
    </div>)}
    {error && <p role="alert" className="mt-2 text-xs text-red-600">{error}</p>}
    <p className="mt-2 text-[11px] text-muted-foreground">Одна шкала времени. Каждая дорожка содержит звук одного участника.</p>
  </section>
}
