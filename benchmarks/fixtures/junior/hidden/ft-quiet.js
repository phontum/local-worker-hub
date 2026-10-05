const assert = require('node:assert/strict');
const { isQuiet } = require('./quiet');
for (const h of [22, 23, 0, 3, 6]) assert.equal(isQuiet(h), true, 'hour ' + h);
for (const h of [7, 12, 21]) assert.equal(isQuiet(h), false, 'hour ' + h);
assert.equal(isQuiet(10, { start: 9, end: 17 }), true);
assert.equal(isQuiet(18, { start: 9, end: 17 }), false);
