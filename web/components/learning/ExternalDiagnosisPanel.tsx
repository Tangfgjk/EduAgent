"use client";

import DiagnosticImpact from "./DiagnosticImpact";
import {notifyDiagnosticImport,type ImportImpact} from "@/lib/diagnostic-impact";
import {useState} from "react";
import {useTranslation} from "react-i18next";
import {projectEndpoint,projectRequest} from "@/lib/learning-project-api";

type Entry={knowledge_id:string;knowledge_name:string;probability:number|null;measurement_status:string};
type Document={source_system:string;student_ref:string;subject:string;observed_on:string;model_version:string;estimates:Entry[]} & Record<string,unknown>;
type Mapping={knowledge_id:string;objective_id:string;relation:"exact"|"partial"|"unmapped"};
type Preview={outline_digest:string;preview_digest:string;duplicate:boolean;can_import:boolean;coverage:{declared:number;supplied:number;measured:number;mapped:number;unmapped:number;mapped_measured:number};rows:(Entry & Mapping & {suggestions:string[]})[];warnings:string[]};
type RecordRow={import_id:string;revoked_at:number|null;document:{source_system:string;result_id:string;result_version:string;observed_on:string;student_ref:string;total_knowledge_count:number;estimates:Entry[]};links:Mapping[]};
type ImportList={imports:RecordRow[];references:{import_id:string;stale:boolean;scope_current:boolean;active_reference:boolean}[]};

export default function ExternalDiagnosisPanel({pathId,objectives}:{pathId:string;objectives:{id:string;name:string}[]}){
  const {i18n}=useTranslation();const zh=i18n.language.startsWith("zh");
  const tr=(cn:string,en:string)=>zh?cn:en;
  const warningLabels:Record<string,string>={
    "Self-uploaded result: student identity and provider authenticity have not been independently verified.":"本人上传的结果：学生身份和提供方真实性尚未独立核实。",
    "Mappings require your confirmation. Same names do not guarantee the same scope.":"映射需要本人确认，同名知识点不代表知识范围一致。",
    "Older than 90 days: retained as history, not an active teaching reference.":"结果超过 90 天，只保留为历史，不作为当前教学参考。",
    "Partial coverage: some report entries or project mappings are missing.":"覆盖不完整：部分报告条目或项目知识点映射缺失。",
    "Partial mappings are limited references, not equivalent objectives.":"部分对应只提供有限参考，不视为同一个学习目标。",
  };
  const endpoint=projectEndpoint(pathId)+"/diagnostic-imports";
  const [successImpact,setSuccessImpact]=useState<ImportImpact|null>(null);
  const [doc,setDoc]=useState<Document|null>(null);
  const [links,setLinks]=useState<Mapping[]>([]);
  const [preview,setPreview]=useState<Preview|null>(null);
  const [list,setList]=useState<ImportList|null>(null);
  const [busy,setBusy]=useState(false);const [error,setError]=useState("");const [message,setMessage]=useState("");
  const [own,setOwn]=useState(false);const [confirmed,setConfirmed]=useState(false);
  async function refresh(){setList(await projectRequest<ImportList>(endpoint));}
  async function run(action:()=>Promise<void>){setBusy(true);setError("");setMessage("");try{await action();}catch(reason){setError(reason instanceof Error?reason.message:tr("操作失败","Request failed"));}finally{setBusy(false);}}
  async function readFile(file:File|undefined){
    if(!file)return;
    setDoc(null);setPreview(null);setLinks([]);setOwn(false);setConfirmed(false);
    await run(async()=>{
      if(file.size>1024*1024)throw new Error(tr("结果文件最多 1 MB。","Result file must be at most 1 MB."));
      const parsed:Document=JSON.parse(await file.text());
      if(!parsed||!Array.isArray(parsed.estimates)||parsed.estimates.length>500||typeof parsed.source_system!=="string"||typeof parsed.student_ref!=="string"||parsed.estimates.some(e=>!e||typeof e.knowledge_id!=="string"||typeof e.knowledge_name!=="string"))throw new Error(tr("请使用标准 JSON 结果模板；此入口不解析原始成绩 CSV 或 PDF 报告。","Use the standard JSON result template; raw score CSV/PDF reports are not parsed here."));
      setDoc(parsed);setLinks(parsed.estimates.map(e=>({knowledge_id:e.knowledge_id,objective_id:"",relation:"unmapped"})));
    });
  }
  function update(index:number,patch:Partial<Mapping>){
    setLinks(current=>current.map((link,i)=>i===index?{...link,...patch}:link));setPreview(null);setConfirmed(false);
  }
  function download(){
    const template={schema_version:1,source_system:"",result_id:"",result_version:"1",student_ref:"",subject:"",scope:"",observed_on:"",model_version:"",taxonomy_version:"",mapping_version:"",zero_encoding:"zero_is_untested",total_knowledge_count:1,estimates:[{knowledge_id:"",knowledge_name:objectives[0]?.name||"",measurement_status:"estimated",probability:null}]};
    const url=URL.createObjectURL(new Blob([JSON.stringify(template,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download="diagnosis-result-template.json";a.click();URL.revokeObjectURL(url);
  }
  return <details id="external-diagnosis-import" className="mt-6 rounded-xl border border-border bg-card p-4" onToggle={event=>{if(event.currentTarget.open&&!list&&!busy)void run(refresh);}}>
    <summary className="cursor-pointer text-sm font-medium">{tr("导入已有诊断（可选）","Import an existing diagnosis (optional)")}</summary>
    <p className="mt-3 text-xs text-muted-foreground">{tr("仅导入已有结果，不运行外部模型。本人上传的数据尚未核实，只用于学习参考，不直接授予掌握；不导入也可正常学习。","Imports existing results only. Self-uploaded data is unverified, grants no mastery, and is optional.")}</p>
    <div className="mt-3 flex flex-wrap gap-3 text-sm"><button type="button" onClick={download} className="text-primary underline">{tr("下载空白 JSON 模板","Download blank JSON template")}</button><button type="button" disabled={busy} onClick={()=>void run(refresh)} className="text-primary underline">{tr("刷新记录","Refresh imports")}</button></div>
    <p className="mt-2 text-xs text-muted-foreground">{tr("填写真实来源、学生编号、测量日期、模型/知识体系/映射版本及各知识点概率。CSV/PDF 需先转成此标准格式。旧模型零值表示未测时使用 zero_is_untested。","Fill real provenance, student reference, measurement date, model/taxonomy/mapping versions and estimates. Convert CSV/PDF first; use zero_is_untested only for that source convention.")}</p>
    <input type="file" accept=".json,application/json" disabled={busy} aria-label={tr("选择已有诊断 JSON","Choose diagnosis JSON")} className="mt-3 block max-w-full text-sm" onChange={event=>{void readFile(event.target.files?.[0]);event.currentTarget.value="";}}/>
    {error&&<p role="alert" className="mt-3 break-words text-sm text-destructive">{error}</p>}{message&&<p role="status" className="mt-3 text-sm text-primary">{message}</p>}
    {successImpact&&<DiagnosticImpact pathId={pathId} impact={successImpact} />}
    {doc&&<section className="mt-4 space-y-3">
      <p className="break-words text-sm">{tr("来源 / 学生编号：","Source / student reference: ")}{doc.source_system} / {doc.student_ref}</p>
      <p className="text-xs text-muted-foreground">{doc.subject} · {doc.observed_on} · {doc.model_version}</p>
      <p className="text-xs">{tr("请人工确认范围对应关系。同名仅作参考，未选择的条目保持未映射。","Confirm scope mappings manually. Names alone do not establish equivalence; unselected entries remain unmapped.")}</p>
      <div className="max-h-80 space-y-3 overflow-auto">{doc.estimates.map((entry,index)=><div key={index} className="rounded-lg border border-border p-3 text-sm"><p>{entry.knowledge_name} <span className="text-xs text-muted-foreground">({entry.knowledge_id})</span></p><div className="mt-2 flex flex-wrap gap-2"><select disabled={busy} aria-label={tr("本项目目标 ","Project objective ")+entry.knowledge_id} value={links[index]?.objective_id||""} onChange={event=>update(index,{objective_id:event.target.value,relation:event.target.value?"partial":"unmapped"})} className="max-w-full rounded-lg border border-border bg-background p-2"><option value="">{tr("未映射","Unmapped")}</option>{objectives.map(o=><option key={o.id} value={o.id}>{o.name}{o.name===entry.knowledge_name?tr("（同名，需确认）"," (same name, verify)"):""}</option>)}</select>{links[index]?.objective_id&&<select disabled={busy} aria-label={tr("对应程度 ","Mapping relation ")+entry.knowledge_id} value={links[index].relation} onChange={event=>update(index,{relation:event.target.value as Mapping["relation"]})} className="rounded-lg border border-border bg-background p-2"><option value="partial">{tr("部分对应","Partial")}</option><option value="exact">{tr("范围一致","Exact scope")}</option></select>}</div></div>)}</div>
      <button type="button" disabled={busy} onClick={()=>void run(async()=>{setPreview(await projectRequest<Preview>(endpoint+"/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({document:doc,links})}));})} className="rounded-lg border border-border px-3 py-2 text-sm disabled:opacity-50">{tr("校验并预览","Validate and preview")}</button>
      {preview&&<div className="space-y-3 rounded-lg bg-muted p-3 text-sm"><p>{tr("覆盖范围：报告声明 ","Coverage: declared ")}{preview.coverage.declared} · {tr("提供 ","supplied ")}{preview.coverage.supplied} · {tr("有效估计 ","estimated ")}{preview.coverage.measured} · {tr("已映射 ","mapped ")}{preview.coverage.mapped} · {tr("未映射 ","unmapped ")}{preview.coverage.unmapped}</p><p className="text-xs">{tr("映射成功不代表实际测量；缺失和未测不会填为零分。超过 90 天的结果只保留为历史参考。","Mapped does not mean measured; missing/untested is not zero. Results older than 90 days are historical references only.")}</p><ul className="list-inside list-disc space-y-1 text-xs">{preview.rows.map(row=><li key={row.knowledge_id}>{row.knowledge_name} · {row.measurement_status==="unmeasured"?tr("未测","Unmeasured"):tr("外部估计 ","External estimate ")+row.probability} · {row.relation==="unmapped"?tr("未映射","Unmapped"):row.relation==="partial"?tr("部分对应","Partial"):tr("范围一致","Exact scope")}</li>)}</ul>
      <details><summary className="cursor-pointer text-xs">{tr("来源与完整性提示","Provenance and coverage warnings")}</summary><ul className="mt-2 list-inside list-disc text-xs">{preview.warnings.map(w=><li key={w}>{zh?(warningLabels[w]||w):w}</li>)}</ul></details>
      <label className="flex gap-2"><input type="checkbox" disabled={busy} checked={own} onChange={e=>setOwn(e.target.checked)}/><span>{tr("我确认报告中的学生编号属于我；这只是本人声明，不代表提供方已验证。","I confirm this student reference is mine; this is self-attestation, not provider verification.")}</span></label>
      <label className="flex gap-2"><input type="checkbox" disabled={busy} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/><span>{tr("我已检查学科、日期和知识范围，并确认所选映射。","I reviewed the subject, date, scope and selected mappings.")}</span></label>
      <button type="button" disabled={busy||!own||!confirmed||!preview.can_import} onClick={()=>void run(async()=>{const result=await projectRequest<{duplicate:boolean;impact:ImportImpact}>(endpoint,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({document:doc,links,outline_digest:preview.outline_digest,preview_digest:preview.preview_digest,belongs_to_me:own,mapping_confirmed:confirmed})});await refresh();setSuccessImpact(result.impact);notifyDiagnosticImport(pathId);setMessage(result.duplicate?tr("该结果已导入，未重复保存。","Already imported; no duplicate was saved."):tr("已保存为外部参考，未改变本地掌握证据。","Saved as external context without changing local mastery evidence."));setDoc(null);setPreview(null);})} className="rounded-lg bg-primary px-3 py-2 text-primary-foreground disabled:opacity-50">{tr("确认导入","Confirm import")}</button>
      {!preview.can_import&&<p>{tr("至少确认一个本项目映射后才能保存；尚无大纲时请先确认学习大纲。","Confirm at least one mapping first; agree on an outline if no objectives exist.")}</p>}</div>}
    </section>}
    {!!list?.imports.length&&<section className="mt-5"><h3 className="text-sm font-medium">{tr("已导入结果","Imported results")}</h3><ul className="mt-3 space-y-3">{list.imports.map(record=>{const refs=list.references.filter(r=>r.import_id===record.import_id);return <li key={record.import_id} className="break-words rounded-lg border border-border p-3 text-sm"><p>{record.document.source_system} · {record.document.result_id} / {record.document.result_version}</p><p className="mt-1 text-xs text-muted-foreground">{record.document.observed_on} · {tr("本人上传，未核实","Self-uploaded, unverified")} · {record.revoked_at?tr("已撤销","Revoked"):refs.some(r=>!r.scope_current)?tr("大纲已变化，请重新映射新版本","Outline changed; map a new version"):refs.some(r=>r.stale)?tr("历史结果","Historical result"):!refs.some(r=>r.active_reference)?tr("已保存，暂无有效估计","Saved; no active estimates"):tr("参考有效，不计入掌握","Reference only, no mastery credit")}</p><p className="mt-1 text-xs">{tr("报告条目 / 声明范围：","Entries / declared scope: ")}{record.document.estimates.length}/{record.document.total_knowledge_count} · {tr("当前可参考估计：","Active mapped estimates: ")}{refs.filter(r=>r.active_reference).length}</p>{!record.revoked_at&&<button type="button" disabled={busy} onClick={()=>void run(async()=>{await projectRequest(endpoint+"/"+encodeURIComponent(record.import_id)+"/revoke",{method:"POST"});await refresh();setSuccessImpact(null);notifyDiagnosticImport(pathId);setMessage(tr("已撤销，保留记录但不再供陪学参考。","Revoked; retained for audit and excluded from tutor context."));})} className="mt-2 text-xs text-primary underline">{tr("撤销此结果的参考使用","Revoke this reference")}</button>}</li>;})}</ul></section>}
    {busy&&<p role="status" className="mt-3 text-xs">{tr("正在处理…","Working…")}</p>}
  </details>;
}
