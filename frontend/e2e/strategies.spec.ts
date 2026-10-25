import {navigateStudent} from './navigation-helper';
import { confirmFixtureCourse } from './course-helper';
import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function login(page: Page, role: 'student' | 'admin', name: string) {
  await page.goto(`/${role}/`);
  await page.getByLabel('账号', { exact: true }).fill(name);
  await page.getByLabel('密码', { exact: true }).fill('S03-test-only-password!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
}
async function navigate(page: Page, name: string) { await navigateStudent(page,name); }
async function create(page: Page, title: string, temporary = false) {
  await navigate(page, '项目与会话');
  await page.getByRole('button', { name: temporary ? '新建临时会话' : '新建学习项目', exact: true }).click();
  await page.getByLabel('空间名称').fill(title);
  await page.getByRole('button', { name: '保存空间', exact: true }).click();
  await expect(page.locator('.context-bar')).toContainText(title);
}
async function prepare(admin: Page, title: string) {
  await login(admin, 'admin', 's03.admin');
  await navigate(admin, '策略评审');
  await admin.getByRole('tab', { name: '课程资料', exact: true }).click();
  await admin.getByRole('button', { name: '录入课程资料', exact: true }).click();
  await admin.getByLabel('资料标题', { exact: true }).fill(title);
  await admin.getByLabel('资料版本', { exact: true }).fill('dev1');
  await admin.getByLabel('资料来源', { exact: true }).fill('浏览器验收自编参考，非课标与教师金标');
  await admin.getByLabel('参考正文', { exact: true }).fill('方程变形关注等式两边相同运算；完成后将结果代入原条件核验。');
  await admin.getByRole('button', { name: '保存资料版本', exact: true }).click();
  const card = admin.locator('.strategy-material-card').filter({ has: admin.getByRole('heading', { name: title, exact: true }) });
  await card.getByRole('checkbox').check();
  await card.getByRole('button', { name: '发布资料', exact: true }).click();
  await expect(card.getByText('资料已发布', { exact: true })).toBeVisible();
}
// Explicitly exercise historical review-required API mode. r14.spec covers default self-service.
async function propose(page: Page, materialTitle: string, model = false, execution = false) {
  await navigate(page, '规划与策略');
  await page.getByLabel('我的学习体验', { exact: false }).fill('我希望先核验数值，再反思为什么这样变形。');
  await page.getByLabel('每日建议时长', { exact: true }).fill('20');
  await page.getByRole('combobox', { name: '反思引导重点', exact: true }).selectOption('verification');
  await page.getByRole('checkbox', { name: new RegExp(materialTitle) }).check();
  if (execution) {
    await page.getByRole('checkbox',{name:'调整执行策略（系统检查与本人确认）'}).check();
    await page.getByLabel('练习顺序',{exact:true}).selectOption('errors_first');
    await page.getByLabel('复习列表排序',{exact:true}).selectOption('due_errors_first');
    await page.getByLabel('生成课程支持方式',{exact:true}).selectOption('step_first');
    await page.getByLabel('通用追问重点',{exact:true}).selectOption('verification');
  }
  await page.getByLabel('使用已开放模型生成有来源的候选').setChecked(model);
  await page.route('**/api/v1/student/workspaces/*/strategies',async route=>{
    if(route.request().method()==='POST')await route.continue({postData:JSON.stringify({...route.request().postDataJSON(),self_service:false})});
    else await route.continue();
  });
  await page.getByRole('button', { name: '生成开发策略提案', exact: true }).click();
  await expect(page.locator('.strategy-detail').getByText('等待管理员审核', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading',{name:'冻结的学情观察摘要',exact:true})).toBeVisible();
}
async function review(admin: Page, project: string, approve: boolean) {
  await admin.getByRole('tab', { name: '提案评审', exact: true }).click();
  const card = admin.locator('section.card').filter({ has: admin.getByRole('heading', { name: project, exact: true }) });
  await card.getByRole('button', { name: '查看提案与依据', exact: true }).click();
  await admin.getByLabel('评审依据', { exact: true }).fill(approve ? '核对来源、白名单和边界后，交由学生决定。' : '资料范围仍需补充，本次先退回。');
  await admin.getByRole('button', { name: approve ? '审核通过，交学生确认' : '退回提案', exact: true }).click();
  await expect(admin.locator('.strategy-detail').getByText(approve ? '等待学生确认' : '管理员已退回', { exact: true })).toBeVisible();
}

test('策略闭环：资料发布、管理员审核、学生确认、后续陪学生效与回滚', async ({ page, browser }) => {
  test.setTimeout(90_000);
  const admin = await browser.newPage(), stamp = Date.now(), materialTitle = '策略参考 ' + stamp, project = '策略项目 ' + stamp;
  try {
    await prepare(admin, materialTitle);
    await navigate(admin, '知识点与题库');
    await expect(admin.getByRole('heading', { name: '一元一次方程与应用 · 开发单元', exact: true })).toBeVisible();
    if (await admin.getByRole('button', { name: '启用开发测试', exact: true }).count()) {
      await admin.getByRole('checkbox').check();
      await admin.getByRole('button', { name: '启用开发测试', exact: true }).click();
    }
    await expect(admin.getByText('开发测试已启用', { exact: true })).toBeVisible();
    await navigate(admin, '策略评审');
    await login(page, 'student', 's03.one');
    await create(page, project);
    await propose(page, materialTitle,false,true);
    await expect(page.getByRole('heading',{name:'本次学习方式调整',exact:true})).toBeVisible();
    await page.reload();
    await expect(page.locator('.strategy-detail').getByText('等待管理员审核', { exact: true })).toBeVisible();
    await review(admin, project, true);
    await expect(page.getByRole('button', { name: '我确认，激活此策略', exact: true })).toBeVisible();
    await expect(page.getByText('平台默认', { exact: true })).toBeVisible();
    await page.getByLabel('我的确认或拒绝理由', { exact: true }).fill('这个节奏合适，由我确认采用。');
    await page.getByRole('button', { name: '我确认，激活此策略', exact: true }).click();
    await expect(page.locator('.strategy-detail').getByText('已确认并激活', { exact: true })).toBeVisible();
    await expect(page.getByText('已激活', { exact: true })).toBeVisible();
    await expect(page.getByText('查看 Skill.md 与 Harness 候选', { exact: true })).toHaveCount(0);
    await expect(page.getByText('查看编译配置及消费者',{exact:true})).toHaveCount(0);
    await expect(page.getByText('查看完整版本与引用明细',{exact:true})).toHaveCount(0);
    mkdirSync('test-results/screens', { recursive: true });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: 'test-results/screens/student-strategy.png', fullPage: true });
    await navigate(page, '学习计划');
    await page.getByRole('button', { name: '采用当前策略建议（20 分钟）', exact: true }).click();
    await expect(page.getByLabel('每天愿意投入几分钟？')).toHaveValue('20');
    await page.getByLabel('这次想学会什么？').fill('核对方程的等式性质与代入检验');
    await page.getByRole('button', { name: '生成计划草案', exact: true }).click();
    await page.getByRole('button', { name: '我确认这份计划', exact: true }).click();
    await expect(page.getByText('计划已确认', { exact: true })).toBeVisible();
    await navigate(page, '津津陪学');
    await confirmFixtureCourse(page);
    await page.getByRole('button', { name: '开始开发单元', exact: true }).click();
    await expect(page.getByText('本任务冻结的执行策略',{exact:true})).toHaveCount(0);
    await page.getByLabel('我的回答', { exact: true }).fill('3');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await page.getByRole('button', { name: '继续下一步', exact: true }).click();
    await expect(page.locator('.chat-current[data-task-id=\"P-EQ-01\"]')).toBeVisible();
    await page.getByLabel('我的回答', { exact: true }).fill('6');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await expect(page.getByLabel('我的反思', { exact: true })).toBeVisible();
    await navigate(page, '规划与策略');
    await page.getByText('查看激活历史与回滚', { exact: true }).click();
    await page.getByLabel('回滚理由', { exact: true }).fill('先回到默认引导，已有陪学保留原策略。');
    await page.getByRole('button', { name: '确认回滚', exact: true }).click();
    await expect(page.getByText('平台默认', { exact: true })).toBeVisible();
    await navigate(page, '津津陪学');
    await expect(page.getByLabel('我的反思', { exact: true })).toBeVisible();
  } finally { await admin.close(); }
});

test('手机规划：临时空间隔离、退回提案、缺失量规与来源不伪造', async ({ page, browser }) => {
  test.setTimeout(60_000);
  const admin = await browser.newPage(), stamp = Date.now(), materialTitle = '手机策略参考 ' + stamp, project = '手机策略项目 ' + stamp;
  try {
    await prepare(admin, materialTitle);
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page, 'student', 's03.one');
    await create(page, '临时策略隔离 ' + stamp, true);
    await navigate(page, '规划与策略');
    await expect(page.getByText('策略规划从正式项目开始', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '生成开发策略提案', exact: true })).toHaveCount(0);
    await create(page, project);
    await propose(page, materialTitle);
    await review(admin, project, false);
    await expect(page.locator('.strategy-detail').getByText('管理员已退回', { exact: true })).toBeVisible();
    await expect(page.getByText('平台默认', { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.evaluate(() => window.scrollTo(0, 0));
    mkdirSync('test-results/screens', { recursive: true });
    await page.screenshot({ path: 'test-results/screens/student-strategy-mobile.png', fullPage: true });
  } finally { await admin.close(); }
});


test('模型规划：来源引用、全局计数、审核后学生确认及回滚', async ({ page, browser }, testInfo) => {
  test.setTimeout(60_000);
  const admin = await browser.newPage(), stamp = Date.now(), title = '模型来源 ' + stamp, project = '模型规划 ' + stamp;
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  try {
    await prepare(admin, title);
    const cookies = await admin.context().cookies();
    const headers = { 'X-Wenjin-Client': 'admin', 'X-CSRF-Token': cookies.find(c => c.name === 'wenjin_admin_csrf')!.value };
    const config = await (await admin.request.get('/api/v1/admin/model-runtime', { headers })).json();
    expect((await admin.request.post('/api/v1/admin/model-runtime/publish', { headers, data: { expected_revision: config.revision,
      vendor: 'fake', prompt_version: 'probability.followup.v3', max_output_tokens: 512, account_daily_calls: 0,
      site_daily_calls: 0, acknowledge_development_only: true } })).ok()).toBeTruthy();
    await login(page, 'student', 's03.two');
    await create(page, project);
    await propose(page, title, true);
    await expect(page.getByText('模型建议与来源引用', { exact: true })).toBeVisible();
    await expect(page.getByText('全局宏观计数（包含当前项目）', { exact: true })).toBeVisible();
    await expect(page.getByText('Fake 规划 · 仅工程验证', { exact: true })).toHaveCount(0);
    await page.reload();
    await expect(page.getByText('模型建议与来源引用', { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('planning-sources.png'), fullPage: true });
    await review(admin, project, true);
    await expect(page.getByText('平台默认', { exact: true })).toBeVisible();
    await page.getByLabel('我的确认或拒绝理由', { exact: true }).fill('阅读具体引用后，由我确认这次软策略建议。');
    await page.getByRole('button', { name: '我确认，激活此策略', exact: true }).click();
    await expect(page.getByText('已激活', { exact: true })).toBeVisible();
    await page.getByText('查看激活历史与回滚', { exact: true }).click();
    await page.getByLabel('回滚理由', { exact: true }).fill('测试完成，恢复默认建议。');
    await page.getByRole('button', { name: '确认回滚', exact: true }).click();
    await expect(page.getByText('平台默认', { exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    expect(errors).toEqual([]);
  } finally { await admin.close(); }
});


test('手机执行策略：四项可选择、说明可读且无横向溢出',async({page,browser})=>{
  await page.setViewportSize({width:390,height:844});
  const admin=await browser.newPage(),title='手机执行参考 '+Date.now();
  try{await prepare(admin,title);}finally{await admin.close();}
  await login(page,'student','s03.two');
  await create(page,'手机执行项目 '+Date.now());
  await navigate(page,'规划与策略');
  await page.getByRole('checkbox',{name:'调整执行策略（系统检查与本人确认）'}).check();
  await page.getByLabel('练习顺序',{exact:true}).selectOption('errors_first');
  await page.getByLabel('复习列表排序',{exact:true}).selectOption('due_errors_first');
  await page.getByLabel('生成课程支持方式',{exact:true}).selectOption('step_first');
  await page.getByLabel('通用追问重点',{exact:true}).selectOption('verification');
  await expect(page.getByLabel('通用追问重点',{exact:true})).toHaveValue('verification');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.getByRole('checkbox',{name:'调整执行策略（系统检查与本人确认）'}).scrollIntoViewIfNeeded();
  mkdirSync('test-results/screens',{recursive:true});
  await page.screenshot({path:'test-results/screens/student-execution-mobile.png'});
});
