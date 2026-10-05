"""Registered providers, the per-install disable list, and the decision schema built from them."""
import json
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, create_model

from hub.settings import CONFIG
from .clock import Clock
from .fx import Fx
from .weather import Weather

PROVIDERS = {p.name: p for p in (Weather(), Fx(), Clock())}

def enabled():
    """All providers minus CONFIG/providers.json {"disabled": [...]}; a malformed file leaves every provider on."""
    try:
        disabled = json.loads((CONFIG / 'providers.json').read_text()).get('disabled') or []
    except (OSError, ValueError, AttributeError):
        disabled = []
    return {name: p for name, p in PROVIDERS.items() if name not in disabled}

def describe(names=None):
    return '\n'.join(f'- {n}: {p.description}' for n, p in (names or enabled()).items())

def decision_model(base, names=None):
    """The decide-call schema: a provider choice and one optional args object per provider, then the base fields.
    The provider fields come first on purpose: the model fills the JSON in order, and a small model that has already written
    needs_web=true rarely goes back to pick a provider."""
    names = names if names is not None else enabled()
    if not names:
        return base
    fields = {'provider': (Literal[tuple(['none', *names])], Field(default='none', description='Structured provider to use instead of web search, or none'))}
    fields.update({n: (Optional[p.Args], Field(default=None, description=f'Arguments when provider is {n}, else null')) for n, p in names.items()})
    fields.update({name: (field.annotation, field) for name, field in base.model_fields.items()})
    return create_model('Decision', __config__=base.model_config, **fields)
