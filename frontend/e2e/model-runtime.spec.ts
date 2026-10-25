import { confirmFixtureCourse } from './course-helper';
import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function login(page: Page, role: 'student' | 'admin', username: string) {
  await page.goto(`/${role}/`);
  await page.getByLabel('账号', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill('S03-test-only-password!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
}

async function headers(page: Page, role: 'student' | 'admin') {
  const cookies = await page.context().cookies();
  return { 'X-Wenjin-Client': role, 'X-CSRF-Token': cookies.find(c => c.name === `wenjin_${role}_csrf`)!.value };
}

test('管理端发布v3候选模拟模型，学生概率思路追问可刷新恢复，并保留独立检测门禁', async ({ page, browser }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await login(page, 'admin', 's03.admin');
  const adminHeaders = await headers(page, 'admin');
  const unitId = 'math.g9.probability.dev1';
  const catalog = await (await page.request.get('/api/v1/admin/content-catalog', { headers: adminHeaders })).json();
  const unit = catalog.items.find((item: { unit_id: string }) => item.unit_id === unitId);
  expect((await page.request.post(`/api/v1/admin/content-catalog/${unitId}/development-release`, { headers: adminHeaders,
    data: { content_version: unit.content_version, expected_revision: unit.revision, enabled: true, acknowledge_development_only: true } })).ok()).toBeTruthy();
  await page.getByRole('link', { name: '配置与权限', exact: true }).click();
  await page.getByLabel('开发服务').selectOption('fake');
  await page.getByLabel('追问模式', { exact: false }).selectOption('probability.followup.v3');
  await page.getByLabel('确认仅用于开发测试', { exact: false }).check();
  await page.getByRole('button', { name: '发布开发模型配置', exact: true }).click();
  await expect(page.getByText('开发模型配置已发布，按已开放用途运行；新服务商需独立验收。')).toBeVisible();
  await expect(page.getByText('每账号 不限次数，全站 不限次数，输出上限 512 tokens', { exact: true })).toBeVisible();
  const context = await browser.newContext();
  const student = await context.newPage();
  student.on('pageerror', e => errors.push(e.message));
  try {
    await login(student, 'student', 's03.two');
    const studentHeaders = await headers(student, 'student');
    const response = await student.request.post('/api/v1/student/workspaces', { headers: { ...studentHeaders, 'Idempotency-Key': crypto.randomUUID() }, data: { kind: 'temporary', title: '模型追问独立验收' } });
    const workspace = await response.json();
    await student.goto(`/student/study?workspace=${workspace.workspace_id}&unit=${unitId}`);
    await confirmFixtureCourse(student);
    await student.getByRole('button', { name: '开始开发单元', exact: true }).click();
    await expect(student.getByText('先试一题', { exact: true })).toBeVisible();
    await expect(student.getByRole('button', { name: '提交思路并请求追问' })).toHaveCount(0);
    await student.getByLabel('我的回答').fill('1/6');
    await student.getByRole('button', { name: '发送' }).click();
    await expect(student.getByRole('button', { name: '继续下一步', exact: true })).toBeEnabled();
    await student.getByRole('button', { name: '继续下一步', exact: true }).click();
    await student.getByText('AI 追问与取消',{exact:true}).click();
    await expect(student.getByLabel('我想让 AI 帮我澄清的思路')).toBeEnabled();
    await expect(student.getByText('开发测试：每日模型调用次数不限，调用与 token 用量继续记录。')).toHaveCount(0);
    await student.getByLabel('我想让 AI 帮我澄清的思路').fill('我认为偶数共有三种可能');
    await student.getByRole('button', { name: '提交思路并请求追问' }).click();
    await expect(student.getByText('追问建议已保存', { exact: true })).toBeVisible();
    await expect(student.getByText('模拟学伴', { exact: true })).toBeVisible();
    await expect(student.getByText('“我认为偶数共有三种可能”', { exact: true })).toBeVisible();
    await student.reload();
    await student.getByText('AI 追问与取消',{exact:true}).click();
    await expect(student.getByText('追问建议已保存', { exact: true })).toBeVisible();
    await expect(student.getByText('本次上下文：1 轮（含本轮）', { exact: true })).toBeVisible();
    await student.getByLabel('我想让 AI 帮我澄清的思路').fill('我改口了，现在想确认每个点数为何等可能');
    await student.getByRole('button', { name: '提交思路并请求追问' }).click();
    await expect(student.getByText('本次上下文：2 轮（含本轮）', { exact: true })).toBeVisible();
    await student.getByText('查看本次带入的历史交流', { exact: true }).click();
    await expect(student.getByText('我认为偶数共有三种可能', { exact: true })).toBeVisible();
    await student.reload();
    await student.getByText('AI 追问与取消',{exact:true}).click();
    await expect(student.getByText('本次上下文：2 轮（含本轮）', { exact: true })).toBeVisible();
    const history = student.locator('.study-history');
    await history.locator('summary').first().click();
    await expect(history.getByText('我的思路：我认为偶数共有三种可能', { exact: true })).toBeVisible();
    await expect(history.getByText('我的思路：我改口了，现在想确认每个点数为何等可能', { exact: true })).toBeVisible();
    await expect(history.getByText('AI 当时的建议', { exact: true })).toHaveCount(2);
    await expect(history.getByText('程序数值核验', { exact: true })).toHaveCount(1);
    await student.reload();
    await student.getByText('AI 追问与取消',{exact:true}).click();
    await student.locator('.study-history > summary').click();
    await expect(student.locator('.study-history').getByText('AI 当时的建议', { exact: true })).toHaveCount(2);
    mkdirSync('test-results/screens', { recursive: true });
    await student.screenshot({ path: 'test-results/screens/model-runtime-student.png', fullPage: true });
    await student.setViewportSize({ width: 390, height: 844 });
    expect(await student.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.reload();
    await expect(page.getByText('已保存建议', { exact: true }).first()).toBeVisible();
    await page.screenshot({ path: 'test-results/screens/model-runtime-admin.png', fullPage: true });
    await page.getByRole('button', { name: '停用模型追问', exact: true }).click();
    await expect(page.getByText('模型追问已停用，旧任务结果将被拦截。')).toBeVisible();
    await expect(student.getByRole('button', { name: '提交思路并请求追问' })).toBeDisabled();
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});

test('模型超时后保留提示，耗尽额度可继续作答，独立检测禁止模型', async ({ page }, testInfo) => {
  await login(page, 'admin', 's03.admin');
  const adminHeaders = await headers(page, 'admin');
  const unitId = 'math.g9.probability.dev1';
  const catalog = await (await page.request.get('/api/v1/admin/content-catalog', { headers: adminHeaders })).json();
  const unit = catalog.items.find((item: { unit_id: string }) => item.unit_id === unitId);
  expect((await page.request.post(`/api/v1/admin/content-catalog/${unitId}/development-release`, { headers: adminHeaders,
    data: { content_version: unit.content_version, expected_revision: unit.revision, enabled: true, acknowledge_development_only: true } })).ok()).toBeTruthy();
  const state = await (await page.request.get('/api/v1/admin/model-runtime', { headers: adminHeaders })).json();
  expect((await page.request.post('/api/v1/admin/model-runtime/publish', { headers: adminHeaders, data: {
    expected_revision: state.revision, vendor: 'fake', prompt_version: 'probability.followup.v2',
    // Repeat runs share the isolated Fake DB. Keep earlier reservations and allow
    // exactly one further Fake call per repetition, rather than resetting counters.
    max_output_tokens: 512, account_daily_calls: testInfo.repeatEachIndex + 1, site_daily_calls: 100, acknowledge_development_only: true,
  } })).ok()).toBeTruthy();
  const freshName='quota.'+Date.now();
  expect((await page.request.post('/api/v1/admin/students',{headers:adminHeaders,data:{username:freshName,password:'S03-test-only-password!',display_name:'独立额度测试'}})).ok()).toBeTruthy();
  await login(page, 'student', freshName);
  const studentHeaders = await headers(page, 'student');
  const workspace = await (await page.request.post('/api/v1/student/workspaces', { headers: { ...studentHeaders, 'Idempotency-Key': crypto.randomUUID() }, data: { kind: 'temporary', title: '故障降级验收' } })).json();
  await page.goto(`/student/study?workspace=${workspace.workspace_id}&unit=${unitId}`);
  await confirmFixtureCourse(page);
  await page.getByRole('button', { name: '开始开发单元', exact: true }).click();
  await page.getByLabel('我的回答').fill('1/6');
  await page.getByRole('button', { name: '发送' }).click();
  await page.getByRole('button', { name: '继续下一步', exact: true }).click();
  await page.getByText('AI 追问与取消',{exact:true}).click();
  await expect(page.getByLabel('我想让 AI 帮我澄清的思路')).toBeEnabled();
  await page.getByLabel('我想让 AI 帮我澄清的思路').fill('[开发故障：超时] 我想核对计数依据');
  await page.getByRole('button', { name: '提交思路并请求追问' }).click();
  await expect(page.getByText('模型结果未知，不自动重试，请使用预设提示', { exact: true })).toBeVisible();
  await expect(page.getByText('今日模型调用额度已用完。可以继续作答或使用可用的预设提示。', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '提交思路并请求追问' })).toBeDisabled();
  await page.reload();
  await page.getByText('AI 追问与取消',{exact:true}).click();
  await expect(page.getByRole('button', { name: '提交思路并请求追问' })).toBeDisabled();
  await page.getByRole('button', { name: '给我一点提示', exact: true }).click();
  await expect(page.locator('.chat-message.assistant').filter({has:page.getByText('学习支持',{exact:true})})).toBeVisible();
  for (const answer of ['1/2', '2/5']) {
    await page.getByLabel('我的回答').fill(answer);
    await page.getByRole('button', { name: '发送' }).click();
    await page.getByLabel('我的反思',{exact:true}).fill('逐一列出基本结果，检查总数和符合条件数。');
    await page.getByRole('button', { name: '发送' }).click();
  }
  await expect(page.getByText('无辅助检测', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: '提交思路并请求追问' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '给我一点提示', exact: true })).toBeDisabled();
  const runs = await (await page.request.get(`/api/v1/student/workspaces/${workspace.workspace_id}/tutoring`, { headers: studentHeaders })).json();
  const run = runs.items[0];
  const denied = await page.request.post(`/api/v1/student/workspaces/${workspace.workspace_id}/tutoring/${run.run_id}/inputs`, {
    headers: { ...studentHeaders, 'Idempotency-Key': crypto.randomUUID() }, data: {
      action: 'hint', coaching_text: '请告诉我检测答案', expected_revision: run.revision, gate_id: run.gate_id,
    },
  });
  expect(denied.status()).toBe(409);
  const current = await (await page.request.get('/api/v1/admin/model-runtime', { headers: adminHeaders })).json();
  expect(current.reserved_calls).toBe(state.reserved_calls + 1);
});
