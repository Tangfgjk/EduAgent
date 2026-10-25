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
async function create(page: Page, title: string, temporary = false) {
  await navigate(page, '项目与会话');
  await page.getByRole('button', { name: temporary ? '新建临时会话' : '新建学习项目', exact: true }).click();
  await page.getByLabel('空间名称').fill(title);
  await page.getByRole('button', { name: '保存空间', exact: true }).click();
  await expect(page.locator('.context-bar')).toContainText(title);
}
async function answer(page: Page, value: string) {
  await page.getByLabel('我的回答', { exact: true }).fill(value);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText('等待你的思考', { exact: true })).toBeVisible();
}

test('成长证据：真实阶段统计、可交互趋势、来源自述与归档只读', async ({ page, browser }) => {
  test.setTimeout(75_000);
  const admin = await browser.newPage();
  try {
    await login(admin, 'admin', 's03.admin');
    await navigate(admin, '知识点与题库');
    await expect(admin.getByRole('heading', { name: '一元一次方程与应用 · 开发单元', exact: true })).toBeVisible();
    if (await admin.getByRole('button', { name: '启用开发测试', exact: true }).count()) {
      await admin.getByRole('checkbox').check();
      await admin.getByRole('button', { name: '启用开发测试', exact: true }).click();
    }
    await expect(admin.getByText('开发测试已启用', { exact: true })).toBeVisible();
  } finally { await admin.close(); }
  await login(page, 'student', 's03.one');
  const title = '成长证据验收 ' + Date.now();
  await create(page, title);
  await navigate(page, '学习计划');
  await page.getByLabel('这次想学会什么？').fill('保留方程学习过程与独立检测依据');
  await page.getByRole('button', { name: '生成计划草案', exact: true }).click();
  await page.getByRole('button', { name: '我确认这份计划', exact: true }).click();
  await expect(page.getByText('计划已确认', { exact: true })).toBeVisible();
  await navigate(page, '津津陪学');
  await confirmFixtureCourse(page);
  await page.getByRole('button', { name: '开始开发单元', exact: true }).click();
  await answer(page, '3');
  await page.getByRole('button', { name: '继续下一步', exact: true }).click();
  await expect(page.locator('.chat-current[data-task-id=\"P-EQ-01\"]')).toBeVisible();
  await page.getByRole('button', { name: '给我一点提示', exact: true }).click();
  await expect(page.getByRole('button', { name: '给我一点提示', exact: true })).toBeEnabled();
  await answer(page, '6');
  await page.getByLabel('我的反思').fill('两边同时进行相同运算');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.locator('.chat-current[data-task-id=\"P-APP-01\"]')).toBeVisible();
  await answer(page, '7');
  await page.getByLabel('我的反思').fill('包装费只出现一次');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.locator('.chat-current[data-task-id=\"C-EQ-01\"]')).toBeVisible();
  await answer(page, '5');
  await navigate(page, '成长证据');
  await page.getByRole('combobox',{name:'查看范围',exact:true}).selectOption('workspace');
  await expect(page.getByRole('tab')).toHaveCount(5);
  await expect(page.locator('.growth-stats')).toContainText('7');
  await expect(page.getByRole('heading',{name:'知识点地图 · 记录覆盖'})).toHaveCount(0);
  await page.getByRole('tab',{name:'知识与表现',exact:true}).click();
  await expect(page.getByRole('heading',{name:'知识点地图 · 记录覆盖'})).toBeVisible();
  await page.getByRole('button',{name:/一元一次方程与应用.*7条事实/}).click();
  await page.getByRole('region',{name:'图形来源钻取'}).getByRole('button',{name:'查看这条原始材料'}).first().click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button',{name:'关闭弹窗',exact:true}).click();
  await expect(page).toHaveURL(/view=knowledge/);
  await page.getByRole('button',{name:'作答表现',exact:true}).click();
  await expect(page.locator('.observation-bars')).toContainText('辅助后练习');
  await expect(page.locator('.observation-bars')).toContainText('首次曝光检测');
  await page.getByRole('tab',{name:'复习与支持',exact:true}).click();
  await expect(page.getByRole('heading',{name:'复习矩阵 · 触发原因与到期状态'})).toBeVisible();
  await page.getByRole('button',{name:'帮助使用',exact:true}).click();
  await expect(page.getByRole('heading',{name:'支架时间线'})).toBeVisible();
  await page.getByRole('tab',{name:'素养材料',exact:true}).click();
  await expect(page.getByRole('group',{name:'六个观察方向候选材料条数'}).getByRole('button')).toHaveCount(6);
  await page.getByRole('button',{name:/逻辑推理.*条候选/}).click();
  await expect(page.getByText('逻辑推理 · 未评定',{exact:true})).toBeVisible();
  await page.getByRole('tab',{name:'学习记录',exact:true}).click();
  await expect(page.getByRole('heading',{name:'支架时间线'})).toHaveCount(0);
  await page.getByRole('combobox', { name: '记录类型', exact: true }).selectOption('checkpoint');
  await expect(page.locator('.evidence-records > li')).toHaveCount(1);
  await page.getByRole('button', { name: '查看原始来源', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('我的答案：5');
  await page.getByLabel('补充或纠正说明（学生自述）').fill('这一次我先独立代入核验');
  await page.getByRole('button', { name: '保存我的说明', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('学生自述：这一次我先独立代入核验');
  await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();

  await page.getByRole('combobox', { name: '记录类型', exact: true }).selectOption('self_report');
  await expect(page.locator('.evidence-records')).toContainText('这一次我先独立代入核验');
  await navigate(page, '项目与会话');
  await page.getByRole('button', { name: '归档 ' + title, exact: true }).click();
  await page.getByRole('button', { name: '确认归档', exact: true }).click();
  await navigate(page, '成长证据');
  await page.getByRole('combobox',{name:'查看范围',exact:true}).selectOption('workspace');
  await page.getByRole('tab',{name:'学习记录',exact:true}).click();
  await page.getByRole('combobox', { name: '记录类型', exact: true }).selectOption('self_report');
  await expect(page.locator('.evidence-records')).toContainText('已归档');
  await page.getByRole('button', { name: '查看原始来源', exact: true }).click();
  await expect(page.getByRole('button', { name: '保存我的说明', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
  await page.getByRole('combobox', { name: '记录类型', exact: true }).selectOption('all');
  await page.getByRole('combobox',{name:'查看范围',exact:true}).selectOption('global');
  await page.getByText('高级筛选',{exact:true}).click();
  await page.getByRole('checkbox',{name:'包含归档项目'}).uncheck();
  await expect(page.locator('.evidence-records > li').filter({hasText:title})).toHaveCount(0);
  await page.getByRole('checkbox',{name:'包含归档项目'}).check();
  await expect(page.locator('.evidence-records > li').filter({hasText:title}).first()).toBeVisible();
  mkdirSync('test-results/screens', { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/screens/student-evidence-live.png', fullPage: true });
});

test('手机证据：空态、来源筛选、时间切换及临时空间排除', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'student', 's03.one');
  const title = '局部空证据 ' + Date.now();
  await create(page, title, true);
  await navigate(page, '成长证据');
  await page.getByRole('combobox',{name:'查看范围',exact:true}).selectOption('workspace');
  await page.getByRole('tab',{name:'学习记录',exact:true}).click();
  await expect(page.getByText('这个范围还没有记录', { exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: '时间范围', exact: true }).selectOption('14');
  await page.getByRole('combobox', { name: '数据来源', exact: true }).selectOption('real_student');
  await page.getByRole('tab',{name:'素养材料',exact:true}).click();
  await expect(page.getByText('尚无评分', { exact: true })).toBeVisible();
  await page.getByRole('combobox',{name:'查看范围',exact:true}).selectOption('global');
  await expect(page.locator('.evidence-records')).toHaveCount(0);
  await page.getByRole('combobox', { name: '数据来源', exact: true }).selectOption('synthetic');
  await page.getByRole('tab',{name:'素养材料',exact:true}).click();
  await expect(page.getByText('尚无评分', { exact: true })).toBeVisible();
  await expect(page.locator('.evidence-records > li').filter({ hasText: title })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  mkdirSync('test-results/screens', { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/screens/student-evidence-mobile.png', fullPage: true });
});
