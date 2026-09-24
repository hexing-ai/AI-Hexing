import { test, expect, type Page } from '@playwright/test';

const task={id:'task-activity',tool_id:'tool',snapshot_id:'snapshot',review_id:'review',audience:'测试观众',revision:1,status:'in_progress',current_script:null,current_copy:null,copy_stale:false};
const base={kind:'generation',kind_label:'正式文本生成',status:'succeeded',phase:'done',title:'demo/markdown-tool',created_at:'2026-09-09T00:00:00Z',updated_at:'2026-09-09T00:00:01Z',status_label:'已完成 · 候选待采用',needs_attention:false,task_id:task.id,result_ref:null,error:null,possible_charge:true,mode:'live'};
const candidate={...base,id:'candidate-job'};
const audio={...base,id:'audio-job',kind:'voice_preview',kind_label:'短句试听',title:'我的声音',task_id:null,status_label:'合成完成 · 可试听'};
const unknown={...audio,id:'unknown-job',status:'needs_reconciliation',status_label:'结果待核对',needs_attention:true};
const invalid={...base,id:'invalid-job',status_label:'已完成 · 有内容需修正',needs_attention:true};
const older=Array.from({length:19},(_,i)=>({...base,id:'old-'+i,title:'旧记录 '+i}));
const guidance='调用可能已经受理。请先核对请求记录及账单；不会自动重复调用。';
const version={id:'version-target',generation_id:'group-target',kind:'script',content:{pain:'目标候选痛点',intro:'栏目句',feature:'亮点',example:'示例'},script_id:null,valid:true,confirmed_at:null,evidence_map:{},issues:[]};

async function mockActivity(page:Page){
  const mutations:string[]=[];const errors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/v1/**',async route=>{
    const request=route.request();if(request.method()!=='GET')mutations.push(request.url());
    const url=new URL(request.url());const path=url.pathname.replace('/api/v1','');let data:unknown;
    if(path==='/auth/me')data={csrf_token:'mock-csrf'};
    else if(path==='/capabilities')data={mode:'live',contracts:[],contract_version:'S1-2026-09-08',validation_tools_enabled:true};
    // The linked task is older than the first page of tasks and must be fetched directly.
    else if(['/tasks','/probe-voices','/validation-runs'].includes(path))data={items:[],next_cursor:null};
    else if(path==='/tasks/'+task.id)data=task;
    else if(path===`/tasks/${task.id}/texts`)data={task,template_intro:'栏目句',versions:[{...version,id:'unrelated-version',generation_id:'other-group'},version],groups:[{id:'group-target',output:{raw_text:'生成原文',issues:[]}}]};
    else if(path==='/jobs'){
      const scoped=url.searchParams.has('task_id');const bucket=url.searchParams.get('bucket');
      let items=bucket==='attention'?(scoped?[invalid]:[unknown,invalid]):bucket==='history'?(scoped?[candidate]:[candidate,audio,...older]):[];
      const cursor=url.searchParams.get('cursor');if(cursor)items=items.slice(items.findIndex(j=>j.id===cursor)+1);
      const limit=Number(url.searchParams.get('limit')||50);data={items:items.slice(0,limit),next_cursor:items.length>limit?items[limit-1].id:null};
    }else if(path.startsWith('/jobs/')){
      const id=path.split('/').pop();const job=[candidate,audio,unknown,invalid,...older].find(j=>j.id===id);
      data={...job,guidance:id==='unknown-job'?guidance:'点击对应结果继续复核。',billing_note:'金额待核实；可按请求编号核对供应商记录。',attempts:[{number:1,request_id:'fixture-request',model:'fixture-model',usage:{total_tokens:10},elapsed_ms:1200}],actions:id==='candidate-job'?[{kind:'task',label:'查看本次候选',task_id:task.id,generation_id:'group-target'}]:[],result:id==='audio-job'?{asset_id:'fixture-audio',duration_ms:200,is_test_tone:true}:id==='invalid-job'?{issues:[{message:'请补充来源依据'}]}:undefined};
    }else if(path==='/assets/fixture-audio/content'){
      const wav=Buffer.alloc(44+3200);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(8000,24);wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(3200,40);
      await route.fulfill({contentType:'audio/wav',body:wav});return;
    }else {await route.fulfill({status:404,json:{error:{message:'Unmocked request '+path}}});return;}
    await route.fulfill({json:{data}});
  });
  return {mutations,errors};
}

for(const width of [1440,390])test(`processing records open exact candidates and scope by task ${width}`,async({page})=>{
  const {mutations,errors}=await mockActivity(page);await page.setViewportSize({width,height:900});
  await page.goto('/activity');
  const records=page.locator('.processing-records');const completed=records.locator('.completed-records');
  await expect(completed).not.toHaveAttribute('open','');
  await expect(records.locator('.activity-record')).toHaveCount(2);
  const pending=records.locator('.activity-record').filter({hasText:'结果待核对'});
  await pending.locator('summary').first().click();
  await expect(pending.getByText(guidance,{exact:true})).toBeVisible();
  await expect(pending.getByRole('button',{name:/重试|重新生成|停止采用/})).toHaveCount(0);
  await expect(pending.locator('summary').first()).not.toContainText('unknown-job');
  await pending.getByText('用量与核对信息',{exact:true}).click();
  await expect(pending.getByText('供应商请求编号：fixture-request')).toBeVisible();
  const bad=records.locator('.activity-record').filter({hasText:'有内容需修正'});
  await bad.locator('summary').first().click();await expect(bad.getByText('请补充来源依据')).toBeVisible();
  await completed.locator('summary').first().click();
  const candidateRow=completed.locator('.activity-record').filter({hasText:'候选待采用'}).first();
  await candidateRow.locator('summary').first().click();await candidateRow.getByRole('button',{name:'查看本次候选'}).click();
  await expect(page).toHaveURL(/\/tasks\/task-activity\/generations\/group-target$/);
  await expect(page.locator('#text-version-version-target')).toBeFocused();
  await expect(page.locator('#text-version-version-target h4')).toBeInViewport();
  await expect(page.locator('.history-versions')).toHaveAttribute('open','');
  await expect(records).toContainText('当前任务相关处理');await expect(records).not.toContainText('我的声音');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect(mutations).toEqual([]);expect(errors).toEqual([]);
});

test('completed records paginate and audio plays without submitting a model request',async({page})=>{
  const {mutations,errors}=await mockActivity(page);await page.goto('/activity');
  const history=page.locator('.completed-records');await history.locator('summary').first().click();
  await expect(history.locator('.activity-record')).toHaveCount(20);
  await history.getByRole('button',{name:'加载更早记录'}).click();
  await expect(history.locator('.activity-record')).toHaveCount(21);
  const row=history.locator('.activity-record').filter({hasText:'短句试听'});
  await row.locator('summary').first().click();
  await expect(row.getByRole('link',{name:'下载本次音频'})).toHaveAttribute('href','/api/v1/assets/fixture-audio/content');
  await row.locator('audio').evaluate(async element=>{await (element as HTMLAudioElement).play();});
  await expect.poll(()=>row.locator('audio').evaluate(el=>(el as HTMLAudioElement).readyState)).toBeGreaterThanOrEqual(2);
  expect(mutations).toEqual([]);expect(errors).toEqual([]);
});
