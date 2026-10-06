const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.EDU_PLAYWRIGHT_MODULE || 'playwright');

async function main() {
  const url = process.env.EDU_QA_URL || 'http://127.0.0.1:8001';
  assert.equal(new URL(url).hostname, '127.0.0.1');
  const output = path.resolve(__dirname,'../data/simulation-browser-20261006');
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true,
    ...(process.env.EDU_CHROME_PATH ? {executablePath:process.env.EDU_CHROME_PATH} :
      process.env.EDU_BROWSER_CHANNEL ? {channel:process.env.EDU_BROWSER_CHANNEL} : {})});
  const errors = [];
  try {
    for (const [name, viewport] of [['desktop',{width:1280,height:900}],['mobile',{width:390,height:844}]]) {
      const page = await browser.newPage({viewport});
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(url);
      await page.waitForFunction(() => !document.querySelector('#start').disabled);
      assert.equal(await page.locator('#profile option').count(),6);
      assert.equal(await page.locator('#scenario option').count(),5);
      assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).backgroundColor),'rgb(13, 17, 23)');
      await page.locator('#profile').selectOption('autonomous');
      await page.locator('#turns').fill('5');
      await page.locator('#seed').fill('11');
      const previous = await page.locator('#run-id').textContent();
      await page.locator('#start').click();
      await page.waitForFunction(previous=>document.querySelector('#run-id').textContent!==previous,previous);
      await page.waitForFunction(()=>document.querySelector('#state').textContent==='已完成',{},{timeout:20000});
      await page.waitForFunction(()=>!document.querySelector('#download').disabled,{},{timeout:20000});
      const report = JSON.parse(await page.locator('#report').textContent());
      assert.equal(report.status,'completed');
      assert.equal(report.educational_effect_claim,false);
      assert.equal(report.automatic_promotion_enabled,false);
      assert.equal(report.errors.length,0);
      assert.equal(await page.locator('#turn-count').textContent(),'5');
      assert((await page.locator('#transcript').textContent()).includes('ANSWER'));
      await page.waitForFunction(()=>document.querySelector('#mastery-chart svg'));
      assert.equal(await page.locator('.charts .card').count(),4);
      assert((await page.locator('#analysis-note').textContent()).includes('synthetic_ai_generated'));
      assert.equal(await page.locator('#hint-chart svg').count(),1);
      const events=(await page.locator('#transcript').textContent()).split('\n').map(line=>JSON.parse(line));
      const analysis=require('../web/simulation-assets/analytics.js').analyze(events,report);
      assert.equal(analysis.independentCount,report.independent_attempts);
      assert.equal(analysis.assistedCount,report.assisted_attempts);
      assert.equal(await page.locator('#kc-values tr').count(),Object.keys(report.per_kc).length);
      const response=await page.request.get(url+'/simulation-assets/lab.js');
      assert(response.ok());assert(!response.headers()['content-security-policy'].includes('unsafe-inline'));
      // A second run exercises real report comparison, not precomputed improvements.
      await page.locator('#scenario').selectOption('zero_gain');
      const priorRun=await page.locator('#run-id').textContent();
      await page.locator('#start').click();
      await page.waitForFunction(prior=>document.querySelector('#run-id').textContent!==prior,priorRun);
      await page.waitForFunction(()=>document.querySelector('#state').textContent==='已完成',{},{timeout:20000});
      await page.waitForFunction(()=>!document.querySelector('#download').disabled,{},{timeout:20000});
      const reference=await page.locator('#compare-run option').nth(1).getAttribute('value');
      await page.locator('#compare-run').selectOption(reference);
      await page.locator('#comparison table').waitFor();
      assert((await page.locator('#comparison').textContent()).includes('独立通过率'));
      await page.locator('#report').evaluate(node=>{node.closest('details').open=true;});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      const overlaps = await page.evaluate(()=>Array.from(document.querySelectorAll('button, input, select, h1, h2')).filter(e=> {
        const r=e.getBoundingClientRect(); return r.width>0 && (r.left<0 || r.right>innerWidth+1);
      }).map(e=>e.id||e.textContent));
      assert.deepEqual(overlaps,[]);
      await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
      await page.close();
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({desktop:true,mobile:true,errors,output}));
  } finally { await browser.close(); }
}
main().catch(e=>{console.error(e);process.exitCode=1});
