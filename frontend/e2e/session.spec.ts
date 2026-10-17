import { test, expect, type Page } from '@playwright/test';

async function login(page: Page, role: 'student' | 'admin', username: string, secret = 'S03-test-only-password!') {
  await page.goto(`/${role}/`);
  await expect(page.getByLabel('账号', { exact: true }).or(page.getByRole('button', { name: '退出并切换账号', exact: true }))).toBeVisible();
  if (await page.getByRole('button', { name: '退出并切换账号', exact: true }).count()) {
    await page.getByRole('button', { name: '退出并切换账号', exact: true }).click();
  }
  await page.getByLabel('账号', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill(secret);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
}

test('切换普通网页再返回，学生保持登录并可刷新', async ({ page, context }) => {
  await page.clock.install();
  await login(page, 'student', 's03.one');
  const other = await context.newPage();
  await other.route('https://outside.example.test/**', route => route.fulfill({ contentType: 'text/html', body: '<h1>另一个网页</h1>' }));
  await other.goto('https://outside.example.test/');
  await other.bringToFront();
  await page.clock.runFor(16_000);
  await page.bringToFront();
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('.app-shell')).toBeVisible();
  await page.reload();
  await expect(page.locator('.app-shell')).toBeVisible();
  await other.close();
});

test('学生端与管理端在同一浏览器切换后保持各自登录', async ({ page, context }) => {
  await login(page, 'student', 's03.one');
  const other = await context.newPage();
  await login(other, 'admin', 's03.admin');
  await page.bringToFront();
  await page.reload();
  await expect(page.locator('.app-shell')).toBeVisible();
  await expect(page.getByRole('link', { name: '数学单元', exact: true })).toBeVisible();
  await other.bringToFront();
  await other.reload();
  await expect(other.getByRole('link', { name: '学生账号', exact: true })).toBeVisible();
  await other.getByRole('button', { name: '退出登录', exact: true }).click();
  await page.bringToFront();
  await page.reload();
  await expect(page.locator('.app-shell')).toBeVisible();
  await other.close();
});


test('同一标签页前往其它网站再后退，学生保持登录', async ({ page }) => {
  await login(page, 'student', 's03.one');
  await page.route('https://outside.example.test/**', route => route.fulfill({ contentType: 'text/html', body: '<h1>另一个网页</h1>' }));
  await page.goto('https://outside.example.test/');
  await page.goBack();
  await expect(page.locator('.app-shell')).toBeVisible();
  await page.reload();
  await expect(page.locator('.app-shell')).toBeVisible();
});

test('管理员用6位初始密码建号与重置，学生可登录且旧会话失效', async ({ page, context }) => {
  await login(page, 'admin', 's03.admin');
  await page.getByRole('link', { name: '学生账号', exact: true }).click();
  await page.getByRole('button', { name: '创建学生账号', exact: true }).click();
  const name = 'six.' + Date.now();
  await page.getByLabel('学生姓名').fill('六位密码验收');
  await page.getByLabel('学生账号', { exact: true }).fill(name);
  await expect(page.getByLabel('初始密码', { exact: false })).toHaveAttribute('minlength', '6');
  await page.getByLabel('初始密码', { exact: false }).fill('123456');
  await page.getByRole('button', { name: '保存学生账号', exact: true }).click();
  const row = page.getByRole('row').filter({ hasText: name });
  await expect(row).toBeVisible();
  const student = await context.newPage();
  await login(student, 'student', name, '123456');
  await row.getByRole('button', { name: '重置密码', exact: true }).click();
  await expect(page.getByLabel('新密码', { exact: false })).toHaveAttribute('minlength', '6');
  await page.getByLabel('新密码', { exact: false }).fill('654321');
  await page.getByRole('button', { name: '确认重置', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('密码已重置');
  await student.reload();
  await expect(student.getByRole('button', { name: '登录', exact: true })).toBeVisible();
  await login(student, 'student', name, '654321');
  await student.close();
});
