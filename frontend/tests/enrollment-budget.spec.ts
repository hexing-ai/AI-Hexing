import {test,expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import path from 'node:path';

// Only the isolated mock backend is used. Route overlays exercise the live-price dialog UI.
test('free enrollment requires confirmation and synthesis has a separate price',async({page})=>{
  const directory=path.resolve(__dirname,'../../data/browser-qa');
  const credentials=JSON.parse(readFileSync(path.join(directory,'credentials.json'),'utf8'));
  await page.route('**/api/v1/capabilities',async route=>{
    const response=await route.fetch();const body=await response.json();
    expect(body.data.mode).toBe('mock');body.data.mode='live';await route.fulfill({response,json:body});
  });
  await page.route('**/api/v1/cost-authorizations/preview',async route=>{
    const response=await route.fetch();const body=await response.json();
    const action=route.request().postDataJSON().action;
    body.data.maximum_cost=action==='voice_enrollment'?'0.000000':'0.010000';
    body.data.budget={single_limit_micros:1000000,monthly_limit_micros:30000000,remaining_micros:30000000};
    body.data.quote={source:'https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide',price_version:'QA-only',notice:action==='voice_enrollment'?'创建音色免费；本次会向百炼发送本人录音，后续试听和合成单独计费、另行确认。':undefined};
    await route.fulfill({response,json:body});
  });
  let submissions=0;const authorized:number[]=[];
  page.on('request',request=>{
    if(request.method()!=='POST')return;
    if(request.url().endsWith('/api/v1/validation-runs'))submissions++;
    if(request.url().endsWith('/api/v1/cost-authorizations'))authorized.push(request.postDataJSON().max_cost);
  });
  await page.goto('/login');await page.getByLabel('账户',{exact:true}).fill(credentials.login_name);await page.getByLabel('密码',{exact:true}).fill(credentials.password);await page.getByRole('button',{name:'进入工作台'}).click();
  await expect(page.getByRole('heading',{name:'发现工具',exact:true})).toBeVisible();
  await page.getByRole('link',{name:'接入实验'}).click();
  await page.getByLabel('样本录音',{exact:true}).setInputFiles(path.join(directory,'sample.wav'));
  const deadline=new Date(Date.now()+3600000);
  await page.getByLabel('本次样本清理时间').fill(new Date(deadline.getTime()-deadline.getTimezoneOffset()*60000).toISOString().slice(0,16));
  await page.getByRole('checkbox').check();await page.getByRole('button',{name:'保存样本',exact:true}).click();
  await expect(page.getByText('北京 CosyVoice 创建音色免费；后续试听和配音单独计费，提交前会分别确认。')).toBeVisible();
  await page.getByRole('button',{name:'创建一次实验音色'}).click();
  const dialog=page.getByRole('dialog',{name:'确认本次真实调用'});
  await expect(dialog).toContainText('¥0.000000');await expect(dialog).toContainText('后续试听和合成单独计费');
  expect(submissions).toBe(0);await dialog.getByRole('button',{name:'取消',exact:true}).click();
  await expect(dialog).not.toBeVisible();expect(submissions).toBe(0);expect(authorized).toEqual([]);
  await page.getByRole('button',{name:'创建一次实验音色'}).click();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await dialog.screenshot({path:path.join(directory,'b04-zero-price-dialog.png')});
  await dialog.getByRole('button',{name:'确认金额并继续一次'}).click();
  await expect(page.getByRole('button',{name:'合成短句试听'})).toBeEnabled({timeout:20000});
  expect(submissions).toBe(1);expect(authorized).toEqual([0]);
  await page.getByRole('button',{name:'合成短句试听'}).click();
  await expect(dialog).toContainText('¥0.010000');await expect(dialog).not.toContainText('创建音色免费');
  await dialog.getByRole('button',{name:'取消',exact:true}).click();
  await expect(dialog).not.toBeVisible();expect(submissions).toBe(1);expect(authorized).toEqual([0]);
});
