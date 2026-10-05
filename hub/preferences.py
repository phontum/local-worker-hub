"""Personal output preferences: metric units, 24-hour clock, local timezone.

Preferences are injected into prompts, and a deliberately conservative normalizer fixes only unambiguous
imperial/12-hour patterns the model left behind. A wrong rewrite is worse than none, so anything unclear is untouched.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from .settings import CONFIG

DEFAULTS = {'units': 'metric', 'clock': '24h', 'timezone': None}

def system_timezone():
    try:
        return os.environ.get('TZ') or Path('/etc/timezone').read_text().strip() or str(datetime.now().astimezone().tzinfo)
    except OSError:
        try:
            return '/'.join(os.readlink('/etc/localtime').split('/')[-2:])
        except OSError:
            return str(datetime.now().astimezone().tzinfo)

def load():
    value = dict(DEFAULTS)
    path = CONFIG / 'preferences.json'
    if path.is_file():
        try:
            stored = json.loads(path.read_text())
        except (OSError, ValueError):
            stored = {}
        if isinstance(stored, dict):
            if stored.get('units') in ('metric', 'imperial'):
                value['units'] = stored['units']
            if stored.get('clock') in ('24h', '12h'):
                value['clock'] = stored['clock']
            if isinstance(stored.get('timezone'), str) and re.fullmatch(r'[A-Za-z0-9_+\-/]{1,60}', stored['timezone']):
                value['timezone'] = stored['timezone']
    value['timezone'] = value['timezone'] or system_timezone()
    return value

def prompt_lines(prefs=None):
    prefs = prefs or load()
    lines = []
    if prefs['units'] == 'metric':
        lines.append('Always give temperatures in °C, speeds in km/h, distances in km or m, and sizes in metric units; convert from the source yourself.')
    if prefs['clock'] == '24h':
        lines.append('Always write times in 24-hour format (for example 15:00), never AM/PM.')
    lines.append(f"The user's timezone is {prefs['timezone']}; express times in it and say when a source uses another zone.")
    return '\n'.join(lines)

def local_now(prefs=None):
    prefs = prefs or load()
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(prefs['timezone'])).strftime('%A %Y-%m-%d %H:%M %Z')
    except Exception:
        return datetime.now().astimezone().strftime('%A %Y-%m-%d %H:%M %Z')

def number(value):
    text = f'{value:.1f}'
    return text[:-2] if text.endswith('.0') else text

FAHRENHEIT = re.compile(r'(-?\d+(?:\.\d+)?)\s?°\s?F\b(?:\s*\(\s*-?\d+(?:\.\d+)?\s?°\s?C\s*\))?')
MPH = re.compile(r'(\d+(?:\.\d+)?)\s?mph\b(?:\s*\(\s*\d+(?:\.\d+)?\s?km/h\s*\))?', re.I)
METRES_PER_SECOND = re.compile(r'(\d+(?:[.,]\d+)?)\s?(?:m/s|м/с)(?![\w/])')
MILES = re.compile(r'(\d+(?:\.\d+)?)\s?miles?\b(?:\s*\(\s*\d+(?:\.\d+)?\s?km\s*\))?', re.I)
FEET = re.compile(r'(\d+(?:\.\d+)?)\s?(?:feet|foot|ft)\b(?:\s*\(\s*\d+(?:\.\d+)?\s?m\s*\))?', re.I)
CLOCK12 = re.compile(r'(?<![\d.:])\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s?([ap])(?:m|\.m(?:\.(?!\s+(?-i:[A-Z0-9])|\s*$))?)(?![\w-])', re.I)

def clock24(match):
    hour, minute, half = int(match.group(1)), match.group(2) or '00', match.group(3).lower()
    hour = (0 if hour == 12 else hour) + (12 if half == 'p' else 0)
    return f'{hour:02d}:{minute}'

def metres_per_second(match):
    raw = match.group(1)
    value = number(float(raw.replace(',', '.')) * 3.6)
    return (value.replace('.', ',') if ',' in raw else value) + (' км/ч' if 'м/с' in match.group(0) else ' km/h')

def convert(text):
    text = METRES_PER_SECOND.sub(metres_per_second, text)
    text = FAHRENHEIT.sub(lambda m: f'{round((float(m.group(1)) - 32) * 5 / 9)}°C', text)
    text = MPH.sub(lambda m: f'{number(float(m.group(1)) * 1.609)} km/h', text)
    text = MILES.sub(lambda m: f'{number(float(m.group(1)) * 1.609)} km', text)
    text = FEET.sub(lambda m: f'{number(float(m.group(1)) * 0.3048)} m', text)
    return text

IMPERIAL_ASKED = re.compile(r'fahrenheit|°\s?F\b|\bmph\b|\bmiles?\b|\bfeet\b|\bfoot\b|\binch|\bknots?\b|m/s|м/с', re.I)
CLOCK_ASKED = re.compile(r'\d\s?(?:am|pm|a\.m\.|p\.m\.)(?![\w-])|12-hour|12 hour|am/pm', re.I)

def normalize(text, prefs=None, task=''):
    """Rewrite imperial units and 12-hour times outside URLs when the preferences ask for metric/24h.
    When the user's own message asks for a unit or clock format, that request wins."""
    prefs = dict(prefs or load())
    if task and IMPERIAL_ASKED.search(task):
        prefs['units'] = 'kept'
    if task and CLOCK_ASKED.search(task):
        prefs['clock'] = 'kept'
    parts = re.split(r'(https?://\S+)', text)
    for i in range(0, len(parts), 2):
        if prefs['units'] == 'metric':
            parts[i] = convert(parts[i])
        if prefs['clock'] == '24h':
            parts[i] = CLOCK12.sub(clock24, parts[i])
    return ''.join(parts)
