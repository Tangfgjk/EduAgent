import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, BookOpen, Check, ChevronRight, CircleHelp, Compass, Leaf, LoaderCircle, LogOut, Menu, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';
import { api, ApiError, type Account } from '@wenjin/api-client';

export function Brand({ admin = false }: { admin?: boolean }) {
  return <div className="brand"><span className="seal" aria-hidden="true">津</span><div><strong>桂子问津</strong><small>{admin ? '教学支持与治理' : '初中数学 · 自主学习'}</small></div></div>;
}

export function MathGarden() {
  return <svg className="math-garden" viewBox="0 0 360 260" role="img" aria-label="曲线、三角形与坐标构成的数学花园">
    <defs><pattern id="garden-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="currentColor" strokeOpacity=".1"/></pattern></defs>
    <rect x="12" y="10" width="336" height="240" rx="22" fill="url(#garden-grid)"/>
    <circle cx="184" cy="134" r="80" fill="none" stroke="currentColor" opacity=".15"/><path d="M47 190H315M85 220V35" stroke="currentColor" opacity=".4"/>
    <path d="M88 183 Q184 -65 300 177" fill="none" stroke="currentColor" strokeWidth="2.5"/>
    <path d="M136 191 216 68 284 191Z" fill="currentColor" fillOpacity=".04" stroke="currentColor" strokeWidth="1.5"/>
    <path d="M216 68V191" stroke="currentColor" strokeDasharray="4 6" opacity=".5"/>
    <circle cx="216" cy="68" r="5" fill="currentColor"/><circle cx="136" cy="191" r="4" fill="currentColor"/>
    <text x="246" y="47" fontFamily="Georgia,serif" fontSize="22" fill="currentColor">y = f(x)</text><text x="44" y="233" fontFamily="Georgia,serif" fontSize="18" fill="currentColor">∴</text>
  </svg>;
}

export function Heading({ kicker, title, description, actions }: { kicker: string; title: string; description?: string; actions?: ReactNode }) {
  return <header className="heading"><div><div className="kicker">{kicker}</div><h1>{title}</h1>{description && <p className="lead">{description}</p>}</div>{actions && <div className="actions">{actions}</div>}</header>;
}

export function ErrorNotice({ error, retry }: { error: unknown; retry?: () => void }) {
  if (!error) return null;
  return <div className="error-notice" role="alert"><CircleHelp size={18}/><div><p>{error instanceof Error ? error.message : '操作未完成，请稍后再试'}</p>{error instanceof ApiError && error.requestId && <small>问题编号：{error.requestId.slice(0, 8)}</small>}</div>{retry && <button type="button" className="button small" onClick={retry}><RefreshCw size={14}/>重试</button>}</div>;
}

export function Loading({ text = '正在读取…' }: { text?: string }) { return <div className="loading" role="status"><LoaderCircle className="spin" size={20}/>{text}</div>; }
export function Empty({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><span className="empty-symbol"><Leaf size={27}/></span><h3>{title}</h3><p>{children}</p>{action}</div>;
}
export function Soon({ children }: { children?: ReactNode }) { return <span className="badge gold">{children ?? '待开放'}</span>; }
export function Success({ children }: { children: ReactNode }) { return <div className="success" role="status"><Check size={16}/>{children}</div>; }

export function Modal({ title, children, busy = false, onClose }: { title: string; children: ReactNode; busy?: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = `modal-${title}`;
  useEffect(() => { const dialog = ref.current!; dialog.showModal(); return () => dialog.close(); }, []);
  return <dialog ref={ref} aria-labelledby={titleId} onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="dialog-head"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" aria-label="关闭弹窗" disabled={busy} onClick={onClose}><X size={20}/></button></div>{children}
  </dialog>;
}

type AuthValue = { account: Account; logout: () => Promise<void>; loggingOut: boolean };
const AuthContext = createContext<AuthValue | null>(null);
export function useAuth() { const value = useContext(AuthContext); if (!value) throw new Error('Auth context missing'); return value; }

function broadcastAuth(role: Account['role']) {
  if ('BroadcastChannel' in window) { const channel = new BroadcastChannel('wenjin-auth:' + role); channel.postMessage('changed'); channel.close(); }
}

function AuthGate({ role, children }: { role: Account['role']; children: (account: Account) => ReactNode }) {
  const client = useQueryClient();
  const [logoutError, setLogoutError] = useState<unknown>(null);
  const me = useQuery({ queryKey: ['me'], queryFn: async ({ signal }) => {
    try { return (await api.me(signal)).account; } catch (error) { if (error instanceof ApiError && error.status === 401) return null; throw error; }
  }, retry: false, staleTime: 15_000 });
  useEffect(() => {
    const changed = () => { void client.invalidateQueries({ queryKey: ['me'] }); };
    const expired = () => { client.setQueryData(['me'], null); };
    window.addEventListener('wenjin:expired', expired);
    const channel = 'BroadcastChannel' in window ? new BroadcastChannel('wenjin-auth:' + role) : null;
    if (channel) channel.onmessage = changed;
    return () => { channel?.close(); window.removeEventListener('wenjin:expired', expired); };
  }, [client, role]);
  const logout = useMutation({ mutationFn: api.logout, onSuccess: () => { client.setQueryData(['me'], null); broadcastAuth(role); }, onError: setLogoutError });
  if (me.isPending) return <div className="startup"><Brand admin={role === 'admin'}/><Loading text="正在确认登录状态…"/></div>;
  if (me.isError) return <div className="startup"><Brand admin={role === 'admin'}/><ErrorNotice error={me.error} retry={() => { void me.refetch(); }}/></div>;
  if (!me.data) return <Login role={role} onLogin={account => { client.setQueryData(['me'], account); broadcastAuth(role); }}/>;
  if (me.data.role !== role) return <div className="startup"><Brand admin={role === 'admin'}/><section className="card role-block"><ShieldCheck size={32}/><h1>请使用{role === 'student' ? '学生' : '管理员'}账号</h1><p>当前登录账号无法访问此页面。你可以退出后切换账号。</p><ErrorNotice error={logoutError}/><button className="button primary" disabled={logout.isPending} onClick={() => logout.mutate()}>退出并切换账号</button></section></div>;
  return <AuthContext.Provider value={{ account: me.data, logout: async () => { await logout.mutateAsync(); }, loggingOut: logout.isPending }}><PrivateQueries key={me.data.id}>{children(me.data)}</PrivateQueries></AuthContext.Provider>;
}

function PrivateQueries({ children }: { children: ReactNode }) {
  // 与登录账号同生命周期；退出或切换账号时整个实例卸载，不删除新页面刚发起的查询。
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 10_000, refetchOnWindowFocus: true }, mutations: { retry: false } } }));
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

export function Application({ role, children }: { role: Account['role']; children: (account: Account) => ReactNode }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: true, staleTime: 10_000 }, mutations: { retry: false } } }));
  return <QueryClientProvider client={client}><AuthGate role={role}>{children}</AuthGate></QueryClientProvider>;
}

function Login({ role, onLogin }: { role: Account['role']; onLogin: (account: Account) => void }) {
  const [username, setUsername] = useState(''), [password, setPassword] = useState('');
  const login = useMutation({ mutationFn: async () => {
    const result = await api.login({ username, password });
    setPassword('');
    if (result.account.role !== role) {
      await api.logout();
      throw new ApiError(403, 'wrong_role', `当前账号无法登录${role === 'admin' ? '管理' : '学生'}端，请使用${role === 'admin' ? '管理员' : '学生'}账号`);
    }
    return result.account;
  }, onSuccess: onLogin });
  const submit = (event: FormEvent) => { event.preventDefault(); login.mutate(); };
  return <main className="login-page"><section className="login-story"><Brand admin={role === 'admin'}/><div className="login-copy"><div className="kicker">{role === 'admin' ? 'TEACHING, WITH CARE' : 'THINK. EXPLORE. GROW.'}</div><h1>{role === 'admin' ? <>让学习的每一步，<br/>都有可靠的支撑。</> : <>把每一次思考，<br/>连成自己的路。</>}</h1><p>{role === 'admin' ? '从内容到证据，从运行到策略。为学生保留思考的空间，也为每次调整留下依据。' : '从一个问题出发，尝试、解释、验证，再回望。与津津一起，慢慢建立属于你的数学理解。'}</p><MathGarden/><span className="story-caption"><BookOpen size={15}/>桂子问津 · 初中数学</span></div><small className="quiet">认真对待每一次「我想明白了」。</small></section>
    <section className="login-panel"><div className="login-form"><span className="badge">{role === 'admin' ? '管理端' : '学生端'}</span><h2>欢迎回来</h2><p className="muted">{role === 'admin' ? '登录后管理学生账号与教学支持工作。' : '登录后，继续你的数学学习项目。'}</p>
      <form onSubmit={submit}><label className="field">账号<input autoFocus name="username" autoComplete="username" maxLength={64} required value={username} onChange={event => setUsername(event.target.value)} placeholder={role === 'admin' ? '输入管理员账号' : '输入学生账号'}/></label>
        <label className="field">密码<input name="password" type="password" autoComplete="current-password" maxLength={128} required value={password} onChange={event => setPassword(event.target.value)} placeholder="输入密码"/></label>
        <ErrorNotice error={login.error}/><button className="button primary login-submit" disabled={login.isPending}>{login.isPending ? <LoaderCircle className="spin" size={17}/> : null}{login.isPending ? '正在登录…' : '登录'}<ArrowRight size={17}/></button>
      </form><p className="login-help"><ShieldCheck size={16}/>{role === 'admin' ? '首次使用请由负责人完成管理员初始化。' : '账号由管理员创建。如忘记密码，请联系管理员。'}</p>
    </div><p className="login-bottom">{role === 'admin' ? '每次教学调整，都应有可追溯的依据。' : '你的尝试和解释，与正确答案同样值得被看见。'}</p></section></main>;
}

export type NavItem = { path: string; label: string; icon: typeof Compass; section?: string };
export function Shell({ role, navigation, sidebar, context, children, search = '' }: { role: Account['role']; navigation: NavItem[]; sidebar?: ReactNode; context?: ReactNode; children: ReactNode; search?: string }) {
  const { account, logout, loggingOut } = useAuth();
  const location = useLocation();
  const [menu, setMenu] = useState(false), [error, setError] = useState<unknown>(null);
  const menuButton=useRef<HTMLButtonElement>(null);
  function closeMenu(){setMenu(false);menuButton.current?.focus();}
  useEffect(()=>{
    if(!menu)return;
    const rail=document.querySelector<HTMLElement>('.rail');
    rail?.querySelector<HTMLElement>('nav a')?.focus();
    const previousOverflow=document.body.style.overflow;
    document.body.style.overflow='hidden';
    const keydown=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){event.preventDefault();closeMenu();}
      if(event.key==='Tab'){
        const controls=rail?.querySelectorAll<HTMLElement>('a[href],button:not(:disabled),select:not(:disabled),input:not(:disabled)');
        if(!controls?.length)return;
        const first=controls[0],last=controls[controls.length-1];
        if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
        else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
      }
    };
    window.addEventListener('keydown',keydown);
    return()=>{window.removeEventListener('keydown',keydown);document.body.style.overflow=previousOverflow;};
  },[menu]);
  useLayoutEffect(() => { setMenu(false); document.getElementById('main-content')?.focus(); }, [location.pathname]);
  const routeTitles:Record<string,string>={courses:'课程与安排',plan:'学习安排',units:'数学单元',settings:'我的设置'};
  const current = navigation.find(item => location.pathname === '/' + item.path);
  const title=current?.label ?? routeTitles[location.pathname.slice(1)] ?? '页面';
  return <div className={`app-shell ${menu ? 'menu-open' : ''}`}><a className="skip-link" href="#main-content">跳到主要内容</a>
    {menu && <button className="rail-overlay" aria-label="关闭导航" onClick={closeMenu}/>}
    <aside className="rail" aria-label={role === 'admin' ? '管理导航' : '学习导航'}><Brand admin={role === 'admin'}/><div className="role-mark">{role === 'admin' ? '管理工作台' : '学生学习工作台'}</div>{sidebar}
      <nav>{navigation.map((item, index) => <div key={item.path}>{item.section && <p className="nav-label">{item.section}</p>}<NavLink to={{ pathname: '/' + item.path, search }} end className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}><item.icon size={18}/><span>{item.label}</span>{index === 0 && <ChevronRight className="nav-arrow" size={14}/>}</NavLink></div>)}</nav>
      <div className="rail-footer">{role==='student'&&<NavLink className="nav-link" to={{pathname:'/settings',search}}>我的设置</NavLink>}<div className="person"><span className="avatar">{account.display_name.slice(0, 1)}</span><div><strong>{account.display_name}</strong><small>{role === 'admin' ? '管理员' : '学生账号'}</small></div></div><button className="button text" disabled={loggingOut} onClick={() => { void logout().catch(setError); }}><LogOut size={15}/>{loggingOut ? '正在退出…' : '退出登录'}</button><ErrorNotice error={error}/></div>
    </aside><div className="main-column"><header className="topbar"><div className="inline"><button ref={menuButton} className="icon-button mobile-menu" aria-label="打开导航" aria-expanded={menu} onClick={() => setMenu(true)}><Menu size={21}/></button><span className="breadcrumb">{role === 'admin' ? '教学支持' : '学习空间'}<ChevronRight size={13}/><strong>{title}</strong></span></div><span className="top-caption"><span className="live-dot"/> {role === 'admin' ? '以证据支持每一次调整' : '先想一想，再向前一步'}</span></header>
      <main id="main-content" className="content" tabIndex={-1}>{context}{children}</main><footer className="page-footer">桂子问津 <span>·</span> {role === 'admin' ? '审慎支持，持续改进' : '以问为津，学有所思'}</footer>
    </div></div>;
}

export { StrategyDetails, strategyPhases } from './strategy';

export { SkillExecution } from './skill-execution';

export { EvolutionWorkbench } from './skill-evolution';

export {MediaBudgets} from './media-budget';
export {PrivacyControls,OperationalReadiness} from './privacy';
