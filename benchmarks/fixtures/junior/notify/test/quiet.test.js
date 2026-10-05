const test = require('node:test');
const assert = require('node:assert/strict');
const { isQuiet } = require('../quiet');

test('late evening is quiet', () => {
  assert.equal(isQuiet(23), true);
});

test('early morning is quiet', () => {
  assert.equal(isQuiet(3), true);
});

test('midday is not quiet', () => {
  assert.equal(isQuiet(12), false);
});
