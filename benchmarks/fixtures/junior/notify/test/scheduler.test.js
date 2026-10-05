const test = require('node:test');
const assert = require('node:assert/strict');
const { reminderTime, dueReminders } = require('../scheduler');

test('reminder is lead minutes before start', () => {
  assert.equal(reminderTime(60 * 60 * 1000, 10), 50 * 60 * 1000);
});

test('only upcoming events inside the lead window are due', () => {
  const now = 100 * 60 * 1000;
  const events = [{ startMs: now + 5 * 60 * 1000 }, { startMs: now + 60 * 60 * 1000 }, { startMs: now - 1000 }];
  assert.equal(dueReminders(events, now).length, 1);
});
