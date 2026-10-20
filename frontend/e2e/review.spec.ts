import {navigateStudent} from './navigation-helper';
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
async function navigate(page: Page, name: string) { await navigateStudent(page,name); }
async function enable(page: Page) {
  await login(page, 'admin', 's03.admin');
  await navigate(page, '知识点与题库');
  await expect(page.getByRole('heading', { name: '一元一次方程与应用 · 开发单元' })).toBeVisible();
  if (await page.getByRole('button', { name: '启用开发测试', exact: true }).count()) {
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: '启用开发测试', exact: true }).click();
  }
  await expect(page.getByText('开发测试已启用', { exact: true })).toBeVisible();
}
async function space(page: Page, title: string, temporary = false) {
  await navigate(page, '项目与会话');
  await page.getByRole('button', { name: temporary ? '新建临时会话' : '新建学习项目', exact: true }).click();
  await page.getByLabel('空间名称').fill(title);
  await page.getByRole('button', { name: '保存空间' }).click();
  await expect(page.locator('.context-bar')).toContainText(title);
}
async function answer(page: Page, value: string) {
  await page.getByLabel('我的回答').fill(value);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText('等待你的思考', { exact: true })).toBeVisible();
}

test('真实复习、来源自述、局部记忆和归档项目全局追溯', async ({ page, browser }) => {
  test.setTimeout(70_000);
  const admin = await browser.newPage();
  try { await enable(admin); } finally { await admin.close(); }
  await login(page, 'student', 's03.one');
  const title = '复习来源验收 ' + Date.now();
  await space(page, title);
  await navigate(page, '学习计划');
  await page.getByLabel('这次想学会什么？').fill('理解方程并保留可复习的尝试');
  await page.getByRole('button', { name: '生成计划草案' }).click();
  await page.getByRole('button', { name: '我确认这份计划' }).click();
  await expect(page.getByText('计划已确认', { exact: true })).toBeVisible();
  await navigate(page, '津津陪学');
  await confirmFixtureCourse(page);
  await page.getByRole('button', { name: '开始开发单元' }).click();
  await expect(page.getByRole('heading', { name: '先试一题', exact: true })).toBeVisible();
  await answer(page, '3');
  await page.getByRole('button', { name: '继续下一步' }).click();
  await expect(page.locator('.chat-current[data-task-id=\"P-EQ-01\"]')).toBeVisible();
  await answer(page, '0');
  await expect(page.locator('.chat-message.assistant').filter({hasText:'最终数值未通过；请检查条件与运算。'})).toBeVisible();
  await navigate(page, '错题与复习');
  await page.getByRole('button', { name: '查看原始尝试', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('我的答案：0');
  await page.getByLabel('补充或纠正说明（学生自述）').fill('这是当时的输入失误，请保留我的补充说明');
  await page.getByRole('button', { name: '保存我的说明' }).click();
  await expect(page.getByRole('dialog').getByText('学生自述：这是当时的输入失误，请保留我的补充说明')).toBeVisible();
  await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
  await page.getByRole('button', { name: '无提示复做', exact: true }).click();
  await page.getByLabel('复做数值答案').fill('6');
  await page.getByRole('button', { name: '提交无提示复做' }).click();
  await expect(page.getByRole('dialog')).toContainText('已保存结果并安排下一次回访');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: '已安排', exact: true }).click();
  await expect(page.getByText('下次建议时间：', { exact: false })).toBeVisible();
  await page.getByRole('tab', { name: '学习记忆', exact: true }).click();
  await expect(page.getByRole('heading', { name: '观察记忆', exact: true })).toBeVisible();
  await expect(page.getByText('这是当时的输入失误，请保留我的补充说明', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '全局正式项目', exact: true }).click();
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  await navigate(page, '项目与会话');
  await page.getByRole('button', { name: '归档 ' + title, exact: true }).click();
  await page.getByRole('button', { name: '确认归档', exact: true }).click();
  await expect(page.locator('.context-bar')).toContainText('已归档');
  await navigate(page, '错题与复习');
  await page.getByRole('tab', { name: '学习记忆', exact: true }).click();
  await page.getByRole('tab', { name: '全局正式项目', exact: true }).click();
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole('tab', { name: '学习记忆', exact: true }).click();
  mkdirSync('test-results/screens', { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/screens/student-memory.png', fullPage: true });
});

test('手机临时复习、独立诊断隔离、全局排除临时空间', async ({ page, browser }) => {
  const admin = await browser.newPage();
  try { await enable(admin); } finally { await admin.close(); }
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'student', 's03.one');
  const title = '仅局部复习 ' + Date.now();
  await space(page, title, true);
  await navigate(page, '津津陪学');
  await confirmFixtureCourse(page);
  await page.getByRole('button', { name: '开始开发单元' }).click();
  await expect(page.getByRole('heading', { name: '先试一题', exact: true })).toBeVisible();
  await answer(page, '0');
  await expect(page.locator('.chat-message.assistant').filter({hasText:'最终数值未通过；请检查条件与运算。'})).toBeVisible();
  await navigate(page, '错题与复习');
  await page.getByRole('button', { name: '无提示复做', exact: true }).click();
  await page.getByLabel('复做数值答案').fill('3');
  await page.getByRole('button', { name: '提交无提示复做' }).click();
  await expect(page.getByRole('dialog')).toContainText('当前空间正在诊断或独立检测');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await navigate(page, '津津陪学');
  await page.getByRole('button', { name: '继续下一步' }).click();
  await expect(page.locator('.chat-current[data-task-id=\"P-EQ-01\"]')).toBeVisible();
  await navigate(page, '错题与复习');
  await page.getByRole('button', { name: '无提示复做', exact: true }).click();
  await page.getByLabel('复做数值答案').fill('3');
  await page.getByRole('button', { name: '提交无提示复做' }).click();
  await expect(page.getByRole('dialog')).toContainText('已保存结果并安排下一次回访');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('tab', { name: '学习记忆', exact: true }).click();
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '全局正式项目', exact: true }).click();
  await expect(page.getByRole('heading', { name: title, exact: true })).toHaveCount(0);
  await page.getByRole('tab', { name: '当前空间记忆', exact: true }).click();
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  mkdirSync('test-results/screens', { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/screens/student-memory-mobile.png', fullPage: true });
});
