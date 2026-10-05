const assert = require('node:assert/strict');
const { send } = require('./sender');
assert.throws(() => send('fax', 'x', 12), { message: 'unknown channel: fax (expected: email, sms)' });
assert.deepEqual(send('email', 'hi', 12), { sent: true, channel: 'email', message: 'hi' });
