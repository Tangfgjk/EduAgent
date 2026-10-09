/* Focused study UX regression against a disposable learner and in-memory database. */
const assert=require('node:assert/strict');
const path=require('node:path');
const crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.EDU_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..');
const port=Number(process.env.EDU_QA_PORT||'8133');
const url='http://127.0.0.1:'+port;
const password=crypto.randomBytes(24).toString('hex');
const python=process.env.EDU_QA_PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function main(){
  assert(!await fetch(url).catch(()=>null),'QA port is already in use');
  const server=spawn(python,['-m','scripts.browser_fixture'],{cwd:root,env:{...process.env,EDU_QA_PORT:String(port),EDU_QA_PASSWORD:password},stdio:['ignore','pipe','pipe'],windowsHide:true});
  let logs='';server.stdout.on('data',data=>logs+=data);server.stderr.on('data',data=>logs+=data);
  let browser;
  try{
    for(let i=0;i<80;i++){if((await fetch(url+'/api/auth/status').catch(()=>null))?.ok)break;if(server.exitCode!==null)throw Error('Fixture exited: '+logs);await wait(100);if(i===79)throw Error('Fixture not ready: '+logs)}
    browser=await chromium.launch({headless:true,...(process.env.EDU_CHROME_PATH?{executablePath:process.env.EDU_CHROME_PATH}:{})});
    const page=await browser.newPage({viewport:{width:1360,height:900}});
    const pageErrors=[];page.on('pageerror',error=>pageErrors.push(error.message));
    await page.goto(url);await page.waitForURL('**/login');await page.locator('#password').fill(password);await page.locator('button[type=submit]').click();await page.waitForURL(url+'/');
    await page.waitForFunction(()=>document.querySelector('#identity').title==='e2e_validation');
    await page.locator('#tab-plan').click();await page.locator('#goal').fill('我想学习洛必达法则');await page.locator('#save-goal').click();await page.locator('#course-requests').filter({hasText:'洛必达法则'}).waitFor();
    await page.locator('#tab-study').click();
    const failures=[];
    const unsupportedStem=await page.locator('#task-stem').textContent();
    if(unsupportedStem.includes('方程'))failures.push(`Unsupported goal still shows equation task: ${unsupportedStem}`);
    await page.locator('#tab-plan').click();await page.locator('#goal').fill('解一元一次方程');await page.locator('#save-goal').click();await page.locator('#workspace-summary').filter({hasText:'已保存目标'}).waitFor();
    await page.locator('#tab-study').click();await page.locator('#target').selectOption('MATH.G7.EQ.SOLVE');await page.locator('#assessment').selectOption('D-SOLVE-1');
    await page.locator('#message').fill('3');await page.locator('#send').click();
    try{await page.locator('#verdict').filter({hasText:'验证通过'}).waitFor({timeout:1200})}catch{failures.push('Sending 3 did not verify the displayed 2x + 1 = 7 task')}
    await page.locator('.study-submit-options>summary').click();
    const overlap=await page.evaluate(()=>{const a=document.querySelector('#message').getBoundingClientRect(),b=document.querySelector('.study-submit-options>div').getBoundingClientRect();return a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top});
    if(overlap)failures.push('Answer options cover the text composer');
    await page.locator('.study-submit-options>summary').click();
    await page.locator('#diagnose').click();
    const assessmentId=await page.locator('#assessment').inputValue();
    const targetId=await page.locator('#target').inputValue();
    const assets=await (await page.request.get(url+'/api/learning/assets')).json();
    const assessment=assets.assessments.find(item=>item.ref.asset_id===assessmentId);
    if(!assessment?.kc_refs.some(item=>item.asset_id===targetId))failures.push(`Target ${targetId} mismatches selected assessment ${assessmentId}`);
    assert.deepEqual(failures,[]);
    // Walk the actual project and temporary routes against this disposable learner.
    await page.locator('#tab-plan').click();
    await page.locator('#project-title').fill('隔离试用项目');
    await page.locator('#project-description').fill('仅测试，不属于真实学习数据');
    await page.locator('#project-task-title').fill('完成一道方程练习');
    await page.locator('#add-project-task').click();
    await page.locator('#save-project').click();
    await page.locator('#sidebar-projects button').filter({hasText:'隔离试用项目'}).click();
    await page.locator('#active-project-todos select').selectOption('done');
    await page.locator('#status').filter({hasText:'待办状态已更新'}).waitFor();
    await page.locator('#tab-study').click();
    await page.locator('#assessment').selectOption('D-SOLVE-1');
    await page.locator('#message').fill('3');await page.locator('#send').click();
    await page.locator('#verdict').filter({hasText:'验证通过'}).waitFor();
    const projects=await (await page.request.get(url+'/api/learning/projects/e2e_validation')).json();
    const project=projects.projects.find(p=>p.content.title==='隔离试用项目');
    assert.equal(project.content.tasks[0].status,'done');
    assert(project.content.evidence_refs.length>0,'Project submission must attach evidence');
    await page.locator('#tab-evidence').click();
    await page.locator('#growth-source').filter({hasText:'项目局部证据'}).waitFor();
    await page.locator('#tab-reviews').click();await page.locator('#preview-reviews').click();
    await page.locator('#review-scope').filter({hasText:'预览截至'}).waitFor();
    await page.locator('#tab-collaboration').click();await page.locator('#refresh-collaboration-evidence').click();
    await page.locator('#collaboration-evidence option').first().waitFor({state:'attached'});
    await page.locator('#collaboration-evidence').selectOption({index:0});
    await page.locator('#collaboration-work').fill('两边先减一，再除以二。');
    await page.locator('#roundtable-review').click();
    await page.locator('#collaboration-status').filter({hasText:'圆桌候选建议已生成'}).waitFor();
    await page.locator('#tab-planner').click();await page.locator('#planner-feedback').fill('希望每次只安排一道题');
    await page.locator('#generate-harness').click();
    await page.locator('#harness-preview').filter({hasText:'希望每次只安排一道题'}).waitFor();
    await page.locator('#approve-harness').click();await page.locator('#status').filter({hasText:'候选已审核通过'}).waitFor();
    await page.locator('#rollback-harness').click();await page.locator('#status').filter({hasText:'候选已回滚'}).waitFor();
    const before=(await (await page.request.get(url+'/api/learning/evidence/e2e_validation')).json()).length;
    page.once('dialog',dialog=>dialog.accept('隔离临时试用'));
    await page.locator('#route-temporary').click();
    await page.locator('#active-context-label').filter({hasText:'临时会话'}).waitFor();
    assert.equal(await page.locator('#assessment-prompt').isVisible(),false);
    await page.locator('#message').fill('我想讨论如何安排复习');await page.locator('#send').click();
    await page.locator('#chat').filter({hasText:'临时讨论已记录'}).waitFor();
    const after=(await (await page.request.get(url+'/api/learning/evidence/e2e_validation')).json()).length;
    assert.equal(after,before,'Temporary messages must not create formal evidence');
    await page.locator('#close-temporary').click();
    await page.locator('#assessment-prompt').waitFor({state:'visible'});
    await page.locator('#tab-settings').click();
    assert.equal(await page.locator('#settings').isVisible(),true);
    assert.deepEqual(pageErrors,[],'No startup or interaction exceptions');
    await page.locator('#tab-study').click();
    await page.screenshot({path:path.join(root,'..','docs','图片','V5-试用修复后.png'),fullPage:true});
    process.stdout.write(JSON.stringify({status:'passed',checks:['unsupported-goal','send-verifies-task','options-do-not-cover-composer','diagnosis-target-match']})+'\n');
    process.stdout.write('Project creation, ToDo, scoped evidence, review preview, roundtable, harness review/rollback, temporary isolation and settings: passed\n');
  }finally{await browser?.close();server.kill()}
}
main().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1});
