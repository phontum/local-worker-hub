"""Current time in up to three IANA time zones, computed locally from the system clock (no network)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, available_timezones

from pydantic import BaseModel, ConfigDict, Field

from .base import Provider, ProviderError, ProviderResult

class ClockArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    timezones: list[str] = Field(min_length=1, max_length=3, description='IANA time zone ids such as Asia/Tokyo or America/New_York')

def offset(moment):
    seconds = int(moment.utcoffset().total_seconds())
    sign = '+' if seconds >= 0 else '-'
    return f'UTC{sign}{abs(seconds) // 3600:02d}:{abs(seconds) % 3600 // 60:02d}'

class Clock(Provider):
    name = 'clock'
    description = 'The current date and time in one to three IANA time zones, with offsets and the difference from the user\'s own zone. Use for "what time is it in <city>".'
    Args = ClockArgs
    network = False

    async def fetch(self, args, prefs, task=''):
        zones = available_timezones()
        unknown = [z for z in args.timezones if z not in zones]
        if unknown:
            raise ProviderError('unknown time zone ' + ', '.join(unknown))
        utc = datetime.now(timezone.utc)
        mine = utc.astimezone(ZoneInfo(prefs['timezone'])) if prefs.get('timezone') in zones else utc.astimezone()
        lines = [f"Reference: your zone {prefs.get('timezone')} is {mine:%A %Y-%m-%d %H:%M} ({offset(mine)})."]
        for name in args.timezones:
            there = utc.astimezone(ZoneInfo(name))
            hours = (there.utcoffset() - mine.utcoffset()).total_seconds() / 3600
            lines.append(f"{name}: {there:%A %Y-%m-%d %H:%M} ({offset(there)}), {abs(hours):g} h {'ahead of' if hours > 0 else 'behind'} your zone" if hours else
                         f'{name}: {there:%A %Y-%m-%d %H:%M} ({offset(there)}), same time as your zone')
        observed = utc.isoformat()
        return ProviderResult('System clock with IANA time zone rules', 'https://www.iana.org/time-zones', '\n'.join(lines),
                              {'method': 'host-clock', 'provider': self.name, 'requested_url': 'https://www.iana.org/time-zones', 'observed_at': observed, 'current_eligible': True},
                              [{'timezone': z} for z in args.timezones])
