"""Current web answers require source-specific origin evidence, not self-citation."""
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .public_page import evidence_metadata


def current_question(task):
    return bool(re.search(r'\b(current(?:ly)?|now|today|latest|live|weather|prices?|cheapest|availability|available|in stock)\b',task,re.I))


def clarification(report,evidence):
    return (not evidence and report and report.get('status')=='PARTIAL' and
            bool(re.fullmatch(r'(?:Which|What) (?:city|location|region|country|date|product|version)[^\n]*\?',report.get('findings','').strip(),re.I)))


def verified_claims(claims,evidence):
    valid=[];issues=[]
    if not isinstance(claims,list):return [],['Missing structured source claims']
    for claim in claims:
        claim=claim.model_dump() if hasattr(claim,'model_dump') else claim
        if not isinstance(claim,dict) or not isinstance(claim.get('quote'),str):
            issues.append('Malformed source claim');continue
        # Repair a retailer/site-name reference only when the exact URL and
        # quoted text identify one observed page. Never repair a wrong R ID or
        # infer facts, resolve an arbitrary name, or use unobserved content.
        ref=claim.get('source');url=claim.get('url')
        if isinstance(url,str) and isinstance(ref,str) and ref not in evidence and not re.fullmatch(r'R\d+',ref):
            host=urlsplit(url).netloc.lower().removeprefix('www.')
            if ref.lower().removeprefix('www.')==host or ref==url:
                candidates={key:value for key,value in evidence.items()
                            if (meta:=evidence_metadata(value)) and meta.get('requested_url')==url}
                repaired=[]
                for key,value in candidates.items():
                    rows,_=verified_claims([{**claim,'source':key}],{key:value})
                    if rows:repaired.append(rows)
                if len(repaired)==1:
                    valid.extend(repaired[0]);continue
        source=evidence.get(ref,'');metadata=evidence_metadata(source)
        body=source.split('\nPage text:\n',1)[1] if '\nPage text:\n' in source else ''
        quote=claim.get('quote','').replace('\\n','\n');url=claim.get('url')
        def literal(piece):
            if piece in body:return piece
            tokens=piece.split()
            match=re.search(r'\s+'.join(re.escape(token) for token in tokens),body) if tokens else None
            if not match:
                # Keep words/numbers exact; allow layout whitespace beside
                # punctuation (e.g. a colon on the next line). Return the
                # original substring rather than a reconstructed quote.
                tokens=re.findall(r'\w+|[^\w\s]',piece)
                pattern=''
                for i,token in enumerate(tokens):
                    if i:pattern+=r'\s+' if tokens[i-1][-1].isalnum() and token[0].isalnum() else r'\s*'
                    pattern+=re.escape(token)
                match=re.search(pattern,body) if pattern else None
            return match.group(0) if match else None
        quotes=[literal(quote)] if quote else []
        if quotes==[None]:
            # A small model may assemble identity, price and conditions from
            # separate lines. Accept ALL literal fragments or reject the claim;
            # persist each original substring, never an invented combined quote.
            pieces=[piece.strip() for piece in re.split(r'\n|\.{3}|\u2026',quote) if piece.strip()]
            quotes=[literal(piece) for piece in pieces] if len(pieces)>1 and all(len(piece)>=3 for piece in pieces) else []
        okay=(metadata and metadata.get('method')=='origin-http' and metadata.get('current_eligible') is True
              and url==metadata.get('requested_url') and quotes and all(piece is not None for piece in quotes))
        if okay:
            try:
                elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(metadata['observed_at'])).total_seconds()
                okay=0<=elapsed<=900
            except (KeyError,ValueError,TypeError):okay=False
        if not okay:
            issues.append('Missing, stale, cached or mismatched origin evidence for '+str(url));continue
        # Expand fragments to full lines: "available" inside "not available",
        # or "Na zalihama" inside "Nema na zalihama", must retain the negation.
        for quote in quotes:
            start=body.index(quote);end=start+len(quote)
            start=body.rfind('\n',0,start)+1
            line_end=body.find('\n',end)
            if line_end!=-1:end=line_end
            valid.append({**claim,'quote':body[start:end],'metadata':metadata})
    return valid,issues


def guard_current_answer(report,claims,evidence,task,reviewed=False):
    valid,issues=verified_claims(claims,evidence)
    # Render the observations from exact source text. Free-form model prose
    # previously kept confident price/stock claims even after PARTIAL review.
    lines=[];groups={}
    for claim in valid:groups.setdefault(claim['url'],[]).append(claim)
    for url,observations in groups.items():
        claim=observations[0]
        quotes=list(dict.fromkeys(item['quote'] for item in observations))
        label=claim['metadata'].get('title') or claim['url']
        # Display source strings as escaped text, not model-generated Markdown.
        label=re.sub(r'[\[\]<>]', '', label).replace('\n',' ')
        lines.append('- '+label+'\n  Source: '+claim['url']+'\n'+
                     '\n'.join('  > '+line for quote in quotes for line in quote.splitlines())+
                     '\n  Observed: '+claim['metadata']['observed_at'])
    if not valid:issues.append('No source-specific current facts were verified')
    report=dict(report)
    if reviewed and valid and not issues:
        # A fresh requirements reviewer owns semantic synthesis and format.
        # The host audits literal provenance, without imposing domain rules or
        # replacing a reviewed answer with a scripted comparison layout.
        report['checks']+='\nCurrent source URLs, exact quotes, origin retrieval and observation age checked.'
        return report,valid,issues
    report['findings']=('Verified source observations ('+str(len(groups))+' pages):\n'+'\n\n'.join(lines) if lines else
        'I could not verify a current answer from the retrieved sources.')
    if issues:
        report['findings']+='\n\nUnverified source claims: '+ '; '.join(dict.fromkeys(issues))+'.'
        if valid:report['findings']+=' Confirmed observations above remain usable.'
    if report.get('status') in ('PARTIAL','BLOCKED'):
        report['findings']+='\n\nSome requested conclusions remain unresolved; see the requirements assessment for specific gaps.'
    if issues:report['status']='PARTIAL'
    report['checks']='Source URL, exact quote, origin retrieval and observation age checked. The model assesses meaning and sufficiency against the task requirements.'
    report['risks']='; '.join(dict.fromkeys(issues)) or 'Origin/CDN content may be cached and JavaScript was not rendered.'
    return report,valid,issues
