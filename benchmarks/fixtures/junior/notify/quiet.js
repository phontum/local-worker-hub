const config = require('./config');

// Quiet hours wrap past midnight (22 -> 7), so the window is not a simple range.
function isQuiet(hour, quiet = config.quietHours) {
  return hour >= quiet.start && hour < quiet.end;
}

module.exports = { isQuiet };
