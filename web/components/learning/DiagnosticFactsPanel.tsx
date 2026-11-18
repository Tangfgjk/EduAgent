"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { projectEndpoint, projectRequest } from "@/lib/learning-project-api";

type Facts = {
  schema_version: number;
  source_revision: number;
  objectives: {objective_id:string;name:string;measurement_status:string;observation_count:number;independent_count:number;assisted_count:number;unknown_assistance_count:number}[];
  observations: {observation_id:string;objective_id:string;question_id:string;source_refs:string[];session_id:string;turn_id:string;assistance:string;validity:string;result:string;answer:unknown;occurred_at:number}[];
};

export default function DiagnosticFactsPanel({pathId}:{pathId:string}) {
  const {i18n}=useTranslation();
  const zh=i18n.language.startsWith("zh");
  const labels:Record<string,string>={recorded:"已记录",voided:"已作废",conflicting:"来源冲突",correct:"正确",partial:"部分正确",incorrect:"错误",independent:"独立完成",assisted:"有辅助",unknown:"辅助情况未知"};
  const label=(value:string)=>zh?(labels[value]||value):value;
  const [facts,setFacts]=useState<Facts|null>(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  async function load(){
    setLoading(true);setError("");
    try{setFacts(await projectRequest<Facts>(projectEndpoint(pathId)+"/diagnostic-facts"));}
    catch(reason){setError(reason instanceof Error?reason.message:(zh?"读取失败":"Could not load facts"));}
    finally{setLoading(false);}
  }
  return <details className="mt-6 rounded-xl border border-border bg-card p-4" onToggle={event=>{if(event.currentTarget.open&&!facts&&!loading)void load();}}>
    <summary className="cursor-pointer text-sm font-medium">{zh?"诊断依据（数据记录）":"Diagnostic facts"}</summary>
    <p className="mt-3 text-xs text-muted-foreground">{zh?"这里展示已记录的表现依据。自述、独立完成和借助提示分开处理；尚无记录不代表不会。这不是掌握概率报告。":"Recorded performance only. Self-reports, independent and assisted attempts are distinct. Missing evidence does not imply weakness."}</p>
    <button disabled={loading} onClick={()=>void load()} className="mt-3 text-xs text-primary underline">{loading?(zh?"正在读取…":"Loading…"):(zh?"刷新记录":"Refresh")}</button>
    {error&&<p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}
    {facts&&<>
      <ul className="mt-3 space-y-3">{facts.objectives.map(o=><li key={o.objective_id} className="text-sm"><span className="font-medium">{o.name}</span><p className="mt-1 text-xs text-muted-foreground">{o.measurement_status==="unmeasured"?(zh?"尚无有效记录":"No valid observations"):(zh?`有效记录 ${o.observation_count} · 独立 ${o.independent_count} · 有辅助 ${o.assisted_count} · 辅助情况未知 ${o.unknown_assistance_count}`:`Valid ${o.observation_count} · Independent ${o.independent_count} · Assisted ${o.assisted_count} · Unknown ${o.unknown_assistance_count}`)}</p></li>)}</ul>
      {!facts.objectives.length&&<p className="mt-3 text-xs">{zh?"确认学习大纲后，这里会列出对应的学习目标。":"Objectives appear after your outline is confirmed."}</p>}
      <details className="mt-4"><summary className="cursor-pointer text-xs text-muted-foreground">{zh?"查看来源与原始回答":"Sources and recorded answers"}</summary><ul className="mt-3 space-y-3">{facts.observations.map(o=><li key={o.observation_id} className="break-words rounded-lg bg-muted p-3 text-xs"><p>{facts.objectives.find(k=>k.objective_id===o.objective_id)?.name||o.objective_id} · {new Date(o.occurred_at*1000).toLocaleString(i18n.language)}</p><p className="mt-1">{zh?"回答：":"Answer: "}{o.answer===null?(zh?"未保留":"Unavailable"):typeof o.answer==="string"?o.answer:JSON.stringify(o.answer)}</p><p className="mt-1">{zh?"记录状态：":"Record status: "}{label(o.validity)} · {label(o.result)} · {label(o.assistance)}</p><p className="mt-1 text-muted-foreground">{zh?"来源：":"Sources: "}{o.source_refs.join(" / ")}</p></li>)}</ul></details>
      <p className="mt-3 text-xs text-muted-foreground">{zh?"记录版本：":"Source revision: "}{facts.source_revision}</p>
    </>}
  </details>;
}
