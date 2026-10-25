import { Link, useSearchParams } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type EvidenceReport, type EvidenceCategory, type EvidenceSource, type Workspace } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading } from '@wenjin/ui';
import { LearningAnalysis } from './learning-insights';
import { EvidenceCalendar } from './evidence-visuals';
import { SourceDialog, type Trace } from './review';
import './evidence.css';
const sources:Record<string,string>={synthetic:'模拟数据',adult_test:'成人测试',real_student:'真实学生'};
const statuses:Record<string,string>={passed:'数值通过',failed:'需要复习',unverifiable:'尚未确定',skipped:'未完成'};
const sections=[['overview','总览'],['knowledge','知识与表现'],['support','复习与支持'],['competency','素养材料'],['records','学习记录']];
export function EvidencePage({workspace}:{workspace?:Workspace}){
 const [params,setParams]=useSearchParams(),[trace,setTrace]=useState<Trace>(),client=useQueryClient();
 const view=sections.some(([id])=>id===params.get('view'))?params.get('view')!:'overview';
 const scope=params.get('scope')==='global'||!workspace?'global':'workspace';
 const days=([14,30,90].includes(Number(params.get('days')))?Number(params.get('days')):30) as 14|30|90;
 const source=(['all','synthetic','adult_test','real_student'].includes(params.get('source')??'')?params.get('source'):'all') as EvidenceSource;
 const assistance=['all','unassisted_practice','assisted','independent'].includes(params.get('assistance')??'')?params.get('assistance')!:'all',offset=Math.max(0,Math.floor(Number(params.get('offset'))||0)),onDay=/^\d{4}-\d{2}-\d{2}$/.test(params.get('day')??'')?params.get('day')!:'',includeArchived=params.get('archived')!=='false';
 const panel=(view==='support'?['review','help']:['map','performance']).includes(params.get('panel')??'')?params.get('panel')!:(view==='support'?'review':'map');
 const wid=scope==='workspace'?workspace?.workspace_id:undefined;
 const activity=useQuery({queryKey:['next-action',workspace?.workspace_id],enabled:!!workspace,
   queryFn:({signal})=>api.nextAction(workspace!.workspace_id,signal),staleTime:0,
   refetchInterval:q=>q.state.data?.processing?750:false});
 useEffect(()=>{if(activity.data&&!activity.data.processing){
   void client.invalidateQueries({queryKey:['evidence']});
   void client.invalidateQueries({queryKey:['learning-insights']});
 }},[activity.dataUpdatedAt,activity.data?.processing,client]);
 function change(key:string,value:string){const p=new URLSearchParams(params);if(value)p.set(key,value);else p.delete(key);if(key!=='offset')p.delete('offset');if(key==='view')p.delete('panel');setParams(p);}
 const category=(['all','answer','checkpoint','hint','reflection','activity','self_report','human_judgment'].includes(params.get('category')??'')?params.get('category')!:'all') as EvidenceCategory;
 const query=useQuery({queryKey:['evidence',scope,wid,days,source,offset,onDay,includeArchived,assistance,category,view],enabled:['overview','records'].includes(view),queryFn:({signal})=>api.evidence(wid,days,source,view==='records'?category:'all',offset,signal,includeArchived,assistance,view==='records'?onDay:'')});
 const insights=useQuery({queryKey:['learning-insights',scope,wid,days,source,includeArchived,assistance],enabled:['knowledge','support','competency'].includes(view),queryFn:({signal})=>api.learningInsights(wid,days,source,includeArchived,signal,assistance)});
 const active=['overview','records'].includes(view)?query:insights;
 const sums=query.data?.groups.reduce((v,g)=>({facts:v.facts+g.facts,hints:v.hints+g.hints,reflections:v.reflections+g.reflections,checks:v.checks+g.checkpoint_answers}),{facts:0,hints:0,reflections:0,checks:0});
 return <><Heading kicker="GROWTH, WITH ITS SOURCES" title="成长" description="从概览到具体作品，找到你想看的学习依据。"/>
 <div className="growth-toolbar"><label className="field">查看范围<select value={scope} onChange={e=>change('scope',e.target.value)}><option value="workspace" disabled={!workspace}>当前空间局部</option><option value="global">全局宏观</option></select></label><label className="field">时间范围<select value={days} onChange={e=>change('days',e.target.value)}><option value="14">最近14天</option><option value="30">最近30天</option><option value="90">最近90天</option></select></label><label className="field">数据来源<select value={source} onChange={e=>change('source',e.target.value)}><option value="all">全部来源（分别统计）</option>{Object.entries(sources).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label></div>
 <p className="card-note">{scope==='workspace'?`仅查看「${workspace?.title}」；${workspace?.kind==='temporary'?'临时探索不进入全局。':''}`:'全部正式项目；排除临时会话。'} · 数量代表学习记录，不是能力得分。</p>
 <nav className="tabs growth-tabs" aria-label="成长内容分类">{sections.map(([id,label])=><button key={id} role="tab" id={'growth-tab-'+id} aria-selected={view===id} aria-controls="growth-panel" onClick={()=>change('view',id)}>{label}</button>)}</nav>
 <section id="growth-panel" role="tabpanel" aria-labelledby={'growth-tab-'+view}>
 {active.isPending?<Loading/>:active.isError?<ErrorNotice error={active.error} retry={()=>{void active.refetch();}}/>:<>
 {view==='overview'&&query.data&&<><div className="grid four growth-stats">{[['学习记录',sums?.facts??0],['独立检测提交',sums?.checks??0],['求助记录',sums?.hints??0],['我的反思',sums?.reflections??0]].map(([n,v])=><section className="card" key={n}><small>{n}</small><strong>{v}</strong></section>)}</div><div className="grid two section-gap">{query.data.groups.length?<Trend key={scope+wid+days+source} report={query.data}/>:<section className="card"><Empty title="还没有学习记录">从一次自己的尝试开始，记录会出现在这里。</Empty></section>}<GrowthNextStep workspace={workspace} returnTo={'/evidence?'+params.toString()}/></div></>}
 {['knowledge','support','competency'].includes(view)&&insights.data&&<>{view!=='competency'&&<div className="tabs" aria-label="分类内容">{(view==='knowledge'?[['map','知识覆盖'],['performance','作答表现']]:[['review','复习安排'],['help','帮助使用']]).map(([id,label])=><button key={id} aria-pressed={panel===id} onClick={()=>change('panel',id)}>{label}</button>)}</div>}<LearningAnalysis key={view+panel+wid+days+source} report={insights.data} trace={setTrace} workspaceId={wid} view={view} panel={panel}/></>}
 {view==='records'&&query.data&&<><label className="field evidence-record-filter">记录类型<select value={category} onChange={e=>change('category',e.target.value)}>{[['all','全部记录'],['answer','作答'],['hint','提示'],['reflection','反思'],['activity','互动'],['checkpoint','独立检测'],['self_report','我的自述'],['human_judgment','专业复核']].map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label><EvidenceCalendar report={query.data} selectedDay={onDay} onSelectDay={v=>change('day',v)}/><section className="card section-gap"><div className="card-head"><h2>{onDay?onDay+' · 学习记录':'学习记录'}</h2>{onDay&&<button className="button small" onClick={()=>change('day','')}>查看整个时间范围</button>}</div><p className="card-note">共{query.data.record_count}条 · 每条可回到原始表达。</p>{query.data.records.length?<ol className="evidence-records">{query.data.records.map(record=><li key={record.record_kind+record.record_id}><strong>{record.workspace_title}{record.archived?' · 已归档':''}</strong><p className="card-note">{new Date(record.created_at*1000).toLocaleString()} · {sources[record.source_kind]} · {record.status?statuses[record.status]??'学习过程':'学习过程'}</p><p>{record.excerpt}</p><button className="button small" onClick={()=>setTrace({wid:record.workspace_id,fid:record.fact_id,archived:record.archived})}>查看原始来源</button></li>)}</ol>:<Empty title="这个范围还没有记录">可以选择其他日期，或在学习活动中留下尝试。</Empty>}<div className="pagination"><button className="button" disabled={!offset} onClick={()=>change('offset',String(Math.max(0,offset-20)))}>上一页</button><span>第{Math.floor(offset/20)+1}页</span><button className="button" disabled={offset+20>=query.data.record_count} onClick={()=>change('offset',String(offset+20))}>下一页</button></div></section></>}
 </>}
 </section><details className="section-gap"><summary>高级筛选</summary><label className="inline"><input type="checkbox" checked={includeArchived} onChange={e=>change('archived',String(e.target.checked))}/>包含归档项目</label><label className="field">辅助情况<select value={assistance} onChange={e=>change('assistance',e.target.value)}><option value="all">全部</option><option value="unassisted_practice">无提示练习</option><option value="assisted">有辅助</option><option value="independent">首次曝光独立检测</option></select></label></details>
 {trace&&<SourceDialog key={trace.wid+trace.fid} trace={trace} close={()=>{setTrace(undefined);void client.invalidateQueries({queryKey:['evidence']});void client.invalidateQueries({queryKey:['learning-insights']});}}/>}
 </>;
}
function GrowthNextStep({workspace,returnTo}:{workspace?:Workspace;returnTo:string}){
 const next=useQuery({queryKey:['next-action',workspace?.workspace_id],enabled:!!workspace,queryFn:({signal})=>api.nextAction(workspace!.workspace_id,signal)});
 const due=useQuery({queryKey:['growth-due',workspace?.workspace_id],enabled:!!workspace,queryFn:({signal})=>api.reviewCards(workspace!.workspace_id,'due',0,signal)});
 function target(path:string){return '/'+path+'?workspace='+workspace!.workspace_id+'&growth_return='+encodeURIComponent(returnTo);}
 const resume=next.data?.stage==='resume';
 const hasDue=!!due.data?.items.length;
 return <section className="card section-gap">
   <h2>下一步，回到具体的问题</h2>
   {!workspace ? <Link className="button primary section-gap" to="/spaces">选择一个项目继续</Link>
     : next.isPending||due.isPending ? <Loading/>
     : next.isError||due.isError ? <ErrorNotice error={next.error??due.error} retry={()=>{void next.refetch();void due.refetch();}}/>
     : <>
       <p>以下建议针对当前所选项目「{workspace.title}」，依据实际任务和复习队列。</p>
       {workspace.status==='archived' ? <Link className="button" to="/spaces">恢复项目后继续</Link> : <>
         <p className="body-copy">{resume?next.data!.explanation:hasDue?`目前至少有 ${due.data!.items.length} 项到期复习，先从一项开始。`:next.data!.explanation}</p>
         <Link className="button primary" to={target(resume?next.data!.destination:hasDue?'review':next.data!.destination)}>{resume?'继续原任务':hasDue?'复习一项到期问题':next.data!.title}</Link>
         <Link className="button section-gap" to={target('planner')}>基于记录调整学习方式</Link>
       </>}
     </>}
 </section>;
}

function Trend({ report }: { report: EvidenceReport }) {
  const [source, setSource] = useState(report.groups[0].source_kind), [metric, setMetric] = useState<'answers' | 'hints' | 'checkpoint_answers'>('answers'), [selected, setSelected] = useState(report.days - 1);
  const metrics = { answers: '作答与原题复做', hints: '提示请求', checkpoint_answers: '首次曝光检测提交' };
  const rows = Array.from({ length: report.days }, (_, index) => {
    const day = new Date((report.starts_at + index * 86400 + 8 * 3600) * 1000).toISOString().slice(0, 10);
    return { day, value: report.daily.find(row => row.day === day && row.source_kind === source)?.[metric] ?? 0 };
  });
  const max = Math.max(1, ...rows.map(row => row.value));
  const x = (index: number) => 35 + index / Math.max(1, rows.length - 1) * 630, y = (value: number) => 180 - value / max * 140;
  return <section className="card section-gap"><h2>过程趋势 · 按天查看</h2><div className="evidence-filters"><label className="field">趋势来源<select value={source} onChange={e => setSource(e.target.value)}>{report.groups.map(group => <option key={group.source_kind} value={group.source_kind}>{sources[group.source_kind] ?? group.source_kind}</option>)}</select></label><label className="field">趋势指标<select value={metric} onChange={e => setMetric(e.target.value as typeof metric)}>{Object.entries(metrics).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label></div>
    <p className="card-note">这是记录次数的走势，不是能力变化。0 表示当日没有此类记录，不代表退步；不同题目和尝试不能直接作为学习增益比较。</p>
    <svg className="evidence-trend" viewBox="0 0 700 220" role="group" aria-label="每日过程次数趋势，选择圆点查看当天数量"><line x1="35" y1="180" x2="665" y2="180" stroke="#cbd3c8"/><text x="5" y="45" fontSize="12" fill="currentColor">{max} 次</text><text x="12" y="185" fontSize="12" fill="currentColor">0</text><polyline points={rows.map((row, index) => `${x(index)},${y(row.value)}`).join(' ')} fill="none" stroke="#4d725e" strokeWidth="2"/>{rows.map((row, index) => <circle key={row.day} cx={x(index)} cy={y(row.value)} r={selected === index ? 6 : 4} fill={selected === index ? '#a47c30' : '#4d725e'} tabIndex={0} role="button" aria-label={`${row.day} ${metrics[metric]} ${row.value} 次`} onClick={() => setSelected(index)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelected(index); } }}><title>{row.day}：{row.value} 次</title></circle>)}<text x="35" y="210" fontSize="12" fill="currentColor">{rows[0].day.slice(5)}</text><text x="665" y="210" textAnchor="end" fontSize="12" fill="currentColor">{rows.at(-1)!.day.slice(5)}</text></svg>
    <p role="status">{rows[selected].day} · {sources[source] ?? source} · {metrics[metric]} {rows[selected].value} 次</p><details><summary>查看趋势数据表</summary><div className="evidence-table-wrap"><table className="evidence-table"><caption>{sources[source] ?? source} · {metrics[metric]}（次数）</caption><thead><tr><th>日期</th><th>次数</th></tr></thead><tbody>{rows.map(row => <tr key={row.day}><td>{row.day}</td><td>{row.value}</td></tr>)}</tbody></table></div></details>
  </section>;
}
