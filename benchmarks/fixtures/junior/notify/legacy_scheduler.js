// Older reminder logic kept for the weekly digest. Same job as scheduler.js, written differently.
function due(events, nowMs, leadMs = 10 * 60 * 1000) {
  const out = [];
  for (const e of events) {
    if (e.startMs - leadMs <= nowMs && e.startMs >= nowMs) out.push(e);
  }
  return out;
}

module.exports = { due };
