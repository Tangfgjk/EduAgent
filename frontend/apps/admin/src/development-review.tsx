import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type DevelopmentReview } from '@wenjin/api-client';
import { ErrorNotice, Loading, Success } from '@wenjin/ui';

export function ContentDevelopmentReview({ target, version }: { target: string; version: string }) {
  const client = useQueryClient(), key = ['development-review', target, version];
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => api.developmentReviews(target, version, signal) });
  return <section className="card section-gap"><h3>开发内容核对记录</h3><p className="card-note">核对题意、参考数值、逐级提示、验证题与来源。这里只记录开发核对，不替代数学教师专业审核；新增内容须先核对再启用。</p>
    {query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error}/> : <ReviewForm kind="content" target={target} version={version} revision={query.data.revision} reviews={query.data.reviews} saved={() => { void client.invalidateQueries({ queryKey: key }); void client.invalidateQueries({ queryKey: ['admin-catalog'] }); void client.invalidateQueries({ queryKey: ['admin-activity-catalog'] }); }}/>}</section>;
}

export function ReviewForm({ kind, target, version, revision, reviews, saved }: { kind: 'content' | 'evaluation'; target: string; version: string; revision: number; reviews: DevelopmentReview[]; saved: () => void }) {
  const options = kind === 'content' ? [['development_ready', '开发核对通过'], ['needs_revision', '需要修改']] : [['acceptable', '本条开发参考可接受'], ['model_issue', '模型方向或问题待改进'], ['reference_ambiguous', '参考方向存在歧义'], ['runtime_issue', '运行失败或结果不可用']];
  const [conclusion, setConclusion] = useState(options[0][0]), [note, setNote] = useState('');
  const mutation = useMutation({ mutationFn: () => api.saveDevelopmentReview({ kind, target_id: target, version, expected_revision: revision,
    conclusion: conclusion as 'development_ready' | 'needs_revision' | 'acceptable' | 'model_issue' | 'reference_ambiguous' | 'runtime_issue', note: note.trim() }),
    onSuccess: () => { setNote(''); saved(); }, onError: saved });
  return <><form className="form-stack section-gap" onSubmit={e => { e.preventDefault(); mutation.mutate(); }}>
    <label className="field">复核结论<select value={conclusion} onChange={e => setConclusion(e.target.value)} disabled={mutation.isPending}>{options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label className="field">复核依据<textarea rows={3} minLength={5} maxLength={2000} required value={note} onChange={e => setNote(e.target.value)} disabled={mutation.isPending}/></label>
    <p className="card-note">新意见保留旧版本。内容需要修改时撤回已开放版本；核对通过不会自动启用内容或发布模型配置。</p>
    <button className="button" disabled={mutation.isPending || note.trim().length < 5}>保存复核记录</button><ErrorNotice error={mutation.error}/>{mutation.isSuccess && <Success>复核记录已保存</Success>}
  </form>{reviews.length > 0 && <details className="section-gap"><summary>查看已有复核意见（{reviews.length} 条）</summary><ol>{reviews.map(row => <li key={row.revision}><strong>v{row.revision} · {row.conclusion}</strong><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{row.note}</p><small>{new Date(row.created_at * 1000).toLocaleString()} · 开发复核</small></li>)}</ol></details>}</>;
}
