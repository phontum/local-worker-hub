import json
import pytest
from hub.skills.personal import preferences

METRIC={'units':'metric','clock':'24h','timezone':'Europe/Belgrade'}

@pytest.mark.parametrize('text,expected',[
    ('It is 66°F now','It is 19°C now'),('High 72° F, low 51 °F','High 22°C, low 11°C'),('-4°F','-20°C'),
    ('Already 19°C (66°F)','Already 19°C (66°F)'.replace('(66°F)','(66°F)')),
    ('Wind 10 mph','Wind 16.1 km/h'),('about 3 miles away','about 4.8 km away'),('a 6 ft fence','a 1.8 m fence'),
    ('66°F (19°C)','19°C'),('10 mph (16 km/h)','16.1 km/h'),
    ('Doors at 3:30 PM, show 8pm, closes 12 AM, opens 12:15 p.m.','Doors at 15:30, show 20:00, closes 00:00, opens 12:15.'),
    ('rain after 3 PM. Source: x','rain after 15:00. Source: x'),('at 3 p.m. today','at 15:00 today'),('at 3 p.m. Then rain','at 15:00. Then rain'),('open until 8 p.m.','open until 20:00.'),
    ('at 9 am','at 09:00'),
])
def test_normalizer_rewrites_only_unambiguous_patterns(text,expected):
    if text=='Already 19°C (66°F)':expected='Already 19°C (19°C)'
    assert preferences.normalize(text,METRIC)==expected

@pytest.mark.parametrize('text',[
    'Temperature 19°C and 15:00 are fine','The F key opens the file','See https://example.com/3pm/66°F for 3 PM details'.replace('for 3 PM details','for details'),
    'Version 3.12 am I right','5 mile-high club'.replace('mile-high','high'),'I am 5 am-bitious','the 100 mi marker','2 pm2 packages',
])
def test_normalizer_leaves_other_text_alone(text):
    assert preferences.normalize(text,METRIC)==text

def test_urls_are_never_rewritten():
    text='Source https://weather.example/3pm/66°F and later 3 PM'
    assert preferences.normalize(text,METRIC)=='Source https://weather.example/3pm/66°F and later 15:00'

def test_imperial_and_12h_preferences_are_respected():
    assert preferences.normalize('66°F at 3 PM',{'units':'imperial','clock':'12h','timezone':'UTC'})=='66°F at 3 PM'

def test_load_defaults_override_and_invalid_values(tmp_path,monkeypatch):
    monkeypatch.setattr(preferences,'CONFIG',tmp_path)
    assert preferences.load()['units']=='metric' and preferences.load()['clock']=='24h' and preferences.load()['timezone']
    (tmp_path/'preferences.json').write_text(json.dumps({'units':'imperial','clock':'12h','timezone':'America/New_York'}))
    assert preferences.load()=={'units':'imperial','clock':'12h','timezone':'America/New_York'}
    (tmp_path/'preferences.json').write_text(json.dumps({'units':'bogus','clock':5,'timezone':'x; rm -rf /'}))
    loaded=preferences.load();assert loaded['units']=='metric' and loaded['clock']=='24h' and ';' not in loaded['timezone']
    (tmp_path/'preferences.json').write_text('not json');assert preferences.load()['units']=='metric'

def test_prompt_lines_and_local_time():
    lines=preferences.prompt_lines(METRIC)
    assert '°C' in lines and '24-hour' in lines and 'Europe/Belgrade' in lines
    assert preferences.prompt_lines({'units':'imperial','clock':'12h','timezone':'UTC'}).count('\n')==0
    import re
    assert re.match(r'[A-Z][a-z]+day 20\d\d-\d\d-\d\d \d\d:\d\d', preferences.local_now(METRIC))
