import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

test('task archive, delete preview cancellation, trash and restoration use isolated data', async ({page}) => {
  const credentials=JSON.parse(readFileSync(path.resolve(__dirname,'../../data/browser-qa/credentials.json'),'utf8'));
  await page.goto('/login');
  await page.getByLabel('账户',{exact:true}).fill(credentials.login_name);
  await page.getByLabel('密码',{exact:true}).fill(credentials.password);
  await page.getByRole('button',{name:'进入工作台'}).click();
  await expect(page.getByRole('heading',{name:'发现工具',exact:true})).toBeVisible();
  const me=(await (await page.request.get('/api/v1/auth/me')).json()).data;
  const post=async (route:string,body:unknown)=>{
    const response=await page.request.post('/api/v1'+route,{data:body,headers:{Origin:'http://127.0.0.1:13060','X-CSRF-Token':me.csrf_token,'Idempotency-Key':randomUUID()}});
    expect(response.ok()).toBeTruthy();
    return (await response.json()).data;
  };
  const job=async(id:string)=>{
    await expect.poll(async()=> (await (await page.request.get('/api/v1/jobs/'+id)).json()).data.status).toBe('succeeded');
    return (await (await page.request.get('/api/v1/jobs/'+id)).json()).data;
  };
  const imported=await job((await post('/imports',{repository_url:'https://github.com/demo/lifecycle-tool'})).job_id);
  const analyzed=await job((await post('/snapshots/'+imported.result_ref.snapshot_id+'/analyses',{audience:'生命周期验收观众'})).job_id);
  const analysis=(await (await page.request.get('/api/v1/analyses/'+analyzed.result_ref.analysis_id)).json()).data;
  const review=await post('/analyses/'+analysis.id+'/reviews',{primary_highlight_id:analysis.highlights[0].id,confirmed:true});
  const task=await post('/tasks',{review_id:review.id,audience:analysis.audience});
  await job((await post(`/tasks/${task.id}/generations`,{task_id:task.id,mode:'joint',expected_revision:task.revision})).job_id);
  await page.goto('/tasks/'+task.id);
  await page.getByText('任务管理 · 归档与回收站',{exact:true}).click();
  const manager=page.getByRole('region',{name:'任务管理',exact:true});
  const card=manager.getByTestId('managed-'+task.id);
  const findCard=async()=>{
    await expect(manager).toBeVisible();
    for(let i=0;i<30;i++){
      await expect(manager.getByText('正在读取任务…',{exact:true})).toHaveCount(0);
      if(await card.count())return;
      const next=manager.getByRole('button',{name:'下一页任务',exact:true});
      await expect(next).toBeEnabled();
      await next.click();
    }
    throw new Error('Task not found in manager pagination');
  };
  await findCard();
  await card.getByRole('button',{name:'归档任务',exact:true}).click();
  await expect(manager.getByRole('status')).toContainText('任务已归档');
  await manager.getByLabel('任务范围').selectOption('archived');
  await findCard();
  await card.getByRole('button',{name:'查看已归档素材'}).click();
  await expect(manager.getByRole('region',{name:'已归档素材'})).toContainText('每天分享好用的工具');
  await expect(manager.getByRole('link',{name:'下载文字'}).first()).toBeVisible();
  await card.getByRole('button',{name:'恢复任务',exact:true}).click();
  await expect(manager.getByRole('status')).toContainText('已恢复');
  await manager.getByLabel('任务范围').selectOption('active');
  await findCard();
  await card.getByRole('button',{name:'预览删除范围'}).click();
  const dialog=manager.getByRole('dialog',{name:'确认任务删除范围'});
  await expect(dialog).toContainText('2 个文本版本');
  await dialog.getByRole('button',{name:'取消删除'}).click();
  await expect(card).toBeVisible();
  await card.getByRole('button',{name:'预览删除范围'}).click();
  await dialog.getByRole('button',{name:'确认移入回收站'}).click();
  await expect(manager.getByRole('status')).toContainText('已移入回收站');
  expect((await page.request.get(`/api/v1/tasks/${task.id}/texts`)).status()).toBe(404);
  await page.reload();
  await expect(page.getByRole('heading',{name:'创作任务',exact:true})).toBeVisible();
  await page.getByText('任务管理 · 归档与回收站',{exact:true}).click();
  await manager.getByLabel('任务范围').selectOption('trashed');
  await findCard();
  await expect(card).toContainText('恢复截止');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
  await page.screenshot({path:'../data/browser-qa/b01-task-trash-mobile.png',fullPage:true});
  await card.getByRole('button',{name:'恢复任务',exact:true}).click();
  await expect(manager.getByRole('status')).toContainText('已恢复');
  const restored=(await (await page.request.get(`/api/v1/tasks/${task.id}/texts`)).json()).data;
  expect(restored.versions).toHaveLength(2);
});
