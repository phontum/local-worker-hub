import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';

const ident=process.env.TRACE_JOB_ID;
if(!ident)throw new Error('TRACE_JOB_ID must name a recorded job with local thinking');
const read=(script)=>execFileSync('../.venv/bin/python',['-c',script],{encoding:'utf8',env:{...process.env,PYTHONPATH:'..'}}).trim();
const code=read("from hub.client import call; print(call('POST','/api/pair-code')['code'])");
const job=JSON.parse(read(`import json; from hub.client import call; print(json.dumps(call('GET','/api/jobs/${ident}')))`));
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8765');await page.getByLabel('Pairing code').fill(code);
 await page.getByRole('button',{name:'Connect',exact:true}).click();
 await page.locator(`button.job[data-job-id="${ident}"]`).click();
 await page.getByRole('tab',{name:'Evidence',exact:true}).click();
 if(job.result?.answer_review){
  await page.getByRole('heading',{name:'Requirements review',exact:true}).waitFor();
  assert.equal(await page.getByRole('link',{name:'Initial answer',exact:true}).getAttribute('href'),`/api/jobs/${ident}/artifacts/draft-report.txt`);
  const review=await page.evaluate(async id=>(await(await fetch(`/api/jobs/${id}/artifacts/answer-review.json`)).json()),ident);
  assert.equal(review.status,job.result.answer_review.status);
  for(const requirement of review.requirements.slice(0,4))await page.getByText(`${requirement.status}: ${requirement.requirement}`,{exact:true}).waitFor();
 }
 if(job.result?.web_verification){
  await page.getByRole('heading',{name:'Source verification',exact:true}).waitFor();
  const link=page.getByRole('link',{name:'Retrieval details and exact quotes',exact:true});
  const expectedPath=`/api/jobs/${ident}/artifacts/${encodeURIComponent(job.result.web_verification.artifact)}`;
  assert.equal(await link.getAttribute('href'),expectedPath);
  const evidence=await page.evaluate(async path=>(await(await fetch(path)).json()),expectedPath);
  assert.equal(evidence.observations.length,job.result.web_verification.verified_observations);
  for(const item of evidence.observations){assert.equal(item.metadata.method,'origin-http');assert.ok(item.quote.length);}
 }
 if(job.result?.research_plans?.length){
  await page.getByRole('heading',{name:'Research plans',exact:true}).waitFor();
  for(const name of job.result.research_plans){
   const path=`/api/jobs/${ident}/artifacts/${encodeURIComponent(name)}`;
   const plan=await page.evaluate(async path=>(await(await fetch(path)).json()),path);
   assert.ok(plan.requirements.length>0);assert.equal(typeof plan.needs_current_evidence,'boolean');
   for(const requirement of plan.requirements)assert.ok(job.request.task.includes(requirement.task_quote));
   assert.equal(await page.getByRole('link',{name:name==='work.research-plan.json' ? 'Initial plan JSON' : 'Reviewer plan JSON',exact:true}).getAttribute('href'),path);
  }
 }

 await page.getByRole('tab',{name:'Trace',exact:true}).click();
 await page.getByText('Local model trace',{exact:true}).waitFor();
 await page.locator('.trace-output strong').filter({hasText:'thinking'}).first().waitFor({timeout:30000});
 const expected=await page.evaluate(async id=>(await(await fetch(`/api/jobs/${id}/trace?offset=0&limit=100`)).json()).segments.filter(x=>x.kind==='thinking').map(x=>x.text).join(''),ident);
 assert.ok(expected.length>0);
 await page.getByLabel('Follow live output').uncheck();
 // Long jobs follow the most recent 60K; select saved pagination before
 // comparing the first evidence page, rather than racing the live tail.
 await page.waitForFunction(text=>document.querySelector('.trace-output')?.textContent?.includes(text),expected.slice(0,60));
 // Text remains escaped even if a model or untrusted page emits HTML.
 await page.route(`**/api/jobs/${ident}/trace?*`,route=>route.fulfill({json:{segments:[{time:Date.now()/1000,phase:'test',step:0,kind:'thinking',text:'<script>window.traceInjected=true</script>'}],next_offset:200,has_more:false,available:true}}));
 await page.getByLabel('Follow live output').check();await page.getByText('<script>window.traceInjected=true</script>',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.traceInjected),undefined);
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(errors,[]);
 await page.screenshot({path:'/tmp/local-worker-live-trace.png',fullPage:true});
 console.log('Paired dashboard displays the same recorded thinking as the API; live/page controls, escaping, mobile layout and runtime checks passed.');
}finally{await browser.close();}
