import {render,screen} from "@testing-library/react";
import {describe,expect,it,vi} from "vitest";
import DiagnosticImpact from "@/components/learning/DiagnosticImpact";
import {diagnosticOpening,diagnosticOpeningScope,type ImportImpact} from "@/lib/diagnostic-impact";
import {initI18n} from "@/i18n/init";

vi.mock("next/navigation",()=>({useRouter:()=>({push:vi.fn()})}));
initI18n("zh");
function impact():ImportImpact{return {
  import_id:"i",source_revision:1,status:"active",source_system:"模拟报告",observed_on:"2026-11-09",provenance:"self_uploaded_unverified",
  coverage:{declared:3,supplied:3,measured:2,untested:1,mapped:1,unmapped:2,usable:1},
  recommendation:{objective_id:"k",objective_name:"方程",knowledge_name:"方程",probability:.35,relation:"partial",missing:["explanation"],has_local_evidence:false},followups:[],
};}

describe("visible diagnostic impact",()=>{
  it("shows the affected objective and a direct check action",()=>{
    render(<DiagnosticImpact pathId="p" impact={impact()}/>);
    expect(screen.getByRole("button",{name:"开始一次核查"})).toBeVisible();
    expect(screen.getByText("建议先确认：方程")).toBeVisible();
    expect(screen.getByText(/外部估计 0.35/)).toBeVisible();
  });
  it("shows assisted follow-up without claiming independent mastery",()=>{
    const data=impact();data.followups=[{objective_id:"k",objective_name:"方程",observation_id:"o",occurred_at:1,result:"correct",assistance:"assisted",ability:"explanation",achieved:[],missing:["explanation"],new_observations:1}];
    render(<DiagnosticImpact pathId="p" impact={data}/>);
    expect(screen.getByText(/有辅助，独立能力待确认/)).toBeVisible();
    expect(screen.getByText(/下一步补充核查：解释/)).toBeVisible();
    expect(screen.getByText(/不代表导入造成了能力提升/)).toBeVisible();
  });
  it("removes the check action after revocation",()=>{
    const data=impact();data.status="revoked";data.recommendation=null;
    render(<DiagnosticImpact pathId="p" impact={data}/>);
    expect(screen.queryByRole("button",{name:"开始一次核查"})).not.toBeInTheDocument();
  });
  it("builds a source-bound opening and refuses inactive references",()=>{
    const data=impact();expect(diagnosticOpening(data,"probe_first",true)).toContain('"objective_id":"k"');
    expect(diagnosticOpening(data,"probe_first",true)).toContain('"external_probability":0.35');
    expect(diagnosticOpeningScope("a")).not.toBe(diagnosticOpeningScope("b"));
    data.status="revoked";expect(diagnosticOpening(data,"probe_first",true)).toBe("");
  });
});
