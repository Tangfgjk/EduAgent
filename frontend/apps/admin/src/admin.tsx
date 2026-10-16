import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, Navigate, Route, Routes, useSearchParams } from 'react-router-dom';
import { ArrowRight, BookOpen, CheckCheck, ClipboardCheck, FileCheck2, KeyRound, LayoutDashboard, ListChecks, LockKeyhole, Plus, Search, Settings, ShieldCheck, Sparkles, UserRound, UserRoundX, Users } from 'lucide-react';
import { api, type Account, type StudentInput } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading, MathGarden, Modal, Shell, Soon, Success, type NavItem } from '@wenjin/ui';

import { ContentPage, ReviewsPage } from './content';
import { StrategiesPage } from './strategies';

const navigation: NavItem[] = [
  { path: 'overview', label: '试点概览', icon: LayoutDashboard, section: '教学支持' },
  { path: 'students', label: '学生账号', icon: Users },
  { path: 'content', label: '知识点与题库', icon: BookOpen, section: '质量与依据' },
  { path: 'review', label: '内容与解答复核', icon: ClipboardCheck },
  { path: 'runs', label: '运行记录', icon: ListChecks },
  { path: 'strategies', label: '策略评审', icon: FileCheck2 },
  { path: 'config', label: '配置与权限', icon: Settings, section: '管理' },
];

export function AdminApp({ account }: { account: Account }) {
  return <Shell role="admin" navigation={navigation}><Routes>
    <Route path="overview" element={<Overview account={account}/>}/><Route path="students" element={<Students account={account}/>}/>
    <Route path="content" element={<ContentPage/>}/><Route path="review" element={<ReviewsPage/>}/>
    <Route path="runs" element={<Preparation page="runs"/>}/><Route path="strategies" element={<StrategiesPage/>}/>
    <Route path="config" element={<Configuration account={account}/>}/><Route path="*" element={<Navigate to="/overview" replace/>}/>
  </Routes></Shell>;
}

function Overview({ account }: { account: Account }) {
  const query = useQuery({ queryKey: ['students', account.id, 0], queryFn: ({ signal }) => api.students(0, signal) });
  return <><Heading kicker="CARE, WITH ACCOUNTABILITY" title="教学支持工作台" description="为学生保留思考空间，为内容与调整留下可靠依据。" actions={<Link className="button primary" to="/students?create=1"><Plus size={16}/>创建学生账号</Link>}/>
    <section className="hero card admin-hero"><div><span className="kicker">教与学之间，是审慎的支持</span><h2 className="serif">让每一步学习，<br/>都有值得信任的支撑。</h2><p>先管理好参与者与空间，再让内容、过程和策略形成可追溯的闭环。</p><Link className="button primary" to="/students">管理学生账号<ArrowRight size={16}/></Link></div><MathGarden/></section>
    <div className="grid three section-gap"><section className="card metric-card"><span className="muted">学生账号</span><div className="metric"><Users size={26}/><strong>已开放</strong></div><p>创建、重置密码、禁用与权限控制。</p><Link className="text-link" to="/students">进入账号管理<ArrowRight size={14}/></Link></section><section className="card metric-card"><span className="muted">内容与解答质量</span><div className="metric"><ClipboardCheck size={26}/><strong>开发版已开放</strong></div><p>题目、提示、解答与人工复核队列。</p><Link className="text-link" to="/review">进入解答复核<ArrowRight size={14}/></Link></section><section className="card metric-card"><span className="muted">策略与运行治理</span><div className="metric"><ShieldCheck size={26}/><strong>开发版已开放</strong></div><p>有来源、可评审、经确认、可回滚。</p><Link className="text-link" to="/strategies">查看评审流程<ArrowRight size={14}/></Link></section></div>
    <div className="grid two section-gap"><section className="card"><div className="card-head"><h2>学生账号速览</h2><Link className="text-link" to="/students">查看账号<ArrowRight size={15}/></Link></div>{query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/> : query.data.items.length ? <div className="recent-list">{query.data.items.slice(-5).reverse().map(student => <div className="recent-row" key={student.id}><span className="avatar">{student.display_name.slice(0, 1)}</span><div><strong>{student.display_name}</strong><small>{student.username}</small></div><span className={`badge ${student.enabled ? '' : 'neutral'}`}>{student.enabled ? '启用' : '已禁用'}</span></div>)}{query.data.items.length === 20 && <p className="card-note">当前展示账号列表首批中的记录。完整分页请进入账号管理。</p>}</div> : <Empty title="从创建第一个学生账号开始" action={<Link className="button" to="/students?create=1">创建学生账号</Link>}>账号创建后，学生即可登录并建立自己的学习项目。</Empty>}</section><section className="card align-start"><h2>一次策略调整的路径</h2><ol className="detail-list"><li>学习证据与课程材料提供输入</li><li>规划提出候选，不直接修改生效策略</li><li>检查通过后进入管理评审</li><li>学生确认后激活，保留回滚路径</li></ol><p className="card-note">策略提案与课程资料审核已开放。教学效果和运行监测仍需后续验证。</p></section></div>
  </>;
}

type AccountAction = { mode: 'create' } | { mode: 'reset' | 'disable'; account: Account };
function Students({ account }: { account: Account }) {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [offset, setOffset] = useState(0), [search, setSearch] = useState(''), [status, setStatus] = useState('all');
  const [action, setAction] = useState<AccountAction | null>(() => params.get('create') === '1' ? { mode: 'create' } : null);
  const [notice, setNotice] = useState('');
  const query = useQuery({ queryKey: ['students', account.id, offset], queryFn: ({ signal }) => api.students(offset, signal) });
  const items = query.data?.items ?? [];
  const shown = items.filter(item => (status === 'all' || item.enabled === (status === 'enabled')) && (item.display_name + item.username).toLowerCase().includes(search.toLowerCase()));
  function close() { setAction(null); if (params.has('create')) setParams({}, { replace: true }); }
  return <><Heading kicker="PEOPLE BEFORE METRICS" title="学生账号" description="为每位学生建立独立身份，学习项目与过程按账号隔离。" actions={<button className="button primary" onClick={() => setAction({ mode: 'create' })}><Plus size={16}/>创建学生账号</button>}/>
    {notice && <Success>{notice}</Success>}
    <section className="card"><div className="table-toolbar"><label className="search-field"><Search size={17}/><input aria-label="搜索本页学生" placeholder="搜索本页姓名或账号" value={search} onChange={event => setSearch(event.target.value)}/></label><select aria-label="账号状态筛选" value={status} onChange={event => setStatus(event.target.value)}><option value="all">全部状态</option><option value="enabled">启用</option><option value="disabled">已禁用</option></select></div>
      {query.isPending ? <Loading text="正在读取学生账号…"/> : query.isError ? <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/> : shown.length ? <div className="table-scroll"><table><caption className="sr-only">学生账号列表</caption><thead><tr><th>学生</th><th>账号</th><th>状态</th><th>账号操作</th></tr></thead><tbody>{shown.map(student => <tr key={student.id}><td><span className="inline"><span className="avatar small-avatar">{student.display_name.slice(0, 1)}</span><strong>{student.display_name}</strong></span></td><td>{student.username}</td><td><span className={`badge ${student.enabled ? '' : 'neutral'}`}>{student.enabled ? '启用' : '已禁用'}</span></td><td><div className="actions"><button className="button small" disabled={!student.enabled} onClick={() => setAction({ mode: 'reset', account: student })}><KeyRound size={14}/>重置密码</button><button className="button small danger-text" disabled={!student.enabled} onClick={() => setAction({ mode: 'disable', account: student })}><UserRoundX size={14}/>{student.enabled ? '禁用' : '已禁用'}</button></div></td></tr>)}</tbody></table></div> : <Empty title={items.length || offset ? '本页没有匹配的学生' : '还没有学生账号'} action={!items.length && !offset ? <button className="button" onClick={() => setAction({ mode: 'create' })}>创建第一个学生账号</button> : undefined}>{items.length ? '试试其他搜索词，或调整状态筛选。筛选只作用于当前页。' : '由管理员创建账号后，学生即可使用自己的学习空间。'}</Empty>}
      <div className="pagination"><span>第 {offset / 20 + 1} 页 · 本页 {items.length} 位学生</span><div className="actions"><button className="button small" disabled={offset === 0 || query.isPending} onClick={() => { setOffset(value => Math.max(0, value - 20)); setSearch(''); }}>上一页</button><button className="button small" disabled={items.length < 20 || query.isPending || query.isError} onClick={() => { setOffset(value => value + 20); setSearch(''); }}>下一页</button></div></div>
    </section><p className="page-note">重置密码与禁用都会使学生已有登录会话失效。禁用保留学习记录；本期暂未提供重新启用操作，请谨慎确认。</p>
    {action && <AccountDialog key={action.mode + (action.mode === 'create' ? '' : action.account.id)} action={action} onClose={close} onSaved={() => {
      setNotice(action.mode === 'create' ? '学生账号已创建，请通过合适的渠道交付账号和初始密码。' : action.mode === 'reset' ? '密码已重置，学生需要使用新密码重新登录。' : '学生账号已禁用，已有会话已失效，学习记录保留。');
      if (action.mode === 'create') { setOffset(0); setSearch(''); setStatus('all'); }
      void queryClient.invalidateQueries({ queryKey: ['students', account.id] }); close();
    }}/>}
  </>;
}

function AccountDialog({ action, onClose, onSaved }: { action: AccountAction; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<StudentInput>({ username: '', display_name: '', password: '' });
  const mutation = useMutation({ mutationFn: () => action.mode === 'create' ? api.createStudent(form) : action.mode === 'reset' ? api.resetPassword(action.account.id, form.password) : api.disableStudent(action.account.id), onSuccess: () => { setForm({ username: '', display_name: '', password: '' }); onSaved(); } });
  const title = action.mode === 'create' ? '创建学生账号' : action.mode === 'reset' ? '重置学生密码' : '禁用这个学生账号？';
  function submit(event: FormEvent) { event.preventDefault(); mutation.mutate(); }
  return <Modal title={title} onClose={onClose} busy={mutation.isPending}><form onSubmit={submit}>
    {action.mode === 'create' ? <><p className="dialog-copy muted">创建后，学生可以登录并建立独立学习项目。</p><label className="field">学生姓名<input autoFocus autoComplete="off" value={form.display_name} required maxLength={80} disabled={mutation.isPending} onChange={event => setForm({ ...form, display_name: event.target.value })}/></label><label className="field">学生账号<input aria-label="学生账号" autoComplete="off" value={form.username} required pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]{2,63}" title="3–64 位字母、数字或 ._-，以字母或数字开头" disabled={mutation.isPending} onChange={event => setForm({ ...form, username: event.target.value })}/><small>3–64 位字母、数字或 ._-，以字母或数字开头。</small></label></> : <p className="dialog-copy">{action.account.display_name}（{action.account.username}）{action.mode === 'disable' ? '禁用后将无法登录，已有会话立即失效。学习记录会保留；当前尚不支持从页面重新启用，请确认后操作。' : '重置后需要使用新密码重新登录，已有会话会失效。'}</p>}
    {action.mode !== 'disable' && <label className="field">{action.mode === 'create' ? '初始密码' : '新密码'}<input autoFocus={action.mode === 'reset'} type="password" autoComplete="new-password" value={form.password} required minLength={12} maxLength={128} disabled={mutation.isPending} onChange={event => setForm({ ...form, password: event.target.value })}/><small>12–128 个字符。不在页面列表或浏览器存储中保留密码。</small></label>}
    <ErrorNotice error={mutation.error}/><div className="dialog-footer"><button type="button" className="button" disabled={mutation.isPending} onClick={onClose}>取消</button><button className={`button ${action.mode === 'disable' ? 'danger' : 'primary'}`} disabled={mutation.isPending}>{mutation.isPending ? '正在保存…' : action.mode === 'create' ? '保存学生账号' : action.mode === 'reset' ? '确认重置' : '确认禁用'}</button></div>
  </form></Modal>;
}

function Preparation({ page }: { page: string }) {
  const data: Record<string, { title: string; description: string; icon: typeof BookOpen; items: [string, string][] }> = {
    content: { title: '知识点与题库', description: '以课标知识点组织内容，逐步建立可审核、可验证的数学任务。', icon: BookOpen, items: [['课程范围', '七至九年级常见单元，后续逐步加入教材映射。'], ['题目与提示', '答案、解法、逐级提示、误区与内容版本一起管理。'], ['内容开放', '审核题目和独立检测就绪后，单元才进入学习入口。']] },
    review: { title: '内容与解答复核', description: '让无法确定的答案有出口，让人工纠正有依据。', icon: ClipboardCheck, items: [['内容审核', '检查题目、答案、提示与引用是否准确。'], ['待复核解答', '不可验证或存在争议的解答不伪造成功结果。'], ['纠正记录', '保留原过程，以补偿记录表达后续修正。']] },
    runs: { title: '运行记录', description: '看清流程走到哪里，按权限追溯工具调用与恢复过程。', icon: ListChecks, items: [['范围与身份', '运行绑定用户、工作空间、会话与版本。'], ['状态与恢复', '观察等待输入、失败、取消和续传等真实状态。'], ['数据来源', '模拟、成人体验和真实学生记录分开标记。']] },
    strategies: { title: '策略评审', description: '把证据转成候选，把候选经过检查与确认，再决定是否生效。', icon: FileCheck2, items: [['评审输入', '学习证据、学生自述与教材、课标、考纲等资料。'], ['策略边界', '检查 Skill / Harness 候选，教学硬约束不可改写。'], ['确认与回滚', '管理员评审后由学生确认，版本与谱系可回溯。']] },
  };
  const info = data[page];
  return <><Heading kicker="QUALITY, BEFORE SCALE" title={info.title} description={info.description} actions={<Soon>功能准备中</Soon>}/><div className="grid two"><section className="card"><div className="card-head"><h2>{page === 'strategies' ? '等待可评审的提案' : '等待真实工作记录'}</h2><info.icon size={23}/></div><Empty title="此工作队列尚未开放">接入对应学习流程后，这里会展示有明确来源与状态的记录。目前没有填入模拟队列或审核结果。</Empty><button className="button" disabled>{page === 'strategies' ? '开始评审' : '查看待办'}</button></section><section className="card align-start"><h2>这里将如何工作</h2>{info.items.map(([title, description], index) => <div className="workflow-item" key={title}><span>0{index + 1}</span><div><h3>{title}</h3><p>{description}</p></div></div>)}{page === 'strategies' && <p className="card-note">正式评审将通过服务端授权与状态处理，不接受原型导出的 JSON 回执作为认证结果。</p>}</section></div></>;
}

function Configuration({ account }: { account: Account }) {
  return <><Heading kicker="CLEAR ROLES, CLEAR RESPONSIBILITIES" title="配置与权限" description="账号管理与教学决策各有明确职责。"/><div className="grid equal"><section className="card"><div className="card-head"><h2>当前管理身份</h2><UserRound size={22}/></div><dl className="definition-list"><div><dt>姓名</dt><dd>{account.display_name}</dd></div><div><dt>账号</dt><dd>{account.username}</dd></div><div><dt>权限</dt><dd>学生账号、开发内容与解答复核</dd></div></dl><div className="notice"><LockKeyhole size={17}/>管理身份不代表可以替学生作答、代签计划或替代学生确认策略。</div></section><section className="card"><h2>后续配置</h2><div className="source-row"><ShieldCheck size={20}/><div><h3>审核职责与内容开放</h3><p>与对应教学功能一同接入。</p></div><Soon/></div><div className="source-row"><Sparkles size={20}/><div><h3>模型与运行配置</h3><p>具体服务与预算确认后开放。</p></div><Soon/></div><div className="source-row"><CheckCheck size={20}/><div><h3>版本与审核规则</h3><p>保留检查与变更依据。</p></div><Soon/></div><p className="card-note">此页面不会在浏览器中存储模型密钥。</p></section></div></>;
}
