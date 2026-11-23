import {render,screen,fireEvent,waitFor,within} from "@testing-library/react";
import {beforeEach,it,expect,vi} from "vitest";
import {WenjinLearningPanel} from "@/components/space/learning/WenjinLearningPanel";
const {fetcher,t,impact}=vi.hoisted(()=>({fetcher:vi.fn(),impact:vi.fn(),t:(key:string)=>key}));
vi.mock('react-i18next',()=>({useTranslation:()=>({t,i18n:{language:'zh'}})}));
vi.mock('@/lib/api',()=>({apiFetch:fetcher,apiUrl:(s:string)=>s}));
vi.mock('@/lib/workspace-scope',()=>({scopedUrl:(s:string)=>s}));
vi.mock('@/lib/learning-routes',()=>({masteryTopicRoute:(s:string)=>'/learning/projects/'+s,masterySessionRoute:(p:string,s:string)=>'/learning/mastery/'+p+'/sessions/'+s}));
vi.mock('@/components/learning/DiagnosticImpact',()=>({default:()=>{impact();return <p>diagnostic detail loaded</p>}}));
const state={enabled:true,complete:false,objective:{id:'k',name:'方程定义'},decision:null,support_level:0,objectives:[],reflections:[],reviews:[],sources:[],strategy:{}};
beforeEach(()=>{impact.mockClear();fetcher.mockReset();fetcher.mockResolvedValue({ok:true,json:async()=>state});HTMLDialogElement.prototype.showModal=function(){this.open=true};HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new Event("close"))}});
it('keeps one help toolbar beside the composer and opens context on demand',async()=>{
 const host=document.createElement('div');document.body.append(host);const send=vi.fn();
 const view=render(<WenjinLearningPanel pathId="p" revision={1} streaming={false} onSend={send} helpHost={host}/>);
 await screen.findByText(/方程定义/);
 expect(within(host).getAllByRole('button',{name:'给我提示'})).toHaveLength(1);
 expect(screen.queryByRole('dialog')).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:/方程定义/}));
 expect(screen.getByRole('dialog',{name:'学习目标与资料'})).toBeVisible();
 expect(screen.queryByText(/0 \/ 3/)).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'关闭'}));
 expect(screen.queryByRole('dialog')).toBeNull();
 fireEvent.click(within(host).getByRole('button',{name:'给我提示'}));await waitFor(()=>expect(send).toHaveBeenCalledTimes(1));
 view.unmount();host.remove();
});
it('disables help while generating',()=>{
 const host=document.createElement('div');document.body.append(host);
 const view=render(<WenjinLearningPanel pathId="p" revision={1} streaming onSend={vi.fn()} helpHost={host}/>);
 expect(within(host).getByRole('button',{name:'给我提示'})).toBeDisabled();
 expect(within(host).getByRole('button',{name:'独立试试'})).toBeDisabled();
 view.unmount();host.remove();
});

it('resumes the original pending-task conversation instead of duplicating it',async()=>{
 fetcher.mockResolvedValueOnce({ok:true,json:async()=>({...state,resume_session_id:'original-session'})});
 render(<WenjinLearningPanel pathId="p" sessionId="new-session" revision={1} streaming={false} showResume onSend={vi.fn()}/>);
 const resume=await screen.findByRole('link',{name:'继续上次'});
 expect(resume).toHaveAttribute('href','/learning/mastery/p/sessions/original-session');
});

it('loads diagnosis details only when requested and releases them on close',async()=>{
 render(<WenjinLearningPanel pathId="p" revision={1} streaming={false} onSend={vi.fn()}/>);
 await screen.findByText(/方程定义/);
 expect(impact).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:/方程定义/}));
 expect(await screen.findByText('diagnostic detail loaded')).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'关闭'}));
 expect(screen.queryByText('diagnostic detail loaded')).toBeNull();
});
