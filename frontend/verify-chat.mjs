import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';

// Live localhost check of the Chat view and the tab keyboard pattern. Needs the service running with the built dashboard and
// a local model: it sends two short Personal messages (a greeting and a follow-up) that need no web search.
const code = execFileSync('../.venv/bin/python', ['-c', "from hub.client import call; print(call('POST','/api/pair-code')['code'])"],
  { cwd: process.cwd(), encoding: 'utf8', env: { ...process.env, PYTHONPATH: '..' } }).trim();
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [], posts = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/api/jobs')) posts.push(JSON.parse(r.postData() ?? '{}')); });
  await page.goto('http://127.0.0.1:8765');
  await page.evaluate(() => localStorage.clear());
  await page.getByLabel('Pairing code').fill(code);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  await page.getByRole('navigation', { name: 'Tasks' }).waitFor();

  // Open the chat; the empty state explains what it is.
  await page.getByRole('navigation', { name: 'View' }).getByRole('button', { name: 'Chat' }).click();
  await page.getByRole('heading', { name: 'Ask the personal model' }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Send' }).isDisabled(), true, 'Send is disabled while the box is empty');

  // Settings: summary and equivalent command follow the choices; conflicting choices are explained and block sending.
  await page.locator('.chat-settings summary').click();
  await page.getByLabel('Mode', { exact: true }).selectOption('small');
  assert.match(await page.locator('.chat-settings summary').innerText(), /Quick/);
  assert.match(await page.locator('.chat-settings code').innerText(), /local-worker --preset small/);
  const models = await page.getByLabel('Model', { exact: true }).locator('option').allInnerTexts();
  assert.ok(models.length >= 2, 'default plus at least one registered model: ' + models.join(' | '));
  await page.getByLabel('Mode', { exact: true }).selectOption('work');
  await page.getByLabel('Board', { exact: false }).check();
  await page.getByLabel('Mode', { exact: true }).selectOption('extended');
  await page.getByRole('alert').filter({ hasText: 'Board uses fixed 16K' }).waitFor();
  await page.locator('#chat-input').fill('Hello! How are you today?');
  assert.equal(await page.getByRole('button', { name: 'Send' }).isDisabled(), true, 'a rejected combination cannot be sent');
  await page.getByRole('button', { name: 'Reset' }).click();
  assert.match(await page.locator('.chat-settings summary').innerText(), /Standard/);
  await page.locator('.chat-settings summary').click();

  // A normal exchange, then a follow-up that carries the first exchange as history.
  await page.locator('#chat-input').fill('Hello! How are you today?');
  await page.locator('#chat-input').press('Enter');
  await page.locator('.bubble.assistant .badge.completed').first().waitFor({ timeout: 120000 });
  const first = await page.locator('.bubble.assistant .bubble-text').first().innerText();
  assert.ok(first.trim().length > 0 && first !== 'Sending…', 'the first reply has text');
  await page.locator('#chat-input').fill('What did I just say to you?');
  await page.getByRole('button', { name: 'Send' }).click();
  await page.locator('.bubble.assistant .badge.completed').nth(1).waitFor({ timeout: 120000 });
  assert.equal(posts.length, 2);
  assert.equal(posts[0].role, 'personal'); assert.equal(posts[0].caller, 'dashboard'); assert.equal(posts[0].history, undefined);
  assert.deepEqual(posts[1].history.map(t => t.role), ['user', 'assistant']);
  assert.equal(posts[1].history[0].text, 'Hello! How are you today?');

  // The conversation survives a reload, and Details opens the same task with its evidence.
  await page.reload();
  await page.getByRole('navigation', { name: 'View' }).getByRole('button', { name: 'Chat' }).click();
  assert.equal(await page.locator('.bubble.user').count(), 2);
  await page.locator('.bubble.assistant').first().getByRole('button', { name: 'Details' }).click();
  await page.getByRole('tab', { name: 'Evidence', exact: true }).click();
  await page.getByRole('heading', { name: 'Lookup' }).waitFor();
  assert.match(await page.locator('.tabbody').innerText(), /Answered without a lookup|Searched the web|Structured provider/);

  // Tab keyboard pattern: arrow keys move the selection, only the selected tab is tabbable, the panel is labelled by it.
  await page.getByRole('tab', { name: 'Answer', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.getByRole('tab', { name: 'Evidence', exact: true }).getAttribute('aria-selected'), 'true');
  assert.equal(await page.getByRole('tab', { name: 'Answer', exact: true }).getAttribute('tabindex'), '-1');
  assert.equal(await page.getByRole('tabpanel').getAttribute('aria-labelledby'), 'tab-evidence');
  await page.keyboard.press('End');
  assert.equal(await page.getByRole('tab', { name: 'Review', exact: true }).getAttribute('aria-selected'), 'true');

  // New chat clears the log; dark scheme changes the page colours; narrow screens do not scroll sideways.
  await page.getByRole('navigation', { name: 'View' }).getByRole('button', { name: 'Chat' }).click();
  await page.getByRole('button', { name: 'New chat' }).click();
  assert.equal(await page.locator('.bubble').count(), 0);
  const light = await page.evaluate(() => getComputedStyle(document.documentElement).backgroundColor);
  await page.emulateMedia({ colorScheme: 'dark' });
  const dark = await page.evaluate(() => getComputedStyle(document.documentElement).backgroundColor);
  assert.notEqual(light, dark, 'dark colour scheme restyles the page');
  await page.setViewportSize({ width: 390, height: 800 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'no horizontal scroll at phone width');
  assert.deepEqual(errors, []);
  console.log('Chat browser verification passed');
} finally {
  await browser.close();
}
