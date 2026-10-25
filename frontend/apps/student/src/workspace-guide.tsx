import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, type Workspace } from '@wenjin/api-client';
import { ErrorNotice, Loading } from '@wenjin/ui';

export function WorkspaceGuide({ workspace }: { workspace: Workspace }) {
  const query = useQuery({ queryKey: ['next-action', workspace.workspace_id],
    queryFn: ({ signal }) => api.nextAction(workspace.workspace_id, signal), refetchInterval: 5000 });
  const search = '?workspace=' + workspace.workspace_id;
  return <section className="card section-gap" aria-label="当前空间的下一步"><h2>{workspace.title} · 下一步</h2>
    {query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error}/> : <><p>{query.data.explanation}</p><Link className="button primary" to={'/' + query.data.destination + search}>{query.data.title}</Link></>}
    <details className="section-gap"><summary>我想自己选择学习方式</summary><div className="actions section-gap">{[['courses','目标与课程'],['classroom','互动课堂'],['plan','学习安排'],['study','陪学'],['roundtable','圆桌讨论'],['teach','我教 AI'],['units','单元库'],['planner','规划与策略'],['skill-evolution','Skill 改进']].map(([path,label]) => <Link key={path} className="button small" to={'/'+path+search}>{label}</Link>)}</div></details>
  </section>;
}
