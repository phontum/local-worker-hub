const config = require('./config');
const { isQuiet } = require('./quiet');

function send(channel, message, hour) {
  if (!config.channels.includes(channel)) {
    throw new Error('unknown channel: ' + channel);
  }
  if (isQuiet(hour)) {
    return { sent: false, reason: 'quiet' };
  }
  return { sent: true, channel, message };
}

module.exports = { send };
