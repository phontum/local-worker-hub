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
 await page.getByRole('heading',{name:'Hardware',exact:true}).waitFor();
 await page.getByRole('heading',{name:'Task history',exact:true}).waitFor();
 await page.getByLabel('Filter tasks').selectOption('validator');
 await page.locator('button.job').filter({hasText:job.task}).first().click();
 await page.getByText('No model inference',{exact:true}).waitFor({timeout:60000});
 await page.getByText('check · quiet-check',{exact:true}).waitFor({timeout:60000});
 await page.getByText('independent · Passed',{exact:true}).waitFor({timeout:60000});
 await page.getByText('dependent · Skipped',{exact:true}).waitFor();
 assert.match(await page.locator('.report').innerText(),/PARTIAL/);
 assert.equal(artifactRequests.length,0,'logs should be lazy');
 await page.getByText('quiet-check · Passed',{exact:true}).click();
 await page.getByRole('button',{name:'Load more output',exact:true}).waitFor();
 assert.equal(artifactRequests.length,1);
 await page.getByRole('button',{name:'Load more output',exact:true}).click();
 await page.waitForTimeout(300);
 assert.equal(artifactRequests.length,2);
 assert.match(await page.locator('.savings').filter({hasText:'Estimated frontier usage avoided'}).innerText(),/0 matched baselines/);
 assert.equal(await page.evaluate(()=>document.cookie.includes('worker_session')),false);
 await page.screenshot({path:'/tmp/local-worker-dashboard-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:'/tmp/local-worker-dashboard-mobile.png',fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 assert.deepEqual(errors,[]);
 console.log('Browser pairing, live quiet-check progress, zero-model report, failed/skipped/independent outcomes, lazy paged logs, privacy, mobile layout and runtime checks passed.');
} finally {await browser.close();}
