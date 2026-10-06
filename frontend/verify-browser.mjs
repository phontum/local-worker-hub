import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';

// Live localhost smoke check. Generates a short-lived pairing code, never reads API credentials.
const code=execFileSync('../.venv/bin/python',['-c',"from hub.client import call; print(call('POST','/api/pair-code')['code'])"],{cwd:process.cwd(),encoding:'utf8',env:{...process.env,PYTHONPATH:'..'}}).trim();
const job=JSON.parse(execFileSync('../.venv/bin/python',['-c',`
import json,sys,tempfile,uuid
from hub.client import call
from hub.models import JobRequest
repo=tempfile.mkdtemp(prefix='local-worker-browser-')
key=uuid.uuid4().hex
request=JobRequest(role='validator',repo=repo,task='Browser acceptance '+key,idempotency_key=key,failure_policy='continue_independent',checks=[
 {'name':'quiet-check','argv':[sys.executable,'-c','import time;time.sleep(12);print("paged browser evidence\\\\n"*2000)'],'timeout':30},
 {'name':'deliberate-failure','argv':[sys.executable,'-c','raise SystemExit(1)']},
 {'name':'dependent','argv':[sys.executable,'-c','print("must skip")'],'depends_on':['deliberate-failure']},
 {'name':'independent','argv':[sys.executable,'-c','print("independent evidence")']}])
job=call('POST','/api/jobs',request.model_dump())
print(json.dumps({'id':job['id'],'task':request.task}))
`],{cwd:process.cwd(),encoding:'utf8',env:{...process.env,PYTHONPATH:'..'}}));
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || undefined});
try {
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const artifactRequests=[];page.on('request',r=>{if(r.url().includes('/artifact-page/'))artifactRequests.push(r.url());});
 await page.goto('http://127.0.0.1:8765');
 await page.getByRole('heading',{name:'Connect this browser'}).waitFor();
 await page.getByLabel('Pairing code').fill(code);
 await page.getByRole('button',{name:'Connect',exact:true}).click();
 await page.getByRole('navigation',{name:'Tasks'}).waitFor();
 await page.getByLabel('Filter tasks').selectOption('validator');
 await page.locator('button.job').filter({hasText:job.task}).first().click();
 await page.getByText('check · quiet-check',{exact:true}).waitFor({timeout:60000});
 await page.locator('.answer').waitFor({timeout:60000});
 assert.match(await page.locator('.meta').innerText(),/PARTIAL/);
 await page.getByText('independent passed',{exact:false}).waitFor({timeout:60000});
 assert.equal(artifactRequests.length,0,'logs should be lazy');
 await page.getByRole('tab',{name:'Evidence',exact:true}).click();
 await page.getByText('dependent · Skipped',{exact:true}).waitFor();
 await page.getByText('quiet-check · Passed',{exact:true}).click();
 await page.getByRole('button',{name:'Load more output',exact:true}).waitFor();
 assert.equal(artifactRequests.length,1);
 await page.getByRole('button',{name:'Load more output',exact:true}).click();
 await page.waitForTimeout(300);
 assert.equal(artifactRequests.length,2);
 // The Stats drawer holds the savings and workflow figures that no longer crowd the main screen.
 await page.getByRole('button',{name:'Stats',exact:true}).click();
 await page.getByText('Frontier accepted',{exact:true}).waitFor();
 const period=page.getByLabel('Statistics period');
 assert.equal(await period.inputValue(),'since_reset');
 await page.getByText('Review coverage:',{exact:false}).waitFor();
 for(const value of ['today','all','since_reset']){
  const response=page.waitForResponse(r=>r.url().includes(`/api/summary?period=${value}&include_eval=false`)&&r.ok());
  await period.selectOption(value);
  const summary=await (await response).json();
  assert.equal(summary.selection.include_eval,false);
  assert.ok(summary.today.timezone);
 }
 await page.screenshot({path:'/tmp/local-worker-stats-desktop.png'});
 await page.getByRole('button',{name:'Close',exact:true}).click();
 // One screen: the page itself must not scroll at common laptop sizes, even with a long job selected.
 for(const [width,height] of [[1440,900],[1366,768]]){
  await page.setViewportSize({width,height});
  const overflow=await page.evaluate(()=>({page:document.documentElement.scrollHeight-innerHeight,body:document.body.scrollHeight-innerHeight}));
  assert.ok(overflow.page<=1&&overflow.body<=1,`page scrolls at ${width}x${height}: ${JSON.stringify(overflow)}`);
 }
 assert.equal(await page.evaluate(()=>document.cookie.includes('worker_session')),false);
 await page.screenshot({path:'/tmp/local-worker-dashboard-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Stats',exact:true}).click();
 await page.getByLabel('Statistics period').waitFor();
 await page.screenshot({path:'/tmp/local-worker-stats-mobile.png',fullPage:true});
 await page.getByRole('button',{name:'Close',exact:true}).click();
 await page.screenshot({path:'/tmp/local-worker-dashboard-mobile.png',fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 assert.deepEqual(errors,[]);
 console.log('Browser pairing, live progress, report, failed/skipped/independent outcomes, lazy paged logs, stats drawer, no page scroll at laptop sizes, privacy, mobile layout and runtime checks passed.');
} finally {await browser.close();}
