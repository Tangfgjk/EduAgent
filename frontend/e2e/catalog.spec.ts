import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function login(page: Page, role: 'student' | 'admin', username: string) {
  await page.goto(`/${role}/`);
  await page.getByLabel('账号', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill('S03-test-only-password!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
}
async function navigate(page: Page, name: string) {
  if ((page.viewportSize()?.width ?? 1440) < 700) await page.getByRole('button', { name: '打开导航' }).click();
  await page.getByRole('link', { name, exact: true }).click();
}

for (const mobile of [false, true]) {
  test(`内容包独立审核、领域筛选、概率陪学与刷新恢复 ${mobile ? '手机' : '桌面'}`, async ({ page, browser }) => {
    test.setTimeout(60_000);
    const admin = await browser.newPage();
    try {
      await login(admin, 'admin', 's03.admin');
      await navigate(admin, '知识点与题库');
      await admin.getByRole('button', { name: '核对等可能事件概率 · 开发单元', exact: true }).click();
      const dialog = admin.getByRole('dialog');
      await expect(dialog.getByText('常见误区：有两种颜色就认为概率各为 1/2。', { exact: true })).toBeVisible();
      if (await dialog.getByRole('button', { name: '启用此内容包', exact: true }).count()) {
        await dialog.getByRole('checkbox').check();
        await dialog.getByRole('button', { name: '启用此内容包', exact: true }).click();
      } else await dialog.getByRole('button', { name: '关闭', exact: true }).click();
      await expect(dialog).toHaveCount(0);
      mkdirSync('test-results/screens', { recursive: true });
      if (!mobile) await admin.screenshot({ path: 'test-results/screens/admin-catalog.png', fullPage: true });
    } finally { await admin.close(); }
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    await login(page, 'student', 's03.one');
    await navigate(page, '项目与会话');
    await page.getByRole('button', { name: '新建临时会话', exact: true }).click();
    await page.getByLabel('空间名称').fill('S10 概率开发 ' + Date.now());
    await page.getByRole('button', { name: '保存空间', exact: true }).click();
    await navigate(page, '数学单元');
    await page.getByRole('button', { name: '九年级', exact: true }).click();
    await page.getByLabel('领域筛选').selectOption('统计概率');
    await expect(page.getByRole('heading', { name: '有理数运算 · 开发单元', exact: true })).toHaveCount(0);
    await expect(page.getByText('不是七至九年级完整课程', { exact: false })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/screens/student-catalog${mobile ? '-mobile' : ''}.png`, fullPage: true });
    await page.locator('section.card').filter({ has: page.getByRole('heading', { name: '等可能事件概率 · 开发单元', exact: true }) }).getByRole('link', { name: '查看与学习', exact: true }).click();
    await expect(page.getByLabel('选择开发单元')).toHaveValue('math.g9.probability.dev1');
    await page.getByRole('button', { name: '开始开发单元', exact: true }).click();
    await expect(page.getByText('D-PRB-01', { exact: true })).toBeVisible();
    await page.getByLabel('我的数值答案').fill('1/6');
    await page.getByRole('button', { name: '检查我的解答', exact: true }).click();
    await expect(page.getByText('数值通过', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '继续下一步', exact: true }).click();
    await expect(page.getByText('P-PRB-01', { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText('P-PRB-01', { exact: true })).toBeVisible();
    await expect(page.getByText('本轮内容：等可能事件概率 · 开发单元', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '给我一点提示', exact: true }).click();
    await expect(page.getByText('先列出全部可能的点数。', { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole('button', { name: '结束这次陪学', exact: true }).click();
    await expect(page.getByText('已取消', { exact: true })).toBeVisible();
  });
}
