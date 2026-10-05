// Live check of the board view against a recorded board job. Needs the service running and Playwright Chromium.
import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';

const ident=process.env.BOARD_JOB_ID;
if(!ident)throw new Error('BOARD_JOB_ID must name a finished board job');
const read=(script)=>execFileSync('../.venv/bin/python',['-c',script],{encoding:'utf8',env:{...process.env,PYTHONPATH:'..'}}).trim();
const code=read("from hub.client import call; print(call('POST','/api/pair-code')['code'])");
const job=JSON.parse(read(`import json; from hub.client import call; print(json.dumps(call('GET','/api/jobs/${ident}')))`));
const board=job.result?.board;assert.ok(board,'Job has no board record');
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8765');await page.getByLabel('Pairing code').fill(code);
 await page.getByRole('button',{name:'Connect',exact:true}).click();
 await page.locator(`button.job[data-job-id="${ident}"]`).click();
 await page.getByRole('tab',{name:'Board',exact:true}).click();
 await page.getByRole('heading',{name:'Board deliberation',exact:true}).waitFor();
 for(const phase of board.phases)await page.getByText(phase.phase,{exact:false}).first().waitFor();
 // Candidates stay anonymous until the reader reveals their sources.
 await page.getByText('Candidate A',{exact:false}).first().waitFor();
 const before=await page.locator('details summary').allInnerTexts();
 assert.ok(before.some(t=>/^Candidate [A-D]$/.test(t.trim())),'candidate summaries should show only labels');
 assert.ok(!before.some(t=>/skeptic|challenger|direct|first-principles/.test(t)),'roles must be hidden before reveal');
 await page.getByRole('button',{name:'Reveal sources',exact:true}).click();
 const after=await page.locator('details summary').allInnerTexts();
 assert.ok(after.some(t=>/skeptic|challenger/.test(t)),'roles should show after reveal');
 // Synthesis requirements and quotes match the saved artifact.
 const synthesis=await page.evaluate(async id=>(await(await fetch(`/api/jobs/${id}/artifacts/board-synthesis.json`)).json()),ident);
 await page.getByRole('heading',{name:'Arbiter synthesis',exact:true}).waitFor();
 for(const r of synthesis.requirements.slice(0,4))assert.ok(job.request.task.includes(r.task_quote)),await page.getByText(r.requirement,{exact:false}).locator('visible=true').first().waitFor();
 await page.getByRole('heading',{name:'Drift against the original task',exact:true}).waitFor();
 const shot=process.env.BOARD_SHOT;if(shot)await page.screenshot({path:shot,fullPage:true});
 // Mobile layout: no horizontal page scroll.
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'horizontal scroll at phone width');
 assert.deepEqual(errors,[]);
 console.log('Board view verified for',ident);
}finally{await browser.close();}
