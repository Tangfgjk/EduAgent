import { useInfiniteQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api, type TutorRun } from '@wenjin/api-client';
import { ErrorNotice, Loading } from '@wenjin/ui';

const actions: Record<string, string> = { start: '开始陪学', answer: '提交解答', hint: '请求提示', reflect: '回望方法', next: '继续学习', skip: '记录未完成' };
const statuses: Record<string, string> = { waiting: '等待教学步骤', pending: '等待模型', running: '模型处理中', succeeded: '当时已保存', failed: '未获得可用建议', cancelled: '已取消', discarded: '旧结果已拦截', unknown: '结果未知，不自动重试' };
const numeric: Record<string, string> = { passed: '数值验证通过', failed: '数值验证未通过', unverifiable: '数值不可自动验证' };

export function StudyHistory({ run }: { run: TutorRun }) {
  const [opened, setOpened] = useState(false);
  const query = useInfiniteQuery({
    queryKey: ['tutor-history', run.workspace_id, run.run_id, run.event_seq, run.coaching?.operation_id, run.coaching?.status],
    enabled: opened, initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => api.tutorHistory(run, pageParam, signal),
    getNextPageParam: last => last.has_more ? last.cursor : undefined,
    refetchInterval: q => q.state.data?.pages.some(page => page.items.some(row => row.model && ['waiting', 'pending', 'running'].includes(row.model.status))) ? 3000 : false,
  });
  return <details className="card section-gap study-history" onToggle={event => setOpened(event.currentTarget.open)}>
    <summary>本次学习回顾 · 查看我的输入与 AI 建议</summary>
    <p className="card-note">只展示当前空间的本次陪学。你的表达、预设提示、历史模型建议和程序数值核验分别保留；旧建议仅用于回顾，不代表当前结论或掌握程度。</p>
    {query.isPending ? <Loading text="正在读取学习回顾…"/> : query.isError ? <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/> : <>
      <ol className="study-timeline">{query.data.pages.flatMap(page => page.items).map(row => <li key={row.sequence}>
        <div className="card-head"><strong>{actions[row.action]}</strong><time>{new Date(row.created_at * 1000).toLocaleString()}</time></div>
        {row.task_id && <p className="card-note">{row.stage === 'checkpoint' ? '独立检测' : row.stage === 'diagnostic' ? '诊断' : '练习'} · {row.task_id}</p>}
        {row.task_stem && <details><summary>查看当时题目</summary><p>{row.task_stem}</p></details>}
        {(row.answer || row.reasoning || row.thinking || row.reflection) && <section className="history-source"><span className="badge neutral">我的输入</span>
          {row.answer && <p>我的答案：{row.answer}</p>}{row.reasoning && <p>我的依据：{row.reasoning}</p>}{row.thinking && <p>我的思路：{row.thinking}</p>}{row.reflection && <p>我的反思：{row.reflection}</p>}
        </section>}
        {row.preset_hint && <section className="history-source"><span className="badge neutral">预设提示</span><p>{row.preset_hint}</p></section>}
        {row.model && <section className="history-source"><span className="badge">AI 当时的建议</span><p>{row.model.vendor === 'fake' ? '模拟模型' : row.model.model} · {statuses[row.model.status]}</p>
          {row.model.result && <><blockquote>引用：“{row.model.result.quote}”</blockquote><p>{row.model.result.question}</p><p className="card-note">历史建议，不自动判分；当时配置 {row.model.prompt_version}。</p></>}
        </section>}
        {row.numeric_status && <section className="history-source"><span className="badge neutral">程序数值核验</span><p>{numeric[row.numeric_status]}；未自动审核你的步骤、推理或素养。</p></section>}
        {!row.processed && ['answer', 'hint', 'reflect', 'skip'].includes(row.action) && <p className="card-note">输入已保存，尚无完成的教学记录；不计作已验证。</p>}
      </li>)}</ol>
      {query.hasNextPage && <button className="button" disabled={query.isFetchingNextPage} onClick={() => { void query.fetchNextPage(); }}>加载后续记录</button>}
      <button className="button text" disabled={query.isFetching} onClick={() => { void query.refetch(); }}>刷新学习回顾</button>
    </>}
  </details>;
}
