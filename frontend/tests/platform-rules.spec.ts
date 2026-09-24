import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

test('publication checks expose unknown limits and sources without changing confirmed copy',async({page})=>{
  const directory=path.resolve(__dirname,'../../data/browser-qa');const login=JSON.parse(readFileSync(path.join(directory,'credentials.json'),'utf8'));
  await page.goto('/login');await page.getByLabel('账户',{exact:true}).fill(login.login_name);await page.getByLabel('密码',{exact:true}).fill(login.password);await page.getByRole('button',{name:'进入工作台'}).click();
  await expect(page.getByRole('heading',{name:'发现工具',exact:true})).toBeVisible();
  const get=async(route:string)=>(await(await page.request.get('/api/v1'+route)).json()).data;
  const me=await get('/auth/me');
  const post=async(route:string,body:unknown)=>{const r=await page.request.post('/api/v1'+route,{data:body,headers:{Origin:'http://127.0.0.1:13060','X-CSRF-Token':me.csrf_token,'Idempotency-Key':randomUUID()}});expect(r.ok()).toBeTruthy();return(await r.json()).data;};
  const job=async(id:string)=>{await expect.poll(async()=>(await get('/jobs/'+id)).status).toBe('succeeded');return get('/jobs/'+id);};
  const imp=await job((await post('/imports',{repository_url:'https://github.com/demo/platform-'+randomUUID().slice(0,8)})).job_id);
  const analyzed=await job((await post('/snapshots/'+imp.result_ref.snapshot_id+'/analyses',{audience:'平台规则工程验收'})).job_id);
  const analysis=await get('/analyses/'+analyzed.result_ref.analysis_id);
  const review=await post('/analyses/'+analysis.id+'/reviews',{primary_highlight_id:analysis.highlights[0].id,confirmed:true});
  const task=await post('/tasks',{review_id:review.id,audience:analysis.audience});
  await job((await post('/tasks/'+task.id+'/generations',{task_id:task.id,mode:'joint',expected_revision:task.revision})).job_id);
  const items=(await get('/tasks/'+task.id+'/texts')).versions;
  for(const kind of ['script','copy']){
    const v=items.find((v:{kind:string})=>v.kind===kind);
    for(const action of ['adopt','confirm'])await post(`/tasks/${task.id}/texts/${v.id}/${action}`,{expected_revision:(await get('/tasks/'+task.id)).revision,...(action==='confirm'?{facts_reviewed:true}:{})});
  }
  const saved=await get('/tasks/'+task.id+'/texts');const copy=saved.versions.find((v:{kind:string})=>v.kind==='copy');
  await page.goto('/tasks/'+task.id);
  await page.getByRole('tab',{name:'发布文案',exact:true}).click();
  const card=page.locator('#text-version-'+copy.id);
  await card.getByText('抖音发布检查 · 未核实',{exact:true}).click();
  const panel=card.getByRole('region',{name:'抖音发布检查'});
  await expect(panel).toContainText('手动复制发布');await expect(panel).toContainText('最近核查');await expect(panel).toContainText('核验：未核实');
  await expect(panel.getByText(/上限 未核实/)).toHaveCount(5);
  await expect(panel.getByRole('link',{name:'抖音创作者中心发布入口'})).toHaveAttribute('href','https://creator.douyin.com/creator-micro/content/upload');
  await expect(card.getByRole('heading',{name:/文案.*已确认/})).toBeVisible();
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await panel.screenshot({path:path.join(directory,'b05-publication-check-mobile.png')});
  // A UI-only expired/over-limit fixture must remain a warning and preserve approval.
  let state='partial';
  await page.route(`**/api/v1/tasks/${task.id}/texts`,async route=>{
    const response=await route.fetch();const body=await response.json();
    for(const v of body.data.versions){if(v.id!==copy.id)continue;
      v.platform_limits={...v.platform_limits,status:state,result:state==='partial'?'exceeded':'needs_review'};
      v.platform_limits.checks[0]={...v.platform_limits.checks[0],status:state==='partial'?'verified':'expired',maximum:state==='partial'?1:null,result:state==='partial'?'exceeded':'not_checked'};
    }
    await route.fulfill({response,json:body});
  });
  await expect(panel).toContainText('已超出部分已核实限制');await expect(card.getByRole('heading',{name:/文案.*已确认/})).toBeVisible();
  state='expired';await expect(card.getByText('抖音发布检查 · 规则已过期',{exact:true})).toBeVisible();
  await expect(panel).not.toContainText('已超出部分已核实限制');
  const final=await get('/tasks/'+task.id+'/texts');expect(final).toEqual(saved);
});
