import { useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Clock3, RefreshCw, Send, Square } from 'lucide-react';
import { api, type AgentEvent, type AgentRun, type Workspace } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading } from '@wenjin/ui';
import './plan.css';
import { Link } from 'react-router-dom';

const statuses: Record<AgentRun['status'], string> = { queued: '等待处理', awaiting_input: '等待你确认', completed: '计划已确认', cancelled: '已取消', failed: '未能完成' };
const eventNames: Record<string, string> = { 'run.queued': '提交已保存', 'plan.proposed': '草案已保存，等待确认', 'plan.confirmed': '你已确认计划', 'run.cancelled': '流程已取消', 'run.failed': '流程未能完成' };

export function PlanPage({ workspace }: { workspace: Workspace }) {
  const client = useQueryClient(), key = ['runs', workspace.workspace_id];
  const [goal, setGoal] = useState(''), [minutes, setMinutes] = useState(15), [selected, setSelected] = useState<string>();
  const strategy = useQuery({ queryKey: ['active-strategy', workspace.workspace_id], queryFn: ({ signal }) => api.activeStrategy(workspace.workspace_id, signal), staleTime: 0 });
  const submission = useRef({ signature: '', key: '' });
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => api.runs(workspace.workspace_id, signal),
    refetchInterval: q => q.state.data?.items.some(run => run.status === 'queued' || run.status === 'awaiting_input') ? 2000 : false });
  const start = useMutation({ mutationFn: () => {
    const signature = JSON.stringify([goal.trim(), minutes]);
    if (submission.current.signature !== signature) submission.current = { signature, key: crypto.randomUUID() };
    return api.startPlan(workspace.workspace_id, goal.trim(), minutes, submission.current.key);
  }, onSuccess: run => {
    submission.current = { signature: '', key: '' };
    setSelected(run.run_id);
    client.setQueryData<{ items: AgentRun[] }>(key, old => ({ items: [run, ...(old?.items ?? []).filter(item => item.run_id !== run.run_id)] }));
    void client.invalidateQueries({ queryKey: key });
  } });
  function submit(event: FormEvent) { event.preventDefault(); if (!start.isPending) start.mutate(); }
  const runs = query.data?.items ?? [];
  const current = runs.find(run => run.run_id === selected) ?? runs[0];
  const pending = runs.some(run => run.status === 'queued' || run.status === 'awaiting_input');
  return <><Heading kicker="A PLAN YOU DECIDE" title="学习计划" description="先说清楚想学会什么，再由你决定节奏与计划。"/>
    <div className="notice"><Clock3 size={18}/><div><strong>流程试运行 · 开发模板</strong><br/>现在可验证目标提交、计划调整、确认与恢复。草案来自固定开发模板，尚未调用真实模型；确认后不会生成题目或素养评分。</div></div>
    {workspace.kind === 'temporary' ? <Empty title="学习计划从正式项目开始">临时探索仍保留独立空间。请在左侧选择或新建一个正式项目，再制定持续学习计划。</Empty> : <>
      {query.isPending ? <Loading text="正在读取这个项目的计划…"/> : query.isError ? <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/> : <div className="grid two section-gap">
        <section className="card"><div className="card-head"><h2>我的学习目标</h2><span className="badge neutral">由你提出</span></div>
          <form onSubmit={submit} className="form-stack"><label htmlFor="plan-goal">这次想学会什么？</label><textarea id="plan-goal" rows={4} maxLength={600} required value={goal} onChange={e => setGoal(e.target.value)} placeholder="例如：理解一元一次方程的等式性质，能说清移项的依据" disabled={start.isPending || pending || workspace.status === 'archived'}/>
            <label htmlFor="plan-minutes">每天愿意投入几分钟？</label>{strategy.data?.effective && <button type="button" className="button small" disabled={pending || workspace.status === 'archived'} onClick={() => setMinutes(strategy.data!.policy.daily_minutes)}>采用当前策略建议（{strategy.data.policy.daily_minutes} 分钟）</button>}<select id="plan-minutes" value={minutes} onChange={e => setMinutes(Number(e.target.value))} disabled={start.isPending || pending || workspace.status === 'archived'}>{[...new Set([5, 10, 15, 20, 25, 30, 45, 60, minutes])].sort((a, b) => a - b).map(value => <option key={value} value={value}>{value} 分钟</option>)}</select>
            <button className="button primary" disabled={!goal.trim() || start.isPending || pending || workspace.status === 'archived'}><Send size={16}/>{start.isPending ? '正在保存…' : '生成计划草案'}</button>
            {start.isError && <ErrorNotice error={start.error} retry={() => start.mutate()}/>}
            <p className="card-note">{workspace.status === 'archived' ? '项目已归档，可查看已有记录；恢复后再继续。' : pending ? '这个项目还有一份计划等待处理或确认，请先完成或取消。' : '草案保存到当前项目。刷新页面后，可以继续确认。'}</p>
          </form>
        </section>
        {current ? <RunPanel key={current.run_id} run={current} archived={workspace.status === 'archived'} changed={() => { void client.invalidateQueries({ queryKey: key }); }}/> : <section className="card"><Empty title="先给自己一个小目标">草案会展示学习步骤和时间安排，只有你确认后，才会成为已确认计划。</Empty></section>}
      </div>}
      {runs.length > 1 && <section className="card section-gap"><h2>最近的计划流程</h2><p className="card-note">显示最近 20 次流程，记录只属于当前项目。</p><div className="plan-history">{runs.map(run => <button className={`button ${current?.run_id === run.run_id ? 'primary' : ''}`} key={run.run_id} onClick={() => setSelected(run.run_id)}>{run.draft?.goal ?? '等待生成的草案'} · {statuses[run.status]}</button>)}</div></section>}
    </>}
  </>;
}

function RunPanel({ run, archived, changed }: { run: AgentRun; archived: boolean; changed: () => void }) {
  const [minutes, setMinutes] = useState<number | null>(null);
  const choice = minutes ?? run.draft?.daily_minutes ?? 15;
  const intent = useRef({ signature: '', key: '' });
  const cursor = useRef<{ seq: number; items: AgentEvent[] }>({ seq: 0, items: [] });
  const events = useQuery({ queryKey: ['run-events', run.run_id, run.event_seq], queryFn: async ({ signal }) => {
    let next = cursor.current;
    while (next.seq < run.event_seq) {
      const batch = await api.runEvents(run, next.seq, signal);
      if (signal.aborted) return cursor.current.items;
      if (!batch.length) break;
      next = { seq: batch.at(-1)!.seq, items: [...next.items, ...batch] };
    }
    cursor.current = next;
    return next.items;
  } });
  const action = useMutation({ mutationFn: async (type: 'confirm' | 'revise' | 'cancel') => {
    if (type === 'cancel') return api.cancelRun(run);
    if (!run.gate_id) throw new Error('请刷新后重新确认');
    const payload = { action: type, gate_id: run.gate_id, expected_revision: run.revision,
      daily_minutes: type === 'confirm' ? run.draft!.daily_minutes : choice };
    const signature = JSON.stringify(payload);
    if (intent.current.signature !== signature) intent.current = { signature, key: crypto.randomUUID() };
    return api.decidePlan(run.workspace_id, run.run_id, payload, intent.current.key);
  }, onSuccess: () => { setMinutes(null); changed(); }, onError: changed });
  const paused = run.status === 'awaiting_input', active = paused || run.status === 'queued';
  return <section className="card plan-result" aria-live="polite"><div className="card-head"><h2>计划草案</h2><span className="badge">{statuses[run.status]}</span></div>
    {run.draft ? <><h3>{run.draft.goal}</h3><p className="body-copy">每天 {run.draft.daily_minutes} 分钟 · 第 {run.draft.generation} 版草案</p><ol className="plan-steps">{run.draft.steps.map(step => <li key={step}>{step}</li>)}</ol></> : <p className="body-copy">目标已保存，worker 处理后会显示草案。等待较久时，请确认开发 worker 已启动。</p>}
    {paused && <><label htmlFor="draft-minutes">调整每天的学习时间</label><select id="draft-minutes" value={choice} onChange={e => setMinutes(Number(e.target.value))} disabled={action.isPending || archived}>{[...new Set([5, 10, 15, 20, 25, 30, 45, 60, choice])].sort((a, b) => a - b).map(value => <option key={value} value={value}>{value} 分钟</option>)}</select><div className="actions section-gap"><button className="button" disabled={action.isPending || archived || choice === run.draft?.daily_minutes} onClick={() => action.mutate('revise')}><RefreshCw size={15}/>更新草案</button><button className="button primary" disabled={action.isPending || archived || choice !== run.draft?.daily_minutes} onClick={() => action.mutate('confirm')}><Check size={15}/>我确认这份计划</button></div><p className="card-note">调整时间后，请先更新并阅读新草案，再确认。</p></>}
    {active && <button className="button text section-gap" disabled={action.isPending} onClick={() => action.mutate('cancel')}><Square size={14}/>取消这次流程</button>}
    {action.isError && <ErrorNotice error={action.error}/>}
    {run.status === 'completed' && <div className="notice">计划已由你确认。<Link to={{ pathname: "/study", search: "?workspace=" + run.workspace_id }}>前往津津陪学体验方程开发单元 →</Link></div>}
    {run.status === 'failed' && <p className="body-copy">流程未完成（{run.error_code}）。已有输入仍保留，请检查服务状态后重新发起。</p>}
    <details className="section-gap"><summary>查看流程记录</summary>{events.isError ? <ErrorNotice error={events.error} retry={() => { void events.refetch(); }}/> : <ol className="plan-steps">{events.data?.map(item => <li key={item.seq}>{eventNames[item.kind]}</li>)}</ol>}</details>
  </section>;
}
