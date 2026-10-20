import { confirmFixtureCourse } from './course-helper';
import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function login(page: Page, role: 'admin' | 'student', username: string) {
  await page.goto(`/${role}/`);
  await page.getByLabel('账号', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill('S03-test-only-password!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
}

test('模型我教 AI：讲解追问刷新、数值核验与验证阶段无辅助', async ({ page, browser }, testInfo) => {
  test.setTimeout(60_000);
  await login(page, 'admin', 's03.admin');
  const cookies = await page.context().cookies();
  const headers = { 'X-Wenjin-Client': 'admin', 'X-CSRF-Token': cookies.find(c => c.name === 'wenjin_admin_csrf')!.value };
  const state = await (await page.request.get('/api/v1/admin/model-runtime', { headers })).json();
  expect((await page.request.post('/api/v1/admin/model-runtime/publish', { headers, data: {
    expected_revision: state.revision, vendor: 'fake', prompt_version: 'probability.followup.v3',
    account_daily_calls: 0, site_daily_calls: 0, max_output_tokens: 512, acknowledge_development_only: true,
  } })).ok()).toBeTruthy();
  const baseId = 'math.g9.probability.dev1', activityId = 'math.g9.probability.teach.dev2';
  for (const [segment, id] of [['content-catalog', baseId], ['activity-catalog', activityId]]) {
    const catalog = await (await page.request.get(`/api/v1/admin/${segment}`, { headers })).json();
    const item = catalog.items.find((x: { unit_id: string }) => x.unit_id === id);
    if (id === activityId) {
      const prior = await (await page.request.get(`/api/v1/admin/development-reviews?target_id=${id}&version=${item.content_version}`, { headers })).json();
      expect((await page.request.post('/api/v1/admin/development-reviews', { headers, data: {
        kind: 'content', target_id: id, version: item.content_version, expected_revision: prior.revision,
        conclusion: 'development_ready', note: '核对开发概率内容与验证题，仅限模拟测试，不代表专业审核。',
      } })).ok()).toBeTruthy();
    }
    expect((await page.request.post(`/api/v1/admin/${segment}/${id}/development-release`, { headers, data: {
      content_version: item.content_version, expected_revision: item.revision, enabled: true, acknowledge_development_only: true,
    } })).ok()).toBeTruthy();
  }
  const context = await browser.newContext(), student = await context.newPage();
  try {
    await login(student, 'student', 's03.one');
    const studentCookies = await context.cookies();
    const auth = { 'X-Wenjin-Client': 'student', 'X-CSRF-Token': studentCookies.find(c => c.name === 'wenjin_student_csrf')!.value };
    const space = await (await student.request.post('/api/v1/student/workspaces', { headers: { ...auth, 'Idempotency-Key': crypto.randomUUID() },
      data: { kind: 'temporary', title: '模型讲解验收' } })).json();
    await student.goto(`/student/teach?workspace=${space.workspace_id}&activity=${activityId}`);
    await confirmFixtureCourse(student);
    await student.getByRole('button', { name: '开始我教 AI', exact: true }).click();
    await expect(student.getByLabel('让 AI 根据本次讲解选择追问方向')).toBeChecked();
    await student.getByLabel('我的数值表达', { exact: true }).fill('1/2');
    await student.getByLabel('我的讲解', { exact: true }).fill('我把颜色当基本结果，红蓝两种就各一半。');
    await student.getByRole('button', { name: '保存表达，邀请学伴', exact: true }).click();
    await expect(student.getByText('学伴追问已保存', { exact: true })).toBeVisible();
    await expect(student.getByText('模拟学伴', { exact: true })).toBeVisible();
    await expect(student.getByText('你使用了哪条关系或运算法则，它的适用条件是什么？', { exact: true }).last()).toBeVisible();
    await student.reload();
    await expect(student.getByText('学伴追问已保存', { exact: true })).toBeVisible();
    mkdirSync('test-results/screens', { recursive: true });
    await student.screenshot({ path: 'test-results/screens/teach-model-question.png', fullPage: true });
    await student.getByLabel('我的数值表达', { exact: true }).fill('2/5');
    await student.getByLabel('我的比较、纠错与检查依据').fill('应按五个等可能的球计数，两个蓝球符合事件。');
    for (const round of [2, 3]) {
      await student.getByRole('button', { name: '保存回应，继续追问', exact: true }).click();
      await expect(student.getByText('学伴追问已保存', { exact: true })).toBeVisible();
      await expect(student.getByText(`已请求 ${round} / 3 次追问。`, { exact: false })).toBeVisible();
      await student.reload();
      await expect(student.getByText(`已请求 ${round} / 3 次追问。`, { exact: false })).toBeVisible();
      await student.getByLabel('我的数值表达', { exact: true }).fill('2/5');
      await student.getByLabel('我的比较、纠错与检查依据').fill('按等可能的球计数，再识别符合事件的球，分子分母使用同一单位。');
    }
    await expect(student.getByRole('button', { name: '保存回应，继续追问', exact: true })).toHaveCount(0);
    await student.screenshot({ path: testInfo.outputPath('teach-multiturn.png'), fullPage: true });
    await student.getByRole('button', { name: '保存修正，进入新题验证', exact: true }).click();
    await expect(student.getByLabel('本题独立检查依据')).toBeVisible();
    await expect(student.getByRole('heading', { name: '针对我的讲解提问', exact: true })).toHaveCount(0);
    await expect(student.getByRole('button', { name: '先使用预设追问', exact: true })).toHaveCount(0);
    await student.getByLabel('我的数值表达', { exact: true }).fill('3/7');
    await student.getByLabel('本题独立检查依据').fill('总共七个等可能的球，三个黑球符合条件。');
    await student.getByRole('button', { name: '提交本题验证，查看报告', exact: true }).click();
    await expect(student.getByText('另一题验证：数值通过', { exact: true })).toBeVisible();
    await expect(student.getByText('学生原始贡献', { exact: true })).toHaveCount(5);
    await expect(student.getByText('你使用了哪条关系或运算法则，它的适用条件是什么？', { exact: true }).last()).toBeVisible();
    await student.setViewportSize({ width: 390, height: 844 });
    expect(await student.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  } finally { await context.close(); }
});
