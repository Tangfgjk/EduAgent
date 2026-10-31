import {test,expect,type Page} from '@playwright/test';

async function login(page:Page,role:'student'|'admin'){
 await page.goto('/'+role+'/');await page.getByLabel('账号',{exact:true}).fill(role==='admin'?'s03.admin':'s03.one');await page.getByLabel('密码',{exact:true}).fill('S03-test-only-password!');await page.getByRole('button',{name:'登录',exact:true}).click();await expect(page.locator('.app-shell')).toBeVisible();
 const cookies=await page.context().cookies();return {'X-Wenjin-Client':role,'X-CSRF-Token':cookies.find(c=>c.name===`wenjin_${role}_csrf`)!.value};
}

test('本地知识库：真实保存、中文检索、来源查看和手机布局',async({page})=>{
 const headers=await login(page,'student');const space=await(await page.request.post('/api/v1/student/workspaces',{headers:{...headers,'Idempotency-Key':crypto.randomUUID()},data:{kind:'project',title:'知识库验收'}})).json();
 await page.goto('/student/library?workspace='+space.workspace_id);
 await page.getByLabel('资料标题',{exact:true}).fill('混合运算笔记');await page.getByLabel('资料正文',{exact:true}).fill('先乘除后加减。资料里包含 $3+4$，只是原文，不执行脚本。');await page.getByRole('button',{name:'保存到当前项目'}).click();
 await expect(page.getByRole('status')).toHaveText('资料已保存到本机知识库。');
 await page.getByLabel('检索关键词').fill('乘除');await page.getByRole('button',{name:'本地检索',exact:true}).click();await page.getByRole('button',{name:'查看来源全文'}).click();await expect(page.locator('pre')).toContainText('先乘除后加减');
 await page.reload();await expect(page.getByRole('button',{name:/混合运算笔记/})).toBeVisible();
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2)).toBeTruthy();
});

test('三页分步课堂：从当前生成课程编排、公式渐显、互动核验与恢复',async({page},info)=>{
 test.setTimeout(120000);const admin=await login(page,'admin');
 const model=await(await page.request.get('/api/v1/admin/model-runtime',{headers:admin})).json();
 expect((await page.request.post('/api/v1/admin/model-runtime/publish',{headers:admin,data:{expected_revision:model.revision,vendor:'fake',max_output_tokens:512,account_daily_calls:0,site_daily_calls:0,prompt_version:'probability.followup.v3',acknowledge_development_only:true}})).ok()).toBeTruthy();
 const policy=await(await page.request.get('/api/v1/admin/generation-policy',{headers:admin})).json();
 expect((await page.request.post('/api/v1/admin/generation-policy',{headers:admin,data:{expected_revision:policy.revision,courses:true,interaction:true,search:false,private_auto_release:true,acknowledge_development_only:true}})).ok()).toBeTruthy();
 const student=await login(page,'student');const space=await(await page.request.post('/api/v1/student/workspaces',{headers:{...student,'Idempotency-Key':crypto.randomUUID()},data:{kind:'temporary',title:'分步课堂验收'}})).json();const wid=space.workspace_id;
 await page.goto('/student/courses?workspace='+wid);await page.getByLabel('我想学什么？').fill('负数乘除和运算顺序（分步课堂）');await page.getByRole('button',{name:'解析主题并准备诊断'}).click();await page.getByLabel('诊断 1 答案').fill('0');await page.getByLabel('诊断 2 答案').fill('0');await page.getByRole('button',{name:'保存诊断并查看课程范围'}).click();await page.getByRole('button',{name:'确认范围并生成课程'}).click();await expect(page.getByRole('button',{name:'确认使用这份课程'})).toBeVisible({timeout:40000});await page.getByRole('button',{name:'确认使用这份课程'}).click();
 await page.goto('/student/classroom?workspace='+wid);await page.getByRole('button',{name:'准备分步课堂',exact:true}).click();
 const player=page.getByRole('region',{name:'互动课堂播放器'});await expect(player).toBeVisible();await player.getByRole('button',{name:'下一页',exact:true}).click();await expect(player.locator('.katex').first()).toBeVisible();
 // Future operations must not be visible at the first reveal action.
 expect(await player.locator('foreignObject .katex').count()).toBe(1);
 await player.getByRole('button',{name:'下一步',exact:true}).click();await player.getByRole('button',{name:'下一步',exact:true}).click();await expect(player.locator('.classroom-caption')).toContainText('观察');
 await page.screenshot({path:info.outputPath('classroom-desktop.png'),fullPage:true});
 await player.getByRole('button',{name:'下一页',exact:true}).click();await player.getByLabel('课堂互动回答').fill('这个答案是0');await player.getByRole('button',{name:'检查本次回答'}).click();await expect(player.getByText('课堂数值核验：',{exact:false})).toBeVisible();
 await player.getByRole('button',{name:'保存进度',exact:true}).click();await expect(player.getByRole('status').first()).toContainText('进度已保存');await page.reload();await expect(page.getByLabel('课堂互动回答')).toBeVisible();
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2)).toBeTruthy();await page.screenshot({path:info.outputPath('classroom-mobile.png'),fullPage:true});
 const latestPolicy=await(await page.request.get('/api/v1/admin/generation-policy',{headers:admin})).json();
 expect((await page.request.post('/api/v1/admin/generation-policy',{headers:admin,data:{expected_revision:latestPolicy.revision,courses:true,interaction:true,search:false,private_auto_release:false,acknowledge_development_only:true}})).ok()).toBeTruthy();
});
