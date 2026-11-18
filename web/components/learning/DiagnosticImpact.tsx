"use client";

import {useCallback,useEffect,useState} from "react";
import Link from "next/link";
import {useRouter} from "next/navigation";
import {useTranslation} from "react-i18next";
import {projectEndpoint,projectRequest,learningProjectRoute} from "@/lib/learning-project-api";
import {masterySessionRoute} from "@/lib/mastery-mode";
import {setPendingPrompt} from "@/lib/pending-prompt";
import {diagnosticOpening,diagnosticOpeningScope,type ImportImpact} from "@/lib/diagnostic-impact";

export default function DiagnosticImpact({pathId,impact:provided,onSend,compact=false,disabled=false}:{pathId:string;impact?:ImportImpact|null;onSend?:(text:string)=>unknown;compact?:boolean;disabled?:boolean}){
  const {i18n}=useTranslation();const zh=i18n.language.startsWith("zh");const tr=(cn:string,en:string)=>zh?cn:en;
  const router=useRouter();
  const [loaded,setLoaded]=useState<{pathId:string;impact:ImportImpact|null}|null>(null);
  const [error,setError]=useState("");const [busy,setBusy]=useState(false);
  const refresh=useCallback(async()=>{try{const data=await projectRequest<ImportImpact|null>(projectEndpoint(pathId)+"/diagnostic-impact");setLoaded({pathId,impact:data});setError("");}catch(reason){setError(reason instanceof Error?reason.message:"Could not read import impact");}},[pathId]);
  useEffect(()=>{
    if(provided!==undefined)return;
    let active=true;
    const read=()=>{void projectRequest<ImportImpact|null>(projectEndpoint(pathId)+"/diagnostic-impact").then(data=>{if(active){setLoaded({pathId,impact:data});setError("");}}).catch(reason=>{if(active)setError(reason.message);});};
    const changed=(event:Event)=>{if((event as CustomEvent<{pathId:string}>).detail?.pathId===pathId)read();};
    read();window.addEventListener("wenjin:diagnosis-import-changed",changed);window.addEventListener("focus",read);
    return()=>{active=false;window.removeEventListener("wenjin:diagnosis-import-changed",changed);window.removeEventListener("focus",read);};
  },[pathId,provided]);
  const impact=provided!==undefined?provided:loaded?.pathId===pathId?loaded.impact:null;
  async function start(mode:"teach_first"|"probe_first"){
    if(!impact||disabled)return;
    setBusy(true);setError("");
    try{
      const latest=await projectRequest<ImportImpact|null>(projectEndpoint(pathId)+"/diagnostic-impact");
      if(!latest||latest.import_id!==impact.import_id||latest.status!=="active"||!latest.recommendation)throw new Error(tr("诊断或本地证据已变化，请刷新建议后再开始。","Diagnostic context changed; refresh recommendations before starting."));
      const text=diagnosticOpening(latest,mode,zh);
      if(!text)return;
      await projectRequest(projectEndpoint(pathId)+"/wenjin/preference",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({strategy:mode})});
      if(onSend)await onSend(text);
      else{setPendingPrompt(text,diagnosticOpeningScope(pathId));router.push(masterySessionRoute(pathId,"study"));}
    }catch(reason){setError(reason instanceof Error?reason.message:tr("无法开始，请重试","Could not start; retry"));}
    finally{setBusy(false);}
  }
  if(!impact)return error?<p role="alert" className="my-3 text-xs text-destructive">{error} <button onClick={()=>void refresh()} className="underline">{tr("重试","Retry")}</button></p>:null;
  const ability=(value:string)=>({recognition:tr("辨别","recognition"),explanation:tr("解释","explanation"),application:tr("应用","application"),transfer:tr("迁移","transfer")}[value]||value);
  const recommendation=impact.recommendation;
  return <section aria-label={tr("诊断导入后的学习建议","Learning recommendations after import")} className={compact?"shrink-0 border-b border-border bg-accent/30 px-4 py-3":"my-5 rounded-xl border border-primary/30 bg-accent/30 p-5"}>
    <h2 className="text-sm font-semibold">{impact.status==="active"?tr("诊断参考已接入 · 下一步建议","Reference connected · Suggested next step"):impact.status==="revoked"?tr("诊断参考已撤销","Reference revoked"):impact.status==="historical"?tr("诊断已保存为历史记录","Historical diagnosis saved"):tr("大纲已变化，诊断映射需要重新确认","Outline changed; confirm mappings again")}</h2>
    <p className="mt-2 text-xs text-muted-foreground">{impact.source_system} · {impact.observed_on} · {tr("本人上传，未核实","Self-uploaded, unverified")}</p>
    {!compact&&<p className="mt-2 text-sm">{tr("提供 ","Supplied ")}{impact.coverage.supplied}{tr(" 个知识点：当前可参考 "," objectives: usable ")}{impact.coverage.usable} · {tr("未映射 ","unmapped ")}{impact.coverage.unmapped} · {tr("未测 ","unmeasured ")}{impact.coverage.untested} · {tr("报告声明范围 ","declared scope ")}{impact.coverage.declared}</p>}
    {!compact&&<p className="mt-1 text-xs text-muted-foreground">{tr("未映射与未测项可能重叠；未测不代表不会。","Unmapped and unmeasured categories may overlap; unmeasured does not mean unable.")}</p>}
    {recommendation&&<><p className="mt-3 text-sm font-medium">{tr("建议先确认：","Suggested check: ")}{recommendation.objective_name}</p><p className="mt-1 text-xs text-muted-foreground">{tr("外部估计 ","External estimate ")}{recommendation.probability} · {recommendation.relation==="partial"?tr("仅部分对应，需核实范围","Partial mapping; verify scope"):tr("已声明范围一致，仍需实际任务确认","Declared equivalent; verify with a real task")}</p><div className="mt-3 flex flex-wrap gap-2"><button disabled={busy||disabled} onClick={()=>void start("probe_first")} className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50">{tr("开始一次核查","Start one check")}</button><button disabled={busy||disabled} onClick={()=>void start("teach_first")} className="rounded-lg border border-border px-3 py-2 text-sm disabled:opacity-50">{tr("先听讲","Teach first")}</button></div></>}
    {!recommendation&&<p className="mt-2 text-xs">{impact.status==="active"?tr("当前没有需要根据该报告新增的起点核查，可继续原学习流程。","No additional starting check is suggested; continue learning."):tr("该结果不会决定当前教学。需要时可在导入区查看来源或重新确认新版本。","This result does not determine current teaching; review it in the import section.")}</p>}
    {!!impact.followups.length&&<div className="mt-3 space-y-2 rounded-lg border border-border p-3"><h3 className="text-xs font-medium">{tr("导入后的本地表现更新","Local observations after import")}</h3>{impact.followups.map(row=><div key={row.observation_id} className="text-xs"><p>{row.objective_name}：{ability(row.ability)} · {row.result==="correct"?tr("回答通过","Passed"):row.result==="partial"?tr("部分通过","Partial"):tr("尚未通过","Not passed")} · {row.assistance==="independent"?tr("独立完成","Independent"):row.assistance==="assisted"?tr("有辅助，独立能力待确认","Assisted; independence unconfirmed"):tr("辅助情况未知","Assistance unknown")}</p><p className="mt-1 text-muted-foreground">{row.missing.length?tr("下一步补充核查：","Next evidence gap: ")+row.missing.map(ability).join("、"):tr("已有所需独立能力证据，可安排后续复习。","Required independent abilities observed; arrange later review.")}</p></div>)}<p className="text-xs text-muted-foreground">{tr("这是导入后发生的本地记录，不代表导入造成了能力提升。","These are subsequent local observations, not evidence of a causal import effect.")}</p></div>}
    <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><span>{tr("报告只作参考，不改变原始分数，也不直接授予掌握。","Reference only: original scores and mastery credit remain separate.")}</span><Link className="text-primary underline" href={learningProjectRoute(pathId)+"#external-diagnosis-import"} onClick={event=>{const panel=document.getElementById("external-diagnosis-import");if(panel instanceof HTMLDetailsElement){event.preventDefault();panel.open=true;panel.scrollIntoView({behavior:"smooth",block:"start"});}}}>{tr("查看来源与映射","View provenance and mappings")}</Link></div>
    {error&&<p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}
  </section>;
}
