import {test, expect, type Page} from '@playwright/test';

const script = {id:'script-reading',kind:'script',script_id:null,content:{pain:'想更清楚地讲一个工具？',intro:'每天分享好用的工具，今天讲阅读测试。',feature:'按已复核资料整理四段正文。',example:'先查看来源，再复制正文。'},valid:true,confirmed_at:'2026-09-10T00:00:00Z',context_stale:false,evidence_map:{},issues:[]};
const copy = {id:'copy-reading',kind:'copy',script_id:script.id,content:{title:'阅读测试标题',body:'保留原文换行。\n\n这是一段隔离测试文案。',tags:['内容创作','前端测试']},valid:true,confirmed_at:null,context_stale:false,evidence_map:{},issues:[]};
const task = {id:'task-reading',tool_id:'tool',snapshot_id:'snapshot',review_id:'review',audience:'阅读与恢复工程验收',revision:1,status:'in_progress',materials_ready:false,current_script:script,current_copy:copy,current_audio:{status:'时长未达标',confirmed_at:null},audio_stale:false,copy_stale:false};
const data = {task,template_intro:script.content.intro,versions:[copy,script],groups:[]};
async function mockWorkspace(page:Page, options:{tasks?:()=>Promise<{status?:number;data?:unknown}>;texts?:()=>{status?:number;data?:unknown}}={}) {
  await page.route('**/api/v1/**',async route=>{
    const pathname=new URL(route.request().url()).pathname.replace('/api/v1','');
    let value:unknown={items:[],next_cursor:null};let status=200;
    if(pathname==='/auth/me')value={csrf_token:'isolated-ui-test'};
    else if(pathname==='/capabilities')value={mode:'mock',contracts:[],validation_tools_enabled:true};
    else if(pathname==='/tasks'){const result=options.tasks?await options.tasks():{data:{items:[task],next_cursor:null}};value=result.data;status=result.status||200;}
    else if(pathname==='/tasks/task-reading')value=task;
    else if(pathname==='/tasks/task-reading/audio')value={task:{...task,data_mode:'mock'},materials:{ready:false,missing:['达标配音','文案确认'],audio:null},versions:[],voices:[],exports:[]};
    else if(pathname==='/tasks/task-reading/texts'){const result=options.texts?.()||{data};value=result.data;status=result.status||200;}
    await route.fulfill({status,json:status===200?{data:value}:{error:{message:'测试中的临时连接失败'}}});
  });
}

test('readable manuscript, precise statuses, separate copies and four responsive widths',async({page,context})=>{
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  const errors:string[]=[];const writes:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/v1/')&&r.method()!=='GET')writes.push(r.method());});
  await mockWorkspace(page);await page.goto('/tasks/task-reading');
  const studio=page.locator('.text-studio');
  await expect(studio.getByLabel('四段口播正文')).toContainText(script.content.feature);
  await expect(studio.locator('.manuscript-card').first()).toHaveAttribute('id','text-version-'+script.id);
  await expect(studio.locator('.manuscript-raw pre').first()).not.toBeVisible();
  await expect(page.getByLabel('任务素材状态')).toContainText('口播 · 已确认');
  await expect(page.getByLabel('任务素材状态')).toContainText('文案 · 草稿');
  await expect(page.getByLabel('任务素材状态')).toContainText('音频 · 时长未达标');
  for(const [button,text] of [['复制标题',copy.content.title],['复制话题','#内容创作 #前端测试'],['复制发布正文',copy.content.body],['复制连续正文',Object.values(script.content).join('\n')]]){
    await studio.getByRole('tab',{name:button==='复制连续正文'?'口播':'发布文案',exact:true}).click();
    await studio.getByRole('button',{name:button,exact:true}).click();
    await expect(studio.getByRole('status')).toContainText('已复制');
    expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe(text);
  }
  await studio.getByRole('tab',{name:'发布文案',exact:true}).click();
  for(const width of [390,768,1280,1440]){
    await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
    const bounds=await studio.getByRole('button',{name:'复制话题',exact:true}).boundingBox();expect(bounds!.height).toBeGreaterThanOrEqual(44);
    await page.screenshot({path:`../data/browser-qa/f01-reading-${width}.png`,fullPage:true});
  }
  await studio.getByRole('tab',{name:'口播',exact:true}).click();
  await studio.getByText('查看结构化原文',{exact:true}).first().focus();await page.keyboard.press('Enter');
  await expect(studio.locator('.manuscript-raw pre').first()).toBeVisible();
  await page.reload();await expect(page.locator('.text-studio').getByLabel('四段口播正文')).toContainText(script.content.pain);
  expect(writes).toEqual([]);expect(errors).toEqual([]);
});

test('task loading and failure are distinct from empty; retry keeps authentication',async({page})=>{
  let phase:'loading'|'failed'|'empty'='loading';let release:()=>void=()=>{};
  const gate=new Promise<void>(resolve=>{release=resolve;});
  await mockWorkspace(page,{tasks:async()=>{if(phase==='loading')await gate;return phase==='failed'?{status:503}:{data:{items:[],next_cursor:null}};}});
  await page.goto('/tasks');await expect(page.getByText('正在读取任务…',{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'还没有创作任务'})).not.toBeVisible();
  phase='failed';release();await expect(page.getByRole('button',{name:'重新读取任务'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'还没有创作任务'})).not.toBeVisible();
  await expect(page.getByRole('heading',{name:'登录工作台'})).not.toBeVisible();
  phase='empty';await page.getByRole('button',{name:'重新读取任务'}).click();
  await expect(page.getByRole('heading',{name:'还没有创作任务'})).toBeVisible();
  await expect(page.getByRole('button',{name:'重新读取任务'})).not.toBeVisible();
});

test('manuscript read failure recovers and later disconnection keeps original result',async({page})=>{
  let failed=true;
  await mockWorkspace(page,{texts:()=>failed?{status:503}:{data}});
  await page.goto('/tasks/task-reading');const studio=page.locator('.text-studio');
  await expect(studio.getByRole('alert')).toContainText('稿件读取失败');
  failed=false;await studio.getByRole('button',{name:'重新读取稿件'}).click();
  await studio.getByRole('tab',{name:'发布文案',exact:true}).click();
  await expect(studio.getByLabel('发布文案正文')).toContainText(copy.content.body);
  await studio.getByRole('tab',{name:'口播',exact:true}).click();
  failed=true;await expect(studio.getByRole('alert')).toContainText('已显示的内容保留');
  await expect(studio.getByLabel('四段口播正文')).toContainText(script.content.pain);
  failed=false;await studio.getByRole('button',{name:'重新读取稿件'}).click();await expect(studio.getByRole('alert')).not.toBeVisible();
});
