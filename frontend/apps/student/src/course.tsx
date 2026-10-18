import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation } from 'react-router-dom';
import { api, type Workspace } from '@wenjin/api-client';
import { ErrorNotice, Loading } from '@wenjin/ui';

export function useCourse(wid: string) {
  return useQuery({ queryKey: ['course', wid], queryFn: ({ signal }) => api.course(wid, signal), staleTime: 0 });
}

export function CourseBar({ workspace }: { workspace: Workspace }) {
  const location = useLocation();
  const query = useCourse(workspace.workspace_id), client = useQueryClient();
  const catalog = useQuery({ queryKey: ['student-catalog', workspace.workspace_id], queryFn: ({ signal }) => api.contentCatalog(false, signal, workspace.workspace_id) });
  const [topic, setTopic] = useState(''), [title, setTitle] = useState(''), [units, setUnits] = useState<string[]>([]);
  const [active, setActive] = useState(''), [editing, setEditing] = useState(false);
  const save = useMutation({ mutationFn: () => api.bindCourse(workspace.workspace_id, {
    schema_version: 'curriculum.v1', expected_revision: query.data!.revision, topic: topic.trim(), title: title.trim(), unit_ids: units, active_unit_id: active,
  }), onSuccess: updated, onError: () => { void query.refetch(); } });
  const select = useMutation({ mutationFn: (unit: string) => api.selectCourseUnit(workspace.workspace_id, { expected_revision: query.data!.revision, active_unit_id: unit }), onSuccess: updated, onError: () => { void query.refetch(); } });
  function updated(data: Awaited<ReturnType<typeof api.course>>) {
    client.setQueryData(['course', workspace.workspace_id], data);
    void client.invalidateQueries({ queryKey: ['course', workspace.workspace_id] });
    setEditing(false);
  }
  function submit(e: FormEvent) { e.preventDefault(); save.mutate(); }
  if (query.isPending) return <Loading text="读取课程上下文…"/>;
  if (query.isError) return <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/>;
  const course = query.data, locked = workspace.status !== 'active' || save.isPending || select.isPending;
  return <section className="card" aria-label="课程上下文" style={{ marginBottom: 20 }}>
    {course.active_unit_id ? <><div className="card-head"><div><strong>主题：{course.topic}</strong><p className="card-note">课程：{course.title} · 版本 {course.curriculum_version?.slice(0, 8)} · 改项目名不会改变课程</p></div><button className="button small" disabled={locked} onClick={() => {
      setTopic(course.topic ?? ''); setTitle(course.title ?? ''); setUnits((course.units ?? []).map(u => String(u.unit_id))); setActive(course.active_unit_id ?? ''); setEditing(!editing);
    }}>{editing ? '收起' : '调整课程'}</button></div>
      <label className="field">当前课程单元<select value={course.active_unit_id} disabled={locked} onChange={e => select.mutate(e.target.value)}>{(course.units ?? []).map(unit => <option key={String(unit.unit_id)} value={String(unit.unit_id)}>{String(unit.title)}</option>)}</select></label>
      <p className="card-note">陪学、圆桌和我教 AI 共用此单元；复习、成长证据与策略仍保留本项目历史。进行中的学习/策略任务须先结束才能切换。</p>
    </> : <><strong>请确认此空间的学习主题和课程</strong><p className="card-note">空间名称不用于推断课程。旧记录会保留，确认后新学习使用统一的当前单元；可先进入“主题与课程”，让智能体准备来源、诊断和课程候选；也可手动选择已有内容。</p></>}
    {!course.active_unit_id && <Link className="button primary section-gap" to={'/courses?workspace='+workspace.workspace_id}>从学习主题准备课程</Link>}
    {(!course.active_unit_id || editing) && location.pathname !== '/courses' && workspace.status === 'active' && <form onSubmit={submit} className="form-stack">
      <div className="grid two"><label className="field">学习主题<input required minLength={2} maxLength={120} value={topic} onChange={e => setTopic(e.target.value)}/></label><label className="field">课程名称<input required minLength={2} maxLength={120} value={title} onChange={e => setTitle(e.target.value)}/></label></div>
      <fieldset disabled={locked} style={{ minWidth: 0 }}><legend>课程单元（仅能选择已发布开发内容）</legend>{catalog.data?.items.filter(u => u.enabled).map(unit => <label className="inline" key={unit.unit_id} style={{ marginRight: 16 }}><input type="checkbox" style={{ width: 16, minWidth: 16, flex: '0 0 16px' }} checked={units.includes(unit.unit_id)} onChange={e => {
        const next = e.target.checked ? [...units, unit.unit_id] : units.filter(id => id !== unit.unit_id);
        setUnits(next); if (!next.includes(active)) setActive(next[0] ?? '');
      }}/>{unit.title}</label>)}</fieldset>
      <ErrorNotice error={catalog.error}/>{catalog.isSuccess && !catalog.data.items.some(u => u.enabled) && <p>暂无已发布内容，请管理员先审核并启用。</p>}
      <label className="field">起始单元<select required value={active} disabled={locked} onChange={e => setActive(e.target.value)}><option value="" disabled>请选择</option>{units.map(id => <option key={id} value={id}>{catalog.data?.items.find(u => u.unit_id === id)?.title ?? id}</option>)}</select></label>
      <button className="button primary" disabled={locked || !units.length || !topic.trim() || !title.trim() || !active}>确认课程绑定</button>
    </form>}
    <ErrorNotice error={save.error ?? select.error}/>
  </section>;
}
