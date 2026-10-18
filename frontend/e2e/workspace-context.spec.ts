import { test, expect } from '@playwright/test';

test('空间按实际陪学匹配互动：名称不作推断、缺包明确、刷新和跨项目隔离、图标提示', async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto('/admin/');
  await page.getByLabel('账号', { exact: true }).fill('s03.admin');
  await page.getByLabel('密码', { exact: true }).fill('S03-test-only-password!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
  let cookies = await page.context().cookies();
  const adminHeaders = { 'X-Wenjin-Client': 'admin', 'X-CSRF-Token': cookies.find(c => c.name === 'wenjin_admin_csrf')!.value };
  const catalog = await (await page.request.get('/api/v1/admin/content-catalog', { headers: adminHeaders })).json();
  for (const unitId of ['math.g7.rational.dev1', 'math.g9.probability.dev1']) {
    const unit = catalog.items.find((item: { unit_id: string }) => item.unit_id === unitId);
    expect((await page.request.post(`/api/v1/admin/content-catalog/${unitId}/development-release`, { headers: adminHeaders,
      data: { content_version: unit.content_version, expected_revision: unit.revision, enabled: true, acknowledge_development_only: true } })).ok()).toBeTruthy();
  }
  await page.goto('/student/');
  await page.getByLabel('账号', { exact: true }).fill('s03.one');
  await page.getByLabel('密码', { exact: true }).fill('S03-test-only-password!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
  cookies = await page.context().cookies();
  const headers = { 'X-Wenjin-Client': 'student', 'X-CSRF-Token': cookies.find(c => c.name === 'wenjin_student_csrf')!.value };
  async function create(title: string, unitId?: string) {
    const response = await page.request.post('/api/v1/student/workspaces', { headers: { ...headers, 'Idempotency-Key': crypto.randomUUID() }, data: { kind: 'temporary', title } });
    expect(response.ok()).toBeTruthy();
    const space = await response.json();
    if (unitId) {
      const run = await page.request.post(`/api/v1/student/workspaces/${space.workspace_id}/tutoring`, { headers: { ...headers, 'Idempotency-Key': crypto.randomUUID() }, data: { unit_id: unitId } });
      expect(run.ok()).toBeTruthy();
      const { run_id } = await run.json();
      await expect.poll(async () => (await (await page.request.get(`/api/v1/student/workspaces/${space.workspace_id}/tutoring/${run_id}`, { headers })).json()).view?.unit_id).toBe(unitId);
    }
    return space.workspace_id as string;
  }
  const rational = await create('概率标题但实际学有理数', 'math.g7.rational.dev1');
  await page.goto(`/student/spaces?workspace=${rational}`);
  await page.getByRole('tab', { name: /临时会话/ }).click();
  const rename = page.getByRole('button', { name: '重命名 概率标题但实际学有理数', exact: true });
  await expect(rename).toHaveAttribute('title', '重命名');
  await rename.hover();
  await expect.poll(() => rename.evaluate(el => getComputedStyle(el, '::after').opacity)).toBe('1');
  await rename.focus();
  await expect(rename).toBeFocused();
  const archive = page.getByRole('button', { name: '归档 概率标题但实际学有理数', exact: true });
  await expect(archive).toHaveAttribute('title', '归档（保留记录，可恢复）');
  await page.keyboard.press('Tab');
  await expect(archive).toBeFocused();
  await expect.poll(() => archive.evaluate(el => getComputedStyle(el, '::after').opacity)).toBe('1');
  await rename.click();
  await page.getByLabel('空间名称').fill('随意改名');
  await page.getByRole('button', { name: '保存空间', exact: true }).click();
  await expect(page.locator('.context-bar')).toContainText('随意改名');
  await page.goto(`/student/study?workspace=${rational}`);
  // End the current unit so the next visit also exercises the default selection control.
  await page.getByRole('button', { name: '结束这次陪学', exact: true }).click();
  await expect(page.getByLabel('选择开发单元')).toHaveValue('math.g7.rational.dev1');
  await page.reload();
  await expect(page.getByLabel('选择开发单元')).toHaveValue('math.g7.rational.dev1');
  for (const route of ['roundtable', 'teach']) {
    await page.goto(`/student/${route}?workspace=${rational}`);
    await expect(page.getByText(/该单元暂未提供.*内容包/)).toBeVisible();
    await expect(page.getByLabel('选择互动单元')).toHaveValue('');
    await expect(page.getByRole('button', { name: route === 'teach' ? '开始我教 AI' : '开始圆桌讨论', exact: true })).toBeDisabled();
  }
  const probability = await create('有理数标题但实际学概率', 'math.g9.probability.dev1');
  await page.goto(`/student/roundtable?workspace=${probability}`);
  await expect(page.getByText('当前学习单元：等可能事件概率 · 开发单元', { exact: true })).toBeVisible();
  await expect(page.getByLabel('选择互动单元')).not.toHaveValue('');
  await expect(page.getByLabel('选择互动单元')).not.toHaveValue('math.g7.equations.activities.dev1');
  await page.goto(`/student/roundtable?workspace=${rational}`);
  await expect(page.getByLabel('选择互动单元')).toHaveValue('');
  const blank = await create('概率');
  await page.goto(`/student/roundtable?workspace=${blank}`);
  await expect(page.getByText('当前空间尚无可匹配的陪学单元', { exact: true })).toBeVisible();
  await expect(page.getByLabel('选择互动单元')).toHaveValue('');
});
