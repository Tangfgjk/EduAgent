import {useEffect,useRef,useState} from 'react';
import {useMutation,useQuery,useQueryClient} from '@tanstack/react-query';
import {api,type MediaBudgetRecord} from '@wenjin/api-client';
import {ErrorNotice,Loading} from './index';

export function MediaBudgets({wid,resourceId}:{wid?:string;resourceId?:string}){
 const admin=!wid,client=useQueryClient(),[id,setId]=useState(''),[ack,setAck]=useState(false);
 const query=useQuery({queryKey:['media-budgets',wid],queryFn:({signal})=>api.mediaBudgets(wid,signal),refetchInterval:3000});
 function refresh(){void client.invalidateQueries({queryKey:['media-budgets']});}
 const propose=useMutation({mutationFn:()=>api.proposeMediaBudget(id.trim()),onSuccess:()=>{setId('');refresh();}});
 const completed=(query.data?.items??[]).flatMap(row=>row.operations.filter(op=>op.status==='completed').map(op=>op.operation_id)).sort().join(',');
 const previous=useRef('');
 useEffect(()=>{if(completed!==previous.current){previous.current=completed;void client.invalidateQueries({queryKey:['learning-resources',wid]});}},[completed,wid,client]);
 const list=(query.data?.items??[]).filter(row=>!resourceId||row.resource_id===resourceId);
 // Students only need available actions and progress; financial records stay in admin.
 if(wid){
  const actionable=list.filter(row=>row.enabled||row.operations.some(op=>['pending','running','submitting'].includes(op.status)));
  if(!actionable.length)return null;
  return <section aria-label="课堂准备进度"><ErrorNotice error={query.error}/>{actionable.map(row=><StudentMedia key={row.budget_id} row={row} wid={wid} refresh={refresh}/>)}</section>;
 }

 return <section className="card section-gap"><h2>{admin?'外部媒体预算与任务':'本课的补充媒体'}</h2><p className="card-note">百炼北京：最多2图、3段配音（合计450字符）、1段5秒480P视频；独立总预算5元，最多6次生成，无自动重试。默认关闭，是否可生成以此批次的授权状态为准。仅已配置并明确授权的批次能执行。</p>
 {admin&&<form className="form-stack" onSubmit={event=>{event.preventDefault();propose.mutate();}}><label className="field">已审核三场景课堂资源ID<input required minLength={36} maxLength={36} value={id} onChange={event=>setId(event.target.value)}/></label><button className="button" disabled={propose.isPending}>创建关闭状态的预算草案</button><ErrorNotice error={propose.error}/><label className="inline"><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)}/>仅在另获费用授权后：确认北京密钥、账号模型权限及当日官方价格已核对，批准一次5元预算（不重开旧文本预算）</label></form>}
 {query.isPending?<Loading/>:<ErrorNotice error={query.error}/>} {!list.length&&<p>暂无已配置的媒体批次。现有课堂仍可阅读；新媒体生成保持关闭。</p>}
 {list.map(row=><Budget key={row.budget_id} row={row} wid={wid} ack={ack} refresh={refresh}/>)}
 </section>;
}

function Budget({row,wid,ack,refresh}:{row:MediaBudgetRecord;wid?:string;ack:boolean;refresh:()=>void}){
 const change=useMutation({mutationFn:(enabled:boolean)=>api.authorizeMediaBudget(row.budget_id,row.revision,enabled,ack),onSuccess:refresh});
 const generate=useMutation({mutationFn:async({kind,index}:{kind:'image'|'audio'|'video';index:number})=>api.startMedia(wid!,row.budget_id,kind,index),onSuccess:refresh});
 return <div className="soft-box section-gap"><strong>{row.enabled?'已授权批次':'生成已关闭'} · {row.budget_id.slice(0,8)}</strong><p>已预占 ¥{(Number(row.body.reserved_microyuan)/1_000_000).toFixed(4)} / ¥5；预占保留不代表实际费用已核账。</p>
 {!wid?<div className="actions"><button className="button" disabled={change.isPending||!ack||row.enabled||!!row.body.approved_at} onClick={()=>change.mutate(true)}>批准一次媒体预算</button><button className="button" disabled={change.isPending||!row.enabled} onClick={()=>change.mutate(false)}>暂停此批次</button></div>:<div className="actions">{(['image','audio','video'] as const).flatMap(kind=>Array.from({length:kind==='image'?2:kind==='audio'?3:1},(_,index)=>{
  const op=row.operations.find(o=>o.slot===kind+':'+index);
  return <button className="button small" key={kind+index} disabled={!row.enabled||generate.isPending||!!op} onClick={()=>generate.mutate({kind,index})}>{kind==='image'?'生成配图':kind==='audio'?'生成本场景配音':'生成5秒视频'} {index+1}{op?' · '+op.status:''}</button>;
 }))}</div>}
 <ul>{row.operations.map(op=><li key={op.operation_id}>{op.slot} · {op.status} · 预占 ¥{(op.reserved_microyuan/1_000_000).toFixed(4)}{op.receipt?.billing_unknown?' · 账单待核对':''}</li>)}</ul><ErrorNotice error={change.error??generate.error}/>
 <p className="card-note">只发送此课已审核的去标识课稿/媒体提示，不发送学生学习记录，不上传参考肖像或录音。生成结果供补充学习，不作为数学核验真值。</p></div>;
}

function StudentMedia({row,wid,refresh}:{row:MediaBudgetRecord;wid:string;refresh:()=>void}){
 const generate=useMutation({mutationFn:({kind,index}:{kind:'image'|'audio'|'video';index:number})=>api.startMedia(wid,row.budget_id,kind,index),onSuccess:refresh});
 const waiting=row.operations.some(op=>['pending','running','submitting'].includes(op.status));
 return <div className="soft-box section-gap">{waiting&&<p role="status">正在准备课堂素材，完成后会自动显示。你可以继续学习。</p>}<div className="actions">{(['image','audio','video'] as const).flatMap(kind=>Array.from({length:kind==='image'?2:kind==='audio'?3:1},(_,index)=>!row.enabled||row.operations.some(op=>op.slot===kind+':'+index)?null:<button className="button small" key={kind+index} disabled={generate.isPending} onClick={()=>generate.mutate({kind,index})}>{kind==='image'?'准备配图':kind==='audio'?'准备配音':'准备视频'} {index+1}</button>))}</div><ErrorNotice error={generate.error}/></div>;
}
