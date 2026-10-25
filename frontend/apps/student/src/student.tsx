import { InteractiveClassroomPage } from './interactive-classroom';
import {PrivacyControls} from '@wenjin/ui';
import { EvolutionWorkbench } from '@wenjin/ui';
import { Units } from './units';
import { WorkspaceGuide } from './workspace-guide';
import { CourseBar } from './course';
import { GenerationPage } from './generation';
import { useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, Navigate, Route, Routes, useNavigate, useSearchParams } from 'react-router-dom';
import { Archive, ArrowRight, ChartNoAxesCombined, ChevronRight, Compass, FolderOpen, GraduationCap, History, MessageCircle, MessageCirclePlus, MessagesSquare, Pencil, Plus, Settings, Undo2 } from 'lucide-react';
import { api, ApiError, type Account, type Workspace } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading, MathGarden, Modal, Shell, Soon, Success, type NavItem } from '@wenjin/ui';
import { LearningCycle } from '@wenjin/math';
import { PlanPage } from './plan';
import { StudyPage } from './study';
import { ReviewPage } from './review';
import { ActivityPage } from './activities';
import { EvidencePage } from './evidence';
import { PlannerPage } from './planner';

const navigation: NavItem[] = [
  { path: 'home', label: '学习工作台', icon: Compass },
  { path: 'spaces', label: '学习空间', icon: FolderOpen },
  { path: 'classroom', label: '互动课堂', icon: GraduationCap },
  { path: 'review', label: '复习', icon: History },
  { path: 'evidence', label: '成长', icon: ChartNoAxesCombined },
  { path: 'planner', label: '规划与策略', icon: Compass },
  { path: 'skill-evolution', label: 'Skill 改进', icon: GraduationCap },
  { path: 'settings', label: '我的', icon: Settings },
];
type Action = { mode: 'create'; kind: Workspace['kind'] } | { mode: 'rename' | 'archive' | 'restore'; id: string };

export function StudentApp({ account }: { account: Account }) {
  const queryClient = useQueryClient(), navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const storageKey = 'wenjin.workspace.' + account.id;
  const [saved, setSaved] = useState(() => { try { return sessionStorage.getItem(storageKey); } catch { return null; } });
  const [action, setAction] = useState<Action | null>(null), [notice, setNotice] = useState('');
  const queryKey = ['workspaces', account.id];
  const spaces = useQuery({ queryKey, queryFn: ({ signal }) => api.workspaces(signal) });
  const items = spaces.data ?? [];
  const requestedId = params.get('workspace') ?? saved;
  const current = requestedId ? items.find(item => item.workspace_id === requestedId) : items.find(item => item.status === 'active') ?? items[0];
  const unavailable = !!requestedId && spaces.isSuccess && !current;
  const search = current ? '?workspace=' + current.workspace_id : '';
  function choose(workspace?: Workspace) {
    const id = workspace?.workspace_id ?? null;
    setSaved(id);
    try { if (id) sessionStorage.setItem(storageKey, id); else sessionStorage.removeItem(storageKey); } catch { /* URL 仍保留当前范围。 */ }
    setParams(id ? { workspace: id } : {}, { replace: true });
  }
  function enter(workspace: Workspace) { choose(workspace); navigate({ pathname: '/home', search: '?workspace=' + workspace.workspace_id }); }
  const sidebar = <div className="workspace-picker"><label htmlFor="workspace-picker">当前工作空间</label><select id="workspace-picker" value={current?.workspace_id ?? ''} disabled={spaces.isPending || spaces.isError} onChange={event => choose(items.find(item => item.workspace_id === event.target.value))}><option value="" disabled>选择一个项目或临时会话</option>{items.map(item => <option key={item.workspace_id} value={item.workspace_id}>{item.kind === 'temporary' ? '临时 · ' : ''}{item.title}{item.status === 'archived' ? '（已归档）' : ''}</option>)}</select><div className="picker-actions"><button className="button small" onClick={() => setAction({ mode: 'create', kind: 'project' })}><Plus size={13}/>项目</button><button className="button small" onClick={() => setAction({ mode: 'create', kind: 'temporary' })}><MessageCirclePlus size={13}/>临时会话</button></div></div>;
  const context = current ? <div className={`context-bar ${current.kind === 'temporary' ? 'temporary' : ''}`}><div className="inline"><span className="context-icon">{current.kind === 'project' ? <FolderOpen size={20}/> : <MessageCircle size={20}/>}</span><div><strong>{current.title}</strong><small>{current.kind === 'temporary' ? '临时空间 · 不自动进入正式项目的长期学习画像' : '正式项目 · 陪学、复习与讨论将围绕此项目展开'}</small></div></div><span className={`badge ${current.status === 'archived' ? 'gold' : ''}`}>{current.status === 'archived' ? '已归档 · 只读' : current.kind === 'project' ? '项目空间' : '临时会话'}</span></div> : undefined;
  const target = action && action.mode !== 'create' ? items.find(item => item.workspace_id === action.id) : undefined;
  return <Shell role="student" navigation={navigation} sidebar={sidebar} context={<>{context}{current && <CourseBar key={current.workspace_id} workspace={current}/>}</>} search={search}>
    {notice && <Success>{notice}</Success>}
    {spaces.isPending ? <Loading text="正在读取你的工作空间…"/> : spaces.isError ? <ErrorNotice error={spaces.error} retry={() => { void spaces.refetch(); }}/> : unavailable ? <Empty title="这个工作空间暂不可访问" action={<button className="button" onClick={() => { choose(); navigate('/spaces'); }}>返回项目列表</button>}>它可能不属于当前账号。请从自己的项目列表重新选择。</Empty> : <Routes>
      <Route path="home" element={<Home account={account} items={items} current={current} search={search} create={kind => setAction({ mode: 'create', kind })} enter={enter}/>}/>
      <Route path="spaces" element={<Spaces items={items} current={current} enter={enter} action={setAction}/>}/>
      <Route path="courses" element={current ? <GenerationPage key={current.workspace_id} workspace={current}/> : <Empty title="先创建或选择学习空间">主题准备和生成课程归属于自己的项目或临时会话。</Empty>}/>
      <Route path="classroom" element={current ? <InteractiveClassroomPage key={current.workspace_id} workspace={current}/> : <Empty title="先选择学习空间">课堂将围绕当前项目的已确认课程展开。</Empty>}/>
      <Route path="units" element={<Units search={search}/>}/>
      <Route path="settings" element={<SettingsView account={account}/>}/>
      <Route path="skill-evolution" element={current?<EvolutionWorkbench key={current.workspace_id} workspace={current}/>:<Empty title="先选择正式项目">请先选择自己的项目。</Empty>}/>
      <Route path="evidence" element={<EvidencePage key={current?.workspace_id ?? 'global'} workspace={current}/>}/>
      {['plan', 'study', 'review', 'roundtable', 'teach', 'planner'].map(page => <Route key={page} path={page} element={current ? page === 'plan' ? <PlanPage key={current.workspace_id} workspace={current}/> : page === 'study' ? <StudyPage key={current.workspace_id} workspace={current}/> : page === 'review' ? <ReviewPage key={current.workspace_id} workspace={current}/> : page === 'roundtable' || page === 'teach' ? <ActivityPage key={current.workspace_id + page} workspace={current} kind={page}/> : <PlannerPage key={current.workspace_id} workspace={current}/> : <Empty title="先选择一个学习空间" action={<button className="button primary" onClick={() => setAction({ mode: 'create', kind: 'project' })}><Plus size={16}/>新建学习项目</button>}>正式项目帮助你持续推进目标；临时会话适合一次探索。</Empty>}/>)}
      <Route path="*" element={<Navigate to={{ pathname: '/home', search }} replace/>}/>
    </Routes>}
    {action && (action.mode === 'create' || target) && <WorkspaceDialog key={action.mode + (target?.workspace_id ?? '')} action={action} target={target} onClose={() => setAction(null)} onSaved={workspace => {
      queryClient.setQueryData<Workspace[]>(queryKey, old => { const rest = old?.filter(item => item.workspace_id !== workspace.workspace_id) ?? []; return [workspace, ...rest]; });
      void queryClient.invalidateQueries({ queryKey });
      if (action.mode === 'create') { choose(workspace); navigate({ pathname: '/home', search: '?workspace=' + workspace.workspace_id }); }
      setNotice(action.mode === 'create' ? '工作空间已创建，请从工作台的下一步开始。' : action.mode === 'rename' ? '名称已更新。' : action.mode === 'archive' ? '已归档，记录保留，可随时恢复。' : '工作空间已恢复。');
      setAction(null);
    }} onConflict={() => { void queryClient.invalidateQueries({ queryKey }); }}/>}
  </Shell>;
}

function Home({ account, items, current, search, create, enter }: { account: Account; items: Workspace[]; current?: Workspace; search: string; create: (kind: Workspace['kind']) => void; enter: (workspace: Workspace) => void }) {
  const active = items.filter(item => item.kind === 'project' && item.status === 'active');
  return <><Heading kicker="A LITTLE THINKING, EVERY DAY" title={`${account.display_name}，今天从哪里开始？`} description="给自己一个清晰的小目标，留下一次认真的尝试。"/>
    {current && <WorkspaceGuide workspace={current}/>}
    <section className="hero card"><div><span className="kicker">以问为津 · 学有所思</span><h2 className="serif">让问题，成为理解的起点。</h2><p>围绕一个项目积累思考，也可以随时开启一次临时探索。每个空间各有自己的学习上下文。</p><div className="actions"><button className="button primary" onClick={() => create('project')}><Plus size={16}/>新建学习项目</button><button className="button translucent" onClick={() => create('temporary')}>开启临时会话<ArrowRight size={16}/></button></div></div><MathGarden/></section>
    <div className="section-heading"><h2>我的学习空间</h2><Link className="text-link" to={{ pathname: '/spaces', search }}>查看全部<ChevronRight size={15}/></Link></div>
    {active.length ? <div className="grid three">{active.slice(0, 3).map(item => <section className="card project-card" key={item.workspace_id}><div className="card-head"><span className="tile-icon"><FolderOpen size={21}/></span><span className="badge">正式项目</span></div><h3>{item.title}</h3><p>准备好目标与计划后，在这里逐步积累自己的学习过程。</p><button className="button" onClick={() => enter(item)}>进入项目<ArrowRight size={15}/></button></section>)}</div> : <section className="card"><Empty title="第一个项目，从一个想学会的问题开始" action={<button className="button" onClick={() => create('project')}>创建我的第一个项目</button>}>比如「弄懂一元一次方程」或「梳理三角形证明」。项目会保存，刷新后也能继续。</Empty></section>}
    <div className="grid two section-gap"><section className="card"><div className="card-head"><h2>你的学习路线</h2><span className="badge neutral">先看方向</span></div><LearningCycle/><p className="card-note">明确目标，确认课程与节奏，逐步练习，再用独立检测检验理解。</p></section><section className="card"><div className="card-head"><h2>不止于解出一道题</h2><span className="badge neutral">一起思考</span></div><Link className="feature-link" to={{ pathname: '/roundtable', search }}><MessagesSquare size={19}/><div><strong>圆桌讨论</strong><small>让不同思路相遇，学会追问依据。</small></div><ChevronRight size={17}/></Link><Link className="feature-link" to={{ pathname: '/teach', search }}><GraduationCap size={19}/><div><strong>我教 AI</strong><small>把你理解的方法，讲给虚拟学伴听。</small></div><ChevronRight size={17}/></Link><p className="card-note">{current ? `当前上下文：${current.title}` : '选定项目后，每条学习路线都将有明确归属。'}</p></section></div>
  </>;
}

function Spaces({ items, current, enter, action }: { items: Workspace[]; current?: Workspace; enter: (workspace: Workspace) => void; action: (action: Action) => void }) {
  const [filter, setFilter] = useState<'project' | 'temporary' | 'archived'>('project');
  const shown = items.filter(item => filter === 'archived' ? item.status === 'archived' : item.status === 'active' && item.kind === filter);
  return <><Heading kicker="YOUR OWN WORKSPACES" title="项目与会话" description="长期目标放进项目，零散好奇留给临时探索。" actions={<><button className="button" onClick={() => action({ mode: 'create', kind: 'temporary' })}><MessageCirclePlus size={16}/>新建临时会话</button><button className="button primary" onClick={() => action({ mode: 'create', kind: 'project' })}><Plus size={16}/>新建学习项目</button></>}/>
    <div className="tabs" role="tablist" aria-label="空间分类">{([['project', '学习项目'], ['temporary', '临时会话'], ['archived', '已归档']] as const).map(([key, label]) => <button key={key} role="tab" aria-selected={filter === key} onClick={() => setFilter(key)}>{label}<span>{items.filter(item => key === 'archived' ? item.status === 'archived' : item.kind === key && item.status === 'active').length}</span></button>)}</div>
    {shown.length ? <div className="grid three" role="tabpanel">{shown.map(item => <section className={`card project-card ${current?.workspace_id === item.workspace_id ? 'selected' : ''}`} key={item.workspace_id}><div className="card-head"><span className="tile-icon">{item.kind === 'project' ? <FolderOpen size={21}/> : <MessageCircle size={21}/>}</span><span className={`badge ${item.status === 'archived' ? 'gold' : ''}`}>{item.status === 'archived' ? '已归档' : item.kind === 'project' ? '正式项目' : '临时探索'}</span></div><h3>{item.title}</h3><p>{item.kind === 'temporary' ? '只在此空间中探索，不自动纳入正式项目的长期画像。' : '计划、陪学、复习与讨论将在这个项目中逐步连起来。'}</p><div className="project-footer"><button className="button small" onClick={() => enter(item)}>{item.status === 'archived' ? '查看空间' : '进入空间'}<ArrowRight size={14}/></button><div className="inline">{item.status === 'active' ? <><button className="icon-button workspace-action" title="重命名" data-tooltip="重命名" aria-label={`重命名 ${item.title}`} onClick={() => action({ mode: 'rename', id: item.workspace_id })}><Pencil size={16}/></button><button className="icon-button workspace-action" title="归档（保留记录，可恢复）" data-tooltip="归档（保留记录，可恢复）" aria-label={`归档 ${item.title}`} onClick={() => action({ mode: 'archive', id: item.workspace_id })}><Archive size={16}/></button></> : <button className="button text" onClick={() => action({ mode: 'restore', id: item.workspace_id })}><Undo2 size={15}/>恢复</button>}</div></div></section>)}</div> : <section className="card"><Empty title={filter === 'archived' ? '还没有归档空间' : filter === 'temporary' ? '给好奇心留一个临时空间' : '还没有学习项目'} action={filter !== 'archived' ? <button className="button" onClick={() => action({ mode: 'create', kind: filter })}><Plus size={15}/>立即创建</button> : undefined}>{filter === 'archived' ? '暂时告一段落的项目可以归档，之后仍可恢复。' : '创建后，空间会保存在当前账号中。学习对话将在陪学开放后接入。'}</Empty></section>}
    <p className="page-note">归档保留已有记录。临时空间和正式项目分开管理，切换空间不会混用学习上下文。</p>
  </>;
}

function WorkspaceDialog({ action, target, onClose, onSaved, onConflict }: { action: Action; target?: Workspace; onClose: () => void; onSaved: (workspace: Workspace) => void; onConflict: () => void }) {
  const [title, setTitle] = useState(target?.title ?? '');
  const key = useRef({ signature: '', value: crypto.randomUUID() });
  const mutation = useMutation({ mutationFn: async () => {
    if (action.mode === 'create') {
      const signature = action.kind + ':' + title.trim();
      if (key.current.signature !== signature) key.current = { signature, value: crypto.randomUUID() };
      return api.createWorkspace({ kind: action.kind, title: title.trim() }, key.current.value);
    }
    if (!target) throw new Error('工作空间已不可用，请刷新列表');
    return action.mode === 'rename' ? api.renameWorkspace(target, title.trim()) : api.changeStatus(target, action.mode);
  }, onSuccess: onSaved, onError: error => { if (error instanceof ApiError && error.status === 409) onConflict(); } });
  const heading = action.mode === 'create' ? action.kind === 'project' ? '新建学习项目' : '新建临时会话' : action.mode === 'rename' ? '重命名空间' : action.mode === 'archive' ? '归档这个空间？' : '恢复这个空间？';
  function submit(event: FormEvent) { event.preventDefault(); mutation.mutate(); }
  return <Modal title={heading} busy={mutation.isPending} onClose={onClose}><form onSubmit={submit}>
    {action.mode === 'create' || action.mode === 'rename' ? <><p className="muted dialog-copy">{action.mode === 'rename' ? `当前名称：${target?.title}` : action.mode === 'create' && action.kind === 'project' ? '用一句话说清你想学会什么，之后可以再调整名称。' : '适合一次短探索，不自动进入正式项目的长期画像。'}</p><label className="field">空间名称<input autoFocus required maxLength={120} value={title} disabled={mutation.isPending} onChange={event => setTitle(event.target.value)} placeholder="例如：弄懂一元一次方程"/></label></> : <p className="dialog-copy">「{target?.title}」{action.mode === 'archive' ? '归档后将停止接收新的学习输入，已有记录会保留，可随时恢复。' : '恢复后会重新出现在进行中的空间列表。'}</p>}
    <ErrorNotice error={mutation.error}/><div className="dialog-footer"><button type="button" className="button" disabled={mutation.isPending} onClick={onClose}>取消</button><button className="button primary" disabled={mutation.isPending || ((action.mode === 'create' || action.mode === 'rename') && !title.trim())}>{mutation.isPending ? '正在保存…' : action.mode === 'archive' ? '确认归档' : action.mode === 'restore' ? '确认恢复' : '保存空间'}</button></div>
  </form></Modal>;
}

function SettingsView({ account }: { account: Account }) {
  return <><Heading kicker="MAKE ROOM FOR YOUR WAY" title="偏好与账号" description="先理解自己适合怎样学习，再逐步调整。"/><section className="card settings-card"><h2>当前账号</h2><dl className="definition-list"><div><dt>姓名</dt><dd>{account.display_name}</dd></div><div><dt>账号</dt><dd>{account.username}</dd></div><div><dt>身份</dt><dd>学生</dd></div><div><dt>账号管理</dt><dd>忘记密码请联系管理员重置</dd></div></dl><h2 className="section-gap">学习偏好</h2><p className="body-copy">偏好调整会在陪学接入后开放，让提示方式和节奏有据可依。目前尚未收集或保存学习偏好。</p><div className="setting-row"><span>提示方式与学习节奏</span><Soon/></div><div className="setting-row"><span>通知与复习提醒</span><Soon/></div></section><PrivacyControls/></>;
}
