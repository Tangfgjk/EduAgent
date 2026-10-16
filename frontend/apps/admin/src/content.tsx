import { ProbabilityActivityPanel } from './probability-activity';
import { CatalogPanel } from './catalog';
import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BookOpen, ClipboardCheck } from 'lucide-react';
import { api, type AnswerReview } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading, Modal } from '@wenjin/ui';

export function ContentPage() {
  const client = useQueryClient(), [acknowledged, setAcknowledged] = useState(false);
  const query = useQuery({ queryKey: ['admin-content'], queryFn: ({ signal }) => api.content(true, signal) });
  const release = useMutation({ mutationFn: () => api.releaseContent(query.data!, !query.data!.enabled),
    onSuccess: data => { client.setQueryData(['admin-content'], data); setAcknowledged(false); },
    onError: () => { void query.refetch(); } });
  return <><Heading kicker="QUALITY, BEFORE SCALE" title="知识点与题库" description="先核对题目、答案和提示，再开放确定版本的开发单元。"/>
    <div className="notice"><BookOpen size={18}/><div>当前内容是自编开发样例，尚无专业教师审核。此处启用只允许模拟和成人测试账号使用，真实学生账号暂不开放。</div></div>
    {query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/> : <>
      <section className="card section-gap"><div className="card-head"><h2>{query.data.title}</h2><span className="badge">{query.data.enabled ? '开发测试已启用' : '未启用'}</span></div><p className="card-note">内容版本：<code style={{ overflowWrap: 'anywhere' }}>{query.data.content_version}</code></p><label className="field section-gap"><span><input type="checkbox" checked={acknowledged} onChange={e => setAcknowledged(e.target.checked)} style={{ width: 'auto', marginRight: 8 }} disabled={release.isPending}/>我已核对开发内容，并了解它尚未通过专业教学审核</span></label><button className="button primary" disabled={!acknowledged || release.isPending} onClick={() => release.mutate()}>{release.isPending ? '正在保存…' : query.data.enabled ? '停用开发单元' : '启用开发测试'}</button>{release.isError && <ErrorNotice error={release.error}/>}</section>
      <div className="grid two section-gap">{(query.data.tasks ?? []).map(task => <section className="card align-start" key={String(task.id)}><div className="card-head"><h2>{String(task.id)}</h2><span className="badge neutral">{String(task.stage)}</span></div><p className="body-copy">{String(task.stem)}</p><p className="card-note">数值真值：{String(task.answer)}</p><p className="card-note">输入要求：{String(task.format)}</p>{Array.isArray(task.hints) && task.hints.length > 0 && <ol className="detail-list">{task.hints.map((hint, index) => <li key={index}>L{index}：{String(hint)}</li>)}</ol>}</section>)}</div>
    </>}
    <CatalogPanel/>
    <ActivityContentPanel/>
    <ProbabilityActivityPanel/>
  </>;
}

export function ReviewsPage() {
  const client = useQueryClient(), [status, setStatus] = useState<'pending' | 'resolved'>('pending'), [offset, setOffset] = useState(0), [selected, setSelected] = useState<AnswerReview>();
  const key = ['answer-reviews', status, offset];
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => api.answerReviews(status, offset, signal) });
  return <><Heading kicker="KEEP UNCERTAINTY VISIBLE" title="内容与解答复核" description="不可验证不等于答错。保留原始提交，补充人工判断与依据。"/><div className="notice"><ClipboardCheck size={18}/>本页处理无法自动验证的数值提交。人工判断保存为独立备注，不重写原始输入、自动验证结果或素养。</div><div className="tabs" role="tablist" aria-label="复核状态">{(['pending', 'resolved'] as const).map(value => <button key={value} role="tab" aria-selected={status === value} onClick={() => { setStatus(value); setOffset(0); }}>{value === 'pending' ? '待复核' : '已复核'}</button>)}</div>
    {query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/> : query.data.items.length ? <div className="grid two">{query.data.items.map(row => <section className="card align-start" key={row.review_id}><div className="card-head"><h2>{row.task_id}</h2><span className="badge neutral">{row.source_kind === 'synthetic' ? '模拟数据' : row.source_kind === 'adult_test' ? '成人测试' : '真实学生'}</span></div><p className="body-copy">{String(row.task.stem)}</p><p className="card-note">提交答案：{row.answer}</p><p className="card-note">步骤与依据：{row.reasoning || '未填写'}</p>{row.status === 'pending' ? <button className="button section-gap" onClick={() => setSelected(row)}>复核这条解答</button> : <><p className="card-note">人工结论：{row.conclusion === 'correct' ? '数值正确' : row.conclusion === 'incorrect' ? '数值错误' : '仍无法判断'}</p><p className="card-note">依据：{row.note}</p></>}</section>)}</div> : <section className="card"><Empty title={status === 'pending' ? '当前没有待复核解答' : '当前没有已复核记录'}>只有真实提交中出现不可验证结果，才会进入此队列。</Empty></section>}
    <div className="pagination section-gap"><button className="button" disabled={offset === 0 || query.isFetching} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button><span>第 {offset / 20 + 1} 页</span><button className="button" disabled={query.isFetching || (query.data?.items.length ?? 0) < 20} onClick={() => setOffset(offset + 20)}>下一页</button></div>
    {selected && <ReviewDialog key={selected.review_id} review={selected} close={() => setSelected(undefined)} saved={() => { setSelected(undefined); void client.invalidateQueries({ queryKey: ['answer-reviews'] }); }}/>}
  </>;
}

function ReviewDialog({ review, close, saved }: { review: AnswerReview; close: () => void; saved: () => void }) {
  const [conclusion, setConclusion] = useState<'correct' | 'incorrect' | 'unresolved'>('unresolved'), [note, setNote] = useState('');
  const mutation = useMutation({ mutationFn: () => api.resolveAnswer(review, conclusion, note.trim()), onSuccess: saved });
  function submit(event: FormEvent) { event.preventDefault(); if (note.trim().length >= 2 && !mutation.isPending) mutation.mutate(); }
  return <Modal title="复核这条解答" onClose={close} busy={mutation.isPending}><form onSubmit={submit}><p className="dialog-copy">{String(review.task.stem)}<br/>该版本数值真值：{String(review.task.answer)}<br/>学生提交：{review.answer}</p><label className="field">人工结论<select value={conclusion} disabled={mutation.isPending} onChange={e => setConclusion(e.target.value as typeof conclusion)}><option value="unresolved">仍无法判断</option><option value="correct">数值正确</option><option value="incorrect">数值错误</option></select></label><label className="field">复核依据<textarea value={note} onChange={e => setNote(e.target.value)} required minLength={2} maxLength={1000} disabled={mutation.isPending}/></label><ErrorNotice error={mutation.error}/><div className="dialog-footer"><button type="button" className="button" disabled={mutation.isPending} onClick={close}>取消</button><button className="button primary" disabled={mutation.isPending || note.trim().length < 2}>保存复核意见</button></div></form></Modal>;
}


function ActivityContentPanel() {
  const client = useQueryClient(), [open, setOpen] = useState(false), [ack, setAck] = useState(false);
  const query = useQuery({ queryKey: ['admin-activity-content'], queryFn: ({ signal }) => api.activityContent(true, signal), staleTime: 0 });
  const release = useMutation({ mutationFn: () => api.releaseActivityContent(query.data!, !query.data!.enabled),
    onSuccess: data => { client.setQueryData(['admin-activity-content'], data); setOpen(false); setAck(false); },
    onError: () => { void query.refetch(); } });
  return <section className="card section-gap"><h2>圆桌与我教 AI · 互动模板审核</h2><p className="card-note">独立版本、单独启停。请同时核对题目、预设错误与角色话术；启用此模板只面向模拟和成人测试账号。解释质量与素养尚未评定。</p>
    {query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error}/> : <><span className="badge">{query.data.enabled ? '互动开发已启用' : '互动开发未启用'}</span><p className="card-note">版本：<code style={{ overflowWrap: 'anywhere' }}>{query.data.content_version}</code></p><details className="section-gap"><summary>查看互动题与完整角色模板</summary>{query.data.tasks?.map((task, index) => <div key={index}><p className="body-copy">互动题：{String(task.stem)}</p><p>数值真值：{String(task.answer)}</p><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(task.activity_templates, null, 2)}</pre><p className="card-note">{String(task.boundary)}</p></div>)}</details><button className="button section-gap" onClick={() => { setAck(false); setOpen(true); }}>审核互动模板</button></>}
    {open && <Modal title="审核互动模板" onClose={() => setOpen(false)} busy={release.isPending}><p className="dialog-copy">角色是固定模板，包含故意设置的移项错误。管理员只确认开发使用范围，不能将此操作视为专业教师审核。</p><label className="field"><span><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} disabled={release.isPending} style={{ width: 'auto', marginRight: 8 }}/>我已核对互动内容，理解它仅供开发测试</span></label><ErrorNotice error={release.error}/><div className="dialog-footer"><button className="button" disabled={release.isPending} onClick={() => setOpen(false)}>取消</button><button className="button primary" disabled={!ack || release.isPending} onClick={() => release.mutate()}>{query.data?.enabled ? '停用互动开发' : '启用互动开发'}</button></div></Modal>}
  </section>;
}
