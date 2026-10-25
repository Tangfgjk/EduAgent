import {test,expect,type Page} from '@playwright/test';

async function login(page:Page,role:'admin'|'student'){
 await page.goto('/'+role+'/');
 await page.getByLabel('账号',{exact:true}).fill(role==='admin'?'s03.admin':'s03.one');
 await page.getByLabel('密码',{exact:true}).fill('S03-test-only-password!');
 await page.getByRole('button',{name:'登录',exact:true}).click();
 await expect(page.locator('.app-shell')).toBeVisible();
 const cookies=await page.context().cookies();
 return {'X-Wenjin-Client':role,'X-CSRF-Token':cookies.find(c=>c.name===`wenjin_${role}_csrf`)!.value};
}

test('A版首次使用与成长分类：首屏主题、URL恢复、失败隔离和手机键盘',async({page},info)=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const headers=await login(page,'student');
 const response=await page.request.post('/api/v1/student/workspaces',{headers:{...headers,'Idempotency-Key':crypto.randomUUID()},data:{kind:'project',title:'A版首次使用 '+Date.now()}});
 expect(response.ok()).toBeTruthy();const wid=(await response.json()).workspace_id;
 await page.goto('/student/courses?workspace='+wid);
 await expect(page.locator('.breadcrumb')).toContainText('课程与安排');
 const box=await page.getByLabel('我想学什么？').boundingBox();expect(box&&box.y+box.height<1000).toBeTruthy();
 for(const label of ['津津陪学','圆桌讨论','我教 AI'])await expect(page.locator('.rail nav').getByRole('link',{name:label,exact:true})).toBeVisible();
 await page.screenshot({path:info.outputPath('a-first-use-desktop.png'),fullPage:true});
 await page.goto(`/student/evidence?workspace=${wid}&view=knowledge&panel=performance&days=90&scope=workspace`);
 await expect(page.getByRole('tab',{name:'知识与表现',exact:true})).toHaveAttribute('aria-selected','true');
 await expect(page.getByRole('heading',{name:'学习表现 · 按辅助和曝光分开'})).toBeVisible();
 await expect(page.getByRole('heading',{name:'支架时间线'})).toHaveCount(0);
 await page.getByRole('tab',{name:'复习与支持',exact:true}).click();
 await page.goBack();
 await expect(page.getByRole('heading',{name:'学习表现 · 按辅助和曝光分开'})).toBeVisible();
 await page.reload();await expect(page.getByRole('combobox',{name:'时间范围',exact:true})).toHaveValue('90');
 await page.route('**/learning-insights?*',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'test_panel_failure',message:'本面板暂不可读取',retryable:true}})}));
 await page.reload();await expect(page.getByRole('alert')).toContainText('本面板暂不可读取');
 await page.getByRole('tab',{name:'总览',exact:true}).click();
 await expect(page.locator('.growth-stats')).toBeVisible();await expect(page.getByRole('alert')).toHaveCount(0);
 await page.unroute('**/learning-insights?*');
 await page.setViewportSize({width:390,height:844});
 for(const label of ['总览','知识与表现','复习与支持','素养材料','学习记录'])await expect(page.getByRole('tab',{name:label,exact:true})).toBeInViewport();
 await page.getByRole('tab',{name:'素养材料',exact:true}).click();
 await expect(page.getByRole('group',{name:'六个观察方向候选材料条数'})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
 await page.getByRole('button',{name:'打开导航',exact:true}).click();
 await expect(page.locator('.rail nav a').first()).toBeFocused();
 await page.keyboard.press('Escape');
 await expect(page.getByRole('button',{name:'打开导航',exact:true})).toBeFocused();
 await page.screenshot({path:info.outputPath('a-growth-mobile.png'),fullPage:true});
 expect(errors).toEqual([]);
});

test('A版切换前恢复或明确结束任务，保留原记录与独立检测边界',async({page})=>{
 const admin=await login(page,'admin');
 const catalog=await(await page.request.get('/api/v1/admin/content-catalog',{headers:admin})).json();
 const pack=catalog.items.find((r:{unit_id:string})=>r.unit_id==='math.g7.equations.dev1');
 expect((await page.request.post('/api/v1/admin/content-catalog/'+pack.unit_id+'/development-release',{headers:admin,data:{content_version:pack.content_version,expected_revision:pack.revision,enabled:true,acknowledge_development_only:true}})).ok()).toBeTruthy();
 const student=await login(page,'student');
 const space=await(await page.request.post('/api/v1/student/workspaces',{headers:{...student,'Idempotency-Key':crypto.randomUUID()},data:{kind:'temporary',title:'A版活动恢复 '+Date.now()}})).json();
 const wid=space.workspace_id;
 expect((await page.request.post(`/api/v1/student/workspaces/${wid}/course`,{headers:student,data:{expected_revision:0,topic:'方程与等式',title:'方程练习',unit_ids:[pack.unit_id],active_unit_id:pack.unit_id}})).ok()).toBeTruthy();
 await page.goto('/student/study?workspace='+wid);
 await page.getByRole('button',{name:'开始开发单元',exact:true}).click();
 await expect(page.getByRole('heading',{name:'先试一题',exact:true})).toBeVisible();
 await page.goto('/student/roundtable?workspace='+wid);
 await expect(page.getByRole('region',{name:'先处理正在进行的活动'})).toBeVisible();
 await expect(page.getByRole('button',{name:'开始圆桌讨论',exact:true})).toHaveCount(0);
 await page.getByRole('link',{name:'回到原任务继续',exact:true}).click();
 await expect(page.getByRole('heading',{name:'先试一题',exact:true})).toBeVisible();
 await page.goto('/student/teach?workspace='+wid);
 await page.getByRole('button',{name:'结束原任务后切换',exact:true}).click();
 await page.getByRole('button',{name:'确认结束并切换',exact:true}).click();
 await expect(page.getByRole('dialog')).toHaveCount(0);
 await expect(page.getByRole('heading',{name:'我教 AI',exact:true})).toBeVisible();
 const runs=await(await page.request.get(`/api/v1/student/workspaces/${wid}/tutoring`,{headers:student})).json();
 expect(runs.items[0].status).toBe('cancelled');
 const report=await(await page.request.get(`/api/v1/student/workspaces/${wid}/evidence`,{headers:student})).json();
 expect(report.record_count).toBe(0); // Cancel does not fabricate assessment success.
});
