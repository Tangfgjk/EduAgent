import { SkillExecution } from '@wenjin/ui';
import { useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { BookOpen, Check, Lightbulb, Send, Square } from 'lucide-react';
import { api, type AgentEvent, type TutorCommand, type TutorRun, type TutorView, type Workspace } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading } from '@wenjin/ui';
import './study.css';
import { StudyHistory } from './study-history';
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
    <SkillExecution snapshot={run?.skill_execution}/>
    {run?.view?.phase === 'practice' && <><p className="card-note">支持方式：{String(run.view.support_decision?.mode ?? 'step_support')}。基于实际提示与数值记录，不代表能力评分；若仍困难，可继续请求具体支持。</p><ResourcePanel workspace={workspace}/></>}
    {workspace.kind === 'temporary' && <p className="page-note">这是临时探索空间。本轮学习只留在此处，不自动并入正式项目长期画像。</p>}
    {content.isPending || plans.isPending || query.isPending ? <Loading text="正在恢复这个空间的学习过程…"/> : content.isError || plans.isError || query.isError ? <ErrorNotice error={content.error ?? plans.error ?? query.error} retry={() => { void content.refetch(); void plans.refetch(); void query.refetch(); }}/> : <>
      {!pending && recentUnit && !params.has('unit') && <p className="page-note">已沿用当前空间最近一次陪学单元。空间名称可自由修改，不用于猜测课程；需要换内容时再选择。</p>}
      {!pending && !course.data?.active_unit_id && <label className="field section-gap">选择开发单元<select value={unitId} disabled={start.isPending || !!course.data?.active_unit_id} onChange={e => { const next = new URLSearchParams(params); next.set('unit', e.target.value); setParams(next); startKey.current = crypto.randomUUID(); start.reset(); }}>{!unit && <option value={unitId}>未找到此内容包，请重新选择</option>}{content.data?.items.map(item => <option key={item.unit_id} value={item.unit_id}>{item.title}{item.enabled ? '' : '（未启用）'}</option>)}</select></label>}
      {!pending && <section className="card section-gap"><div className="card-head"><h2>{unit?.title}</h2><span className="badge neutral">{unit?.task_count ?? 0} 道开发题</span></div><p className="body-copy">诊断 1 题 → 练习与反思 2 题 → 无辅助检测 2 题。每次输入都会保存在当前空间，刷新后可继续。</p>
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
  const submit = useMutation({ mutationFn: (command: TutorCommand | { action: 'cancel' }) => {
    if (command.action === 'cancel') return api.cancelTutor(run);
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
    {view?.phase === 'summary' ? <Summary view={view}/> : view ? <div className="grid two">
      <section className="card"><div className="card-head"><h2>第 {view.task_index} / {view.task_count} 题</h2><span className="badge neutral">{view.task?.task_id}</span></div><p className="study-stem">{view.task?.stem}</p><p className="card-note">{view.task?.answer_format}</p>
        <AnswerForm key={"answer:" + view.task?.task_id} view={view} locked={locked} send={command => submit.mutate(command)}/>
        {view.feedback && <div className={`study-feedback ${view.feedback.status}`} role="status"><strong>{outcomeNames[view.feedback.status]}</strong><p>{view.feedback.message}</p></div>}
        {view.can_continue && <button className="button primary section-gap" disabled={locked} onClick={() => submit.mutate({ action: 'next' })}><Check size={16}/>{view.task_index === view.task_count ? '查看学习小结' : '继续下一步'}</button>}
        {view.can_reflect && <Reflection prompt={view.reflection_prompt} key={"reflection:" + view.task?.task_id} locked={locked} send={command => submit.mutate(command)}/>}
      </section>
      <section className="card align-start"><div className="card-head"><h2>津津 · 学习伙伴</h2><span className="badge neutral">{run.coaching_available || run.coaching ? "预设提示＋AI 追问" : "预设教学支架"}</span></div>
        <p className="body-copy">{view.phase === 'checkpoint' || view.phase === 'diagnostic' ? '这一段请独立完成。提交前不提供提示，提交后不能改写这一题的结果。' : '先试着说出你的想法。提示逐级开放，使用提示的次数会单独保留。'}</p>
        <div className="hint-steps">{['轻推', '方向', '部分步骤', '完整对照'].map((label, index) => <span className={view.hints.length > index ? 'revealed' : ''} key={label}>L{index}<small>{label}</small></span>)}</div>
        {view.hints.map((hint, index) => <div className="study-hint" key={index}><span className="badge neutral">L{index}</span><p>{hint}</p></div>)}
        <button className="button" disabled={locked || !view.can_hint} onClick={() => submit.mutate({ action: 'hint', coaching_text: '' })}><Lightbulb size={16}/>{view.hints.length ? '再给我一级提示' : '给我一点提示'}</button>
        <ModelCoaching key={'coach:' + view.task?.task_id} run={run} locked={locked} send={command => submit.mutate(command)} update={update} refresh={refresh}/>
        {view.can_skip && <button className="button text section-gap" disabled={locked} onClick={() => submit.mutate({ action: 'skip' })}>记录为未完成并反思</button>}
        {view.pending_reviews > 0 && <p className="card-note">本轮有 {view.pending_reviews} 条不可验证提交等待复核，不计为自动验证通过。</p>}
        <p className="card-note">你的步骤与反思是学习记录；本轮程序只验证数值结果，不自动评定推理质量或素养。</p>
      </section>
    </div> : <section className="card"><Empty title="输入已经保存">正在准备第一道题。等待较久时，请检查开发 worker 是否启动。</Empty></section>}
    {(run.status === 'queued' || run.status === 'awaiting_input') && <button className="button text section-gap" disabled={submit.isPending} onClick={() => submit.mutate({ action: 'cancel' })}><Square size={14}/>结束这次陪学</button>}
    <TutorEvents key={run.run_id} run={run}/>
    <StudyHistory run={run}/>
  </section>;
}

function ModelCoaching({ run, locked, send, update, refresh }: { run: TutorRun; locked: boolean; send: (command: TutorCommand) => void; update: (run: TutorRun) => void; refresh: () => void }) {
  const [text, setText] = useState('');
  const op = run.coaching;
  const cancel = useMutation({ mutationFn: () => api.cancelModelTask(run), onSuccess: update, onError: refresh });
  if (run.view?.unit_id !== 'math.g9.probability.dev1' || run.view.phase !== 'practice') return null;
  const relevant = !!op && op.task_id === run.view.task?.task_id;
  const waiting = !!op && relevant && ['waiting', 'pending', 'running'].includes(op.status);
  const labels: Record<string, string> = { waiting: '思路已保存，等待教学步骤', pending: '思路已保存，等待模型', running: '模型正在读取本轮思路', succeeded: '追问建议已保存', failed: '模型未能给出可用追问，请使用上方预设提示', cancelled: '本次模型追问已取消', discarded: '学习步骤或权限已变化，旧回复已拦截', unknown: '模型结果未知，不自动重试，请使用预设提示' };
  return <section className="section-gap"><h3>让 AI 听听我的思路</h3><p className="card-note">{run.coaching_context_limit === 4 ? '发送当前题目、本轮文字及同题同配置下最多 3 轮已完成交流，含本轮最多 4 轮。' : '仅发送当前题目与本轮文字。'}请勿填写姓名、联系方式等个人信息。每次使用一级提示并记录一次调用，单题最多四次。可以回应上一条追问后再请求下一条，也可以直接提交数值答案；模型不负责判分。等待时继续作答会使旧追问失效。</p>
    {run.coaching_available && run.coaching_remaining_calls == null && <p className="card-note">开发测试：每日模型调用次数不限，调用与 token 用量继续记录。</p>}
    {run.coaching_remaining_calls != null && <p className="card-note">今日模型追问剩余 {run.coaching_remaining_calls} 次（按账号与全站额度取较小值，北京时间零点重置；并发预约可能改变余量）。</p>}
    {!run.coaching_available && !waiting && <p className="card-note">{run.coaching_remaining_calls === 0 ? '今日模型调用额度已用完。' : '当前模型追问未开放或提示额度已用完。'}可以继续作答或使用可用的预设提示。</p>}
    <form className="form-stack" onSubmit={event => { event.preventDefault(); if (text.trim().length >= 2) send({ action: 'hint', coaching_text: text.trim() }); }}><label htmlFor="coach-thinking">我想让 AI 帮我澄清的思路</label><textarea id="coach-thinking" rows={3} maxLength={1500} value={text} onChange={e => setText(e.target.value)} disabled={locked || waiting || !run.coaching_available}/><button className="button" disabled={locked || waiting || !run.coaching_available || text.trim().length < 2}>提交思路并请求追问</button></form>
    {relevant && op && <div className="study-hint" role="status"><span className="badge neutral">{op.simulated ? '模拟模型' : 'DeepSeek'} · {op.model}</span><p>{labels[op.status]}</p>
      {!op.current && op.status === 'succeeded' && <p className="card-note">这是较早学习步骤的记录，当前不展示旧建议。</p>}
      {op.current && op.result && <><blockquote>“{op.result.quote}”</blockquote><p>{op.result.question}</p><p className="card-note">{op.result.judgment}。追问措辞来自受限支架，模型仅选择方向和原文引用。</p></>}
      {op.current && <><p className="card-note">本次上下文：{op.context_turns ?? 1} 轮（含本轮）</p>{!!op.context?.length && <details><summary>查看本次带入的历史交流</summary><ol>{op.context.map((item, index) => <li key={index}><blockquote>{item.student_text}</blockquote><p>{item.question}</p></li>)}</ol></details>}</>}
      {waiting && <button type="button" className="button small" disabled={cancel.isPending} onClick={() => cancel.mutate()}>取消本次模型追问</button>}{waiting && <button type="button" className="button small" onClick={refresh}>刷新追问状态</button>}
      {op.billing_unknown && <p className="card-note">停止等待不保证服务商停止计费；本次不自动重发。</p>}
    </div>}
    <ErrorNotice error={cancel.error}/>
  </section>;
}

function AnswerForm({ view, locked, send }: { view: TutorView; locked: boolean; send: (command: TutorCommand) => void }) {
  const [answer, setAnswer] = useState(''), [reasoning, setReasoning] = useState('');
  function submit(event: FormEvent) { event.preventDefault(); if (!locked && view.can_answer && answer.trim()) send({ action: 'answer', answer: answer.trim(), reasoning }); }
  return <form onSubmit={submit} className="form-stack section-gap"><label htmlFor="math-answer">我的数值答案</label><input id="math-answer" value={answer} onChange={e => setAnswer(e.target.value)} maxLength={128} required disabled={locked || !view.can_answer} placeholder="例如 x=3 或 6/2"/><label htmlFor="math-reasoning">我的步骤与依据（可选）</label><textarea id="math-reasoning" rows={4} maxLength={1500} value={reasoning} onChange={e => setReasoning(e.target.value)} disabled={locked || !view.can_answer} placeholder="写下算式、猜想或卡住的位置，先保留自己的思考。"/><button className="button primary" disabled={locked || !view.can_answer || !answer.trim()}><Send size={16}/>检查我的解答</button></form>;
}

function Reflection({ prompt, locked, send }: { prompt: string; locked: boolean; send: (command: TutorCommand) => void }) {
  const [text, setText] = useState('');
  return <form className="form-stack section-gap" onSubmit={event => { event.preventDefault(); if (!locked && text.trim().length >= 2) send({ action: 'reflect', reflection: text.trim() }); }}><label htmlFor="math-reflection">{prompt}</label><textarea id="math-reflection" value={text} maxLength={1000} onChange={e => setText(e.target.value)} disabled={locked} required rows={3}/><button className="button primary" disabled={locked || text.trim().length < 2}>保存反思并继续</button></form>;
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
