/* Optional browser acceptance test. Requires a local fixture and Playwright. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.EDU_PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const url = process.env.EDU_QA_URL || 'http://127.0.0.1:8002';
  assert.equal(new URL(url).hostname, '127.0.0.1');
  const configuration = Object.fromEntries(fs.readFileSync('.env.local','utf8').split(/\r?\n/)
    .filter(line=>line.includes('=') && !line.startsWith('#')).map(line=>[line.slice(0,line.indexOf('=')),line.slice(line.indexOf('=')+1)]));
  const password = process.env.EDU_QA_PASSWORD || configuration.RSI_LOCAL_TEACHER_TOKEN;
  assert(password, 'Local fixture password required');
  const browser = await chromium.launch({headless:true,channel:process.env.EDU_CHROME_PATH?undefined:'chrome',executablePath:process.env.EDU_CHROME_PATH});
  const page = await browser.newPage({viewport:{width:1280,height:900}});
  const errors = [];
  page.on('pageerror',error=>errors.push(error.message));
  const outputs = path.resolve('data/browser-acceptance-20261006'); fs.mkdirSync(outputs,{recursive:true});
  try {
    await page.goto(url+'/');
    await page.waitForURL('**/login');
    await page.locator('#password').fill(password);
    await page.locator('button[type=submit]').click();
    await page.waitForURL(url+'/');
    await page.waitForFunction(()=>document.querySelector('#identity').textContent.includes('e2e_validation'));
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).backgroundColor),'rgb(13, 17, 23)');
    assert.equal(await page.getByRole('tab').count(),7);
    await page.screenshot({path:path.join(outputs,'desktop-study.png'),fullPage:true});
    await page.locator('#assessment').selectOption('D-SOLVE-1');
    await page.locator('#answer').fill('999'); await page.locator('#submit').click();
    await page.locator('#verdict').filter({hasText:'尚未通过'}).waitFor();
    await page.locator('#answer').fill('3'); await page.locator('#submit').click();
    await page.locator('#verdict').filter({hasText:'验证通过'}).waitFor();
    const request = async (route,data,headers={})=>{
      const response=data===undefined?await page.request.get(url+route,{headers}):await page.request.post(url+route,{data,headers});
      assert(response.ok(),route+' status '+response.status()); return response.json();
    };
    const evidence=await request('/api/learning/evidence/e2e_validation');
    assert.equal(evidence.length,2);
    await page.locator('#tab-plan').click();
    await page.locator('#goal').fill('合成验收：自主解释方程步骤');await page.locator('#save-goal').click();
    await page.locator('#workspace-summary').filter({hasText:'已保存目标'}).waitFor();
    await page.locator('#get-path').click();await page.locator('#draft-plan').click();
    await page.locator('#plan-detail').filter({hasText:'待学习者签署'}).waitFor();
    await page.reload(); await page.locator('#tab-plan').click();
    await page.locator('#plan-detail').filter({hasText:'待学习者签署'}).waitFor();
    await page.locator('#sign-plan').click();
    await page.locator('#plan-detail').filter({hasText:'已签署并生效'}).waitFor();
    await page.locator('#project-title').fill('合成浏览器验收项目');
    await page.locator('#project-task-title').fill('写出等价变形理由');await page.locator('#add-project-task').click();
    await page.locator('#save-project').click();await page.locator('#project-list').filter({hasText:'合成浏览器验收项目'}).waitFor();
    await page.locator('#tab-study').click();await page.locator('#assessment').selectOption('Q-EXPLAIN-SOLVE');
    await page.locator('#answer').fill('两边减去同一个数，再检查代回结果。');await page.locator('#submit').click();
    await page.locator('#verdict').filter({hasText:'待匹配 Rubric'}).waitFor();
    await page.locator('#new-session').click();
    await page.waitForFunction(()=>document.querySelector('#session-recovery-id').value.length>0);
    const sid=await page.locator('#session-recovery-id').inputValue();
    await page.locator('#help').click();
    await page.reload();
    await page.waitForFunction(()=>document.querySelector('#identity').textContent.includes('e2e_validation'));
    await page.locator('#session-recovery-id').fill(sid);await page.locator('#restore-session').click();
    await page.locator('#chat').filter({hasText:'已恢复会话'}).waitFor();
    const artifacts=await request('/api/learning/evidence/e2e_validation');
    const artifact=artifacts.find(e=>e.assessment_id==='Q-EXPLAIN-SOLVE'); assert(artifact);
    await page.locator('#tab-settings').click();
    await page.locator('#share-audience').selectOption('both');await page.locator('#save-sharing').click();
    await page.locator('#sharing-status').filter({hasText:'共享版本'}).waitFor();
    await page.locator('#dashboard-audience').selectOption('parent');
    await page.locator('#dashboard-token').fill(configuration.RSI_LOCAL_PARENT_TOKEN);await page.locator('#load-dashboard').click();
    await page.locator('#dashboard-result').filter({hasText:'家长看板'}).waitFor();
    const parent=await request('/api/learning/dashboard/e2e_validation?audience=parent',undefined,{'x-parent-token':configuration.RSI_LOCAL_PARENT_TOKEN});
    assert(!('evidence_refs' in parent));
    await page.locator('#correction-evidence').fill(artifact.evidence_id);
    await page.locator('#teacher-token').fill(password);await page.locator('#read-artifact').click();
    await page.locator('#artifact-result').filter({hasText:'两边减去'}).waitFor();
    await page.locator('#correction-reason').fill('合成验收人工核对：步骤与验算完整');
    for (const input of await page.locator('#rubric-dimensions input').all()) await input.fill('1');
    await page.locator('#teacher-token').fill(password);await page.locator('#qualitative-review').click();
    await page.locator('#recovery-result').filter({hasText:'待执行'}).waitFor();
    await page.locator('#retry-recovery').click();await page.locator('#recovery-result').filter({hasText:'已完成'}).waitFor();
    await page.locator('#tab-evidence').click();await page.locator('#rebuild-memory').click();
    await page.locator('#memory-result').filter({hasText:'事实来源'}).waitFor();
    await page.locator('#tab-collaboration').click();await page.locator('#knowledge-query').fill('等式 方程');
    await page.locator('#search-knowledge').click();await page.locator('#knowledge-results').filter({hasText:'查看原文'}).waitFor();
    await page.locator('#load-strategies').click();await page.locator('#strategy-results').filter({hasText:'策略'}).waitFor();
    await page.locator('#refresh-collaboration-evidence').click();
    await page.waitForFunction(()=>document.querySelector('#collaboration-evidence').options.length>0);
    const selected=await page.locator('#collaboration-evidence option').first().getAttribute('value');
    await page.locator('#collaboration-evidence').selectOption(selected);
    await page.locator('#collaboration-work').fill('我已经做了等价变形并代回检查。');
    await page.locator('#roundtable-review').click();await page.locator('#collaboration-results').filter({hasText:'未评估共识'}).waitFor();
    await page.locator('#teach-lesson').fill('要在等式两边同时减去同一个数，才能保持相等。');
    await page.locator('#teach-student').click();await page.locator('#teach-results').filter({hasText:'待验证候选'}).waitFor();
    await page.screenshot({path:path.join(outputs,'desktop-collaboration.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});await page.locator('#tab-study').click();
    await page.screenshot({path:path.join(outputs,'mobile-study.png'),fullPage:true});
    for(const tab of await page.getByRole('tab').all()) {
      await tab.click();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
    }
    await page.screenshot({path:path.join(outputs,'mobile-collaboration.png'),fullPage:true});
    await page.locator('#logout').click();await page.waitForURL('**/login');
    assert.equal((await page.request.get(url+'/api/learning/evidence/e2e_validation')).status(),401);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'passed',viewport_checks:[1280,390],workflows:15,source:'isolated_synthetic_fixture',screenshots:outputs}));
  } finally { await browser.close(); }
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
