"""Exchange rates from Frankfurter (European Central Bank reference rates); the host does the arithmetic."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from pydantic import BaseModel, ConfigDict, Field

from .base import Provider, ProviderError, ProviderResult, get_json

LATEST = 'https://api.frankfurter.dev/v1/latest'
SITE = 'https://frankfurter.dev/'  # shown to the user as the source; the exact API request is kept in the evidence record

class FxArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    amount: float = Field(default=1.0, gt=0, lt=1e12, description='Amount to convert')
    from_currency: str = Field(pattern=r'^[A-Za-z]{3}$', description='ISO 4217 code, e.g. EUR')
    to_currency: str = Field(pattern=r'^[A-Za-z]{3}$', description='ISO 4217 code, e.g. USD')

class Fx(Provider):
    name = 'fx'
    description = 'Convert an amount between two currencies at the latest European Central Bank reference rate (about 30 major currencies, no RSD).'
    Args = FxArgs

    async def fetch(self, args, prefs, task=''):
        source, target = args.from_currency.upper(), args.to_currency.upper()
        if source == target:
            raise ProviderError('same currency')
        data, evidence = await get_json(LATEST, {'base': source, 'symbols': target})
        try:
            rate = Decimal(str(data['rates'][target]))
            date = str(data['date'])
        except (KeyError, TypeError, InvalidOperation) as error:
            raise ProviderError(f'no {source}->{target} rate') from error
        amount = Decimal(str(args.amount))
        converted = (amount * rate).quantize(Decimal('0.01'), ROUND_HALF_UP)
        text = (f'{amount.normalize():f} {source} = {converted} {target}\n1 {source} = {rate} {target}\n'
                f'European Central Bank reference rate published {date} (daily, around 16:00 CET on working days; not a bank or card rate).')
        return ProviderResult(f'ECB reference rate {source}/{target} (Frankfurter)', SITE, text,
                              {**evidence, 'provider': self.name, 'rate_date': date}, [{'from': source, 'to': target, 'rate': str(rate), 'date': date}])
