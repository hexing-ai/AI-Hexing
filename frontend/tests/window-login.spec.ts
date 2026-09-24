import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';

test('an existing login explicitly connects a separate cookie jar and survives refresh', async ({browser,page})=>{
  const credentials=JSON.parse(readFileSync(path.resolve(__dirname,'../../data/browser-qa/credentials.json'),'utf8'));
  await page.goto('/tasks');
  await page.getByLabel('账户',{exact:true}).fill(credentials.login_name);
  await page.getByLabel('密码',{exact:true}).fill(credentials.password);
  await page.getByRole('button',{name:'进入工作台'}).click();
  await expect(page.getByRole('heading',{name:'发现工具',exact:true})).toBeVisible();
  const other=await browser.newContext({baseURL:'http://127.0.0.1:13060'});
  try {
    const target=await other.newPage();
    await target.goto('/tasks');
    await target.getByText('已在其他窗口登录？',{exact:true}).click();
    await target.getByRole('button',{name:'生成本窗口配对码'}).click();
    const code=await target.getByTestId('window-pair-code').innerText();
    await expect(target.getByRole('heading',{name:'登录工作台',exact:true})).toBeVisible();
    await page.getByText('连接另一个窗口',{exact:true}).click();
    await page.getByLabel('目标窗口配对码').fill(code);
    await page.getByRole('button',{name:'确认连接这个窗口'}).click();
    await expect(page.getByText('已授权，目标窗口将自动进入工作台。',{exact:true})).toBeVisible();
    await expect(target.getByRole('heading',{name:'创作任务',exact:true})).toBeVisible({timeout:15000});
    await target.reload();
    await expect(target.getByRole('heading',{name:'创作任务',exact:true})).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading',{name:'发现工具',exact:true})).toBeVisible();
  } finally {await other.close();}
});
