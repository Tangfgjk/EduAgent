import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, type Workspace } from '@wenjin/api-client';
import { ErrorNotice, Loading } from '@wenjin/ui';
import { useCourse } from './course';

export function CourseOverview({ workspace }: { workspace: Workspace }) {
  const wid = workspace.workspace_id, course = useCourse(wid);
  const catalog = useQuery({ queryKey: ['student-catalog', wid], queryFn: ({ signal }) => api.contentCatalog(false, signal, wid) });
  const activities = useQuery({ queryKey: ['activity-catalog', wid], queryFn: ({ signal }) => api.activityCatalog(false, signal, wid) });
  const plans = useQuery({ queryKey: ['runs', wid], queryFn: ({ signal }) => api.runs(wid, signal) });
  const unit = catalog.data?.items.find(item => item.unit_id === course.data?.active_unit_id);
  const current = course.data?.units?.find(item => item.unit_id === course.data?.active_unit_id);
  const planReady = workspace.kind === 'temporary' || (!!plans.data?.items.some(item => item.status === 'completed') && !plans.data.items.some(item => ['queued', 'awaiting_input'].includes(item.status)));
  const bound = !!course.data?.active_unit_id, archived = workspace.status !== 'active';
  const routes = [
    { route: 'study', title: '津津陪学', available: unit?.enabled, description: '独立尝试、分级求助、反思与无辅助检测。' },
    ...(['roundtable', 'teach'] as const).map(kind => ({ route: kind, title: kind === 'roundtable' ? '圆桌讨论' : '我教 AI',
      available: activities.data?.items.some(item => item.parent_unit_id === course.data?.active_unit_id && item.enabled && (kind === 'teach' ? item.flow !== 'roundtable_cycle_v1' : item.flow !== 'teach_cycle_v1')),
      description: kind === 'roundtable' ? '先表达，再比较视角、修正并验证。' : '讲清方法，回应追问，再做独立验证。' })),
  ];
  return <section className="card section-gap" aria-label="当前课程概览">
    <div className="card-head"><div><span className="kicker">YOUR CURRENT COURSE</span><h2>当前课程与学习路线</h2></div><span className="badge neutral">{archived ? '归档 · 只读' : bound ? '已确认课程' : '等待课程确认'}</span></div>
    {course.isPending ? <Loading/> : course.isError ? <ErrorNotice error={course.error}/> : bound ? <><h3>{course.data.title}</h3><p>{current?.title ?? '当前单元'} · 版本 {current?.version.slice(0, 8)}</p><p className="card-note">主题：{course.data.topic}。三条路线沿用当前单元；切换空间不会混用课程。开始按钮与运行关口仍在各活动页检查。</p></> : <p>先在下方输入主题、完成诊断并确认已审核课程，或到“数学单元”选择已发布内容并在顶部确认绑定。</p>}
    <ErrorNotice error={catalog.error ?? activities.error ?? plans.error}/>
    {bound && !archived && !planReady && <div className="notice">正式项目先确认学习计划，再开始陪学。<Link className="text-link" to={'/plan?workspace='+wid}>前往学习计划 →</Link></div>}
    <div className="grid three section-gap">{routes.map(item => {
      const loading = catalog.isPending || activities.isPending || plans.isPending || course.isPending;
      const failed = course.isError || (item.route === 'study' ? catalog.isError || plans.isError : activities.isError);
      const status = failed ? '状态读取失败' : loading ? '正在核对' : !bound ? '先确认课程' : archived ? '可查看历史' : !item.available ? '当前单元尚未开放' : item.route === 'study' && !planReady ? '先确认计划' : '内容已开放';
      return <article className="course-route" key={item.route}><span className="badge neutral">{status}</span><h3>{item.title}</h3><p>{item.description}</p><Link className="button small" to={`/${item.route}?workspace=${wid}`}>{archived ? '查看历史' : '查看'+item.title}</Link></article>;
    })}</div><p className="card-note">同一空间依次进行陪学和活动；已有任务请先完成或结束。未开放不代表你不会，也不会自动切换成其他单元。</p>
    <div className="actions"><Link className="button small" to={'/review?workspace='+wid}>错题与复习</Link><Link className="button small" to={'/evidence?workspace='+wid}>查看成长证据</Link><Link className="button small" to={'/planner?workspace='+wid}>规划与策略</Link><Link className="button small" to={'/classroom?workspace='+wid}>进入互动课堂</Link><a className="text-link" href="#course-resources">查看文字与其他资料 ↓</a></div>
  </section>;
}
