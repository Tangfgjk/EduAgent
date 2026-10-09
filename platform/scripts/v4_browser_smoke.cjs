/* Disposable browser acceptance: never loads .env.local or the personal DB. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const crypto=require('node:crypto');
const {chromium}=require(process.env.EDU_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..');
const port=Number(process.env.EDU_QA_PORT||'8132');
const url='http://127.0.0.1:'+port;
const password=crypto.randomBytes(24).toString('hex');
const python=process.env.EDU_QA_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const output=path.join(root,'data','v4-browser-acceptance');
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function capture(page,name){
  await page.evaluate(()=>window.scrollTo(0,0));await wait(100);
  assert.equal(await page.locator('.topbar').evaluate(node=>Math.round(node.getBoundingClientRect().top)),0);
  await page.screenshot({path:path.join(output,name),fullPage:true});
}
async function main(){
  const probe=await fetch(url).catch(()=>null);assert(!probe,'QA port already serves an instance; choose unused EDU_QA_PORT');
  const server=spawn(python,['-m','scripts.browser_fixture'],{cwd:root,env:{...process.env,EDU_QA_PASSWORD:password,EDU_QA_PORT:String(port)},stdio:['ignore','pipe','pipe'],windowsHide:true});
  let logs='';server.stdout.on('data',data=>logs+=data);server.stderr.on('data',data=>logs+=data);
  let browser;
  try{
    for(let attempt=0;attempt<80;attempt++){const response=await fetch(url+'/api/auth/status').catch(()=>null);if(response?.ok)break;if(server.exitCode!==null)throw Error('Fixture exited: '+logs);await wait(250);if(attempt===79)throw Error('Fixture not ready: '+logs);}
    browser=await chromium.launch({headless:true,...(process.env.EDU_CHROME_PATH?{executablePath:process.env.EDU_CHROME_PATH}:process.env.EDU_BROWSER_CHANNEL?{channel:process.env.EDU_BROWSER_CHANNEL}:{})});
    fs.mkdirSync(output,{recursive:true});
    const context=await browser.newContext({viewport:{width:1440,height:960}}),page=await context.newPage(),errors=[];
    context.on('page',p=>p.on('pageerror',error=>errors.push(error.message)));page.on('pageerror',error=>errors.push(error.message));
    await page.goto(url);await page.waitForURL('**/login');await page.locator('#password').fill(password);await page.locator('button[type=submit]').click();await page.waitForURL(url+'/');
    await page.waitForFunction(()=>document.querySelector('#identity').title==='e2e_validation');
    assert.equal(await page.getByRole('tab').count(),7);assert.equal(await page.locator('#staff-token').count(),0);
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).backgroundColor),'rgb(246, 247, 244)');
    await page.locator('#tab-evidence').click();await page.locator('#growth-source').filter({hasText:'有效证据 0 条'}).waitFor();assert.equal(await page.locator('#growth-charts svg').count(),0);
    await page.locator('#tab-study').click();await page.locator('#assessment').selectOption('D-SOLVE-1');
    await page.locator('#message').fill('999');await page.locator('#submit').click();await page.locator('#verdict').filter({hasText:'尚未通过'}).waitFor();
    await page.locator('#message').fill('3');await page.locator('#submit').click();await page.locator('#verdict').filter({hasText:'验证通过'}).waitFor();
    await page.locator('#tab-plan').click();await page.locator('#goal').fill('我想学习洛必达法则');await page.locator('#save-goal').click();await page.locator('#course-requests').filter({hasText:'洛必达法则'}).waitFor();assert.equal(await page.locator('#path .path-node').count(),0);
    await page.locator('#goal').fill('自主解释方程步骤');await page.locator('#save-goal').click();await page.locator('#workspace-summary').filter({hasText:'已保存目标'}).waitFor();
    await page.locator('#get-path').click();await page.locator('.path-node').first().waitFor();await page.locator('#draft-plan').click();await page.locator('#plan-detail').filter({hasText:'待学习者签署'}).waitFor();
    await page.reload();await page.locator('#tab-plan').click();await page.locator('#plan-detail').filter({hasText:'待学习者签署'}).waitFor();await page.locator('#sign-plan').click();await page.locator('#plan-detail').filter({hasText:'已签署并生效'}).waitFor();
    await page.locator('#project-title').fill('自主学习项目');await page.locator('#project-task-title').fill('写出等价变形理由');await page.locator('#add-project-task').click();await page.locator('#save-project').click();await page.locator('#sidebar-projects').filter({hasText:'自主学习项目'}).waitFor();
    await capture(page,'desktop-plan.png');
    await page.locator('#tab-study').click();await page.locator('#assessment').selectOption('Q-EXPLAIN-SOLVE');await page.locator('#message').fill('两边减去同一个数，再检查代回结果。');await page.locator('#submit').click();await page.locator('#verdict').filter({hasText:'待匹配 Rubric'}).waitFor();
    await page.locator('#help').click();await page.locator('#chat').filter({hasText:'当前题目提示'}).waitFor();
    await page.locator('#message').fill('请陪我分析另一个方程问题');await page.locator('#send').click();await page.waitForFunction(()=>document.querySelector('#session-recovery-id').value.length>0);const sid=await page.locator('#session-recovery-id').inputValue();await page.locator('#chat').filter({hasText:'自由陪学探究'}).waitFor();
    await page.reload();await page.waitForFunction(()=>document.querySelector('#identity').title==='e2e_validation');await page.locator('#session-recovery-id').evaluate(node=>{node.closest('details').open=true;node.closest('.study-options').open=true});await page.locator('#session-recovery-id').fill(sid);await page.locator('#restore-session').click();await page.locator('#chat').filter({hasText:'已恢复自由陪学会话'}).waitFor();
    await capture(page,'desktop-study.png');
    const request=async(route,data,headers={})=>{const response=data===undefined?await page.request.get(url+route,{headers}):await page.request.post(url+route,{data,headers});assert(response.ok(),route+' status '+response.status());return response.json();};
    const evidence=await request('/api/learning/evidence/e2e_validation'),artifact=evidence.find(e=>e.assessment_id==='Q-EXPLAIN-SOLVE');assert(artifact);
    await page.locator('#tab-settings').click();await page.locator('#share-audience').selectOption('both');await page.locator('#save-sharing').click();await page.locator('#sharing-status').filter({hasText:'共享版本'}).waitFor();
    const parent=await context.newPage();await parent.goto(url+'/parent');await parent.locator('#staff-token').fill(password);await parent.locator('#load-dashboard').click();await parent.locator('#dashboard-result').filter({hasText:'家长看板'}).waitFor();assert.equal(await parent.locator('#staff-token').inputValue(),'');assert.equal(await parent.locator('#correction-evidence').count(),0);
    const parentData=await request('/api/learning/dashboard/e2e_validation?audience=parent',undefined,{'x-parent-token':password});assert(!('evidence_refs' in parentData));
    const teacher=await context.newPage();await teacher.goto(url+'/teacher');await teacher.locator('#correction-evidence').fill(artifact.evidence_id);await teacher.locator('#staff-token').fill(password);await teacher.locator('#read-artifact').click();await teacher.locator('#artifact-result').filter({hasText:'两边减去'}).waitFor();await teacher.locator('#correction-reason').fill('人工核对步骤与验算完整');for(const input of await teacher.locator('#rubric-dimensions input').all())await input.fill('1');await teacher.locator('#staff-token').fill(password);await teacher.locator('#qualitative-review').click();await teacher.locator('#correction-result').filter({hasText:'修正已追加'}).waitFor();assert.equal(await teacher.locator('#staff-token').inputValue(),'');
    const research=await context.newPage();await research.goto(url+'/research');await research.locator('#refresh-recovery').click();await research.locator('#recovery-result button').first().click();await research.locator('#recovery-detail').filter({hasText:'已完成重建'}).waitFor();
    await research.route('**/api/learning/recovery/e2e_validation',route=>route.fulfill({json:{jobs:[{job_id:'qa-failed',status:'failed',correction_id:'synthetic-failure',attempts:1}]}}));
    await research.route('**/api/learning/recovery/e2e_validation/qa-failed/run',route=>route.fulfill({json:{status:'failed',job_id:'qa-failed'}}));
    await research.locator('#refresh-recovery').click();await research.locator('#recovery-result button').first().click();await research.locator('#recovery-detail').filter({hasText:'恢复状态：failed'}).waitFor();assert(!(await research.locator('#recovery-detail').textContent()).includes('已完成重建'));
    await page.locator('#tab-evidence').click();await page.locator('#mastery-chart svg').waitFor();await page.locator('#rebuild-memory').click();await page.locator('#memory-result').filter({hasText:'事实来源'}).waitFor();await capture(page,'desktop-growth.png');
    await page.locator('#tab-collaboration').click();await page.locator('#knowledge-query').fill('等式 方程');await page.locator('#search-knowledge').click();await page.locator('#knowledge-results').filter({hasText:'查看原文'}).waitFor();await page.locator('#load-strategies').click();await page.locator('#strategy-results').filter({hasText:'策略'}).waitFor();await page.locator('#refresh-collaboration-evidence').click();await page.waitForFunction(()=>document.querySelector('#collaboration-evidence').options.length>0);await page.locator('#collaboration-evidence').selectOption(await page.locator('#collaboration-evidence option').first().getAttribute('value'));await page.locator('#collaboration-work').fill('我已经做了等价变形并代回检查。');await page.locator('#roundtable-review').click();await page.locator('#collaboration-results').filter({hasText:'未评估共识'}).waitFor();await page.locator('#teach-lesson').fill('在等式两边同时减去同一个数，保持相等。');await page.locator('#teach-student').click();await page.locator('#teach-results').filter({hasText:'待验证候选'}).waitFor();await capture(page,'desktop-roundtable.png');
    for(const width of [1440,1301,1101,900,390]){await page.setViewportSize({width,height:844});await page.evaluate(()=>window.scrollTo(0,0));assert(await page.evaluate(()=>{const nav=document.querySelector('nav[role=tablist]').getBoundingClientRect(),actions=document.querySelector('.top-actions').getBoundingClientRect();return nav.right<=actions.left||nav.bottom<=actions.top||nav.top>=actions.bottom;}),'Header controls overlap at '+width);}
    await page.locator('#tab-study').click();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);await capture(page,'mobile-study.png');
    for(const tab of await page.getByRole('tab').all()){await tab.click();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);}await capture(page,'mobile.png');
    await page.locator('#tab-settings').click();await page.locator('#withdraw').click();await page.locator('#consent-status').filter({hasText:'未授权'}).waitFor();assert.equal(await page.locator('#mastery-chart svg').count(),0);assert.equal((await page.request.get(url+'/api/learning/growth/e2e_validation')).status(),403);await parent.waitForFunction(()=>document.querySelector('#dashboard-result').textContent==='');await teacher.waitForFunction(()=>document.querySelector('#artifact-result').textContent==='');await parent.locator('#staff-token').fill(password);await parent.locator('#load-dashboard').click();await parent.locator('#status.error').waitFor();assert.equal(await parent.locator('#dashboard-result').textContent(),'');
    await page.locator('#teaching-consent').check();await page.locator('#authorize').click();await page.locator('#consent-status').filter({hasText:'已授权'}).waitFor();await page.locator('#logout').click();await page.waitForURL('**/login');assert.equal((await page.request.get(url+'/api/learning/evidence/e2e_validation')).status(),401);assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'passed',source:'isolated_synthetic_in_memory',workflows:21,viewports:[1440,390],page_errors:errors,screenshots:output}));
  }finally{if(browser)await browser.close();server.kill();await wait(500);}
}
main().catch(error=>{console.error(error.stack);process.exitCode=1;});
