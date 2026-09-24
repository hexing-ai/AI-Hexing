import { test, expect } from '@playwright/test';

for (const viewport of [{width:1440,height:900},{width:390,height:844}]) {
  test(`text entry is in viewport after first, return and switched clicks ${viewport.width}`,async({page})=>{
    const tasks=Array.from({length:12},(_,i)=>({id:`task-${String(i).padStart(3,'0')}`,tool_id:'tool',snapshot_id:'snapshot',review_id:'review',audience:'测试观众',revision:1,status:'in_progress',current_script:null,current_copy:null,copy_stale:false}));
    const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/api/v1/**',async route=>{
      const path=new URL(route.request().url()).pathname.replace('/api/v1','');
      let data:unknown;
      if(path==='/auth/me')data={csrf_token:'mock-csrf'};
      else if(path==='/capabilities')data={mode:'mock',contracts:[],contract_version:'S1-2026-09-08',validation_tools_enabled:true};
      else if(path==='/tasks')data={items:tasks,next_cursor:null};
      else if(['/jobs','/probe-voices','/validation-runs'].includes(path))data={items:[],next_cursor:null};
      else if(/^\/tasks\/task-\d+\/texts$/.test(path))data={task:tasks.find(t=>path.includes(t.id)),template_intro:'每天分享好用的工具，今天讲测试工具。',versions:[],groups:[]};
      else {await route.fulfill({status:404,json:{error:{message:'Unmocked request'}}});return;}
      await route.fulfill({json:{data}});
    });
    await page.setViewportSize(viewport);
    await page.goto('/tasks');
    const buttons=page.getByRole('link',{name:'继续创作',exact:false});
    const entry=page.locator('#text-creation');
    const title=entry.getByRole('heading',{name:'正式文本创作',exact:true});
    await expect(buttons).toHaveCount(12);
    await buttons.first().click();
    // toBeVisible alone does not catch off-screen content; explicitly check the viewport.
    await expect(title).toBeInViewport();
    await expect(entry).toBeFocused();
    await expect(entry).toHaveAccessibleName('任务 task-000 的文本创作');
    await page.getByRole('link',{name:'返回任务',exact:true}).click();
    await buttons.first().click();
    await expect(title).toBeInViewport();
    await expect(entry).toBeFocused();
    await page.getByRole('link',{name:'返回任务',exact:true}).click();
    await buttons.nth(1).click();
    await expect(title).toBeInViewport();
    await expect(entry).toHaveAccessibleName('任务 task-001 的文本创作');
    expect(await page.evaluate(()=>sessionStorage.getItem('hexing-selected-task'))).toBe('task-001');
    expect(errors).toEqual([]);
  });
}
