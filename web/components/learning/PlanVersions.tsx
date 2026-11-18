"use client";
import {useEffect,useState} from "react";
import {projectEndpoint,projectRequest} from "@/lib/learning-project-api";
type Module={name:string;knowledge_points:{name:string}[]};
type Version={id:number;revision:number;created_at:number;reason:string;before:Module[];after:Module[]};
export default function PlanVersions({pathId,revision,onUpdated}:{pathId:string;revision:number;onUpdated:()=>void}){
 const [versions,setVersions]=useState<Version[]>([]),[error,setError]=useState(""),[busy,setBusy]=useState(false);
 const endpoint=projectEndpoint(pathId)+"/diagnosis/plans";
 useEffect(()=>{let alive=true;projectRequest<Version[]>(endpoint).then(v=>{if(alive)setVersions(v)}).catch(e=>{if(alive)setError(String(e))});return()=>{alive=false}},[endpoint,revision]);
 async function rollback(id:number){setBusy(true);setError("");try{await projectRequest(endpoint+"/rollback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({event_id:id,source_revision:revision})});onUpdated()}catch(e){setError(String(e))}finally{setBusy(false)}}
 return <section className="rounded-2xl border border-border p-5"><h2 className="text-lg font-semibold">学习计划变化</h2><p className="mt-2 text-sm text-muted-foreground">导师根据目标与实际表现判断是否调整。撤销保留已产生的学习证据；涉及知识点增删的版本需要导师协调恢复。</p>{error&&<p role="alert" className="mt-3 text-destructive">{error}</p>}{!versions.length&&<p className="py-6 text-sm text-muted-foreground">尚无已记录的计划调整。</p>}{versions.map((v,index)=><article key={v.id} className="mt-4 rounded-xl bg-muted/40 p-4"><p className="font-medium">计划版本 {v.revision}</p><p className="mt-1 text-sm">{v.reason}</p><details className="mt-3 text-sm"><summary className="cursor-pointer">查看前后变化</summary><div className="mt-3 grid gap-3 md:grid-cols-2">{[['调整前',v.before],['调整后',v.after]].map(([name,modules])=><div key={String(name)}><strong>{String(name)}</strong>{(modules as Module[]).map((m,i)=><p key={i} className="mt-2">{m.name}：{m.knowledge_points.map(k=>k.name).join('、')}</p>)}</div>)}</div></details>{index===0&&v.before.length>0&&<button disabled={busy} onClick={()=>void rollback(v.id)} className="mt-3 rounded-full border border-border px-4 py-2 text-sm disabled:opacity-50">{busy?'正在撤销…':'撤销这次调整'}</button>}</article>)}</section>
}
