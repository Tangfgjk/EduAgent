import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, type Workspace } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading, MediaBudgets } from '@wenjin/ui';
import { useCourse } from './course';
import { ClassroomPlayer } from './classroom-deferred';
import { StudentClassroomGeneration } from './generation';

export function InteractiveClassroomPage({ workspace }: { workspace: Workspace }) {
  const course = useCourse(workspace.workspace_id);
  const [selected, setSelected] = useState('');
  const resources = useQuery({ queryKey: ['learning-resources', workspace.workspace_id],
    queryFn: ({ signal }) => api.learningResources(workspace.workspace_id, signal),
    refetchInterval: 5000 });
  const lessons = (resources.data?.items ?? []).filter(item => item.unit_id === course.data?.active_unit_id && item.classroom);
  const lesson = lessons.find(item => item.resource_id === selected) ?? lessons[0];
  return <>
    <Heading kicker="LEARN, WATCH, AND THINK" title="互动课堂" description="跟随幻灯片理解概念，暂停思考并回答课堂问题。课堂与当前学习空间、课程保持一致。"/>
    <div className="actions"><Link className="button" to={'/courses?workspace='+workspace.workspace_id}>选择或调整课程</Link><Link className="button" to={'/study?workspace='+workspace.workspace_id}>课后陪学练习</Link><button className="button" onClick={()=>{void resources.refetch();}}>刷新课堂</button></div>
    <ErrorNotice error={course.error ?? resources.error} retry={()=>{void course.refetch();void resources.refetch();}}/>
    {course.isPending || resources.isPending ? <Loading text="正在查找当前课程的互动课堂…"/> : course.isSuccess && resources.isSuccess && <>
      {lesson ? <>
        <section className="card section-gap"><label className="field">选择课堂版本<select value={lesson.resource_id} onChange={event=>setSelected(event.target.value)}>{lessons.map(item=><option key={item.resource_id} value={item.resource_id}>{item.title} · {item.sha256.slice(0,8)}</option>)}</select></label><p>先点击“播放课堂”，遇到问题可以暂停；课堂回答用于练习，独立检测单独进行。</p></section>
        <ClassroomPlayer key={workspace.workspace_id+lesson.resource_id+lesson.sha256} wid={workspace.workspace_id} resourceId={lesson.resource_id} hash={lesson.sha256} manifest={lesson.classroom!}/>
        <details className="card section-gap"><summary>配图、配音、视频与生成状态</summary><MediaBudgets wid={workspace.workspace_id} resourceId={lesson.resource_id}/></details>
      </> : <section className="card section-gap"><Empty title={course.data.active_unit_id?'当前课程还没有互动课堂':'先确认想学习的课程'}>普通文字资料不会自动成为幻灯片课堂。请先确认课程，再查看下方课堂准备状态。</Empty></section>}
      {workspace.status==='active' && <section className="card section-gap"><h2>准备课堂</h2><StudentClassroomGeneration workspace={workspace} unitId={course.data.active_unit_id??undefined} refresh={()=>{void resources.refetch();}}/></section>}
    </>}
  </>;
}
