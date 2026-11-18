import {render,screen,fireEvent,waitFor} from "@testing-library/react";
import {it,expect,vi} from "vitest";
import PersonalTraining from "@/components/learning/PersonalTraining";
const {request}=vi.hoisted(()=>({request:vi.fn()}));
vi.mock("@/lib/learning-project-api",()=>({projectRequest:request,projectEndpoint:(id:string)=>"/topics/"+id}));
it("trains with current project revision and explains insufficient data",async()=>{
 request.mockImplementation(async(path:string,init?:RequestInit)=>init?.method==='POST'?{id:'j',status:'queued',source_revision:8}:path.endsWith('/jobs')?[]:[{id:'r',status:'insufficient_data',created_at:1,n:4,sequences:1,reason:'需要更多独立记录',parameters:{},activated:false}]);
 render(<PersonalTraining pathId="p" revision={8} eligible={4}/>);
 fireEvent.click(screen.getByRole('button',{name:'训练并更新诊断'}));
 expect(await screen.findByText('需要更多独立记录')).toBeVisible();
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/topics/p/diagnosis/training',expect.objectContaining({method:'POST',body:JSON.stringify({source_revision:8})})));
 expect(screen.getByText(/未启用候选参数/)).toBeVisible();
});

it("allows zero-data review without suggesting model activation", async()=>{
 request.mockImplementation(async(path:string,init?:RequestInit)=>init?.method==='POST'?{id:'zero',status:'queued',source_revision:2}:[]);
 render(<PersonalTraining pathId="empty" revision={2} eligible={0}/>);
 expect(screen.getByText(/数据不足不代表你没有掌握/)).toBeVisible();
 const button=screen.getByRole('button',{name:'训练并更新诊断'});
 expect(button).toBeEnabled();fireEvent.click(button);
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/topics/empty/diagnosis/training',expect.objectContaining({method:'POST'})));
});
