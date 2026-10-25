import { confirmFixtureCourse } from './course-helper';
import { test, expect, type Page } from '@playwright/test';

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

test('V8：内容核对开放、评测分类复核、我教 AI 新题验证与刷新恢复', async ({ page, browser }) => {
  test.setTimeout(60_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await login(page, 'admin', 's03.admin');
  await page.getByRole('link', { name: '知识点与题库', exact: true }).click();
  await page.getByRole('button', { name: '核对概率计数与事件 · 扩展开发单元', exact: true }).click();
  let dialog = page.getByRole('dialog');
  await dialog.getByLabel('复核依据').fill('核对卡片计数、补事件、逐级提示及检测参考数值，仅供开发测试。');
  await dialog.getByRole('button', { name: '保存复核记录', exact: true }).click();
  await expect(dialog.getByText('复核记录已保存', { exact: true })).toBeVisible();
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: '启用此内容包', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByRole('link', { name: '评测工作台', exact: true }).click();
  await page.getByLabel('评测运行').selectOption('holdout-fake');
  const caseRow = page.locator('section.card').filter({ has: page.getByRole('heading', { name: 'holdout-fake:M07-1', exact: true }) });
  await caseRow.getByLabel('复核结论').selectOption('model_issue');
  await caseRow.getByLabel('复核依据').fill('模拟输出固定澄清，本条先记录方向不匹配，不能推断真实模型质量。');
  await caseRow.getByRole('button', { name: '保存复核记录', exact: true }).click();
  await expect(caseRow.getByText('复核记录已保存', { exact: true })).toBeVisible();
  await page.reload();
  await page.getByLabel('评测运行').selectOption('holdout-fake');
  await expect(page.getByText('查看已有复核意见（1 条）', { exact: true })).toBeVisible();
  const adminHeaders = await headers(page, 'admin');
  const baseId = 'math.g9.probability.dev1';
  const catalog = await (await page.request.get('/api/v1/admin/content-catalog', { headers: adminHeaders })).json();
  const base = catalog.items.find((x: { unit_id: string }) => x.unit_id === baseId);
  expect((await page.request.post(`/api/v1/admin/content-catalog/${baseId}/development-release`, { headers: adminHeaders,
    data: { content_version: base.content_version, expected_revision: base.revision, enabled: true, acknowledge_development_only: true } })).ok()).toBeTruthy();
  await page.getByRole('link', { name: '知识点与题库', exact: true }).click();
  await page.getByRole('button', { name: '审核概率我教 AI · 讲解与新题验证', exact: true }).click();
  dialog = page.getByRole('dialog');
  await dialog.getByLabel('复核依据').fill('核对预设误区、修正流程与另一题数值，确认不将开发核对作为专业审核。');
  await dialog.getByRole('button', { name: '保存复核记录', exact: true }).click();
  await expect(dialog.getByText('复核记录已保存', { exact: true })).toBeVisible();
  await dialog.getByRole('checkbox').check();
  const enableActivity = dialog.getByRole('button', { name: '启用此互动包', exact: true });
  if (await enableActivity.count()) await enableActivity.click();
  else await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const context = await browser.newContext();
  const student = await context.newPage();
  student.on('pageerror', e => errors.push(e.message));
  try {
    await login(student, 'student', 's03.two');
    const auth = await headers(student, 'student');
    const space = await (await student.request.post('/api/v1/student/workspaces', { headers: { ...auth, 'Idempotency-Key': crypto.randomUUID() }, data: { kind: 'temporary', title: 'V8 我教 AI 验收' } })).json();
    await student.goto(`/student/teach?workspace=${space.workspace_id}&activity=math.g9.probability.teach.dev2`);
    await confirmFixtureCourse(student);
    await student.getByRole('button', { name: '开始我教 AI', exact: true }).click();
    await student.getByLabel('我的数值表达', { exact: true }).fill('1/2');
    await student.getByLabel('我的讲解', { exact: true }).fill('我先用颜色列结果，再检查这样是否等可能。');
    const modelChoice = student.getByLabel('让 AI 根据讲解与历史提出具体问题', { exact: true });
    if (await modelChoice.count()) await modelChoice.uncheck();
    await student.getByRole('button', { name: '保存表达，邀请学伴', exact: true }).click();
    await expect(student.getByText('预设误区 · 待纠正', { exact: true })).toBeVisible();
    await student.getByLabel('我的数值表达', { exact: true }).fill('2/5');
    await student.getByLabel('我的比较、纠错与检查依据', { exact: true }).fill('应按五个球作为基本结果，其中两个蓝球符合条件。');
    await student.getByRole('button', { name: '保存修正，进入新题验证', exact: true }).click();
    await expect(student.getByRole('heading', { name: '另一题 · 无辅助数值验证', exact: true })).toBeVisible();
    await student.reload();
    await expect(student.getByLabel('本题独立检查依据')).toBeVisible();
    await student.getByLabel('我的数值表达', { exact: true }).fill('3/7');
    await student.getByLabel('本题独立检查依据').fill('七个等可能球中有三个黑球。');
    await student.getByRole('button', { name: '提交本题验证，查看报告', exact: true }).click();
    await expect(student.getByRole('heading', { name: 'TeachReport · 讲解记录', exact: true })).toBeVisible();
    await expect(student.getByText('另一题验证：数值通过', { exact: true })).toBeVisible();
    await expect(student.getByText('学生原始贡献', { exact: true })).toHaveCount(3);
    await student.setViewportSize({ width: 390, height: 844 });
    expect(await student.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});
