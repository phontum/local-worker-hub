// Notification settings shared by the scheduler, the quiet-hours filter and the sender.
module.exports = {
  leadMinutes: 10,
  channels: ['email', 'sms'],
  quietHours: { start: 22, end: 7 },
};
