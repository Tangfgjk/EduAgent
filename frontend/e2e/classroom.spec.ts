import {test,expect,type Page} from '@playwright/test';
import {deflateSync} from 'node:zlib';
import {readFileSync} from 'node:fs';

function crc32(data:Buffer){let value=0xffffffff;for(const byte of data){value^=byte;for(let i=0;i<8;i++)value=(value>>>1)^((value&1)?0xedb88320:0);}return (value^0xffffffff)>>>0;}
function zip(manifest:unknown){
 const name=Buffer.from('manifest.json'),data=Buffer.from(JSON.stringify(manifest)),crc=crc32(data);
 const header=Buffer.alloc(30);header.writeUInt32LE(0x04034b50);header.writeUInt16LE(20,4);header.writeUInt32LE(crc,14);header.writeUInt32LE(data.length,18);header.writeUInt32LE(data.length,22);header.writeUInt16LE(name.length,26);
 const central=Buffer.alloc(46);central.writeUInt32LE(0x02014b50);central.writeUInt16LE(20,4);central.writeUInt16LE(20,6);central.writeUInt32LE(crc,16);central.writeUInt32LE(data.length,20);central.writeUInt32LE(data.length,24);central.writeUInt16LE(name.length,28);
 const end=Buffer.alloc(22);end.writeUInt32LE(0x06054b50);end.writeUInt16LE(1,8);end.writeUInt16LE(1,10);end.writeUInt32LE(central.length+name.length,12);end.writeUInt32LE(header.length+name.length+data.length,16);
 return Buffer.concat([header,name,data,central,name,end]);
}
function png(){
 function chunk(tag:string,data:Buffer){const label=Buffer.from(tag),head=Buffer.alloc(4),tail=Buffer.alloc(4);head.writeUInt32BE(data.length);tail.writeUInt32BE(crc32(Buffer.concat([label,data])));return Buffer.concat([head,label,data,tail]);}
 const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(1);ihdr.writeUInt32BE(1,4);ihdr[8]=8;ihdr[9]=2;
 return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ihdr),chunk('IDAT',deflateSync(Buffer.from([0,46,112,86]))),chunk('IEND',Buffer.alloc(0))]);
}
function wav(){const data=Buffer.alloc(44+16000);data.write('RIFF');data.writeUInt32LE(data.length-8,4);data.write('WAVEfmt ',8);data.writeUInt32LE(16,16);data.writeUInt16LE(1,20);data.writeUInt16LE(1,22);data.writeUInt32LE(8000,24);data.writeUInt32LE(16000,28);data.writeUInt16LE(2,32);data.writeUInt16LE(16,34);data.write('data',36);data.writeUInt32LE(16000,40);return data;}
async function login(page:Page,role:'student'|'admin'){
 await page.goto('/'+role+'/');await page.getByLabel('账号',{exact:true}).fill(role==='admin'?'s03.admin':'s03.one');await page.getByLabel('密码',{exact:true}).fill('S03-test-only-password!');await page.getByRole('button',{name:'登录',exact:true}).click();await expect(page.locator('.app-shell')).toBeVisible();
 const cookies=await page.context().cookies();return {'X-Wenjin-Client':role,'X-CSRF-Token':cookies.find(c=>c.name===`wenjin_${role}_csrf`)!.value};
}

test('课堂布局、KaTeX、真实本机音视频、互动及断点恢复，无外部生成调用',async({page},info)=>{
 test.setTimeout(90000);const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 const admin=await login(page,'admin');
 let model=await(await page.request.get('/api/v1/admin/model-runtime',{headers:admin})).json();
 expect((await page.request.post('/api/v1/admin/model-runtime/publish',{headers:admin,data:{expected_revision:model.revision,vendor:'fake',max_output_tokens:512,account_daily_calls:0,site_daily_calls:0,acknowledge_development_only:true}})).ok()).toBeTruthy();
 const policy=await(await page.request.get('/api/v1/admin/generation-policy',{headers:admin})).json();
 expect((await page.request.post('/api/v1/admin/generation-policy',{headers:admin,data:{expected_revision:policy.revision,courses:true,interaction:true,search:false,private_auto_release:false,acknowledge_development_only:true}})).ok()).toBeTruthy();
 const student=await login(page,'student');
 const space=await(await page.request.post('/api/v1/student/workspaces',{headers:{...student,'Idempotency-Key':crypto.randomUUID()},data:{kind:'temporary',title:'课堂媒体开发验收'}})).json();const wid=space.workspace_id;
 const root=`/api/v1/student/workspaces/${wid}/course-builds`;
 let build=await(await page.request.post(root,{headers:{...student,'Idempotency-Key':crypto.randomUUID()},data:{topic:'负数运算'}})).json();
 build=await(await page.request.post(root+'/'+build.build_id+'/diagnostic',{headers:student,data:{expected_revision:build.revision,answers:[{answer:'0'},{answer:'0'}]}})).json();
 expect((await page.request.post(root+'/'+build.build_id+'/generate',{headers:{...student,'Idempotency-Key':crypto.randomUUID()},data:{expected_revision:build.revision}})).ok()).toBeTruthy();
 await expect.poll(async()=>{const data=await(await page.request.get('/api/v1/admin/course-builds',{headers:admin})).json();build=data.items.find((r:{workspace_id:string})=>r.workspace_id===wid);return build.status;}).toBe('pending_review');
 expect((await page.request.post('/api/v1/admin/course-builds/'+build.build_id+'/review',{headers:admin,data:{expected_revision:build.revision,approve:true,note:'自编课堂媒体离线验收，核对数学内容与检测隔离，仅模拟开发'}})).ok()).toBeTruthy();
 build=await(await page.request.get(root+'/'+build.build_id,{headers:student})).json();
 expect((await page.request.post(`/api/v1/student/workspaces/${wid}/course`,{headers:student,data:{expected_revision:0,title:'自编课堂',topic:'负数运算',unit_ids:[build.unit_id],active_unit_id:build.unit_id}})).ok()).toBeTruthy();
 const video=readFileSync(new URL('./fixtures/self-authored-color-vp8.webm',import.meta.url)).toString('base64');
 const doc={formatVersion:1,stage:{name:'自编布局媒体课堂'},scenes:[0,1,2].map(i=>({id:'scene'+i,title:['符号与条件','运算顺序','独立检查'][i],content:{canvas:{viewportSize:1000,viewportRatio:.5625,elements:[
  {id:'title',type:'text',left:30,top:20,width:900,height:90,content:'<p style="font-size:36px">'+['符号与条件','运算顺序','独立检查'][i]+'</p>'},
  {id:'formula',type:'latex',left:30,top:120,width:900,height:80,latex:'a\\div b=a\\times\\frac{1}{b}\\quad(b\\ne0)'},
  {id:'image',type:'image',left:30,top:230,width:200,height:150,src:'data:image/png;base64,'+png().toString('base64')},
  {id:'audio',type:'audio',left:250,top:230,width:300,height:60,src:'data:audio/wav;base64,'+wav().toString('base64'),caption:'本机生成的1秒静音验收素材'},
  {id:'video',type:'video',left:600,top:220,width:320,height:180,src:'data:video/webm;base64,'+video,caption:'自编纯色离线验收素材，无教学效果含义'}]}},
  actions:[{type:'speech',text:'先独立检查条件与符号。'},{type:'hide',elementId:'image'},{type:'show',elementId:'image'},{type:'question',id:'q'+i,text:'负三的相反数是多少？',answer:'3'}]}))};
 const imported=await page.request.post('/api/v1/admin/course-builds/'+build.build_id+'/openmaic-import',{headers:{...admin,'Content-Type':'application/zip'},data:zip(doc)});expect(imported.ok()).toBeTruthy();
 const rid=(await imported.json()).resource_id;
 expect((await page.request.post('/api/v1/admin/resources/'+rid+'/review',{headers:admin,data:{expected_revision:1,approve:true,note:'离线自编布局、公式、图片、WAV、WebM与互动问题检查，仅开发验收'}})).ok()).toBeTruthy();
 const network:string[]=[];page.on('request',r=>{if(r.url().startsWith('http')&&!r.url().startsWith('http://127.0.0.1:8781/'))network.push(r.url());});
 await page.route('**/api/v1/student/workspaces/*/media-budgets',route=>route.fulfill({json:{items:[{budget_id:'closed-acceptance-budget',resource_id:rid,workspace_id:wid,enabled:false,revision:8,body:{reserved_microyuan:1107760},operations:[{operation_id:'internal-operation',slot:'audio:0',kind:'audio',status:'unknown',reserved_microyuan:2320,receipt:{billing_unknown:true}}]}]}}));
 await page.goto(`/student/classroom?workspace=${wid}`);await expect(page.getByRole('heading',{name:'互动课堂',exact:true})).toBeVisible();await expect(page.getByRole('link',{name:'互动课堂',exact:true})).toHaveAttribute('href',`/student/classroom?workspace=${wid}`);
 await expect(page.getByText('外部媒体预算与任务',{exact:true})).toHaveCount(0);await expect(page.getByText('closed-acceptance-budget',{exact:false})).toHaveCount(0);await expect(page.getByText('已预占',{exact:false})).toHaveCount(0);
 const player=page.getByRole('region',{name:'互动课堂播放器'});await expect(player).toBeVisible();await expect(player.locator('.katex').first()).toBeVisible();
 await expect.poll(()=>player.locator('audio').evaluate((n:HTMLAudioElement)=>n.readyState)).toBeGreaterThan(0);
 await expect.poll(()=>player.locator('video').evaluate((n:HTMLVideoElement)=>({ready:n.readyState,error:n.error?.message,length:n.currentSrc.length}))).toMatchObject({ready:expect.any(Number),error:undefined});
 await expect.poll(()=>player.locator('video').evaluate((n:HTMLVideoElement)=>n.readyState)).toBeGreaterThan(0);
 await player.locator('audio').evaluate(async(n:HTMLAudioElement)=>{await n.play();n.currentTime=.25;n.pause();});
 await player.getByRole('button',{name:'保存进度',exact:true}).click();await expect(player.getByText('进度已保存',{exact:true})).toBeVisible();
 await player.getByRole('button',{name:'下一步',exact:true}).click();await expect(player.locator('svg image')).toHaveCount(0);
 await player.getByRole('button',{name:'下一步',exact:true}).click();await expect(player.locator('svg image')).toHaveCount(1);
 await player.getByRole('button',{name:'下一步',exact:true}).click();await player.getByLabel('课堂互动回答').fill('这个答案是3');await player.getByRole('button',{name:'检查本次回答'}).click();
 await expect(player.getByText('课堂数值核验：数值符合参考',{exact:false})).toBeVisible();
 await player.getByRole('button',{name:'2. 运算顺序',exact:true}).click();await player.getByRole('button',{name:'保存进度',exact:true}).click();await expect(player.getByText('进度已保存',{exact:true})).toBeVisible();
 await page.reload();await expect(player.getByLabel('当前课堂页')).toContainText('第 2 / 3 页');
 await page.screenshot({path:info.outputPath('classroom-desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2)).toBeTruthy();await page.screenshot({path:info.outputPath('classroom-mobile.png'),fullPage:true});
 expect(network).toEqual([]);expect(errors).toEqual([]);await expect(player.locator('iframe')).toHaveCount(0);
});
