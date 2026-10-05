const assert = require('node:assert/strict');
const { isQuiet } = require('./quiet');
for (const bad of [24, -1, 1.5, '3', NaN]) assert.throws(() => isQuiet(bad), RangeError);
assert.equal(isQuiet(12), false);
assert.equal(isQuiet(10, { start: 9, end: 17 }), true);
assert.equal(isQuiet(8, { start: 9, end: 17 }), false);
