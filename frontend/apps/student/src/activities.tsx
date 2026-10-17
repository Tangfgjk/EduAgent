import { useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { GraduationCap, MessagesSquare, Send, Square } from 'lucide-react';
import { api, type ActivityRun, type ActivityKind, type Workspace } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading } from '@wenjin/ui';
import './study.css';
import './activities.css';

const statusNames = { queued: '正在保存与处理', awaiting_input: '等待你的表达', completed: '本轮已完成', cancelled: '本轮已结束', failed: '流程暂停' };

export function ActivityPage({ workspace, kind }: { workspace: Workspace; kind: ActivityKind }) {
  const client = useQueryClient(), [offset, setOffset] = useState(0), [selected, setSelected] = useState<string>();
  const startKey = useRef(crypto.randomUUID()), key = ['activities', workspace.workspace_id, kind, offset];
  const [params, setParams] = useSearchParams();
  const unitId = params.get('activity') ?? (kind === 'teach' ? 'math.g9.probability.teach.dev2' : 'math.g7.equations.activities.dev1');
  const title = kind === 'roundtable' ? '圆桌讨论' : '我教 AI';
  const content = useQuery({ queryKey: ['activity-catalog'], queryFn: ({ signal }) => api.activityCatalog(false, signal), staleTime: 0 });
  const unit = content.data?.items.find(item => item.unit_id === unitId);
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => api.activities(workspace.workspace_id, kind, offset, signal), staleTime: 0,
    refetchInterval: q => q.state.data?.items.some(run => run.status === 'queued' || ['waiting', 'pending', 'running'].includes(run.coaching?.status ?? '')) ? 700 : q.state.data?.items.some(run => run.status === 'awaiting_input') ? 3000 : false });
  function update(run: ActivityRun) {
    setSelected(run.run_id);
    client.setQueryData<{ items: ActivityRun[] }>(key, old => ({ items: [run, ...(old?.items ?? []).filter(item => item.run_id !== run.run_id)] }));
    void client.invalidateQueries({ queryKey: ['activities', workspace.workspace_id] });
    void client.invalidateQueries({ queryKey: ['memory'] });
  }
  const start = useMutation({ mutationFn: () => api.startActivity(workspace.workspace_id, kind, unit!.unit_id, startKey.current),
    onSuccess: run => { startKey.current = crypto.randomUUID(); setOffset(0); update(run); } });
  const runs = query.data?.items ?? [], pending = runs.find(run => ['queued', 'awaiting_input'].includes(run.status));
  const run = runs.find(item => item.run_id === selected) ?? pending ?? runs[0];
  return <><Heading kicker={kind === 'roundtable' ? 'COMPARE, QUESTION, VERIFY' : 'EXPLAIN TO UNDERSTAND'} title={title}
    description={kind === 'roundtable' ? '先说出自己的思路，再比较观点，最后用依据核验。' : '把方法讲给虚拟学伴，回应它的疑问与错误。'}/>
    <div className="notice">{kind === 'roundtable' ? <MessagesSquare size={20}/> : <GraduationCap size={20}/>}<div><strong>{kind === 'teach' ? '开发互动 · 可选真实学伴追问' : '开发互动 · 固定角色模板'}</strong><br/>{kind === 'teach' ? '概率讲解可请求管理员开放的模型提出一个追问；其他互动仍为预设模板。模型追问不是教师结论，数值由程序另行核验，不评定讲解质量或素养。' : '圆桌仍为预设角色，观点可能包含待纠正的错误；不根据你的文字智能回应。数值另行核验。'}</div></div>
    {workspace.kind === 'temporary' && <p className="page-note">本次互动仅保留在临时空间，不进入全局正式项目的学习记忆。</p>}
    {workspace.status === 'archived' && <p className="page-note">此空间已归档，可以查看历史，恢复后可继续。</p>}
    {query.isPending || content.isPending ? <Loading/> : query.isError || content.isError ? <ErrorNotice error={query.error ?? content.error} retry={() => { void query.refetch(); void content.refetch(); }}/> : <>
      {offset === 0 && !pending && <label className="field section-gap">选择互动单元<select value={unitId} disabled={start.isPending} onChange={e => { const next = new URLSearchParams(params); next.set('activity', e.target.value); setParams(next); startKey.current = crypto.randomUUID(); start.reset(); }}>{!unit && <option value={unitId}>未找到此互动包，请重新选择</option>}{content.data?.items.filter(item => kind === 'teach' || item.flow !== 'teach_cycle_v1').map(item => <option key={item.unit_id} value={item.unit_id}>{item.title}{item.enabled ? '' : '（未启用）'}</option>)}</select></label>}
      {offset === 0 && !pending && <section className="card section-gap"><h2>{unit?.title ?? "选择互动单元"}</h2><p className="body-copy">{kind === 'roundtable' ? '我的方案 → 三种角色观点 → 我的比较与数值核验 → 讨论小结。' : unit?.flow === 'teach_cycle_v1' ? '我的讲解 → 学伴追问 → 我的修正 → 另一题无辅助验证 → TeachReport。' : '我的讲解 → 学伴的预设误区 → 我的纠错与数值核验 → TeachReport。'}</p><p className="card-note">陪学与互动活动在同一空间中依次进行。请先结束当前陪学或另一条互动路线；正式项目和临时会话都可开始本活动。</p>
        {!unit?.enabled && <p className="card-note">请管理员到“知识点与题库”核对并单独启用互动模板。陪学题库的启用不会自动启用此活动。</p>}
        <button className="button primary section-gap" disabled={!unit?.enabled || start.isPending || workspace.status !== 'active'} onClick={() => start.mutate()}>{start.isPending ? '正在发起…' : '开始' + title}</button><ErrorNotice error={start.error}/></section>}
      {run?.view && <p className="card-note">本轮内容：{content.data?.items.find(item => item.content_version === run.view?.activity_version)?.title ?? "历史互动版本"}</p>}
      {run ? <ActivitySession key={run.run_id} run={run} archived={workspace.status !== 'active'} update={update} refresh={() => { void query.refetch(); }}/>
        : offset > 0 && <Empty title="这一页没有历史活动">可以返回上一页查看。</Empty>}
      {runs.length > 0 && <section className="card section-gap"><h2>这个空间的互动记录</h2><div className="plan-history">{runs.map(item => <button className="button" key={item.run_id} onClick={() => setSelected(item.run_id)}>{new Date(item.created_at * 1000).toLocaleString()} · {statusNames[item.status]}</button>)}</div></section>}
      <div className="pagination section-gap"><button className="button" disabled={offset === 0 || query.isFetching} onClick={() => { setOffset(Math.max(0, offset - 20)); setSelected(undefined); }}>上一页</button><span>第 {offset / 20 + 1} 页</span><button className="button" disabled={runs.length < 20 || query.isFetching} onClick={() => { setOffset(offset + 20); setSelected(undefined); }}>下一页</button></div>
    </>}
  </>;
}

function ActivitySession({ run, archived, update, refresh }: { run: ActivityRun; archived: boolean; update: (run: ActivityRun) => void; refresh: () => void }) {
  const intent = useRef({ signature: '', key: '' });
  const submit = useMutation({ mutationFn: (command: { answer: string; reasoning: string; request_model?: boolean } | 'cancel') => {
    if (command === 'cancel') return api.cancelActivity(run);
    const action = run.view?.phase === 'opening' ? 'contribute' : run.view?.phase === 'verification' ? 'verify' : 'conclude';
    const signature = JSON.stringify([run.revision, run.gate_id, action, command]);
    if (intent.current.signature !== signature) intent.current = { signature, key: crypto.randomUUID() };
    return api.activityInput(run, { action, ...command }, intent.current.key);
  }, onSuccess: update, onError: refresh });
  const view = run.view, locked = submit.isPending || archived || run.status !== 'awaiting_input';
  const task = view?.phase === 'verification' ? view.verification_task! : view?.task;
  const events = useQuery({ queryKey: ['activity-events', run.run_id, run.event_seq], queryFn: ({ signal }) => api.runEvents(run, 0, signal, run.kind) });
  return <section className="section-gap">{view?.flow === 'teach_cycle_v1' && <p className="notice">讲解 → 学伴追问 → 修正 → 另一题无辅助数值验证 → 回顾报告</p>}<div className="section-heading"><h2>{view?.phase === 'verification' ? '另一题 · 无辅助数值验证' : view?.phase === 'summary' ? run.kind === 'teach' ? 'TeachReport · 讲解记录' : '圆桌讨论小结' : view?.phase === 'challenge' ? '比较、纠错与核验' : '先留下自己的想法'}</h2><span className="badge">{statusNames[run.status]}</span></div>
    <ErrorNotice error={submit.error}/>{run.status === 'failed' && <div className="notice">流程未能继续（{run.error_code}）。记录已保留，请核对内容状态后重新开始。</div>}
    {view && task ? <div className="grid two activity-layout"><section className="card align-start"><span className="badge neutral">{task.task_id}</span><p className="study-stem">{task.stem}</p><p className="card-note">{task.answer_format}</p>
      {view.phase !== 'summary' && <ActivityForm key={view.phase} phase={view.phase} kind={run.kind} cycle={view.flow === 'teach_cycle_v1'} modelAvailable={run.coaching_available} locked={locked} send={command => submit.mutate(command)}/>}
      {view.check && <div className={`study-feedback ${view.check.status}`}><strong>{view.check.status === 'passed' ? '数值核验通过' : view.check.status === 'failed' ? '数值尚未通过' : '数值待人工复核'}</strong><p>{view.check.message}</p><p>辅助后的核验 · 不是独立检测</p></div>}
      {view.verification_check && <div className="study-feedback"><strong>另一题验证：{view.verification_check.status === 'passed' ? '数值通过' : view.verification_check.status === 'failed' ? '数值未通过' : '数值待复核'}</strong><p>{view.verification_check.message}</p><p>本题未提供辅助，不保证此前从未见过此题；不自动评定迁移能力。</p></div>}
      {view.phase === 'summary' && <><ul className="detail-list">{view.report.map(text => <li key={text}>{text}</li>)}</ul><Link className="button section-gap" to={{ pathname: '/review', search: '?workspace=' + run.workspace_id }}>查看学习记忆与来源</Link></>}
    </section><section className="card align-start"><h2>过程记录</h2><TeachQuestion run={run} update={update} refresh={refresh}/>{view.phase === 'verification' ? <p>本题请独立提交，不提供角色追问；此前讲解在报告中保留。</p> : view.messages.length ? <ol className="activity-messages">{view.messages.map((message, index) => <li key={index} className={message.category === 'student_contribution' ? 'student-contribution' : 'role-opinion'}><div className="card-head"><strong>{message.speaker}</strong><span className="badge neutral">{message.category === 'student_contribution' ? '学生原始贡献' : message.category === 'preset_misconception' ? '预设误区 · 待纠正' : '模板观点 · 待核验'}</span></div>{message.answer && <p>我的数值表达：{message.answer}</p>}<p>{message.text}</p></li>)}</ol> : <Empty title="等待你的第一段表达">先尝试，再邀请学伴加入。</Empty>}</section></div> : <Loading text="正在准备互动任务…"/>}
    {!archived && ['queued', 'awaiting_input'].includes(run.status) && <button className="button section-gap" disabled={submit.isPending} onClick={() => submit.mutate('cancel')}><Square size={14}/>结束本轮活动</button>}
    <details className="card section-gap"><summary>查看互动流程记录</summary>{events.isError ? <ErrorNotice error={events.error}/> : <ol>{events.data?.map(event => <li key={event.seq}>{event.kind === 'run.queued' ? '输入已保存' : event.kind === 'activity.completed' ? '小结已保存' : event.kind === 'activity.updated' ? '互动步骤已更新' : event.kind === 'run.cancelled' ? '已结束本轮' : '流程暂停'}</li>)}</ol>}</details>
  </section>;
}

function ActivityForm({ phase, kind, cycle, modelAvailable, locked, send }: { phase: string; kind: ActivityKind; cycle: boolean; modelAvailable: boolean; locked: boolean; send: (command: { answer: string; reasoning: string; request_model?: boolean }) => void }) {
  const [answer, setAnswer] = useState(''), [reasoning, setReasoning] = useState('');
  const [useModel, setUseModel] = useState(true);
  function submit(event: FormEvent) { event.preventDefault(); if (!locked && answer.trim() && reasoning.trim().length >= 2) send({ answer: answer.trim(), reasoning: reasoning.trim(), request_model: phase === 'opening' && modelAvailable && useModel }); }
  return <form className="form-stack section-gap" onSubmit={submit}><label htmlFor="activity-answer">我的数值表达</label><input id="activity-answer" value={answer} onChange={e => setAnswer(e.target.value)} required maxLength={128} disabled={locked} placeholder="给出你的候选数值；还不确定也可以如实写下"/><label htmlFor="activity-reasoning">{phase === 'verification' ? '本题独立检查依据' : phase === 'opening' ? kind === 'teach' ? '我的讲解' : '我的初步思路' : '我的比较、纠错与检查依据'}</label><textarea id="activity-reasoning" rows={5} value={reasoning} onChange={e => setReasoning(e.target.value)} required minLength={2} maxLength={1500} disabled={locked}/>{phase === 'opening' && modelAvailable && <label className="inline"><input type="checkbox" checked={useModel} disabled={locked} onChange={e => setUseModel(e.target.checked)}/>让 AI 根据本次讲解提出一个问题</label>}{phase === 'opening' && modelAvailable && <p className="card-note">仅发送当前题目、数值表达和本次讲解；请勿填写个人信息。取消勾选可使用预设学伴，不会发送模型请求。</p>}<button className="button primary" disabled={locked || !answer.trim() || reasoning.trim().length < 2}><Send size={16}/>{phase === 'verification' ? '提交本题验证，查看报告' : phase === 'opening' ? '保存表达，邀请学伴' : cycle ? '保存修正，进入新题验证' : '保存核验，生成小结'}</button></form>;
}

function TeachQuestion({ run, update, refresh }: { run: ActivityRun; update: (run: ActivityRun) => void; refresh: () => void }) {
  const cancel = useMutation({ mutationFn: () => api.cancelActivityModel(run), onSuccess: update, onError: refresh });
  const [showFallback, setShowFallback] = useState(false);
  if (!run.view?.model_requested || run.view.phase === 'verification') return null;
  const op = run.coaching;
  const waiting = !!op && ['waiting', 'pending', 'running'].includes(op.status);
  const labels: Record<string, string> = { waiting: '讲解已保存，等待教学步骤', pending: '等待学伴读取讲解', running: '学伴正在根据讲解提问', succeeded: '学伴追问已保存', failed: '本次追问未通过检查，请使用预设追问', cancelled: '已取消本次追问', discarded: '步骤已更新，旧追问已拦截', unknown: '未收到确定结果，不自动重发' };
  return <section className="study-hint" role="status"><h3>针对我的讲解提问</h3>{op && <><span className="badge neutral">{op.simulated ? '模拟学伴' : 'DeepSeek 学伴'}</span><p>{labels[op.status]}</p>{op.result && <><blockquote>“{op.result.quote}”</blockquote><p>{op.result.question}</p><p className="card-note">{op.result.judgment}。可质疑这条追问；它不参与数值判分。</p></>}{waiting && <><button type="button" className="button small" disabled={cancel.isPending} onClick={() => cancel.mutate()}>取消学伴追问</button><button type="button" className="button small" onClick={refresh}>刷新追问状态</button><p className="card-note">可以先使用预设追问继续修正。进入下一步会拦截迟到的回复。</p></>}</>}
    {!op?.result && <><button type="button" className="button small" onClick={() => setShowFallback(!showFallback)}>{showFallback ? '收起预设追问' : '先使用预设追问'}</button>{showFallback && <p>学伴有疑问：为什么应该按球的个数计数，而不能只按颜色种类计算概率？</p>}</>}
    <ErrorNotice error={cancel.error}/>
  </section>;
}
