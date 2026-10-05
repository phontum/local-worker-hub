const config = require('./config');

// When should we remind the user about an event that starts at `startMs`?
function reminderTime(startMs, leadMinutes = config.leadMinutes) {
  return startMs - leadMinutes * 60 * 1000;
}

function dueReminders(events, nowMs) {
  return events.filter((e) => reminderTime(e.startMs) <= nowMs && e.startMs > nowMs);
}

module.exports = { reminderTime, dueReminders };
