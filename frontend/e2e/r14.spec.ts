import { test, expect, type Page } from '@playwright/test';

async function login(page:Page,role:'student'|'admin'){
  await page.goto('/'+role+'/');await page.getByLabel('账号',{exact:true}).fill(role==='admin'?'s03.admin':'s03.one');
  await page.getByLabel('密码',{exact:true}).fill('S03-test-only-password!');await page.getByRole('button',{name:'登录',exact:true}).click();
  await expect(page.locator('.app-shell')).toBeVisible();const cookies=await page.context().cookies();
  return {'X-Wenjin-Client':role,'X-CSRF-Token':cookies.find(c=>c.name===`wenjin_${role}_csrf`)!.value};
}

test('R14 学生引导、自主课程开放、中文回答与对话恢复',async({page},info)=>{
  test.setTimeout(120_000);const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const admin=await login(page,'admin');
  const model=await(await page.request.get('/api/v1/admin/model-runtime',{headers:admin})).json();
  expect((await page.request.post('/api/v1/admin/model-runtime/publish',{headers:admin,data:{expected_revision:model.revision,vendor:'fake',max_output_tokens:512,account_daily_calls:0,site_daily_calls:0,prompt_version:'probability.followup.v3',acknowledge_development_only:true}})).ok()).toBeTruthy();
  const policy=await(await page.request.get('/api/v1/admin/generation-policy',{headers:admin})).json();
  expect((await page.request.post('/api/v1/admin/generation-policy',{headers:admin,data:{expected_revision:policy.revision,courses:true,interaction:true,search:false,private_auto_release:true,acknowledge_development_only:true}})).ok()).toBeTruthy();
  const student=await login(page,'student');
  await page.getByRole('button',{name:'开启临时会话',exact:false}).click();
  await page.getByLabel('空间名称').fill('R14自主学习'+Date.now());await page.getByRole('button',{name:'保存空间',exact:true}).click();
  const guide=page.getByRole('region',{name:'当前空间的下一步'});
  await expect(guide).toBeVisible();await guide.getByRole('link',{name:'告诉我你想学什么'}).click();
  await page.getByLabel('我想学什么？').fill('负数乘除和运算顺序');
  await page.getByRole('checkbox',{name:'启用有理数教学 Agent 设计',exact:false}).uncheck();
  await page.getByRole('button',{name:'解析主题并准备诊断'}).click();
  await page.getByLabel('诊断 1 答案').fill('0');await page.getByLabel('诊断 2 答案').fill('0');
  await page.getByRole('button',{name:'保存诊断并查看课程范围'}).click();
  await page.getByRole('button',{name:'确认范围并生成课程'}).click();
  await expect(page.getByRole('button',{name:'确认使用这份课程'})).toBeVisible({timeout:40_000});
  await page.getByRole('button',{name:'确认使用这份课程'}).click();
  const wid=new URL(page.url()).searchParams.get('workspace')!;
  await page.goto(`/student/study?workspace=${wid}`);await page.getByRole('button',{name:'开始开发单元',exact:true}).click();
  const chat=page.getByRole('region',{name:'持续陪学对话'});await expect(chat).toBeVisible();
  await page.getByLabel('我的回答',{exact:true}).fill('这个答案是3');await chat.getByRole('button',{name:'发送',exact:true}).click();
  await expect(chat.getByText('这个答案是3',{exact:true})).toBeVisible();
  await expect(chat.getByRole('button',{name:'继续下一步',exact:true})).toBeEnabled();
  await page.reload();await expect(page.getByRole('log',{name:'学习对话记录'}).locator('.chat-message.student').filter({hasText:'这个答案是3'})).toBeVisible();
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2)).toBeTruthy();
  await page.screenshot({path:info.outputPath('r14-chat-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:info.outputPath('r14-chat-desktop.png'),fullPage:true});
  expect((await page.request.get(`/api/v1/student/workspaces/${wid}/next-action`,{headers:student})).ok()).toBeTruthy();
  expect(errors).toEqual([]);
});

test('R14 无资料上传的个人策略与教学效果协议页面',async({page})=>{
  const admin=await login(page,'admin');await page.goto('/admin/effect-studies');
  await expect(page.getByRole('heading',{name:'教学效果验证平台',exact:true})).toBeVisible();
  await expect(page.getByText('目前无教师与真实学生试点。',{exact:false})).toBeVisible();
  const student=await login(page,'student');
  const response=await page.request.post('/api/v1/student/workspaces',{headers:{...student,'Idempotency-Key':crypto.randomUUID()},data:{kind:'project',title:'R14策略默认资料'}});
  const wid=(await response.json()).workspace_id;
  await page.goto(`/student/planner?workspace=${wid}`);await page.getByLabel('我的学习体验').fill('我希望先独立思考，再请求逐级提示');
  await page.getByRole('button',{name:'生成开发策略提案'}).click();
  await expect(page.getByText('等待学生确认',{exact:true})).toBeVisible({timeout:20_000});
  expect((await page.request.get('/api/v1/admin/effect-studies',{headers:student})).status()).toBe(403);
  expect((await page.request.get('/api/v1/admin/effect-studies',{headers:admin})).ok()).toBeTruthy();
});
