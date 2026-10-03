"""Bounded anonymous origin reads with DNS pinning and explicit provenance."""
import asyncio
import hashlib
import ipaddress
import json
import re
import socket
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
from httpcore._backends.auto import AutoBackend


class PublicBackend(AutoBackend):
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        host=host.decode() if isinstance(host,bytes) else host
        addresses=await asyncio.to_thread(socket.getaddrinfo,host,port,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError('Private destinations are blocked')
        # Connect to the checked IP, rather than resolving the hostname again.
        # httpcore retains the original hostname for Host and TLS verification.
        return await super().connect_tcp(addresses[0][4][0],port,timeout,local_address,socket_options)


def transport():
    result=httpx.AsyncHTTPTransport(trust_env=False,retries=0)
    result._pool._network_backend=PublicBackend()
    return result


class PageText(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[];self.main=[];self.main_depth=0;self.hidden=[];self.title=[];self.in_title=False

    def handle_starttag(self,tag,attrs):
        # Product availability and purchase conditions often live inside forms.
        # Read their text; never submit them or drop those conditions.
        if tag in ('script','style','template','svg','nav','header','footer'):self.hidden.append(tag)
        if tag=='main':self.main_depth+=1
        if tag=='title':self.in_title=True

    def handle_endtag(self,tag):
        if self.hidden and tag==self.hidden[-1]:self.hidden.pop()
        if tag=='main':self.main_depth=max(0,self.main_depth-1)
        if tag=='title':self.in_title=False

    def handle_data(self,data):
        data=data.strip()
        if self.in_title:self.title.append(data)
        if data and not self.hidden:
            self.parts.append(data)
            if self.main_depth:self.main.append(data)



async def fetch_origin(url, validate_url):
    started=time.monotonic();requested=url;redirects=[]
    async with asyncio.timeout(30):
        async with httpx.AsyncClient(transport=transport(),trust_env=False,follow_redirects=False,
                timeout=httpx.Timeout(12,connect=8),headers={'User-Agent':'local-worker/1.0 public-page-reader','Cache-Control':'no-cache'}) as client:
            for hop in range(4):
                await validate_url(url)
                async with client.stream('GET',url) as response:
                    if response.status_code in (301,302,303,307,308):
                        location=response.headers.get('location')
                        if not location or hop==3:raise ValueError('Public redirect limit exceeded')
                        url=urljoin(url,location);redirects.append(url);continue
                    if response.status_code!=200:raise ValueError(f'Origin HTTP {response.status_code}')
                    mime=response.headers.get('content-type','').lower()
                    if not any(value in mime for value in ('text/html','text/plain','application/xhtml+xml')):
                        raise ValueError('Origin did not return supported webpage text')
                    data=bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data)>2_000_000:raise ValueError('Origin page exceeds the evidence size limit')
                    raw=bytes(data).decode(response.encoding or 'utf-8',errors='replace')
                    if 'html' in mime:
                        parser=PageText();parser.feed(raw)
                        text='\n'.join(parser.main or parser.parts);title=' '.join(parser.title)[:500]
                    else:text=raw;title=''
                    text=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]','',text)
                    if not text.strip():raise ValueError('Origin returned no readable text')
                    age=response.headers.get('age')
                    age_seconds=int(age) if age is not None and age.isdigit() else None
                    cache_unknown=age is not None and age_seconds is None
                    metadata={'method':'origin-http','requested_url':requested,'final_url':str(response.url),
                        'title':title,'observed_at':datetime.now(timezone.utc).isoformat(),'http_status':200,
                        'server_date':response.headers.get('date'),'cache_age_seconds':age_seconds,
                        'cache_control':response.headers.get('cache-control'),'redirects':redirects,
                        'current_eligible':not cache_unknown and (age_seconds is None or age_seconds<=900),
                        'body_sha256':hashlib.sha256(data).hexdigest(),'seconds':round(time.monotonic()-started,2),
                        'truncated':len(text)>24000,'javascript_rendered':False}
                    return text[:24000],metadata


def evidence_metadata(text):
    """Only trust host-written front matter, never markers found inside a page."""
    if text.startswith('Observed evidence '):text=text.split('\n',1)[1]
    if text.startswith('{'):
        record,_,rest=text.partition('\n')
        try:record=json.loads(record)
        except ValueError:return None
        if not isinstance(record,dict) or record.get('tool')!='fetch_web' or record.get('status')!='completed':return None
        text=rest.split('\n',1)[1] if rest.startswith('Characters ') and '\n' in rest else rest
        if text.startswith('Observed evidence '):text=text.split('\n',1)[1]
    line=text.split('\n',1)[0]
    if not line.startswith('WEB_EVIDENCE '):return None
    try:
        value=json.loads(line[len('WEB_EVIDENCE '):])
        return value if isinstance(value,dict) else None
    except ValueError:return None
