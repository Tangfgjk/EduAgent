import {test,expect,type Page} from '@playwright/test';
import {mkdirSync} from 'node:fs';

async function login(page:Page,role:'admin'|'student',username:string){
 await page.goto(`/${role}/`);await page.getByLabel('账号',{exact:true}).fill(username);await page.getByLabel('密码',{exact:true}).fill('S03-test-only-password!');await page.getByRole('button',{name:'登录',exact:true}).click();await expect(page.locator('.app-shell')).toBeVisible();
}
async function headers(page:Page,role:'admin'|'student'){
 const cookies=await page.context().cookies();return {'X-Wenjin-Client':role,'X-CSRF-Token':cookies.find(c=>c.name===`wenjin_${role}_csrf`)!.value};
}
async function seed(page:Page,admin:Page){
 const sh=await headers(page,'student'),ah=await headers(admin,'admin');
 async function post(owner:Page,path:string,data:unknown,role:'admin'|'student'='student'){
  const response=await owner.request.post(path,{headers:{...(role==='admin'?ah:sh),'Idempotency-Key':crypto.randomUUID()},data});expect(response.ok(),await response.text()).toBeTruthy();return response.json();
 }
 async function settled(path:string){await expect.poll(async()=> (await (await page.request.get(path,{headers:sh})).json()).status).not.toBe('queued');return (await page.request.get(path,{headers:sh})).json();}
 const content=await (await admin.request.get('/api/v1/admin/content',{headers:ah})).json();
 if(!content.enabled)await post(admin,'/api/v1/admin/content/development-release',{content_version:content.content_version,expected_revision:content.revision,enabled:true,acknowledge_development_only:true},'admin');
 const material=await post(admin,'/api/v1/admin/planning-materials',{title:'进化开发依据 '+Date.now(),kind:'development_reference',edition:'dev1',source_ref:'自编工程验收，非专业量规',body:'发现数值错误后先核对等式条件与运算，再独立代回检查。'},'admin');
 await post(admin,`/api/v1/admin/planning-materials/${material.material_id}/release`,{expected_revision:material.revision,enabled:true,acknowledge_development_only:true},'admin');
 const title='离线改进验收 '+Date.now();const space=await post(page,'/api/v1/student/workspaces',{kind:'project',title});const root=`/api/v1/student/workspaces/${space.workspace_id}`;
 await post(page,root+'/course',{topic:'等式性质与核验',title:'方程开发课',unit_ids:['math.g7.equations.dev1'],active_unit_id:'math.g7.equations.dev1',expected_revision:0});
 let run=await post(page,root+'/runs',{goal:'核对条件与数值'});let path=root+'/runs/'+run.run_id;run=await settled(path);
 await post(page,path+'/resume',{action:'confirm',expected_revision:run.revision,gate_id:run.gate_id,daily_minutes:15});await settled(path);
 run=await post(page,root+'/tutoring',{unit_id:'math.g7.equations.dev1'});path=root+'/tutoring/'+run.run_id;run=await settled(path);
 for(const command of [{action:'answer',answer:'3'},{action:'next'},{action:'answer',answer:'0',reasoning:'我需要核对等式条件'}]){await post(page,path+'/inputs',{...command,expected_revision:run.revision,gate_id:run.gate_id});run=await settled(path);}
 await post(page,path+'/cancel',{expected_revision:run.revision});
 run=await post(page,root+'/strategies',{daily_minutes:20,reflection_focus:'basis',learning_experience:'先建立当前开发基线再核对变化',material_ids:[material.material_id],execution:{schema_version:'teaching-policy.v1',practice_order:'original',review_order:'due_first',support_mode:'adaptive',question_focus:'basis'}});path=root+'/strategies/'+run.run_id;run=await settled(path);
 await post(admin,'/api/v1/admin/strategies/'+run.run_id+'/decision',{action:'approve',expected_revision:run.revision,gate_id:run.gate_id,note:'仅开发基线，核对资料与软策略边界'},'admin');run=await settled(path);
 await post(page,path+'/decision',{action:'confirm',expected_revision:run.revision,gate_id:run.gate_id,note:'我确认采用这份受限基线'});run=await settled(path);
 return {wid:space.workspace_id,title,baseline:run.run_id,sh,ah};
}

test('离线Skill：原始材料、冻结评测、人工复核、待审提案及撤回门禁',async({page,browser})=>{
 test.setTimeout(90_000);const admin=await browser.newPage();
 try{
  await login(admin,'admin','s03.admin');await login(page,'student','s03.one');
  const data=await seed(page,admin);
  await page.goto('/student/skill-evolution?workspace='+data.wid);
  await page.getByLabel('改进方式').selectOption('offline');await page.getByRole('button',{name:'生成离线改进候选',exact:true}).click();
  await expect(page.locator('.evolution-detail')).toContainText('等待开发评测复核');
  await expect(page.getByText('工程检查通过，可申请人工复核',{exact:true})).toBeVisible();
  await page.getByText('查看冻结来源与原始材料',{exact:true}).click();
  await page.getByRole('listitem').filter({hasText:/^P-EQ-01 · failed/}).getByRole('button',{name:'查看原始材料',exact:true}).click();
  await expect(page.getByRole('dialog')).toContainText('原答案：0');await page.getByRole('button',{name:'关闭弹窗',exact:true}).click();
  await admin.goto('/admin/skill-evolution');await admin.getByRole('button',{name:new RegExp(data.title)}).click();
  await expect(admin.getByRole('heading',{name:'冻结机制评测',exact:true})).toBeVisible();
  await admin.getByLabel('离线评测复核依据',{exact:true}).fill('已经核对来源和隔离工程样本，只批准创建开发策略提案');
  await admin.getByRole('button',{name:'复核通过，允许生成提案',exact:true}).click();
  await expect(page.getByRole('button',{name:'采纳为待审策略',exact:true})).toBeVisible();
  await page.getByLabel('采纳为待审策略的理由',{exact:true}).fill('我同意将优先回看错误作为下一轮待审假设，继续核对实际表现');
  await page.getByRole('button',{name:'采纳为待审策略',exact:true}).click();
  await expect(page.getByRole('link',{name:'前往策略审核与确认',exact:true})).toBeVisible();
  const active=await (await page.request.get(`/api/v1/student/workspaces/${data.wid}/active-strategy`,{headers:data.sh})).json();expect(active.version_id).toBe(data.baseline);
  mkdirSync('test-results/screens',{recursive:true});await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:'test-results/screens/student-evolution.png',fullPage:true});
  await admin.getByRole('button',{name:'刷新改进状态',exact:true}).click();
  await expect(admin.locator('.evolution-detail .card-head .badge')).toHaveText('已生成待审策略');
  await expect.poll(async()=>{
    const result=await (await page.request.get(`/api/v1/student/workspaces/${data.wid}/strategies`,{headers:data.sh})).json();
    return result.items.some((row:{status:string;view?:{phase:string}})=>row.status==='awaiting_input'&&row.view?.phase==='admin_review');
  }).toBe(true);
  await admin.getByLabel('离线评测复核依据',{exact:true}).fill('需要补充更充分的开发验证，先撤回此候选的复核');
  await admin.getByRole('button',{name:'撤回评测复核',exact:true}).click();
  await expect(admin.locator('.evolution-detail .card-head .badge')).toHaveText('复核已撤回');
  await page.getByRole('button',{name:'刷新改进状态',exact:true}).click();
  await expect(page.locator('.evolution-detail .card-head .badge')).toHaveText('复核已撤回');
  await page.getByRole('link',{name:'前往策略审核与确认',exact:true}).click();
  await expect(page.locator('.strategy-detail')).toContainText('等待管理员审核');
 }finally{await admin.close();}
});

test('手机Skill改进：缺少执行基线提示、临时空间隔离与布局',async({page})=>{
 await page.setViewportSize({width:390,height:844});await login(page,'student','s03.two');const h=await headers(page,'student');
 let response=await page.request.post('/api/v1/student/workspaces',{headers:{...h,'Idempotency-Key':crypto.randomUUID()},data:{kind:'project',title:'手机待准备 '+Date.now()}});const project=await response.json();
 await page.goto('/student/skill-evolution?workspace='+project.workspace_id);await page.getByLabel('改进方式').selectOption('offline');await page.getByRole('button',{name:'生成离线改进候选',exact:true}).click();
 await expect(page.getByText('请先审核并激活含四项执行参数的策略作为基线',{exact:false})).toBeVisible();
 response=await page.request.post('/api/v1/student/workspaces',{headers:{...h,'Idempotency-Key':crypto.randomUUID()},data:{kind:'temporary',title:'局部探索 '+Date.now()}});const temp=await response.json();
 await page.goto('/student/skill-evolution?workspace='+temp.workspace_id);await expect(page.getByText('Skill改进从正式项目开始',{exact:true})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 mkdirSync('test-results/screens',{recursive:true});await page.screenshot({path:'test-results/screens/student-evolution-mobile.png',fullPage:true});
});
