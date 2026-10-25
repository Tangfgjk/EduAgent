import { SkillExecution } from '@wenjin/ui';
import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { BookOpen, Square } from 'lucide-react';
import { api, type AgentEvent, type TutorCommand, type TutorRun, type TutorView, type Workspace } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading } from '@wenjin/ui';
import './study.css';
import { StudyHistory } from './study-history';
import { TutorChat } from './tutor-chat';
import { useCourse } from './course';
import { ResourcePanel } from './generation';

const phaseNames: Record<TutorView['phase'], string> = { diagnostic: '先试一题', practice: '尝试与验证', reflection: '回望方法', checkpoint: '无辅助检测', summary: '学习小结' };
const statusNames: Record<TutorRun['status'], string> = { queued: '正在处理', awaiting_input: '等待你的思考', completed: '本轮已结束', cancelled: '已取消', failed: '流程暂停' };
const outcomeNames: Record<string, string> = { passed: '数值通过', failed: '数值未通过', unverifiable: '不可验证', skipped: '未完成' };

export function StudyPage({ workspace }: { workspace: Workspace }) {
  const client = useQueryClient(), key = ['tutoring', workspace.workspace_id];
  const [selected, setSelected] = useState<string>();
  const [params, setParams] = useSearchParams();
  const startKey = useRef(crypto.randomUUID());
  const content = useQuery({ queryKey: ['student-catalog', workspace.workspace_id], queryFn: ({ signal }) => api.contentCatalog(false, signal, workspace.workspace_id) });
  const plans = useQuery({ queryKey: ['runs', workspace.workspace_id], queryFn: ({ signal }) => api.runs(workspace.workspace_id, signal) });
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => api.tutoring(workspace.workspace_id, signal),
    refetchInterval: q => q.state.data?.items.some(run => run.status === 'queued') ? 1000 : q.state.data?.items.some(run => run.status === 'awaiting_input') ? 3000 : false });
  const course = useCourse(workspace.workspace_id);
  const recentUnit = query.data?.items[0]?.view?.unit_id;
  const unitId = course.data?.active_unit_id ?? params.get('unit') ?? recentUnit ?? 'math.g7.equations.dev1';
  const unit = content.data?.items.find(item => item.unit_id === unitId);
  function update(run: TutorRun) {
    client.setQueryData<{ items: TutorRun[] }>(key, old => ({ items: [run, ...(old?.items ?? []).filter(item => item.run_id !== run.run_id)] }));
    void client.invalidateQueries({ queryKey: key });
  }
  const start = useMutation({ mutationFn: () => api.startTutor(workspace.workspace_id, unit!.unit_id, startKey.current),
    onSuccess: run => { startKey.current = crypto.randomUUID(); setSelected(run.run_id); update(run); } });
  const runs = query.data?.items ?? [];
  const pending = runs.find(run => run.status === 'queued' || run.status === 'awaiting_input');
  const run = runs.find(item => item.run_id === selected) ?? pending ?? runs[0];
  const planReady = workspace.kind === 'temporary' || (!!plans.data?.items.some(plan => plan.status === 'completed') && !plans.data.items.some(plan => plan.status === 'queued' || plan.status === 'awaiting_input'));
  const unavailable = !course.data?.active_unit_id || !unit?.enabled || !planReady || workspace.status === 'archived';
  return <><Heading kicker="THINK BEFORE THE NEXT HINT" title="津津陪学" description="把尝试、依据和检查留下来，一步步接近理解。"/>
    <div className="notice"><BookOpen size={18}/><div><strong>{run?.view?.title ?? unit?.title ?? '选择开发单元'}</strong><br/>自编开发题，程序核验数值，提示逐级开放。概率练习可使用管理员开放的模型追问；当前无教师金标，不自动评价步骤质量或掌握程度。</div></div>
    <details className="section-gap"><summary>本轮学习方式</summary><SkillExecution snapshot={run?.skill_execution}/></details>
    {run?.view?.phase === 'practice' && <><p className="card-note">支持方式：{String(run.view.support_decision?.mode ?? 'step_support')}。基于实际提示与数值记录，不代表能力评分；若仍困难，可继续请求具体支持。</p><details className="section-gap"><summary>需要回看课程资料？</summary><ResourcePanel workspace={workspace}/></details></>}
    {run?.view?.phase === 'practice' && run.view.support_decision?.profile === 'rational-agent.v2' && <section className="soft-box section-gap"><h3>为什么这样引导</h3><p>本题目标：{String(run.view.support_decision.concept)}</p><p>{String(run.view.support_decision.basis)}</p><details><summary>查看课程Agent的安排依据</summary><p>{String(run.view.support_decision.plan_rationale)}</p></details></section>}
    {workspace.kind === 'temporary' && <p className="page-note">这是临时探索空间。本轮学习只留在此处，不自动并入正式项目长期画像。</p>}
    {content.isPending || plans.isPending || query.isPending ? <Loading text="正在恢复这个空间的学习过程…"/> : content.isError || plans.isError || query.isError ? <ErrorNotice error={content.error ?? plans.error ?? query.error} retry={() => { void content.refetch(); void plans.refetch(); void query.refetch(); }}/> : <>
      {!pending && recentUnit && !params.has('unit') && <p className="page-note">已沿用当前空间最近一次陪学单元。空间名称可自由修改，不用于猜测课程；需要换内容时再选择。</p>}
      {!pending && !course.data?.active_unit_id && <label className="field section-gap">选择开发单元<select value={unitId} disabled={start.isPending || !!course.data?.active_unit_id} onChange={e => { const next = new URLSearchParams(params); next.set('unit', e.target.value); setParams(next); startKey.current = crypto.randomUUID(); start.reset(); }}>{!unit && <option value={unitId}>未找到此内容包，请重新选择</option>}{content.data?.items.map(item => <option key={item.unit_id} value={item.unit_id}>{item.title}{item.enabled ? '' : '（未启用）'}</option>)}</select></label>}
      {!pending && <section className="card section-gap"><div className="card-head"><h2>{unit?.title}</h2><span className="badge neutral">{unit?.task_count ?? 0} 道开发题</span></div><p className="body-copy">诊断 → 练习与反思 → 无辅助检测。题目数量与组合依当前课程版本；每次输入保存在当前空间，刷新后可继续。</p>
        {!unit?.enabled && <p className="card-note">当前开发内容未启用，请管理员先在“知识点与题库”核对版本并启用。</p>}
        {!planReady && <p className="card-note">正式项目先由你确认目标与节奏。<Link to={{ pathname: '/plan', search: '?workspace=' + workspace.workspace_id }}>前往学习计划 →</Link></p>}
        {workspace.status === 'archived' && <p className="card-note">当前空间已归档，恢复后才能开始或继续作答。</p>}
        <button className="button primary section-gap" disabled={unavailable || start.isPending} onClick={() => start.mutate()}><BookOpen size={16}/>{start.isPending ? '正在保存…' : '开始开发单元'}</button>
        {start.isError && <ErrorNotice error={start.error} retry={() => start.mutate()}/>}
      </section>}
      {run && <TutorSession key={run.run_id} run={run} archived={workspace.status === 'archived'} update={update} refresh={() => { void client.invalidateQueries({ queryKey: key }); }}/>}
      {runs.length > 1 && <section className="card section-gap"><h2>最近的学习记录</h2><p className="card-note">最近 20 次，仅属于当前空间。</p><div className="plan-history">{runs.map(item => <button className="button" key={item.run_id} onClick={() => setSelected(item.run_id)}>{new Date(item.created_at * 1000).toLocaleString()} · {statusNames[item.status]}</button>)}</div></section>}
    </>}
  </>;
}

function TutorSession({ run, archived, update, refresh }: { run: TutorRun; archived: boolean; update: (run: TutorRun) => void; refresh: () => void }) {
  const intent = useRef({ signature: '', key: '' });
  const submit = useMutation({ mutationFn: async (command: TutorCommand | { action: 'cancel' }) => {
    if (command.action === 'cancel') {
      const latest=(await api.tutoring(run.workspace_id)).items.find(r=>r.run_id===run.run_id);
      if(!latest)throw new Error('原任务已不在最近记录，请刷新后重试。');
      return ['completed','cancelled','failed'].includes(latest.status)?latest:api.cancelTutor(latest);
    }
    const signature = JSON.stringify([run.revision, run.gate_id, command]);
    if (intent.current.signature !== signature) intent.current = { signature, key: crypto.randomUUID() };
    return api.tutorInput(run, command, intent.current.key);
  }, onSuccess: update, onError: refresh });
  const view = run.view, locked = submit.isPending || run.status !== 'awaiting_input' || archived;
  return <section className="section-gap"><div className="section-heading"><h2>{view ? phaseNames[view.phase] : '准备学习任务'}</h2><span className="badge">{statusNames[run.status]}</span></div>
    {view && <p className="card-note">本轮内容：{view.title}</p>}
    {run.queue && <div className="notice" role="status"><div><strong>{({ waiting: '输入已保存，等待教学处理', processing: '正在处理已保存的输入', retrying: '教学步骤暂未完成，正在准备重试', recovering: '正在等待教学任务恢复', failed: '教学处理已暂停，请联系管理员' })[run.queue.state]}</strong><br/>已等待 {run.queue.wait_seconds} 秒。{run.queue.retry_after_seconds ? `约 ${run.queue.retry_after_seconds} 秒后可再次处理。` : ''}{run.queue.wait_seconds >= 15 ? '等待较久；可以刷新状态或结束本次陪学，已有输入不会丢失。' : '请稍候，无需重复提交。'}<button className="button small" type="button" onClick={refresh}>刷新处理状态</button></div></div>}
    {submit.isError && <ErrorNotice error={submit.error}/>}
    {run.status === 'failed' && <div className="notice">流程未能继续（{run.error_code}）。已有记录保留，请检查内容状态后重新开始。</div>}
    {view?.phase === 'summary' ? <Summary view={view}/> : view ? <><TutorChat run={run} locked={locked} send={command => submit.mutate(command)}/></> : <section className="card"><Empty title="输入已经保存">正在准备第一道题。等待较久时，请稍后刷新，或联系管理员。</Empty></section>}
    {(run.status === 'queued' || run.status === 'awaiting_input') && <button className="button text section-gap" disabled={submit.isPending} onClick={() => submit.mutate({ action: 'cancel' })}><Square size={14}/>结束这次陪学</button>}
    {!!view?.support_decision?.agenda_decision&&<p className="card-note">本轮调度：{String((view!.support_decision!.agenda_decision as Record<string,unknown>).reason??'沿用当前课程任务')}。选择题目与支架已受课程约束；资料建议仍需自己打开，不代表已完成阅读。</p>}
    <TutorEvents key={run.run_id} run={run}/>
    <StudyHistory run={run}/>
  </section>;
}

function Summary({ view }: { view: TutorView }) {
  const tests = view.records.filter(record => record.stage === 'checkpoint');
  return <section className="card"><h2>这一次，你留下了哪些过程？</h2><div className="notice section-gap">独立检测：{tests.filter(record => record.status === 'passed').length} / {tests.length} 道数值验证通过。这只是本轮样例题的结果，不代表掌握度或素养评分。</div><div className="study-records">{view.records.map(record => <article key={record.task_id}><div className="card-head"><strong>{record.task_id}</strong><span className="badge neutral">{outcomeNames[record.status]}</span></div><p>{record.stage === 'checkpoint' ? '无辅助检测' : record.stage === 'diagnostic' ? '初始诊断' : '练习'} · 尝试 {record.attempts} 次 · 提示 {record.hints_used} 次</p>{record.reflection && <blockquote>{record.reflection}</blockquote>}</article>)}</div><p className="card-note">{view.pending_reviews} 条提交仍待人工复核。完整输入按来源保留，人工备注不覆盖自动验证历史。复习安排与来源详情可在“错题与复习”和“成长证据”查看。</p></section>;
}

function TutorEvents({ run }: { run: TutorRun }) {
  const cursor = useRef<{ seq: number; items: AgentEvent[] }>({ seq: 0, items: [] });
  const events = useQuery({ queryKey: ['tutor-events', run.run_id, run.event_seq], queryFn: async ({ signal }) => {
    let next = cursor.current;
    while (next.seq < run.event_seq) {
      const batch = await api.runEvents(run, next.seq, signal, 'tutoring');
      if (signal.aborted) return cursor.current.items;
      if (!batch.length) break;
      next = { seq: batch.at(-1)!.seq, items: [...next.items, ...batch] };
    }
    cursor.current = next;
    return next.items;
  } });
  const labels: Record<string, string> = { 'run.queued': '输入已保存', 'tutor.started': '诊断题已准备', 'tutor.answer': '数值验证结果已保存', 'tutor.hint': '提示已开放', 'tutor.reflect': '反思已保存', 'tutor.next': '已进入下一步', 'tutor.skip': '已记录未完成', 'tutor.completed': '本轮小结已保存', 'run.cancelled': '本轮已结束', 'run.failed': '流程暂停' };
  return <details className="card section-gap study-events"><summary>查看陪学流程记录</summary>{events.isError ? <ErrorNotice error={events.error} retry={() => { void events.refetch(); }}/> : <ol>{events.data?.map(event => <li key={event.seq}>{labels[event.kind] ?? event.kind}</li>)}</ol>}</details>;
}
