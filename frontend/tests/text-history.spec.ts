import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';

test('history stays folded; unchanged save preserves version; cleanup preview protects current', async ({page}) => {
  const credentials=JSON.parse(readFileSync(path.resolve(__dirname,'../../data/browser-qa/credentials.json'),'utf8'));
  await page.goto('/login');
  await page.getByLabel('账户',{exact:true}).fill(credentials.login_name);
  await page.getByLabel('密码',{exact:true}).fill(credentials.password);
  await page.getByRole('button',{name:'进入工作台'}).click();
  await expect(page.getByRole('heading',{name:'发现工具',exact:true})).toBeVisible();
  // Set up only an isolated mock task, using the same private API and CSRF boundary.
  const taskId=await page.evaluate(async()=>{
    const me=await (await fetch('/api/v1/auth/me')).json();
    const post=async(path:string,body:unknown)=>{
      const response=await fetch('/api/v1'+path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':me.data.csrf_token,'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(body)});
      if(!response.ok)throw new Error(`Setup failed: ${response.status}`);
      return (await response.json()).data;
    };
    const tasks=(await (await fetch('/api/v1/tasks')).json()).data.items;
    const base=tasks[0];
    if(!base)throw new Error('Run creation workflow first to provide an isolated mock review.');
    const task=await post('/tasks',{review_id:base.review_id,audience:base.audience});
    const snapshot=(await (await fetch('/api/v1/snapshots/'+task.snapshot_id)).json()).data;
    const name=snapshot.metadata.full_name.split('/').pop();
    for(let i=0;i<3;i++)await post(`/tasks/${task.id}/texts`,{expected_revision:i+1,kind:'script',content:{pain:`版本${i}，想看看展示效果？`,intro:`每天分享好用的工具，今天讲${name}。`,feature:'支持 Markdown 页面预览，需要 Node.js。',example:'导入 Markdown 后预览。'},evidence_map:{feature:['f1'],example:['f1'],copy:['f1']}});
    sessionStorage.setItem('hexing-selected-task',task.id);
    return task.id as string;
  });
  await page.goto('/tasks/'+taskId);
  const studio=page.locator('.text-studio');
  const history=studio.locator('.history-versions');
  await expect(history).not.toHaveAttribute('open','');
  await expect(history.locator('summary').first()).toHaveText('历史候选与旧版本（2）');
  await expect(studio.getByRole('button',{name:'编辑口播',exact:true})).toHaveCount(1);
  const versionCount=async()=>page.evaluate(async id=>(await (await fetch(`/api/v1/tasks/${id}/texts`)).json()).data.versions.length,taskId);
  await studio.getByRole('button',{name:'编辑口播',exact:true}).click();
  await studio.getByRole('button',{name:'保存／重试',exact:true}).click();
  await expect(studio.getByRole('status')).toContainText('内容未变化');
  expect(await versionCount()).toBe(3);
  await studio.getByRole('button',{name:'结束编辑',exact:true}).click();
  await studio.getByRole('button',{name:'清理历史版本',exact:true}).click();
  const dialog=studio.getByRole('dialog');
  await expect(dialog).toContainText('可清理 2 条，保留 1 条');
  await dialog.getByRole('button',{name:'取消清理'}).click();
  expect(await versionCount()).toBe(3);
  await studio.getByRole('button',{name:'清理历史版本',exact:true}).click();
  await dialog.getByRole('button',{name:'确认清理 2 条旧版本'}).click();
  await expect(studio.getByRole('status')).toContainText('已清理 2 条旧版本');
  expect(await versionCount()).toBe(1);
  await page.reload();
  await expect(page.locator('.text-studio .history-versions summary').first()).toHaveText('历史候选与旧版本（0）');
  await expect(page.locator('.text-studio').getByLabel('四段口播正文').getByText(/版本2，想看看展示效果/)).toBeVisible();
});
