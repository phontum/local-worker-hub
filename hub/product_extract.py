"""Structured product data from page markup: JSON-LD, schema.org microdata and product meta tags.

Shopping pages publish exact price, currency and availability for search engines. Reading those fields on the host is more reliable
than asking a small model to pick them out of page text, and the host-written block can be quoted as evidence. Nothing here is specific
to a retailer: it understands the open schema.org vocabulary only. Text that contradicts the markup (for example a sold-out banner next to
an InStock offer) stays visible in the ordinary excerpts, and the answer prompt tells the model to report such conflicts.
"""
import html as htmllib
import json
import re
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

MAX_PRODUCTS = 8
LDJSON = re.compile(r'<script\b[^>]*\btype\s*=\s*["\']?application/(?:ld\+)?json[^>]*>(.*?)</script\s*>', re.I | re.S)
AVAILABILITY = {'instock': 'in_stock', 'onlineonly': 'limited', 'instoreonly': 'limited', 'limitedavailability': 'limited', 'outofstock': 'out_of_stock',
                'soldout': 'out_of_stock', 'preorder': 'preorder', 'presale': 'preorder', 'backorder': 'backorder', 'discontinued': 'discontinued'}
CONDITION = {'newcondition': 'new', 'new': 'new', 'usedcondition': 'used', 'used': 'used', 'refurbishedcondition': 'refurbished', 'refurbished': 'refurbished',
             'damagedcondition': 'damaged'}
SYMBOLS = {'€': 'EUR', '$': 'USD', '£': 'GBP', '¥': 'JPY', '₹': 'INR', '₽': 'RUB', 'дин': 'RSD', 'din': 'RSD', 'rsd': 'RSD', 'zł': 'PLN', 'kč': 'CZK'}
VARIANT_KEYS = ('color', 'size', 'material', 'pattern', 'model')

@dataclass
class Product:
    name: str = ''
    brand: str = ''
    sku: str = ''
    gtin: str = ''
    variant: str = ''
    price: Decimal | None = None
    price_high: Decimal | None = None
    currency: str = ''
    availability: str = 'unknown'
    availability_raw: str = ''
    condition: str = ''
    seller: str = ''
    price_valid_until: str = ''
    source: str = ''
    link: str = ''  # the product's own URL when the markup gives one (listing pages carry many products)

    def key(self):
        return (self.name, self.sku, self.variant, self.price, self.currency, self.availability, self.seller, self.condition)

def parse_price(raw, hint=''):
    """Decimal from numbers or locale strings ('1.299,00 €', '1 299,00', '$1,299.99'); None when there is no amount. Returns (amount, currency)."""
    if isinstance(raw, bool) or raw is None:
        return None, ''
    if isinstance(raw, (int, float)):
        return (Decimal(str(raw)) if raw >= 0 else None), ''
    text = str(raw).replace('\xa0', ' ').strip()
    currency = next((code for symbol, code in SYMBOLS.items() if symbol in text.lower()), '')
    match = re.search(r'\d[\d\s.,’\']*\d|\d', text)
    if not match:
        return None, currency
    number = re.sub(r'[\s’\']', '', match.group(0))
    dot, comma = number.rfind('.'), number.rfind(',')
    if dot >= 0 and comma >= 0:
        decimal_mark = '.' if dot > comma else ','
        number = number.replace(',' if decimal_mark == '.' else '.', '').replace(decimal_mark, '.')
    elif dot >= 0 or comma >= 0:
        mark = '.' if dot >= 0 else ','
        head, _, tail = number.rpartition(mark)
        thousands = len(tail) == 3 and number.count(mark) >= 1 and 0 < len(head.replace(mark, '')) <= 3
        number = number.replace(mark, '') if thousands or number.count(mark) > 1 else number.replace(mark, '.')
    try:
        return Decimal(number), currency
    except InvalidOperation:
        return None, currency

def text_of(value):
    if isinstance(value, dict):
        value = value.get('name') or value.get('@id') or ''
    if isinstance(value, list):
        value = value[0] if value else ''
    return htmllib.unescape(str(value)).strip() if value is not None else ''

def last_word(value):
    return re.split(r'[/#]', text_of(value))[-1].strip().lower()

def availability(raw):
    return AVAILABILITY.get(last_word(raw), 'unknown')

def types_of(node):
    kinds = node.get('@type')
    return {k.rsplit('/', 1)[-1] for k in ([kinds] if isinstance(kinds, str) else kinds or []) if isinstance(k, str)}

def flatten(data):
    """All dict nodes in a JSON-LD document, including @graph members."""
    if isinstance(data, list):
        for item in data:
            yield from flatten(item)
    elif isinstance(data, dict):
        yield data
        for key in ('@graph', 'mainEntity', 'itemListElement', 'item'):
            if key in data:
                yield from flatten(data[key])

def offers_of(node):
    offers = node.get('offers') or node.get('offer') or []
    for offer in offers if isinstance(offers, list) else [offers]:
        if isinstance(offer, dict):
            yield offer
            if types_of(offer) & {'AggregateOffer'} and offer.get('offers'):
                yield from offers_of(offer)

def jsonld_products(html):
    products = []
    for block in LDJSON.findall(html):
        cleaned = re.sub(r'^\s*(?:<!--|//<!\[CDATA\[)|(?:-->|//\]\]>)\s*$', '', block.strip())
        try:
            data = json.loads(cleaned)
        except ValueError:
            try:
                data = json.loads(re.sub(r',\s*([}\]])', r'\1', cleaned))
            except ValueError:
                continue
        for node in flatten(data):
            kinds = types_of(node)
            if 'ProductGroup' in kinds:
                for variant in node.get('hasVariant') or []:
                    if isinstance(variant, dict):
                        products += from_node({**{k: v for k, v in node.items() if k in ('name', 'brand', 'offers')}, **variant}, 'jsonld')
            elif 'Product' in kinds or 'IndividualProduct' in kinds or 'Vehicle' in kinds:
                products += from_node(node, 'jsonld')
    return products

def from_node(node, source):
    base = Product(name=text_of(node.get('name')), brand=text_of(node.get('brand')), sku=text_of(node.get('sku') or node.get('mpn')),
                   gtin=text_of(next((node[k] for k in ('gtin', 'gtin13', 'gtin14', 'gtin12', 'gtin8') if node.get(k)), '')),
                   variant=', '.join(f'{k} {text_of(node[k])}' for k in VARIANT_KEYS if node.get(k)), source=source, link=text_of(node.get('url')) if str(node.get('url', '')).startswith('http') else '')
    out = []
    for offer in offers_of(node):
        specs = offer.get('priceSpecification')
        spec = next((s for s in (specs if isinstance(specs, list) else [specs]) if isinstance(s, dict) and s.get('price') not in (None, '')), {})
        low, hint = parse_price(offer.get('price', offer.get('lowPrice', spec.get('price'))))
        high, _ = parse_price(offer.get('highPrice'))
        currency = text_of(offer.get('priceCurrency') or spec.get('priceCurrency')).upper()
        raw = text_of(offer.get('availability'))
        out.append(Product(**{**asdict(base), 'price': low, 'price_high': high if high != low else None, 'currency': currency if re.fullmatch(r'[A-Z]{3}', currency) else hint,
                              'availability': availability(raw), 'availability_raw': raw.rsplit('/', 1)[-1], 'seller': text_of(offer.get('seller')),
                              'condition': CONDITION.get(last_word(offer.get('itemCondition')), ''), 'price_valid_until': text_of(offer.get('priceValidUntil'))}))
    return out or ([base] if base.name else [])

class Node:
    __slots__ = ('tag', 'attrs', 'children', 'text')
    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.children, self.text = tag, dict(attrs), [], []

class Tree(HTMLParser):
    VOID = {'meta', 'link', 'img', 'br', 'hr', 'input', 'source', 'area', 'base', 'col', 'embed', 'wbr'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('root', [])
        self.stack = [self.root]
    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)
    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))
    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
    def handle_data(self, data):
        self.stack[-1].text.append(data)

def inner_text(node):
    return ' '.join(' '.join(node.text + [inner_text(c) for c in node.children]).split())

def prop_value(node):
    for attr in ('content', 'href', 'src', 'datetime'):
        if node.attrs.get(attr):
            return node.attrs[attr].strip()
    return inner_text(node)

def collect(node, scope):
    """Fill scope {prop: [values or nested scopes]} from itemprop descendants, stopping at nested itemscopes."""
    for child in node.children:
        name = child.attrs.get('itemprop')
        if 'itemscope' in child.attrs:
            nested = {'@type': (child.attrs.get('itemtype') or '').rsplit('/', 1)[-1]}
            collect(child, nested)
            for key in (name or '').split():
                scope.setdefault(key, []).append(nested)
            continue
        for key in (name or '').split():
            scope.setdefault(key, []).append(prop_value(child))
        collect(child, scope)

def first(scope, key, default=''):
    value = (scope.get(key) or [default])[0]
    return text_of(value) if isinstance(value, dict) else value

def microdata_products(html):
    if 'itemscope' not in html.lower():
        return []
    tree = Tree()
    try:
        tree.feed(html)
    except Exception:
        return []
    products, pending = [], [tree.root]
    while pending:
        node = pending.pop()
        for child in node.children:
            if 'itemscope' in child.attrs and re.search(r'schema\.org/(?:Individual)?Product$', child.attrs.get('itemtype', '')):
                scope = {}
                collect(child, scope)
                node_like = {'name': first(scope, 'name'), 'brand': first(scope, 'brand'), 'sku': first(scope, 'sku') or first(scope, 'mpn'),
                             'gtin': first(scope, 'gtin13') or first(scope, 'gtin') or first(scope, 'gtin14') or first(scope, 'gtin8'),
                             **{k: first(scope, k) for k in VARIANT_KEYS},
                             'offers': [{'price': first(o, 'price') or first(o, 'lowPrice'), 'priceCurrency': first(o, 'priceCurrency'), 'availability': first(o, 'availability'),
                                         'itemCondition': first(o, 'itemCondition'), 'seller': first(o, 'seller'), 'priceValidUntil': first(o, 'priceValidUntil')}
                                        for o in scope.get('offers', []) if isinstance(o, dict)]}
                products += from_node(node_like, 'microdata')
            else:
                pending.append(child)
    return products

def meta_products(html):
    meta = {}
    for tag in re.findall(r'<meta\b[^>]*>', html, re.I):
        key = re.search(r'\b(?:property|name)\s*=\s*["\']([^"\']+)', tag, re.I)
        value = re.search(r'\bcontent\s*=\s*["\']([^"\']*)', tag, re.I)
        if key and value:
            meta.setdefault(key.group(1).lower(), value.group(1))
    amount = meta.get('product:price:amount') or meta.get('og:price:amount')
    price, hint = parse_price(amount)
    if price is None:
        return []
    currency = (meta.get('product:price:currency') or meta.get('og:price:currency') or hint).upper()
    raw = meta.get('product:availability') or meta.get('og:availability') or ''
    return [Product(name=meta.get('og:title', ''), brand=meta.get('product:brand', ''), price=price, currency=currency if re.fullmatch(r'[A-Z]{3}', currency) else hint,
                    availability=AVAILABILITY.get(re.sub(r'[\s_-]', '', raw.lower()), 'unknown'), availability_raw=raw, source='meta')]

def extract_products(html):
    """Products from the best source available: JSON-LD, else microdata, else meta tags; deduplicated and capped."""
    for finder in (jsonld_products, microdata_products, meta_products):
        found, seen = [], set()
        for product in finder(html):
            if product.price is not None and product.key() not in seen:
                seen.add(product.key())
                found.append(product)
        if found:
            return found[:MAX_PRODUCTS]
    return []

def line_safe(value, limit=160):
    """Markup values are page-controlled: one line, bounded, and no pipe that could fake a field boundary."""
    return ' '.join(str(value).replace('|', '/').split())[:limit]

def product_rows(products, url=''):
    """JSON-safe rows for the lookup log and dashboard."""
    return [{**{k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(p).items()}, 'url': url} for p in products]

def format_products(products, url=''):
    """Host-written block for the excerpts; values are copied from the markup, never inferred."""
    if not products:
        return ''
    rows = [f"PRODUCT DATA (structured data in the page's markup, source: {products[0].source}{', page ' + line_safe(url, 300) if url else ''}):"]
    for n, p in enumerate(products, 1):
        price = f'{p.price:f}' + (f'-{p.price_high:f}' if p.price_high else '') + (f' {p.currency}' if p.currency else ' (currency not stated)')
        parts = [line_safe(p.name) or '(unnamed)', f'price {price}', f"availability {p.availability}" + (f' (markup: {line_safe(p.availability_raw, 40)})' if p.availability_raw else '')]
        for label, value in (('brand', p.brand), ('variant', p.variant), ('condition', p.condition), ('seller', p.seller), ('sku', p.sku), ('gtin', p.gtin), ('price valid until', p.price_valid_until), ('link', p.link)):
            if value:
                parts.append(f'{label} {line_safe(value)}')
        rows.append(f'{n}. ' + ' | '.join(parts))
    return '\n'.join(rows)
