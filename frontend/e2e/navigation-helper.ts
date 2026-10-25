import {expect,type Page} from '@playwright/test';
/** Exercise preserved activity deep links after the student navigation reduction. */
export async function navigateStudent(page:Page,name:string){
 await expect(page.getByRole('dialog')).toHaveCount(0);
 const current=new URL(page.url());
 const routes:Record<string,string>={'学习计划':'plan','津津陪学':'study','数学单元':'units','圆桌讨论':'roundtable','我教 AI':'teach','规划与策略':'planner','Skill 离线改进':'skill-evolution'};
 if(current.pathname.startsWith('/student')&&routes[name]){
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const space=current.searchParams.get('workspace');await page.goto('/student/'+routes[name]+(space?'?workspace='+space:''));return;
 }
 const names:Record<string,string>={'项目与会话':'学习空间','成长证据':'成长','错题与复习':'错题与复习','今日学习':'今日学习'};
 if((page.viewportSize()?.width??1440)<700&&!await page.getByRole('complementary').first().isVisible())await page.getByRole('button',{name:'打开导航'}).click();
 await page.getByRole('link',{name:current.pathname.startsWith('/student')?names[name]??name:name,exact:true}).click();
}
