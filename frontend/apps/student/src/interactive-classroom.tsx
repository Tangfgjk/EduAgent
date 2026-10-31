import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, type Workspace } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading, MediaBudgets } from '@wenjin/ui';
import { useCourse } from './course';
import { ClassroomPlayer } from './classroom-deferred';
import { StudentClassroomGeneration } from './generation';

export function InteractiveClassroomPage({ workspace }: { workspace: Workspace }) {
  const course = useCourse(workspace.workspace_id);
  const prepare=useMutation({mutationFn:()=>api.prepareClassroom(workspace.workspace_id),onSuccess:data=>{setSelected(data.resource_id);void resources.refetch().then(()=>requestAnimationFrame(()=>document.querySelector('.classroom-player')?.scrollIntoView({block:'start'})));}});
  const [selected, setSelected] = useState('');
  const resources = useQuery({ queryKey: ['learning-resources', workspace.workspace_id],
    queryFn: ({ signal }) => api.learningResources(workspace.workspace_id, signal),
    refetchInterval: 5000 });
  const lessons = (resources.data?.items ?? []).filter(item => item.unit_id === course.data?.active_unit_id && item.classroom);
  const lesson = lessons.find(item => item.resource_id === selected) ?? lessons[0];
  return <>
    <Heading kicker="LEARN, WATCH, AND THINK" title="互动课堂" description="跟随幻灯片理解概念，暂停思考并回答课堂问题。课堂与当前学习空间、课程保持一致。"/>
    <div className="actions"><Link className="button" to={'/courses?workspace='+workspace.workspace_id}>选择或调整课程</Link><Link className="button" to={'/study?workspace='+workspace.workspace_id}>课后陪学练习</Link><Link className="button" to={'/library?workspace='+workspace.workspace_id}>查阅本地资料</Link><button className="button" onClick={()=>{void resources.refetch();}}>刷新课堂</button></div>
    <details className="card section-gap" open={!lesson}><summary>准备分步数学课堂</summary><p>从当前课程的练习例题编排：观察、逐步推演、自己尝试。运算逐步核验，文字公式由程序绘制；不调用收费图片或视频服务。当前支持有理数四则运算。</p><button className="button primary" disabled={prepare.isPending||workspace.status!=='active'} onClick={()=>prepare.mutate()}>{prepare.isPending?'正在编排…':'准备分步课堂'}</button><ErrorNotice error={prepare.error}/></details>
    <ErrorNotice error={course.error ?? resources.error} retry={()=>{void course.refetch();void resources.refetch();}}/>
    {course.isPending || resources.isPending ? <Loading text="正在查找当前课程的互动课堂…"/> : course.isSuccess && resources.isSuccess && <>
      {lesson ? <>
        <section className="card section-gap"><label className="field">选择课堂版本<select value={lesson.resource_id} onChange={event=>setSelected(event.target.value)}>{lessons.map(item=><option key={item.resource_id} value={item.resource_id}>{item.title}</option>)}</select></label><p>先点击“播放课堂”，遇到问题可以暂停；课堂回答用于练习，独立检测单独进行。</p></section>
        <ClassroomPlayer key={workspace.workspace_id+lesson.resource_id+lesson.sha256} wid={workspace.workspace_id} resourceId={lesson.resource_id} hash={lesson.sha256} manifest={lesson.classroom!}/>
        <MediaBudgets wid={workspace.workspace_id} resourceId={lesson.resource_id}/>
      </> : <section className="card section-gap"><Empty title={course.data.active_unit_id?'当前课程还没有互动课堂':'先确认想学习的课程'}>普通文字资料不会自动成为幻灯片课堂。动态生成课程可在下方查看准备状态；固定单元可先继续陪学，或前往课程页准备新的主题课程。</Empty></section>}
      {workspace.status==='active' && course.data.active_unit_id?.startsWith('dynamic.generated.') && <section className="card section-gap"><h2>准备课堂</h2><StudentClassroomGeneration workspace={workspace} unitId={course.data.active_unit_id??undefined} refresh={()=>{void resources.refetch();}}/></section>}
    </>}
  </>;
}
