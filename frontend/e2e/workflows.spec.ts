import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const password = 'S03-test-only-password!';
async function login(page: Page, role: 'student' | 'admin', username: string, secret = password) {
  await page.goto(`/${role}/`);
  await page.getByLabel('账号', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill(secret);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
}
async function createProject(page: Page, name: string) {
  await page.getByRole('link', { name: '项目与会话', exact: true }).click();
  await page.getByRole('button', { name: '新建学习项目', exact: true }).click();
  await page.getByLabel('空间名称').fill(name);
  await page.getByRole('button', { name: '保存空间' }).click();
  await expect(page.locator('.context-bar')).toContainText(name);
  return new URL(page.url()).searchParams.get('workspace')!;
}

test('学生真实项目创建、刷新、重命名、归档恢复与临时路线', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await login(page, 'student', 's03.one');
  const project = '方程项目 ' + Date.now();
  const wid = await createProject(page, project);
  await page.reload();
  await expect(page.locator('.context-bar')).toContainText(project);
  await page.getByRole('button', { name: '重命名 ' + project, exact: true }).click();
  const renamed = project + ' · 调整';
  await page.getByLabel('空间名称').fill(renamed);
  await page.getByRole('button', { name: '保存空间' }).click();
  await expect(page.locator('.context-bar')).toContainText(renamed);
  await page.getByRole('button', { name: '归档 ' + renamed, exact: true }).click();
  await page.getByRole('button', { name: '确认归档' }).click();
  await expect(page.locator('.context-bar')).toContainText('已归档');
  await page.getByRole('tab', { name: /已归档/ }).click();
  await page.locator('section.project-card').filter({ has: page.getByRole('heading', { name: renamed, exact: true }) }).getByRole('button', { name: '恢复', exact: true }).click();
  await page.getByRole('button', { name: '确认恢复' }).click();
  await expect(page.locator('.context-bar')).not.toContainText('已归档');
  await page.getByRole('button', { name: '新建临时会话', exact: true }).click();
  await page.getByLabel('空间名称').fill('一次临时探索');
  await page.getByRole('button', { name: '保存空间' }).click();
  await expect(page.locator('.context-bar')).toContainText('一次临时探索');
  await page.getByRole('link', { name: '圆桌讨论', exact: true }).click();
  await expect(page.getByRole('heading', { name: '圆桌讨论', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '开始圆桌讨论' })).toBeVisible();
  await expect(page.getByText('本次互动仅保留在临时空间', { exact: false })).toBeVisible();
  await page.getByLabel('当前工作空间').selectOption(wid);
  await expect(page.locator('.context-bar')).toContainText(renamed);
  await page.getByRole('link', { name: '我教 AI', exact: true }).click();
  await expect(page.getByRole('heading', { name: '我教 AI', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '开始我教 AI' })).toBeVisible();
  await page.getByRole('link', { name: '成长证据', exact: true }).click();
  await page.getByRole('tab', { name: '当前空间局部' }).click();
  await expect(page.getByText('尚无评分', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator('.context-bar')).toContainText(renamed);
  mkdirSync('test-results/screens', { recursive: true });
  await page.screenshot({ path: 'test-results/screens/student-evidence.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('管理端真实建号、重置密码、禁用使学生会话失效', async ({ page, browser }) => {
  await login(page, 'admin', 's03.admin');
  await page.getByRole('link', { name: '学生账号', exact: true }).click();
  await page.getByRole('button', { name: '创建学生账号', exact: true }).click();
  const username = 's03.new.' + Date.now();
  await page.getByLabel('学生姓名').fill('新同学');
  await page.getByLabel('学生账号', { exact: true }).fill(username);
  await page.getByLabel('初始密码', { exact: false }).fill(password);
  await page.getByRole('button', { name: '保存学生账号' }).click();
  const row = page.getByRole('row').filter({ hasText: username });
  await expect(row).toBeVisible();
  const studentContext = await browser.newContext();
  const student = await studentContext.newPage();
  try {
    await login(student, 'student', username);
    await row.getByRole('button', { name: '重置密码' }).click();
    const newPassword = 'S03-new-password-456!';
    await page.getByLabel('新密码', { exact: false }).fill(newPassword);
    await page.getByRole('button', { name: '确认重置' }).click();
    await expect(page.getByRole('status')).toContainText('密码已重置');
    await student.reload();
    await expect(student.getByRole('button', { name: '登录', exact: true })).toBeVisible();
    await login(student, 'student', username, newPassword);
    await row.getByRole('button', { name: '禁用', exact: true }).click();
    await page.getByRole('button', { name: '确认禁用' }).click();
    await expect(row.getByText('已禁用', { exact: true }).first()).toBeVisible();
    await student.reload();
    await expect(student.getByRole('button', { name: '登录', exact: true })).toBeVisible();
    await page.screenshot({ path: 'test-results/screens/admin-students.png', fullPage: true });
  } finally { await studentContext.close(); }
});

test('切换学生不泄露上一个账号项目，错误工作空间不可访问', async ({ page }) => {
  await login(page, 'student', 's03.one');
  const project = '隔离检查 ' + Date.now();
  const wid = await createProject(page, project);
  await page.getByRole('button', { name: '退出登录' }).click();
  await login(page, 'student', 's03.two');
  await page.getByRole('link', { name: '项目与会话', exact: true }).click();
  await expect(page.getByRole('heading', { name: project, exact: true })).toHaveCount(0);
  await page.goto('/student/study?workspace=' + wid);
  await expect(page.getByRole('heading', { name: '这个工作空间暂不可访问' })).toBeVisible();
  await expect(page.locator('.context-bar')).toHaveCount(0);
});

test('错误密码、角色边界与深层路由', async ({ page }) => {
  await page.goto('/student/roundtable');
  await page.getByLabel('账号', { exact: true }).fill('s03.one');
  await page.getByLabel('密码', { exact: true }).fill('wrong-password');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('账号或密码不正确');
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible();
  await expect(page.getByRole('link', { name: '学生账号', exact: true })).toHaveCount(0);
  await page.goto('/admin/students');
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(0);
  await page.getByLabel('账号', { exact: true }).fill('s03.one');
  await page.getByLabel('密码', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('请使用管理员账号');
  await page.goto('/student/');
  await expect(page.locator('.app-shell')).toBeVisible();
});

test('请求失败可重试，加载期间不展示虚构空态', async ({ page }) => {
  await login(page, 'student', 's03.two');
  await page.route('**/api/v1/student/workspaces?*', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'test_outage', message: '测试中的服务暂不可用', request_id: 'test', retryable: true } }) }));
  await page.reload();
  await expect(page.getByRole('alert')).toContainText('测试中的服务暂不可用');
  await expect(page.getByText('还没有学习项目', { exact: true })).toHaveCount(0);
  await page.unroute('**/api/v1/student/workspaces?*');
  await page.getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.getByRole('heading', { name: /今天从哪里开始/ })).toBeVisible();
});

test('手机布局、键盘弹窗、核心入口没有横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/student/');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await login(page, 'student', 's03.one');
  await page.getByRole('button', { name: '新建学习项目', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  for (const name of ['圆桌讨论', '我教 AI', '成长证据', '项目与会话']) {
    await page.getByRole('button', { name: '打开导航' }).click();
    await page.getByRole('link', { name, exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await expect(page.getByRole('complementary', { name: '学习导航' })).not.toBeVisible();
  await expect(page.locator('.skip-link')).not.toBeFocused();
  await page.screenshot({ path: 'test-results/screens/student-mobile.png', fullPage: true, animations: 'disabled' });
});

test('导航保留全部后续功能且没有运行时错误', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await login(page, 'admin', 's03.admin');
  for (const name of ['知识点与题库', '内容与解答复核', '运行记录', '策略评审', '配置与权限']) {
    await page.getByRole('link', { name, exact: true }).click();
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
  }
  await page.reload();
  await expect(page.getByRole('heading', { name: '配置与权限', exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test('跨标签页切换身份时旧页面不继续展示前一个学生项目', async ({ page, context }) => {
  await login(page, 'student', 's03.one');
  const title = '跨标签隔离 ' + Date.now();
  await createProject(page, title);
  const other = await context.newPage();
  await other.goto('/student/');
  await expect(other.getByRole('button', { name: '退出登录' })).toBeVisible();
  await other.getByRole('button', { name: '退出登录' }).click();
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible();
  await login(other, 'student', 's03.two');
  await expect(page.getByRole('heading', { name: '这个工作空间暂不可访问' })).toBeVisible();
  await expect(page.locator('.context-bar')).toHaveCount(0);
  await other.close();
});
