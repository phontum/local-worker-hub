"""Weather from Open-Meteo: geocode the place, then current conditions plus an hourly table for the requested day."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .base import Provider, ProviderError, ProviderResult, get_json

GEOCODE = 'https://geocoding-api.open-meteo.com/v1/search'
FORECAST = 'https://api.open-meteo.com/v1/forecast'
SITE = 'https://open-meteo.com/'  # shown to the user as the source; the exact API request is kept in the evidence record
WMO = {0: 'clear sky', 1: 'mostly clear', 2: 'partly cloudy', 3: 'overcast', 45: 'fog', 48: 'rime fog', 51: 'light drizzle', 53: 'drizzle',
       55: 'heavy drizzle', 56: 'freezing drizzle', 57: 'heavy freezing drizzle', 61: 'light rain', 63: 'rain', 65: 'heavy rain',
       66: 'freezing rain', 67: 'heavy freezing rain', 71: 'light snow', 73: 'snow', 75: 'heavy snow', 77: 'snow grains',
       80: 'light showers', 81: 'showers', 82: 'violent showers', 85: 'snow showers', 86: 'heavy snow showers',
       95: 'thunderstorm', 96: 'thunderstorm with hail', 99: 'thunderstorm with heavy hail'}

class WeatherArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    place: str = Field(max_length=80, description='City or place name as people write it, ideally in English or the local language')
    country: str = Field(default='', max_length=40, description='Country name or two-letter code when the user gave one, else empty')
    day_offset: int = Field(default=0, ge=0, le=6, description='0 today, 1 tomorrow, up to 6; counted in the place\'s local date')
    from_hour: Optional[int] = Field(default=None, ge=0, le=23, description='First local hour of the window the user cares about')
    to_hour: Optional[int] = Field(default=None, ge=0, le=23, description='Last local hour of that window')

def label(place):
    return ', '.join(dict.fromkeys(str(place[k]) for k in ('name', 'admin1', 'country') if place.get(k)))

def ambiguous(places):
    """Other places with the same name that are big enough that the user may have meant one of them."""
    first = places[0]
    size = first.get('population') or 0
    return [p for p in places[1:] if str(p.get('name', '')).lower() == str(first.get('name', '')).lower()
            and (p.get('country_code'), p.get('admin1')) != (first.get('country_code'), first.get('admin1'))
            and (p.get('population') or 0) >= 0.25 * size]

class Weather(Provider):
    name = 'weather'
    description = ('Current weather and the hourly forecast for one named place, today up to six days ahead: temperature, rain, snow, wind, clouds, '
                   '"will it rain", "how windy", "do I need a jacket", in any language. Use it for any such question that names a place.')
    Args = WeatherArgs

    async def fetch(self, args, prefs, task=''):
        params = {'name': args.place.strip(), 'count': 5, 'language': 'en', 'format': 'json'}
        geo, _ = await get_json(GEOCODE, params)
        places = [p for p in geo.get('results') or [] if isinstance(p, dict) and 'latitude' in p and 'longitude' in p]
        wanted = args.country.strip().lower()
        if wanted:
            narrowed = [p for p in places if wanted in (str(p.get('country', '')).lower(), str(p.get('country_code', '')).lower())]
            places = narrowed or places
        if not places:
            raise ProviderError(f'no place named {args.place!r}')
        place = places[0]
        imperial = prefs.get('units') == 'imperial'
        unit = {'temperature_unit': 'fahrenheit' if imperial else 'celsius', 'wind_speed_unit': 'mph' if imperial else 'kmh', 'precipitation_unit': 'inch' if imperial else 'mm'}
        data, evidence = await get_json(FORECAST, {
            'latitude': place['latitude'], 'longitude': place['longitude'], 'timezone': 'auto', 'forecast_days': args.day_offset + 1,
            'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m,wind_gusts_10m',
            'hourly': 'temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m', **unit})
        hourly, current = data.get('hourly') or {}, data.get('current') or {}
        times = hourly.get('time') or []
        if not times or not current:
            raise ProviderError('forecast response was empty')
        units = data.get('current_units') or {}
        dates = sorted({str(t)[:10] for t in times})
        day = dates[min(args.day_offset, len(dates) - 1)]
        low, high = (args.from_hour, args.to_hour) if args.from_hour is not None and args.to_hour is not None else (None, None)
        if low is not None and low > high:
            low, high = high, low
        rows = []
        for i, stamp in enumerate(times):
            hour = datetime.fromisoformat(str(stamp)).hour
            if str(stamp)[:10] != day or (low is not None and not low <= hour <= high) or (low is None and hour % 3):
                continue
            p = hourly.get('precipitation_probability', [None] * len(times))[i]
            rows.append(f"{str(stamp).replace('T', ' ')} | {hourly['temperature_2m'][i]} {units.get('temperature_2m', '')} | "
                        f"rain {hourly['precipitation'][i]} {units.get('precipitation', '')}" + (f' (chance {p}%)' if p is not None else '') +
                        f" | wind {hourly['wind_speed_10m'][i]} {units.get('wind_speed_10m', '')} | {WMO.get(hourly['weather_code'][i], 'code ' + str(hourly['weather_code'][i]))}")
        lines = [f"Weather for {label(place)} (local time zone {data.get('timezone')}). Forecast day: {day}.",
                 f"Now ({str(current.get('time', '')).replace('T', ' ')} local): {current.get('temperature_2m')} {units.get('temperature_2m', '')}, "
                 f"feels like {current.get('apparent_temperature')} {units.get('apparent_temperature', '')}, {WMO.get(current.get('weather_code'), 'unknown')}, "
                 f"wind {current.get('wind_speed_10m')} {units.get('wind_speed_10m', '')} (gusts {current.get('wind_gusts_10m')}), humidity {current.get('relative_humidity_2m')}%, "
                 f"precipitation {current.get('precipitation')} {units.get('precipitation', '')}."]
        lines.append('Hourly (local time | temperature | rain | wind | sky):\n' + ('\n'.join(rows) or 'no hours in the requested window'))
        others = ambiguous(places)
        if others:
            lines.append('Note: other places share this name: ' + '; '.join(label(p) for p in others[:3]) + '. State which place you used and offer the others.')
        return ProviderResult(f'Open-Meteo forecast for {label(place)}', SITE, '\n'.join(lines),
                              {**evidence, 'provider': self.name, 'place': label(place)}, [{'place': label(place), 'day': day, 'hours': len(rows), 'alternatives': [label(p) for p in others[:3]]}])
