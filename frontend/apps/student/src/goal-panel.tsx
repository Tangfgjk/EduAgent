import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, type Workspace } from '@wenjin/api-client';
import { ErrorNotice } from '@wenjin/ui';

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function GoalPanel({ workspace }: { workspace: Workspace }) {
  const query = useQuery({ queryKey: ['next-action', workspace.workspace_id],
    queryFn: ({ signal }) => api.nextAction(workspace.workspace_id, signal), staleTime: 5000 });
  const goal = query.data?.learning_goal;
  const search = '?workspace=' + workspace.workspace_id;
  const decisions = record(goal) && Array.isArray(goal.decisions) ? goal.decisions.filter(record) : [];
  return <section className="soft-box section-gap" aria-label="共享学习目标与依据">
    <strong>围绕同一个学习目标</strong>
    <p>{record(goal) && goal.topic ? String(goal.topic) : '确认课程后，互动、规划、证据和改进会共同使用当前课程目标。'}</p>
    <ErrorNotice error={query.error}/>
    {!!decisions.length && <details><summary>查看最近的 Agent 调整依据</summary>
      {decisions.map(item => <p key={String(item.operation_id)}>{String(item.reason)}<br/><small>建议：{String(item.action)} · 支持：{String(item.support)} · 效果待验证</small></p>)}
    </details>}
    <div className="actions section-gap">{[['courses','课程与资源'],['planner','规划与策略'],['evidence','成长证据'],['skill-evolution','Skill 改进']].map(([path,label]) =>
      <Link key={path} className="button small" to={'/'+path+search}>{label}</Link>)}</div>
    <p className="card-note">共用当前项目目标及有界证据；生成资源仍受独立开关与预算控制，不自动开启收费媒体。</p>
  </section>;
}
