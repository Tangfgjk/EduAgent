'use client'
import ClassroomPlayer, { type Timeline, type Playback } from './ClassroomPlayer'
export { parseCaptions } from '@/lib/classroom-captions'
import LinearSimulation from './LinearSimulation'
import { useEffect, useState, useCallback, useRef } from 'react'
import { projectRequest, projectEndpoint } from '@/lib/learning-project-api'
import { apiUrl, requestBlob } from '@/lib/api'
type State = {
  prediction?: string
  choices?: number[]
  explanation?: string
  result?: { correct: boolean; explanation: string }[]
}
type Asset = {
  key: string
  kind: string
  index: number
  state: string
  error?: string
  files: string[]
  interaction: State
  range?: 'page' | 'chapter' | 'course'
  indices?: number[]
  engine?: string
  rate?: number
  page_count?: number
  partial?: boolean
  duration?: number
  progress?: { stage: string; completed: number; total: number }
  timeline?: Timeline[]
  playback?: Playback
  spec?: {
    type?: string
    a?: number
    b?: number
    c?: number
    title: string
    instructions: string
    categories: string[]
    items: { label: string }[]
  }
}
const button = 'rounded-xl border border-border px-3 py-2 text-sm disabled:opacity-40'
export default function ClassroomMedia({
  pathId,
  cid,
  index,
  ready,
  navigate,
  tutor,
}: {
  pathId: string
  cid: string
  index: number
  ready: boolean
  navigate?: (index: number) => void
  tutor?: (index: number) => void
}) {
  const base = projectEndpoint(pathId) + '/classrooms/' + cid + '/assets'
  const [assets, setAssets] = useState<Asset[]>([]),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [text, setText] = useState(''),
    [choices, setChoices] = useState<number[]>([]),
    [notice, setNotice] = useState(''),
    [downloading, setDownloading] = useState(false),
    [exportKey, setExportKey] = useState<string | null>(null)
  const [showHistory,setShowHistory] = useState(false)
  const awaitingExport = useRef<string | null>(null)
  const [range, setRange] = useState('page'), [engine, setEngine] = useState('local'), [speechRate, setSpeechRate] = useState(1)
  const [capabilities, setCapabilities] = useState({ local: false, configured: false, local_label: '本地中文语音' })
  useEffect(() => {
    void projectRequest<{local:boolean;configured:boolean;local_label?:string}>('/classroom-media/capabilities').then(value=>{
      if(typeof value.local==='boolean'){setCapabilities({...value,local_label:value.local_label || '本地中文语音'});if(!value.local && value.configured)setEngine('configured')}
    }).catch(()=>{})
  }, [])
  async function resourceAction(a: Asset, action: string) {
    setError('');setBusy(true)
    try { await projectRequest(base+'/'+a.key+'/control', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});await load() }
    catch(e){setError(e instanceof Error?e.message:'操作失败，请重试')}
    finally{setBusy(false)}
  }
  const url = useCallback((a: Asset, name: string) =>
    apiUrl('/api/mastery-paths' + base + '/' + a.key + '/files/' + encodeURIComponent(name)), [base])
  const download = useCallback(async (a: Asset, name: string) => {
    setDownloading(true)
    setError('')
    setNotice(`正在下载 ${name}…`)
    try {
      const blob = await requestBlob(url(a, name))
      if (!blob.size || /text\/html/i.test(blob.type) || (/application\/json/i.test(blob.type) && !name.endsWith('.json')))
        throw new Error('下载内容无效，请重新尝试')
      const href = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = href
      link.download = name
      document.body.appendChild(link)
      link.click()
      link.remove()
      // Keep the URL alive until the browser has accepted the download.
      window.setTimeout(() => URL.revokeObjectURL(href), 60_000)
      setNotice(`${name} 已开始下载；若浏览器拦截，可点击下方下载链接重试。`)
    } catch (e) {
      setNotice('')
      setError(e instanceof Error ? e.message : '下载失败，请重试')
    } finally {
      setDownloading(false)
    }
  }, [url])
  const load = useCallback(async () => setAssets(await projectRequest<Asset[]>(base)), [base])
  useEffect(() => {
    void load().catch(() => setError('资源状态加载失败，请刷新'))
  }, [load])
  const pending = !!exportKey || assets.some(a => a.state === 'queued' || a.state === 'running')
  useEffect(() => {
    if (!pending) return
    const timer = setInterval(() => void load().catch(() => {}), 2500)
    return () => clearInterval(timer)
  }, [pending, load])
  useEffect(() => {
    setText('')
    setChoices([])
  }, [index, cid])
  useEffect(() => {
    if (!exportKey || awaitingExport.current !== exportKey) return
    const asset = assets.find(a => a.key === exportKey)
    if (!asset || !['ready', 'failed'].includes(asset.state)) return
    awaitingExport.current = null
    setExportKey(null)
    if (asset.state === 'failed') {
      setNotice('')
      setError(asset.error || '导出生成失败，请重试资源')
    } else {
      const name = 'lesson.' + asset.kind
      if (asset.files.includes(name)) void download(asset, name)
      else { setNotice(''); setError('导出文件不存在，请重试') }
    }
  }, [assets, exportKey, download])
  async function generate(kind: string) {
    setBusy(true)
    setError('')
    try {
      const asset = await projectRequest<Asset>(base, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, index, ...(["voice","video"].includes(kind) ? {range,engine,rate:speechRate} : {}) }),
      })
      if (['pdf', 'pptx'].includes(kind)) {
        if (asset.state === 'ready') {
          const name = 'lesson.' + kind
          if (!asset.files.includes(name)) throw new Error('导出文件不存在，请重试')
          await download(asset, name)
        } else if (asset.state === 'failed') {
          throw new Error(asset.error || '导出生成失败，请重试')
        } else {
          awaitingExport.current = asset.key
          setExportKey(asset.key)
          setNotice('正在生成导出文件，完成后将自动下载。离开本页后可从资源列表下载。')
        }
      }
      await load()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  async function act(a: Asset, phase: string, override: Record<string, unknown> = {}) {
    setBusy(true)
    setError('')
    try {
      await projectRequest(base + '/' + a.key + '/interact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phase, text, choices, ...override }),
      })
      setText('')
      await load()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section id="classroom-resources" aria-label="课堂音视频与导出" className="my-5 scroll-mt-6 rounded-2xl border border-border p-4">
      <h3 className="font-semibold">课堂资源</h3>
      <p className="my-2 text-sm text-muted-foreground">按需准备配音或讲解视频，也可导出课件。音视频共用本项目的课件与配音；刷新后仍可查看任务。</p>
      <div className="my-4 rounded-xl bg-muted/40 p-3">
        <div className="mb-3 flex flex-wrap gap-4 text-sm">
          <label>生成范围 <select aria-label="音视频生成范围" value={range} onChange={e=>setRange(e.target.value)} className="rounded-lg border border-border bg-background p-2">
            <option value="page">当前页</option><option value="chapter">当前章节已准备页面</option><option value="course" disabled={!ready}>完整课堂{!ready?'（等待课件完成）':''}</option>
          </select></label>
          <label>配音来源 <select aria-label="配音来源" value={engine} onChange={e=>setEngine(e.target.value)} className="rounded-lg border border-border bg-background p-2">
            <option value="local" disabled={!capabilities.local}>{capabilities.local_label}</option><option value="configured" disabled={!capabilities.configured}>我的语音模型</option>
          </select></label>
          <label>配音语速 <select aria-label="配音语速" value={speechRate} onChange={e=>setSpeechRate(Number(e.target.value))} className="rounded-lg border border-border bg-background p-2">
            {[.75,1,1.25,1.5].map(v=><option key={v} value={v}>{v}×</option>)}
          </select></label>
        </div>
        <div className="flex flex-wrap gap-2">
          {([['voice','生成配音'],['video','生成讲解视频']] as const).map(([kind,label])=><button key={kind} className={button}
            disabled={busy || (range==='course'&&!ready) || (engine==='local'?!capabilities.local:!capabilities.configured)} onClick={()=>void generate(kind)}>{label}</button>)}
        </div>
        <p className="mt-3 text-xs text-muted-foreground">{capabilities.local ? `${capabilities.local_label}无需配置密钥。` : '本地语音尚未就绪，可先配置个人语音模型。'}“我的语音模型”沿用你的语音设置。视频包含PPT画面、讲解声音与字幕，完成后可播放和下载。</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {([['pdf','导出PDF'],['pptx','导出PPTX']] as const).map(([kind,label])=><button key={kind} className={button} disabled={busy||downloading||!ready||!!exportKey} onClick={()=>void generate(kind)}>{label}</button>)}
      </div>
      {!ready && <p className="mt-2 text-xs text-muted-foreground">先为已准备的页面生成配音/视频；完整课堂导出需等待全部课件完成。</p>}
      <details className="my-3" open={assets.some(a=>a.spec && a.index===index)}><summary className="cursor-pointer text-sm">互动实验（可选）</summary><div className="mt-2 flex flex-wrap gap-2">
        <button className={button} disabled={busy} onClick={()=>void generate('simulation')}>生成分类模拟</button>
        <button className={button} disabled={busy} onClick={()=>void generate('linear')}>方程坐标实验</button>
      </div></details>
      {notice && <p role="status" className="mt-3 text-sm text-primary">{notice}</p>}
      {error && (
        <p role="alert" className="mt-3 text-destructive">
          {error}
        </p>
      )}
      {!!assets.length && <button className="mt-3 text-xs underline" onClick={()=>setShowHistory(v=>!v)}>{showHistory?'收起历史资源':'显示历史资源'}</button>}
      {assets
        .filter((a,i,all)=>showHistory || all.findIndex(b=>b.kind===a.kind && b.range===a.range && b.engine===a.engine && b.rate===a.rate && JSON.stringify(b.indices || [b.index])===JSON.stringify(a.indices || [a.index]))===i)
        .filter(a => ['simulation','linear'].includes(a.kind) ? a.index===index : ['voice','video'].includes(a.kind) && a.range!=='course' ? (a.indices || [a.index]).includes(index) : true)
        .map(a => (
          <article key={a.key} className="mt-4 rounded-xl bg-muted/40 p-3">
            <p className="text-sm">
              {
                (
                  {
                    voice: '课堂配音',
                    pdf: 'PDF课件',
                    pptx: 'PPTX课件',
                    simulation: '分类模拟',
                    linear: '方程实验',
                    video: '讲解视频',
                  } as Record<string, string>
                )[a.kind]
              }{' '}
              ·{' '}
              {
                (
                  {
                    queued: '排队中',
                    running: '生成中，可离开后回来',
                    ready: '已就绪',
                    failed: '生成失败',
                    cancelled: '已取消，可继续',
                  } as Record<string, string>
                )[a.state]
              }
            </p>
            {a.page_count ? <p className="mt-1 text-xs text-muted-foreground">{{page:'本页',chapter:'本章节',course:'全课'}[a.range || 'course']} · {a.page_count} 页{a.partial?'（提交时已准备的页面快照）':''}{a.duration?` · ${Math.round(a.duration/60)} 分钟`:''}{['voice','video'].includes(a.kind)?` · ${a.engine==='configured'?'我的语音模型':'本地中文语音'} · 配音 ${a.rate || 1}×`:''}</p> : null}
            {a.progress && ['queued','running'].includes(a.state) && <div className="my-3 text-sm">
              <p>{a.progress.stage} · 已处理 {a.progress.completed}/{a.progress.total} 页</p>
              <progress className="mt-2 h-2 w-full accent-primary" value={a.progress.completed} max={a.progress.total || 1} aria-label="资源生成进度" />
            </div>}
            {a.error && <p className="my-2 text-sm text-destructive">{a.error}</p>}
            {['queued','running'].includes(a.state) && <button disabled={busy} className={button} onClick={()=>void resourceAction(a,'cancel')}>取消此任务</button>}
            {['failed','cancelled'].includes(a.state) && <button disabled={busy} className={button} onClick={()=>void resourceAction(a,'retry')}>继续生成（复用已完成页面）</button>}
            {a.state === 'ready' && (
              <>
                {['voice','video'].includes(a.kind) && <ClassroomPlayer key={a.key} kind={a.kind}
                  source={url(a,a.kind==='video'?'lesson.mp4':'narration.wav')} subtitles={url(a,'subtitles.vtt')}
                  timeline={a.timeline || []} playback={a.playback || {}} navigate={navigate} tutor={tutor}
                  save={(seconds,rate)=>projectRequest(base+'/'+a.key+'/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'playback',seconds,playback_rate:rate})}).then(()=>{})} />}
                <div className="my-2 flex flex-wrap gap-3">
                  {a.files.filter(name => name !== 'timeline.json').map(name => (
                    <a key={name} href={url(a, name)} download={name} className="text-sm underline"
                      aria-disabled={downloading}
                      onClick={event => {
                        if (name.endsWith('.mp4') || name.endsWith('.wav')) return
                        event.preventDefault()
                        if (!downloading) void download(a, name)
                      }}>
                      {({'lesson.pdf':'下载PDF','lesson.pptx':'下载PPTX','lesson.mp4':'下载视频','narration.wav':'下载配音','subtitles.vtt':'下载字幕','script.txt':'下载讲稿'} as Record<string,string>)[name] || `下载 ${name}`}
                    </a>
                  ))}
                </div>
                {a.spec?.type === 'linear' && (
                  <LinearSimulation
                    key={a.key}
                    spec={a.spec as { a: number; b: number; c: number; title: string }}
                    state={a.interaction}
                    busy={busy}
                    submit={(phase, text, choices) => void act(a, phase, { text, choices })}
                  />
                )}
                {a.spec && a.spec.type !== 'linear' && (
                  <div className="space-y-3">
                    <h4 className="font-semibold">{a.spec.title}</h4>
                    <p className="text-sm">{a.spec.instructions}</p>
                    <p className="text-xs">
                      预测 → 操作 → 解释。这里只记录学习过程，不直接认定掌握。
                    </p>
                    {!a.interaction.prediction ? (
                      <>
                        <label className="block">
                          先预测：你打算按什么规则分类？
                          <textarea
                            aria-label="模拟预测"
                            className="mt-2 w-full rounded border border-border bg-background p-2"
                            value={text}
                            onChange={e => setText(e.target.value)}
                            maxLength={3000}
                          />
                        </label>
                        <button
                          disabled={busy || !text.trim()}
                          className={button}
                          onClick={() => void act(a, 'predict')}
                        >
                          保存预测，开始操作
                        </button>
                      </>
                    ) : !a.interaction.choices ? (
                      <>
                        <p className="text-sm">你的预测：{a.interaction.prediction}</p>
                        {a.spec.items.map((item, i) => (
                          <label key={i} className="block text-sm">
                            {item.label}
                            <select
                              aria-label={'分类' + (i + 1)}
                              className="ml-2 rounded border border-border bg-background p-2"
                              value={choices[i] ?? -1}
                              onChange={e =>
                                setChoices(old =>
                                  Array.from({ length: a.spec!.items.length }, (_, n) =>
                                    n === i ? Number(e.target.value) : (old[n] ?? -1)
                                  )
                                )
                              }
                            >
                              <option value={-1}>请选择类别</option>
                              {a.spec!.categories.map((cat, n) => (
                                <option key={n} value={n}>
                                  {cat}
                                </option>
                              ))}
                            </select>
                          </label>
                        ))}
                        <button
                          disabled={
                            busy ||
                            choices.length !== a.spec.items.length ||
                            choices.some(n => n < 0)
                          }
                          className={button}
                          onClick={() => void act(a, 'operate')}
                        >
                          提交操作，查看结果
                        </button>
                      </>
                    ) : (
                      <>
                        {a.interaction.result?.map((r, i) => (
                          <p key={i} className="text-sm">
                            {a.spec!.items[i].label}：
                            {r.correct ? '分类符合参考规则' : '与参考规则不同'}。{r.explanation}
                          </p>
                        ))}
                        {a.interaction.explanation ? (
                          <p className="rounded bg-muted p-3">
                            你的解释：{a.interaction.explanation}
                          </p>
                        ) : (
                          <>
                            <label className="block">
                              解释：结果与预测有什么不同？
                              <textarea
                                aria-label="模拟解释"
                                className="mt-2 w-full rounded border border-border bg-background p-2"
                                value={text}
                                maxLength={3000}
                                onChange={e => setText(e.target.value)}
                              />
                            </label>
                            <button
                              className={button}
                              disabled={busy || !text.trim()}
                              onClick={() => void act(a, 'explain')}
                            >
                              保存解释与反思
                            </button>
                          </>
                        )}
                      </>
                    )}
                  </div>
                )}
              </>
            )}
          </article>
        ))}
    </section>
  )
}
