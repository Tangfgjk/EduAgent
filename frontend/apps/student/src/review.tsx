import { useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { CalendarDays, History, ListChecks } from 'lucide-react';
import { api, type RecallResult, type ReviewCard, type SourceRef, type Workspace } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading, Modal } from '@wenjin/ui';
import './review.css';

export type Trace = { wid: string; fid: string; archived: boolean };
const outcomes: Record<string, string> = { passed: '数值通过', failed: '数值未通过', unverifiable: '不可验证', skipped: '未完成' };
const stages: Record<string, string> = { diagnostic: '初始诊断', practice: '练习', checkpoint: '独立检测', review: '原题无提示复做', roundtable: '圆桌讨论', teach: '我教 AI' };
const sources: Record<string, string> = { synthetic: '模拟数据', adult_test: '成人测试', real_student: '真实学生', human_judgment: '人工判断', student_self_report: '学生自述' };
const date = (value: number) => new Date(value * 1000).toLocaleString();

export function ReviewPage({ workspace }: { workspace: Workspace }) {
  const [tab, setTab] = useState<'cards' | 'unknowns' | 'memory'>('cards'), [trace, setTrace] = useState<Trace>();
  return <><Heading kicker="RETURN, WITH A NEW UNDERSTANDING" title="错题与复习" description="回到具体的尝试，保留不确定，也给理解一次新的机会。"/>
    <div className="notice"><History size={18}/>{workspace.kind === 'temporary' ? '这是临时空间的局部复习与记录，不并入全局正式项目记忆。' : '数值未通过、提示后答对和不可验证分开保留；一次复做通过不代表已经掌握。'}</div>
    {workspace.status === 'archived' && <p className="page-note">当前空间已归档，可查阅来源与记忆，恢复后再提交复做或补充说明。</p>}
    <div className="tabs" role="tablist" aria-label="复习与记忆">{([['cards','复习安排'],['unknowns','不可验证与复核'],['memory','学习记忆']] as const).map(([key,label]) => <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}</button>)}</div>
    {tab === 'cards' ? <Cards key={workspace.workspace_id} workspace={workspace} trace={setTrace}/> : tab === 'unknowns' ? <Unknowns key={workspace.workspace_id} workspace={workspace} trace={setTrace}/> : <Memories key={workspace.workspace_id} workspace={workspace} trace={setTrace}/>}
    {trace && <SourceDialog key={trace.wid + trace.fid} trace={trace} close={() => setTrace(undefined)}/>}
  </>;
}

function Cards({ workspace, trace }: { workspace: Workspace; trace: (value: Trace) => void }) {
  const client = useQueryClient(), [filter, setFilter] = useState<'all' | 'due' | 'scheduled'>('due'), [offset, setOffset] = useState(0), [selected, setSelected] = useState<ReviewCard>();
  const query = useQuery({ staleTime: 0, queryKey: ['review-cards', workspace.workspace_id, filter, offset], queryFn: ({ signal }) => api.reviewCards(workspace.workspace_id, filter, offset, signal) });
  function changed(result: RecallResult) {
    setSelected(result.card);
    void client.invalidateQueries({ queryKey: ['review-cards', workspace.workspace_id] });
    void client.invalidateQueries({ queryKey: ['memory'] });
    void client.invalidateQueries({ queryKey: ['unknowns', workspace.workspace_id] });
  }
  return <><div className="notice"><CalendarDays size={18}/>原题无提示复做，默认间隔 1 / 3 / 7 天，尚未做真实学生校准。提前练习会保存结果，但不会推进间隔；原题通过不等于迁移检验通过。</div><div className="filters section-gap" aria-label="复习筛选">{([['due','待复习'],['scheduled','已安排'],['all','全部记录']] as const).map(([key,label]) => <button key={key} className={filter === key ? 'active' : ''} onClick={() => { setFilter(key); setOffset(0); }}>{label}</button>)}</div>
    {query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/> : query.data.items.length ? <><p className="page-note">当前空间有 {query.data.due_count} 条到期复习。来源类别始终单独标注。</p><div className="grid two">{query.data.items.map(card => <section className="card align-start" key={card.card_id}><div className="card-head"><h2>{card.task.task_id}</h2><span className="badge neutral">{sources[card.source_kind] ?? card.source_kind}</span></div><p className="study-stem">{card.task.stem}</p><div className="review-reasons">{card.reasons.map(reason => <span className="badge neutral" key={reason}>{reason === 'numeric_error' ? '数值未通过' : reason === 'assisted_success' ? '提示后答对，待独立复做' : '人工复核建议回看'}</span>)}</div><p className="card-note">{card.due ? '现在可复习' : '下次建议时间：' + date(card.next_due_at)}</p><div className="actions section-gap"><button className="button primary" disabled={workspace.status === 'archived'} onClick={() => setSelected(card)}>无提示复做</button><button className="button" onClick={() => trace({ wid: workspace.workspace_id, fid: card.source_fact_ids[0], archived: workspace.status === 'archived' })}>查看原始尝试</button></div><details className="section-gap"><summary>查看最近复做记录</summary>{card.attempts.length ? <ol className="detail-list">{card.attempts.map(attempt => <li key={attempt.attempt_id}>{date(attempt.created_at)} · {outcomes[attempt.status]} · {attempt.counted_for_schedule ? '已安排下一次回访' : '本次未推进间隔'}<button className="button text" onClick={() => trace({ wid:workspace.workspace_id, fid:attempt.fact_id, archived:workspace.status==='archived' })}>查看来源</button></li>)}</ol> : <p className="card-note">尚无复做记录。</p>}</details></section>)}</div></> : <section className="card"><Empty title={filter === 'due' ? '当前没有到期复习' : '当前筛选没有记录'}>陪学中确实数值未通过或使用提示后答对的尝试会进入这里。不可验证提交单独放在复核页。</Empty><Link className="button" to={{ pathname:'/study', search:'?workspace='+workspace.workspace_id }}>前往津津陪学</Link></section>}
    <Pagination offset={offset} count={query.data?.items.length ?? 0} busy={query.isFetching} change={setOffset}/>
    {selected && <RecallDialog key={selected.card_id} card={selected} close={() => setSelected(undefined)} changed={changed} refresh={async () => setSelected(await api.reviewCard(workspace.workspace_id, selected.card_id))}/>}
  </>;
}

function RecallDialog({ card, close, changed, refresh }: { card: ReviewCard; close: () => void; changed: (result: RecallResult) => void; refresh: () => Promise<void> }) {
  const [answer,setAnswer]=useState(''), [reasoning,setReasoning]=useState(''), [result,setResult]=useState<RecallResult>();
  const intent=useRef({signature:'',key:''});
  const mutation=useMutation({mutationFn:()=>{
    const signature=JSON.stringify([card.revision,answer.trim(),reasoning]);
    if(intent.current.signature!==signature) intent.current={signature,key:crypto.randomUUID()};
    return api.recall(card,answer.trim(),reasoning,intent.current.key);
  },onSuccess:value=>{setResult(value);changed(value);}});
  function submit(event:FormEvent){event.preventDefault();if(answer.trim()&&!mutation.isPending) mutation.mutate();}
  return <Modal title="原题无提示复做" busy={mutation.isPending} onClose={close}><p className="dialog-copy">{card.task.stem}</p><p className="card-note">本次不提供提示，只验证数值；每次提交独立保存，原题回看不代表迁移能力。</p><form onSubmit={submit} className="section-gap"><label className="field">复做数值答案<input required maxLength={128} value={answer} onChange={e=>setAnswer(e.target.value)} disabled={mutation.isPending}/></label><label className="field">这一次的步骤与依据<textarea maxLength={1500} value={reasoning} onChange={e=>setReasoning(e.target.value)} disabled={mutation.isPending}/></label>{result&&<div className="notice" role="status"><div><strong>{outcomes[result.attempt.status]}</strong><p>{result.attempt.status==='unverifiable'?'这不代表答错，已进入人工复核，原安排保留。':result.attempt.status==='failed'?'数值未通过，已安排重新回看。':result.attempt.counted_for_schedule?'已保存结果并安排下一次回访。':'本次为提前复做，结果已保存，间隔保持不变。'}</p></div></div>}<ErrorNotice error={mutation.error}/>{mutation.isError&&<button type="button" className="button" onClick={()=>{void refresh();}}>刷新条目</button>}<div className="dialog-footer"><button type="button" className="button" disabled={mutation.isPending} onClick={close}>关闭</button><button className="button primary" disabled={!answer.trim()||mutation.isPending}>{mutation.isPending?'正在保存…':'提交无提示复做'}</button></div></form></Modal>;
}

function Unknowns({workspace,trace}:{workspace:Workspace;trace:(value:Trace)=>void}){
  const [offset,setOffset]=useState(0);
  const query=useQuery({staleTime:0,queryKey:['unknowns',workspace.workspace_id,offset],queryFn:({signal})=>api.unverifiable(workspace.workspace_id,offset,signal)});
  return <><div className="notice"><ListChecks size={18}/>这些提交无法自动确认，不直接当作错题。管理员的意见单独保留，不覆盖当时的自动验证记录。</div>{query.isPending?<Loading/>:query.isError?<ErrorNotice error={query.error} retry={()=>{void query.refetch();}}/>:query.data.items.length?<div className="grid two section-gap">{query.data.items.map(row=><section className="card align-start" key={row.review_id}><div className="card-head"><h2>{row.task_id}</h2><span className="badge neutral">{row.status==='pending'?'等待复核':'已有人工意见'}</span></div><p className="body-copy">原始提交：{row.answer}</p>{row.conclusion&&<p className="card-note">人工意见：{row.conclusion==='correct'?'数值正确':row.conclusion==='incorrect'?'数值错误':'仍无法判断'}。{row.note}</p>}{row.fact_id?<button className="button section-gap" onClick={()=>trace({wid:workspace.workspace_id,fid:row.fact_id!,archived:workspace.status==='archived'})}>查看来源与说明</button>:<p className="card-note">历史来源尚待核对。</p>}</section>)}</div>:<section className="card section-gap"><Empty title="当前没有不可验证提交">无法解析或超出数值验证范围的答案会保留在这里。</Empty></section>}<Pagination offset={offset} count={query.data?.items.length??0} busy={query.isFetching} change={setOffset}/></>;
}

function Memories({workspace,trace}:{workspace:Workspace;trace:(value:Trace)=>void}){
  const [scope,setScope]=useState<'project'|'global'>('project'),[offset,setOffset]=useState(0);
  const query=useQuery({staleTime:0,queryKey:['memory',scope,workspace.workspace_id,offset],queryFn:({signal})=>api.memory(scope==='project'?workspace.workspace_id:undefined,offset,signal)});
  return <><div className="tabs" role="tablist" aria-label="记忆范围"><button role="tab" aria-selected={scope==='project'} onClick={()=>{setScope('project');setOffset(0);}}>当前空间记忆</button><button role="tab" aria-selected={scope==='global'} onClick={()=>{setScope('global');setOffset(0);}}>全局正式项目</button></div><div className="notice">{scope==='global'?'汇集自己的正式项目，包含归档项目，排除临时空间；每个项目仍保留独立来源。':'只展示当前空间的记录。'} 摘要按提交次数描述，不推断掌握度、误区成因或素养。</div>
    {query.isPending?<Loading/>:query.isError?<ErrorNotice error={query.error} retry={()=>{void query.refetch();}}/>:query.data.items.length?<>{scope==='global'&&<p className="page-note">共 {query.data.project_count} 个正式项目，分页展示。</p>}{query.data.items.map(item=><section className="card section-gap" key={item.workspace_id}><div className="card-head"><h2>{item.title}</h2><span className="badge neutral">{item.status==='archived'?'已归档':item.kind==='temporary'?'临时局部':'正式项目'}</span></div>{item.digest?<><h3 className="section-gap">学习摘要</h3><div className="memory-rows">{item.digest.rows.map(row=><div key={row.source_kind+row.stage}><strong>{stages[row.stage]??row.stage}</strong><span className="badge neutral">{sources[row.source_kind]??row.source_kind}</span><p>数值通过 {row.passed} 次 · 未通过 {row.failed} 次 · 不可验证 {row.unverifiable} 次</p>{row.stage==='practice'&&<small>其中使用提示后通过 {row.hinted_passed} 次，未作为独立检测结果。</small>}</div>)}</div><p className="card-note">提示请求 {item.digest.hint_requests} 次 · 反思 {item.digest.reflections} 条 · 自述 {item.digest.self_reports} 条 · 人工判断 {item.digest.manual_judgments} 条</p><details className="section-gap"><summary>查看摘要来源</summary><SourceLinks refs={item.digest.source_refs} wid={item.workspace_id} archived={item.status==='archived'} trace={trace}/></details></>:<Empty title="尚无可追溯学习记录">在这个空间留下学习尝试后，再来看摘要。</Empty>}{item.observations&&<><h3 className="section-gap">观察记忆</h3>{item.observations.claims.map((claim,index)=><article className="memory-claim" key={index}><span className="badge neutral">{sources[claim.source_kind]??claim.source_kind}</span><p>{claim.text}</p><SourceLinks refs={claim.source_refs} wid={item.workspace_id} archived={item.status==='archived'} trace={trace}/></article>)}<p className="card-note">{item.observations.limitation}</p></>}</section>)}</>:<section className="card section-gap"><Empty title="还没有正式项目记忆">临时空间不会进入全局视图。</Empty></section>}
    {scope==='global'&&<Pagination offset={offset} count={query.data?.items.length??0} busy={query.isFetching} change={setOffset}/>}
  </>;
}

function SourceLinks({refs,wid,archived,trace}:{refs:SourceRef[];wid:string;archived:boolean;trace:(value:Trace)=>void}){
  return <div className="source-links">{refs.filter(ref=>ref.kind==='fact'&&ref.id).map((ref,index)=><button className="button small" key={ref.id} onClick={()=>trace({wid,fid:ref.id!,archived})}>来源 {index+1}</button>)}</div>;
}

export function SourceDialog({trace,close}:{trace:Trace;close:()=>void}){
  const client=useQueryClient(),[text,setText]=useState('');
  const intent=useRef({signature:'',key:''});
  const query=useQuery({staleTime:0,queryKey:['fact',trace.wid,trace.fid],queryFn:({signal})=>api.learningFact(trace.wid,trace.fid,signal)});
  const note=useMutation({mutationFn:()=>{if(intent.current.signature!==text.trim()) intent.current={signature:text.trim(),key:crypto.randomUUID()};return api.memoryNote(trace.wid,trace.fid,text.trim(),intent.current.key);},onSuccess:()=>{setText('');intent.current={signature:'',key:''};void query.refetch();void client.invalidateQueries({queryKey:['memory']});void client.invalidateQueries({queryKey:['evidence']});}});
  function submit(event:FormEvent){event.preventDefault();if(text.trim().length>=2&&!note.isPending)note.mutate();}
  return <Modal title="学习记录来源" busy={note.isPending} onClose={close}>{query.isPending?<Loading/>:query.isError?<ErrorNotice error={query.error} retry={()=>{void query.refetch();}}/>:<><span className="badge neutral">{sources[query.data.source_kind]??query.data.source_kind} · {stages[query.data.stage]??query.data.stage}</span><p className="dialog-copy section-gap">{query.data.task.stem}</p><dl className="definition-list"><div><dt>自动验证</dt><dd>{query.data.status?outcomes[query.data.status]:'过程记录'}</dd></div><div><dt>当时提示次数</dt><dd>{query.data.hints_used}</dd></div><div><dt>记录时间</dt><dd>{date(query.data.created_at)}</dd></div></dl>{query.data.activity_version&&<p className="notice">学生互动贡献 · 辅助后的核验不计作独立检测，解释质量尚未评定。</p>}{query.data.answer&&<p className="trace-text">我的答案：{query.data.answer}</p>}{query.data.reasoning&&<p className="trace-text">步骤与依据：{query.data.reasoning}</p>}{query.data.reflection&&<p className="trace-text">反思：{query.data.reflection}</p>}{query.data.revealed_hint&&<p className="trace-text">当时开放的提示：{query.data.revealed_hint}</p>}{query.data.judgments.map(row=><p className="trace-text" key={row.judgment_id}>人工判断：{row.conclusion==='correct'?'数值正确':row.conclusion==='incorrect'?'数值错误':'仍无法判断'}。{row.note}</p>)}{query.data.notes.map(row=><p className="trace-text" key={row.note_id}>学生自述：{row.text}</p>)}<form className="section-gap" onSubmit={submit}><label className="field">补充或纠正说明（学生自述）<textarea maxLength={1000} minLength={2} required value={text} onChange={event=>setText(event.target.value)} disabled={note.isPending||trace.archived}/><small>说明会单独保留，不覆盖原始提交或自动判断。</small></label><ErrorNotice error={note.error}/><button className="button primary" disabled={note.isPending||trace.archived||text.trim().length<2}>保存我的说明</button></form></>}</Modal>;
}

function Pagination({offset,count,busy,change}:{offset:number;count:number;busy:boolean;change:(offset:number)=>void}){
  return <div className="pagination section-gap"><button className="button" disabled={offset===0||busy} onClick={()=>change(Math.max(0,offset-20))}>上一页</button><span>第 {offset/20+1} 页</span><button className="button" disabled={busy||count<20} onClick={()=>change(offset+20)}>下一页</button></div>;
}
