import type { ReactNode } from 'react';
import { useMutation,useQuery,useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api,type Workspace } from '@wenjin/api-client';
import { ErrorNotice,Loading,Modal } from '@wenjin/ui';
import { useState } from 'react';

/** An early explanation; server-side idle/independent gates remain authoritative. */
export function SessionGuard({workspace,destination,children}:{workspace:Workspace;destination:string;children:ReactNode}){
 const client=useQueryClient(),[confirm,setConfirm]=useState(false);
 const state=useQuery({queryKey:['next-action',workspace.workspace_id],queryFn:({signal})=>api.nextAction(workspace.workspace_id,signal),staleTime:0,refetchInterval:5000});
 const next=state.data;
 const blocked=['study','teach','roundtable'].includes(destination)&&next?.stage==='resume'&&next.destination!==destination;
 const end=useMutation({mutationFn:async()=>{
   if(!next?.run_id)throw new Error('原任务已变化，请刷新。');
   if(next.destination==='study'){
    const row=(await api.tutoring(workspace.workspace_id)).items.find(r=>r.run_id===next.run_id);
    if(!row)throw new Error('请返回原活动结束任务。');return api.cancelTutor(row);
   }
   if(next.destination==='teach'||next.destination==='roundtable'){
    const row=(await api.activities(workspace.workspace_id,next.destination,0)).items.find(r=>r.run_id===next.run_id);
    if(!row)throw new Error('请返回原活动结束任务。');return api.cancelActivity(row);
   }
   const row=(await api.runs(workspace.workspace_id)).items.find(r=>r.run_id===next.run_id);
   if(!row)throw new Error('请返回原计划结束任务。');return api.cancelRun(row);
 },onSuccess:()=>{setConfirm(false);void client.invalidateQueries();},onError:()=>{void state.refetch();}});
 if(state.isPending)return <Loading text="正在恢复当前学习任务…"/>;
 if(state.isError)return <ErrorNotice error={state.error} retry={()=>{void state.refetch();}}/>;
 if(!blocked)return <>{children}</>;
 return <section className="card section-gap" aria-label="先处理正在进行的活动"><h2>这个空间已有进行中的学习</h2><p>你的表达和进度已经保存。同一空间依次开展活动；独立检测期间保持隔离。</p><div className="actions"><Link className="button primary" to={'/'+next.destination+'?workspace='+workspace.workspace_id+'&run='+next.run_id}>回到原任务继续</Link><button className="button" onClick={()=>setConfirm(true)}>结束原任务后切换</button></div>{confirm&&<Modal title="结束原任务？" busy={end.isPending} onClose={()=>setConfirm(false)}><p>本轮将停止，已有输入和学习记录保留。不会自动判定已完成，也不会绕过独立检测。</p><ErrorNotice error={end.error}/><div className="actions"><button className="button" disabled={end.isPending} onClick={()=>setConfirm(false)}>继续原任务</button><button className="button primary" disabled={end.isPending} onClick={()=>end.mutate()}>确认结束并切换</button></div></Modal>}</section>;
}
