import { confirmFixtureCourse } from './course-helper';
import { test, expect, type Page } from '@playwright/test';

async function login(page: Page, role: 'admin' | 'student', username: string) {
  await page.goto(`/${role}/`);
  await page.getByLabel('账号', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill('S03-test-only-password!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
}

test('模型圆桌：审核启用、三视角、刷新、学生比较及另一题无辅助验证', async ({ page, browser }, testInfo) => {
  test.setTimeout(60_000);
  const errors: string[] = [];
  await login(page, 'admin', 's03.admin');
  const cookies = await page.context().cookies();
  const auth = { 'X-Wenjin-Client': 'admin', 'X-CSRF-Token': cookies.find(c => c.name === 'wenjin_admin_csrf')!.value };
  const state = await (await page.request.get('/api/v1/admin/model-runtime', { headers: auth })).json();
  expect((await page.request.post('/api/v1/admin/model-runtime/publish', { headers: auth, data: {
    expected_revision: state.revision, vendor: 'fake', prompt_version: 'probability.followup.v3', max_output_tokens: 512,
    account_daily_calls: 0, site_daily_calls: 0, acknowledge_development_only: true,
  } })).ok()).toBeTruthy();
  const generation=await(await page.request.get('/api/v1/admin/generation-policy',{headers:auth})).json();
  expect((await page.request.post('/api/v1/admin/generation-policy',{headers:auth,data:{expected_revision:generation.revision,courses:true,interaction:true,search:false,private_auto_release:true,acknowledge_development_only:true}})).ok()).toBeTruthy();
  const base = (await (await page.request.get('/api/v1/admin/content-catalog', { headers: auth })).json()).items.find((x: { unit_id: string }) => x.unit_id === 'math.g9.probability.dev1');
  expect((await page.request.post(`/api/v1/admin/content-catalog/${base.unit_id}/development-release`, { headers: auth,
    data: { content_version: base.content_version, expected_revision: base.revision, enabled: true, acknowledge_development_only: true } })).ok()).toBeTruthy();
  await page.getByRole('link', { name: '知识点与题库', exact: true }).click();
  await page.getByRole('button', { name: '审核概率圆桌 · 三种视角与新题验证', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('复核依据').fill('核对自编题、三视角边界、预设回退及另一题参考数值，仅用于开发。');
  await dialog.getByRole('button', { name: '保存复核记录', exact: true }).click();
  await expect(dialog.getByText('复核记录已保存', { exact: true })).toBeVisible();
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: '启用此互动包', exact: true }).click();
  const context = await browser.newContext(), student = await context.newPage();
  student.on('pageerror', error => errors.push(error.message));
  try {
    await login(student, 'student', 's03.two');
    const studentCookies = await context.cookies();
    const headers = { 'X-Wenjin-Client': 'student', 'X-CSRF-Token': studentCookies.find(c => c.name === 'wenjin_student_csrf')!.value };
    const space = await (await student.request.post('/api/v1/student/workspaces', { headers: { ...headers, 'Idempotency-Key': crypto.randomUUID() },
      data: { kind: 'temporary', title: '圆桌三视角验收' } })).json();
    await student.goto(`/student/roundtable?workspace=${space.workspace_id}&activity=math.g9.probability.roundtable.dev2`);
    await confirmFixtureCourse(student);
    await student.getByRole('button', { name: '开始圆桌讨论', exact: true }).click();
    await expect(student.getByLabel('让 AI 根据思路与历史生成针对性讨论')).toBeChecked();
    await student.getByLabel('我的数值表达', { exact: true }).fill('1/2');
    await student.getByLabel('我的初步思路', { exact: true }).fill('我把红和蓝两种颜色作为两个结果，各一半。');
    await student.getByRole('button', { name: '保存表达，邀请学伴', exact: true }).click();
    await expect(student.getByText('圆桌视角已保存', { exact: true })).toBeVisible();
    for (const role of ['思路提出者', '质疑者', '整理者']) await expect(student.getByRole('heading', { name: `${role} · AI 生成追问`, exact: true })).toBeVisible();
    await student.reload();
    await expect(student.getByText('圆桌视角已保存', { exact: true })).toBeVisible();
    await student.screenshot({ path: testInfo.outputPath('roundtable-three-voices.png'), fullPage: true });
    await student.getByLabel('我的数值表达', { exact: true }).fill('2/5');
    await student.getByLabel('我的比较、纠错与检查依据').fill('我比较了按球和按颜色计数的前提，题干保证每个球等可能，不能直接认为颜色等可能。');
    await student.getByRole('button', { name: '保存修正，进入新题验证', exact: true }).click();
    await expect(student.getByLabel('本题独立检查依据')).toBeVisible();
    await expect(student.getByRole('heading', { name: '围绕我的思路 · 三种圆桌视角', exact: true })).toHaveCount(0);
    await student.getByLabel('我的数值表达', { exact: true }).fill('3/7');
    await student.getByLabel('本题独立检查依据').fill('七个等可能的球，三个符合条件，按同一单位计数。');
    await student.getByRole('button', { name: '提交本题验证，查看报告', exact: true }).click();
    await expect(student.getByRole('heading', { name: '圆桌讨论小结', exact: true })).toBeVisible();
    await expect(student.getByText('另一题验证：数值通过', { exact: true })).toBeVisible();
    await expect(student.getByText('学生原始贡献', { exact: true })).toHaveCount(3);
    await expect(student.getByText('圆桌视角已保存', { exact: true })).toBeVisible();
    await student.setViewportSize({ width: 390, height: 844 });
    expect(await student.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});
