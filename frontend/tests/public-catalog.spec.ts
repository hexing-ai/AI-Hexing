import {test,expect,type APIRequestContext} from '@playwright/test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
const directory=path.resolve(__dirname,'../../data/browser-qa');
let admin:APIRequestContext;
let csrf:string;
let entryId:string;
let publicId:string;

async function write(url:string,body:unknown,method='POST'){
  const response=await admin.fetch('/api/v1'+url,{method,data:body,headers:{Origin:'http://127.0.0.1:13060','X-CSRF-Token':csrf,'Idempotency-Key':crypto.randomUUID()}});
  expect(response.ok()).toBeTruthy();return (await response.json()).data;
}
async function publish(){
  const p=(await (await admin.get('/api/v1/ranking/entries/'+entryId+'/preview')).json()).data;
  return write('/ranking/entries/'+entryId+'/publish',{expected_revision:p.entry_revision,draft_revision_id:p.draft_revision_id,content_hash:p.content_hash,content_reviewed:true});
}

test.beforeAll(async({playwright})=>{
  admin=await playwright.request.newContext({baseURL:'http://127.0.0.1:13060'});
  const credentials=JSON.parse(readFileSync(path.join(directory,'credentials.json'),'utf8'));
  const response=await admin.post('/api/v1/auth/login',{data:credentials,headers:{Origin:'http://127.0.0.1:13060'}});
  expect(response.ok()).toBeTruthy();csrf=(await response.json()).data.csrf_token;
  const fixture=JSON.parse(readFileSync(path.join(directory,'public-catalog-fixture.json'),'utf8'));
  const result=await write('/ranking/entries',{tool_id:fixture.tool_id,source_snapshot_id:fixture.snapshot_id,analysis_id:fixture.analysis_id});
  entryId=result.entry.id;publicId=result.entry.public_id;await publish();
});
test.afterAll(async()=>{
  if(entryId){const current=(await (await admin.get('/api/v1/ranking/entries/'+entryId)).json()).data;
    if(current.state==='published')await write('/ranking/entries/'+entryId+'/withdraw',{expected_revision:current.revision});}
  await admin.dispose();
});

test('anonymous introduction, separate events, update and withdrawal synchronization',async({page,context})=>{
  const requested:string[]=[];
  page.on('request',request=>{if(request.url().includes('/api/v1/'))requested.push(new URL(request.url()).pathname);});
  const current=(await (await admin.get('/api/v1/ranking/entries/'+entryId)).json()).data;
  const counts=async()=>(await (await admin.get('/api/v1/ranking/entries/'+entryId+'/stats')).json()).data;
  const before=await counts();
  await page.clock.install();
  await page.goto('/display');
  await expect(page.getByRole('heading',{name:'工具封神榜',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:new RegExp(current.content.name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'))})).toBeVisible();
  await page.getByRole('button').filter({hasText:current.content.name}).click();
  const dialog=page.getByRole('dialog');
  await expect(dialog.getByRole('heading',{name:current.content.name,exact:true})).toBeVisible();
  await expect.poll(async()=>(await counts()).intro_open).toBe(before.intro_open+1);
  expect((await counts()).github_click).toBe(before.github_click);
  expect(page.url()).toContain('/display');
  // Record the click but don't depend on external GitHub availability in this test.
  await context.route('https://github.com/**',route=>route.fulfill({contentType:'text/html',body:'<p>Isolated external-link target</p>'}));
  const popupPromise=page.waitForEvent('popup');
  await dialog.getByRole('link',{name:'打开 GitHub 仓库'}).click();
  const popup=await popupPromise;await popup.close();
  await expect.poll(async()=>(await counts()).github_click).toBe(before.github_click+1);
  await page.keyboard.press('Escape');await expect(dialog).not.toBeVisible();
  await expect(page.getByRole('button').filter({hasText:current.content.name})).toBeFocused();
  const changed=await write('/ranking/entries/'+entryId,{expected_revision:current.revision,content:{...current.content,name:'S4.2 更新测试工具'}},'PATCH');
  await page.getByRole('button',{name:'刷新工具列表'}).click();
  await expect(page.getByRole('button').filter({hasText:current.content.name})).toBeVisible();
  await publish();
  await page.clock.runFor(31000);
  await expect(page.getByRole('button').filter({hasText:'S4.2 更新测试工具'})).toBeVisible();
  await page.getByRole('button').filter({hasText:'S4.2 更新测试工具'}).click();
  await expect(dialog).toBeVisible();
  const latest=(await (await admin.get('/api/v1/ranking/entries/'+entryId)).json()).data;
  await write('/ranking/entries/'+entryId+'/withdraw',{expected_revision:latest.revision});
  await page.clock.runFor(31000);
  await expect(dialog).not.toBeVisible();
  await expect(page.getByText('该工具已下架，介绍已关闭。')).toBeVisible();
  await expect(page.getByRole('button').filter({hasText:'S4.2 更新测试工具'})).toHaveCount(0);
  expect(requested.every(p=>p.startsWith('/api/v1/public/tools'))).toBeTruthy();
  expect(changed.has_unpublished_changes).toBeTruthy();
});

test('stale data and failed event collection do not expose old links or block reading',async({page})=>{
  await publish();
  await page.clock.install();
  await page.route('**/events',route=>route.fulfill({status:503,contentType:'application/json',body:'{}'}));
  await page.goto('/display');
  const item=page.getByRole('button').filter({hasText:'S4.2 更新测试工具'});
  await item.click();
  const dialog=page.getByRole('dialog');
  await expect(dialog.getByRole('link',{name:'打开 GitHub 仓库'})).toBeVisible();
  await page.route('**/api/v1/public/tools**',route=>route.fulfill({status:503,contentType:'application/json',body:'{}'}));
  await page.clock.runFor(61000);
  await expect(page.getByText('公开资料已过期，刷新成功后继续查看。')).toBeVisible();
  await expect(dialog.getByRole('link',{name:'打开 GitHub 仓库'})).toHaveCount(0);
  await page.unroute('**/api/v1/public/tools**');
  await dialog.getByRole('button',{name:'刷新工具介绍'}).click();
  await expect(dialog.getByRole('link',{name:'打开 GitHub 仓库'})).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'刷新工具列表'}).click();
  await expect(item).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await item.click();
  await expect(dialog).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:path.join(directory,'s4-2-public-introduction-mobile.png')});
  await page.getByRole('button',{name:'关闭工具介绍'}).click();
  await page.setViewportSize({width:1440,height:900});
  await page.screenshot({path:path.join(directory,'s4-2-public-list.png'),fullPage:true});
  expect((await page.request.get('/api/v1/public/tools/'+publicId)).headers()['cache-control']).toBe('no-store');
});
