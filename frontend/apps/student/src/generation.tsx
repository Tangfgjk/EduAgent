import {MediaBudgets} from '@wenjin/ui';
import { ClassroomPlayer, Formula } from './classroom-deferred';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, type Workspace } from '@wenjin/api-client';
import { Heading, ErrorNotice, Loading, Empty, Success } from '@wenjin/ui';
import { useCourse } from './course';
import { CourseOverview } from './course-overview';

const rationalGoals: Record<string,string> = { number_line: '数轴位置与移动', opposite: '相反数', absolute: '绝对值与距离', comparison: '大小比较', add_subtract: '加减法', multiply_divide: '乘除法', mixed_order: '混合运算顺序' };

type Build = Awaited<ReturnType<typeof api.startCourseBuild>>;
const names: Record<string, string> = { semantic_review:'系统正在检查课程解释与条件', diagnostic: '先尝试诊断', confirm_plan: '请确认课程范围', queued: '已进入生成队列', generating: '正在生成', pending_review: '数学检查通过 · 内容待开发审核', ready: '课程已发布 · 等待你的选择', failed: '本次生成未完成', unknown: '调用结果未知 · 不自动重发', needs_clarification: '主题需要澄清', out_of_scope: '超出当前学科范围', source_missing: '缺少可用知识依据', cancelled: '已取消', rejected: '审核退回', source_revoked: '来源已撤回', source_conflict: '知识来源冲突 · 等待核对' };

export function GenerationPage({ workspace }: { workspace: Workspace }) {
  const [agentMode, setAgentMode] = useState(true), [goal, setGoal] = useState(''), [goals, setGoals] = useState<string[]>([]);
  const [topic, setTopic] = useState(''), [selected, setSelected] = useState<string>(), [offset, setOffset] = useState(0);
  const key = useRef(crypto.randomUUID()), client = useQueryClient(), course = useCourse(workspace.workspace_id);
  const query = useQuery({ queryKey: ['course-builds', workspace.workspace_id, offset], queryFn: ({ signal }) => api.courseBuilds(workspace.workspace_id, offset, signal),
    refetchInterval: q => q.state.data?.items.some(row => ['queued', 'generating', 'semantic_review', 'pending_review'].includes(row.status)) ? 1500 : false });
  const start = useMutation({ mutationFn: () => api.startCourseBuild(workspace.workspace_id, topic.trim(), key.current, { agent_mode: agentMode ? 'rational_agent_v2' : 'bounded_v1', learning_goal: goal.trim(), target_concepts: goals }), onSuccess: row => {
    key.current = crypto.randomUUID(); setSelected(row.build_id); setOffset(0); void query.refetch();
  } });
  function update() { void client.invalidateQueries({ queryKey: ['course-builds', workspace.workspace_id] }); }
  const row = query.data?.items.find(item => item.build_id === selected) ?? query.data?.items[0];
  return <><Heading kicker="A TOPIC BECOMES A LEARNING PATH" title="主题与课程" description="从一个想弄懂的问题开始，先看依据、尝试诊断，再确认范围与学习课程。"/>
    <CourseOverview workspace={workspace}/>
    <ResourcePanel workspace={workspace}/>
    <section className="card section-gap"><h2>准备新的主题课程</h2><form onSubmit={(e: FormEvent) => { e.preventDefault(); start.mutate(); }} className="form-stack"><label className="field">我想学什么？<textarea rows={3} required minLength={2} maxLength={600} value={topic} onChange={e => setTopic(e.target.value)} placeholder="例如：负数乘除和运算顺序，想说明每一步依据"/></label><p className="card-note">限初中数学。有理数可由教学Agent设计多概念任务和活动；其他主题仍沿用当前受限题族。证明与解释质量需专业复核。准备新候选不自动替换当前课程。</p><label className="inline"><input type="checkbox" checked={agentMode} onChange={e => setAgentMode(e.target.checked)}/>启用有理数教学 Agent 设计（其他主题沿用受限生成）</label>{agentMode && <><label className="field">本轮具体想学会什么？<textarea maxLength={400} value={goal} onChange={e => setGoal(e.target.value)} placeholder="例如：能区分绝对值与相反数，并说明数轴依据"/></label><fieldset><legend>有理数本轮重点（最多3项，也可由具体主题识别）</legend>{Object.entries(rationalGoals).map(([id,label]) => <label className="inline" key={id}><input type="checkbox" checked={goals.includes(id)} disabled={!goals.includes(id) && goals.length>=3} onChange={e => setGoals(old => e.target.checked ? [...old,id] : old.filter(v=>v!==id))}/>{label}</label>)}</fieldset><p className="card-note">宽泛的“有理数”需要先选重点。任务数量、题型组合与概念问题由Agent提出，数学由独立编译器核验；Fake仅模拟链路。沿用课程4096输出tokens与既有调用上限。</p></>}<button className="button primary" disabled={workspace.status !== 'active' || start.isPending || topic.trim().length < 2}>解析主题并准备诊断</button><ErrorNotice error={start.error}/></form></section>
    {query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/> : row ? <BuildView key={row.build_id} row={row} workspace={workspace} courseRevision={course.data?.revision ?? 0} update={update}/> : <Empty title="还没有主题课程">提交主题后会显示来源、诊断与待确认步骤。</Empty>}
    {!!query.data?.items.length && <section className="card section-gap"><h2>这个空间的课程准备记录</h2><div className="actions">{query.data.items.map(item => <button className="button small" key={item.build_id} onClick={() => setSelected(item.build_id)}>{item.topic} · {names[item.status] ?? item.status}</button>)}</div><div className="actions section-gap"><button className="button small" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 20))}>上一批</button><button className="button small" disabled={query.data.items.length < 20} onClick={() => setOffset(offset + 20)}>下一批</button></div></section>}
  </>;
}

function BuildView({ row, workspace, courseRevision, update }: { row: Build; workspace: Workspace; courseRevision: number; update: () => void }) {
  const client = useQueryClient(), [answers, setAnswers] = useState(()=>row.diagnostics.map(()=>({ answer: '', reasoning: '' }))), [notice, setNotice] = useState('');
  const diagnosticIds=row.diagnostics.map(t=>String(t.id)).join('|');
  useEffect(()=>setAnswers(row.diagnostics.map(()=>({answer:'',reasoning:''}))),[diagnosticIds]);
  const key = useRef(crypto.randomUUID());
  const [focus, setFocus] = useState<'basis' | 'representation' | 'verification'>('basis');
  const plan = useMutation({ mutationFn: () => api.reviseCoursePlan(workspace.workspace_id, row, focus), onSuccess: update });
  const diagnostic = useMutation({ mutationFn: (finish:boolean) => api.diagnoseCourse(workspace.workspace_id, row, finish?[]:answers, finish), onSuccess: update });
  const generate = useMutation({ mutationFn: (repair: boolean) => api.generateCourse(workspace.workspace_id, row, key.current, repair ? 'repair' : 'generate'), onSuccess: () => { key.current = crypto.randomUUID(); update(); } });
  const cancel = useMutation({ mutationFn: () => api.cancelCourseBuild(workspace.workspace_id, row), onSuccess: update });
  const activate = useMutation({ mutationFn: () => api.activateCourseBuild(workspace.workspace_id, row, courseRevision), onSuccess: data => {
    client.setQueryData(['course', workspace.workspace_id], data); void client.invalidateQueries({ queryKey: ['student-catalog', workspace.workspace_id] });
    void client.invalidateQueries({ queryKey: ['activity-catalog', workspace.workspace_id] }); setNotice('课程已由你确认，三条学习路线使用同一单元。');
  } });
  const locked = workspace.status !== 'active' || diagnostic.isPending || generate.isPending || cancel.isPending || activate.isPending;
  return <section className="card section-gap"><div className="card-head"><h2>{row.topic}</h2><span className="badge">{names[row.status] ?? row.status}</span></div>{!!row.intent.message && <p>{String(row.intent.message)}</p>}
    <details className="section-gap"><summary>知识来源与版本（{row.sources.length}）</summary>{row.sources.map(source => <article key={String(source.source_id)}><h3>{String(source.title)}</h3><p>{String(source.excerpt)}</p><small>{String(source.locator)} · {String(source.version).slice(0, 12)} · 未经专业审核</small></article>)}</details>
    {row.status === 'diagnostic' && <form className="form-stack section-gap" onSubmit={e => { e.preventDefault(); diagnostic.mutate(false); }}><p>先保留你的尝试；目前只核对数值，不自动判断误区成因或解释质量。此页面提供了来源概念，不将本次尝试计为严格无辅助效果证据。</p>{row.diagnostics.map((task, index) => <article key={String(task.id)}><h3>诊断 {index + 1}</h3><p>{String(task.stem)}</p><label className="field">诊断 {index + 1} 答案<input required maxLength={128} value={answers[index]?.answer??''} onChange={e => setAnswers(old => old.map((v, i) => i === index ? { ...v, answer: e.target.value } : v))}/></label><label className="field">诊断 {index + 1} 依据<textarea maxLength={1500} value={answers[index]?.reasoning??''} onChange={e => setAnswers(old => old.map((v, i) => i === index ? { ...v, reasoning: e.target.value } : v))}/></label></article>)}<button className="button primary" disabled={locked}>保存诊断并查看课程范围</button>{row.attempts.length>0&&<><p>{String(row.intent.diagnostic_basis??'追加诊断可帮助澄清，不愿继续时可保留未知。')}</p><button type="button" className="button" disabled={locked} onClick={()=>diagnostic.mutate(true)}>保留未确定，结束诊断</button></>}</form>}
    {row.plan && <section className="section-gap"><h3>本轮课程范围</h3><p>{String(row.plan.scope)}</p><p>数值符合参考：{Number(row.plan.observed_numeric_passes)} / {Number(row.plan.diagnostic_count)}；解释尚未评阅。</p><ol>{Array.isArray(row.plan.steps) && row.plan.steps.map((step, index) => <li key={index}>{String(step)}</li>)}</ol><p className="card-note">{String(row.plan.support_policy)}</p></section>}
    {row.status === 'confirm_plan' && <><label className="field">这轮更想练什么？<select value={focus} onChange={e => setFocus(e.target.value as typeof focus)}><option value="basis">说明法则与前提</option><option value="representation">表示已知条件与所求</option><option value="verification">独立检查与自我提问</option></select></label><button className="button section-gap" disabled={locked || plan.isPending} onClick={() => plan.mutate()}>保存本轮重点调整</button><p>{String(row.plan?.student_focus_label ?? '可先调整本轮重点，再确认生成。')}</p><p className="card-note">确认后使用管理员已发布的 Fake 或 DeepSeek 生成候选，最多 4096 输出 tokens/次；数值由独立程序核验，课程讲解和检测安排还需开发审核。</p><button className="button primary" disabled={locked} onClick={() => generate.mutate(false)}>确认范围并生成课程</button></>}
    {row.operation && <p className="card-note">模型状态：{String(row.operation.status)}；{String(row.operation.vendor)} / {String(row.operation.model)}。未知费用不计为零，失败不自动重试。</p>}
    {!!row.candidate?.teaching_design && <section className="soft-box section-gap"><h3>Agent 教学设计与依据</h3><p>{String((row.candidate.teaching_design as Record<string,unknown>).limitation)}</p>{Array.isArray((row.candidate.teaching_design as Record<string,unknown>).designs) && ((row.candidate.teaching_design as Record<string,unknown>).designs as Record<string,unknown>[]).map((design,index) => <article key={index}><h4>{rationalGoals[String(design.concept)] ?? String(design.concept)}</h4><p>{String(design.rationale)}</p></article>)}</section>}
    {row.status === 'pending_review' && <p>候选已保存，请管理员到“课程生成与资源”核对题目、讲解与检测后发布。学生端暂不展示未审内容。</p>}
    {row.status === 'failed' && <button className="button" disabled={locked} onClick={() => generate.mutate(true)}>申请一次明确的内容修复</button>}
    {row.status === 'ready' && <><button className="button primary" disabled={locked} onClick={() => activate.mutate()}>确认使用这份课程</button><div className="actions section-gap">{[['study', '津津陪学'], ['roundtable', '圆桌讨论'], ['teach', '我教 AI']].map(([route, label]) => <Link className="button" key={route} to={`/${route}?workspace=${workspace.workspace_id}`}>{label}</Link>)}</div></>}
    {['diagnostic', 'confirm_plan', 'queued', 'unknown', 'failed', 'pending_review'].includes(row.status) && <button className="button section-gap" disabled={locked} onClick={() => cancel.mutate()}>取消剩余准备工作</button>}
    <SourceSearch workspace={workspace} row={row}/>
    {notice && <Success>{notice}</Success>}<ErrorNotice error={plan.error ?? diagnostic.error ?? generate.error ?? cancel.error ?? activate.error}/>
  </section>;
}

function SourceSearch({ workspace, row }: { workspace: Workspace; row: Build }) {
  const key = useRef(crypto.randomUUID());
  const query = useQuery({ queryKey: ['course-searches', row.build_id], queryFn: ({ signal }) => api.courseSearches(workspace.workspace_id, row.build_id, signal), refetchInterval: q => q.state.data?.items.some(item => ['pending', 'running'].includes(item.status)) ? 1500 : false });
  const search = useMutation({ mutationFn: () => api.searchCourseSources(workspace.workspace_id, row.build_id, key.current), onSuccess: () => { key.current = crypto.randomUUID(); void query.refetch(); } });
  return <details className="section-gap"><summary>外部知识发现</summary><p className="card-note">只发送标准数学关键词；每主题最多 2 次、全站每日 20 次。搜索结果不等于内容授权，未经确认不能直接用于生成。</p><button className="button" disabled={search.isPending || workspace.status !== 'active'} onClick={() => search.mutate()}>检索允许来源</button><ErrorNotice error={search.error ?? query.error}/>{query.data?.items.map(op => <article key={op.operation_id}><p>检索状态：{op.status} · credits：{op.credits ?? '未知'}</p>{op.result?.results?.map(result => <div key={result.url}><a href={result.url} target="_blank" rel="noopener noreferrer">{result.title}</a><p>{result.excerpt}</p></div>)}</article>)}</details>;
}

export function ResourcePanel({ workspace }: { workspace: Workspace }) {
  const course = useCourse(workspace.workspace_id);
  const query = useQuery({ queryKey: ['learning-resources', workspace.workspace_id], queryFn: ({ signal }) => api.learningResources(workspace.workspace_id, signal) });
  const resources = query.isSuccess && course.isSuccess ? query.data.items.filter(item => item.unit_id === course.data.active_unit_id) : [];
  return <section id="course-resources" className="card section-gap" aria-label="当前课程资源"><div className="card-head"><h2>当前课程资源</h2><span className="badge neutral">{query.isError || course.isError ? '暂不可读取' : query.isPending || course.isPending ? '读取中' : resources.length ? `${resources.length} 份可用` : '暂无可用资源'}</span></div><p className="card-note">课程配套的 OpenMAIC 课堂或导入材料，通过配置范围内的检查后在这里阅读。只展示当前单元、当前空间可用资源；诊断与独立检测期间隔离。</p><StudentClassroomGeneration workspace={workspace} unitId={course.data?.active_unit_id??undefined} refresh={()=>{void query.refetch();}}/><ErrorNotice error={query.error ?? course.error}/>{query.isPending && <Loading/>}{resources.map(item => <details key={item.resource_id}><summary>{item.title} · {item.sha256.slice(0, 8)}</summary>{item.classroom ? <><details><summary>补充媒体与生成状态</summary><MediaBudgets wid={workspace.workspace_id} resourceId={item.resource_id}/></details><ClassroomPlayer key={item.sha256} wid={workspace.workspace_id} resourceId={item.resource_id} hash={item.sha256} manifest={item.classroom}/></> : <div className="classroom-text">{(item.cards??[]).map((card,i)=><section key={i}>{card.text.split('\n').filter(line=>line.trim()!==item.title.trim()).map((line,n)=><p key={n}>{line.startsWith('公式（纯文本）：')?<Formula text={line.slice('公式（纯文本）：'.length).trim()}/>:line}</p>)}</section>)}</div>}</details>)}{query.isSuccess && course.isSuccess && !resources.length && <div className="soft-box"><strong>{course.data.active_unit_id ? '此单元尚无已审核资源。' : '先确认课程，再查看配套资源。'}</strong><p>课堂与课程任务分别准备。平台预算关闭时不能新增生成，现有学习任务仍可继续。私有开发课堂通过两次机器检查后自动开放；不确定内容留待复核。</p></div>}</section>;
}

function StudentClassroomGeneration({workspace,unitId,refresh}:{workspace:Workspace;unitId?:string;refresh:()=>void}){
 const bid=unitId?.startsWith('dynamic.generated.')?unitId.slice('dynamic.generated.'.length):undefined;
 const query=useQuery({queryKey:['student-classroom-jobs',workspace.workspace_id,bid],enabled:!!bid,
  queryFn:({signal})=>api.studentClassroomJobs(workspace.workspace_id,bid!,signal),refetchInterval:q=>q.state.data?.items.some(r=>!['completed','cancelled','failed','unknown','pending_review'].includes(r.status))?2000:false});
 const start=useMutation({mutationFn:()=>api.startStudentClassroom(workspace.workspace_id,bid!),onSuccess:()=>{void query.refetch();}});
 const cancel=useMutation({mutationFn:(jid:string)=>api.cancelStudentClassroom(workspace.workspace_id,bid!,jid),onSuccess:()=>{void query.refetch();}});
 useEffect(()=>{if(query.data?.items.some(r=>r.status==='completed'||r.status==='pending_review'))refresh();},[query.data?.items.map(r=>r.status).join('|')]);
 if(!bid)return null;
 return <details className="section-gap"><summary>需要讲解课堂？查看生成与恢复</summary><p>先使用上方已有课堂；没有可用版本时，可在平台预先批准的预算内选择生成。当前旧文本预算仍关闭，不会自动追加费用。等待媒体不妨碍继续练习。</p><button className="button" disabled={workspace.status!=='active'||start.isPending||!!query.data?.items.length} onClick={()=>start.mutate()}>为当前课程准备 OpenMAIC 课堂</button><button className="button" onClick={()=>{void query.refetch();refresh();}}>刷新课堂状态</button>{query.data?.items.map(row=><p key={row.job_id}>课堂任务：{({pending:'已保存，等待准备',submitting:'正在提交课堂',running:'正在生成课堂',import_pending:'正在恢复已有课堂',checking:'系统正在检查课堂内容',pending_review:'课堂已保存，等待内容复核；可以继续现有练习',completed:'课堂已开放',cancelled:'已停止准备',failed:'准备未完成，可继续现有练习',unknown:'调用结果未知，不自动重试'} as Record<string,string>)[row.status]??row.status}{row.error_code?' · '+row.error_code:''}{!['completed','cancelled','failed'].includes(row.status)&&<button className="button small" disabled={cancel.isPending} onClick={()=>cancel.mutate(row.job_id)}>停止这次准备</button>}</p>)}<ErrorNotice error={start.error??query.error??cancel.error}/></details>;
}
