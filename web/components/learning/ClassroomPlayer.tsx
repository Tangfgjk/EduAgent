"use client"
import { useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { parseCaptions } from '@/lib/classroom-captions'
export type Timeline = { page_id: string; index: number; title: string; start: number; end: number }
export type Playback = { seconds?: number; rate?: number }
const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`
export default function ClassroomPlayer({ kind, source, subtitles, timeline, playback, save, navigate, tutor }: {
  kind: string; source: string; subtitles: string; timeline: Timeline[]; playback: Playback
  save: (seconds: number, rate: number) => Promise<void>
  navigate?: (index: number) => void; tutor?: (index: number) => void
}) {
  const media = useRef<HTMLMediaElement | null>(null)
  const latest = useRef({ seconds: playback.seconds || 0, rate: playback.rate || 1 })
  const persist = useRef(save); persist.current = save
  const lastSaved = useRef(0)
  const restored = useRef(false)
  const [time, setTime] = useState(0), [rate, setRate] = useState(playback.rate || 1)
  const [cues, setCues] = useState<ReturnType<typeof parseCaptions>>([])
  const [error, setError] = useState('')
  const record = () => { void persist.current(latest.current.seconds, latest.current.rate).catch(() => setError('播放位置暂未保存，请检查连接')) }
  useEffect(() => {
    let alive = true
    void apiFetch(subtitles).then(async r => { if (!r.ok) throw new Error(); const value=parseCaptions(await r.text()); if(alive)setCues(value) }).catch(() => {if(alive)setError('字幕加载失败，可重载页面或下载字幕')})
    return () => { alive=false }
  }, [subtitles])
  useEffect(() => {
    const onHide = () => {if(document.visibilityState==='hidden')record()}
    document.addEventListener('visibilitychange',onHide)
    return () => { document.removeEventListener('visibilitychange',onHide); void persist.current(latest.current.seconds,latest.current.rate).catch(()=>{}) }
  // Persist on unmount, using the latest callback and position.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const current = timeline.find(p => p.start <= time && time < p.end) || timeline[0]
  const common = {
    controls: true, preload: 'metadata' as const, src: source,
    onLoadedMetadata: (event: React.SyntheticEvent<HTMLMediaElement>) => {
      const player=event.currentTarget
      if(!restored.current){player.currentTime=Math.min(latest.current.seconds,Math.max(0,player.duration-0.2));player.playbackRate=rate;restored.current=true}
    },
    onTimeUpdate: (event: React.SyntheticEvent<HTMLMediaElement>) => {
      const player=event.currentTarget; setTime(player.currentTime)
      latest.current={seconds:player.currentTime,rate:player.playbackRate}
      if(Date.now()-lastSaved.current>5000){lastSaved.current=Date.now();record()}
    },
    onPause: (event: React.SyntheticEvent<HTMLMediaElement>) => { latest.current={seconds:event.currentTarget.currentTime,rate:event.currentTarget.playbackRate}; record() },
    onSeeked: (event: React.SyntheticEvent<HTMLMediaElement>) => { latest.current={seconds:event.currentTarget.currentTime,rate:event.currentTarget.playbackRate}; record() },
    onEnded: (event: React.SyntheticEvent<HTMLMediaElement>) => { latest.current={seconds:event.currentTarget.currentTime,rate:event.currentTarget.playbackRate}; record() },
    onRateChange: (event: React.SyntheticEvent<HTMLMediaElement>) => { setRate(event.currentTarget.playbackRate); latest.current={seconds:event.currentTarget.currentTime,rate:event.currentTarget.playbackRate}; record() },
    onError: () => setError('媒体暂时无法播放，请重试或下载文件播放'),
  }
  return <div className="mt-3 space-y-3">
    {kind==='video' ? <video ref={node=>{media.current=node}} {...common} className="w-full rounded-xl">
      <track kind="captions" src={subtitles} srcLang="zh" label="中文" default />
    </video> : <audio ref={node=>{media.current=node}} {...common} className="w-full" />}
    <p className="min-h-12 rounded-xl bg-background p-3 text-sm" aria-label="同步字幕">{cues.find(c=>c.start<=time&&time<c.end)?.text || '播放后显示同步字幕'}</p>
    <div className="flex flex-wrap items-center gap-3 text-sm">
      <label>播放速度 <select aria-label="播放速度" value={rate} onChange={e=>{const value=Number(e.target.value);setRate(value);latest.current.rate=value;if(media.current)media.current.playbackRate=value;record()}} className="rounded-lg border border-border bg-background p-1">
        {[.75,1,1.25,1.5,2].map(v=><option key={v} value={v}>{v}×</option>)}
      </select></label>
      {playback.seconds ? <span className="text-xs text-muted-foreground">接续上次 {clock(playback.seconds)}</span> : null}
      {current && navigate && <button className="underline" onClick={()=>navigate(current.index)}>查看当前讲解页</button>}
      {current && tutor && <button className="underline" onClick={()=>{media.current?.pause();tutor(current.index)}}>暂停并向津津提问</button>}
    </div>
    {timeline.length>1 && <details><summary className="cursor-pointer text-sm">章节时间轴 · {timeline.length} 页</summary><div className="mt-2 max-h-48 space-y-1 overflow-auto">
      {timeline.map(p=><button key={p.page_id} className={'block w-full rounded-lg px-3 py-2 text-left text-sm '+(current?.page_id===p.page_id?'bg-primary/10 text-primary':'hover:bg-background')} onClick={()=>{if(media.current){media.current.currentTime=p.start;setTime(p.start)}}}>{clock(p.start)} · {p.title}</button>)}
    </div></details>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <p className="text-xs text-muted-foreground">播放与观看记录用于接续学习，不直接计为掌握。</p>
  </div>
}
