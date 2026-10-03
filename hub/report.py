import re

CONTRACT = '''Return a concise final report, under 500 words, with this envelope:
LOCAL_WORKER_REPORT
Status: COMPLETE | PARTIAL | BLOCKED
Findings:
Answer the assigned items with observed evidence; mark unknowns explicitly.
Files:
Verified paths and line/symbol references, or None.
Checks:
Checks observed, or Not run.
Risks:
Uncertainties and frontier verification items, or None identified.
END_LOCAL_WORKER_REPORT
Use exactly one status. Tool activity is not task success. Do not invent tests or citations.
'''

def parse_report(text):
    if len(re.findall(r'(?m)^LOCAL_WORKER_REPORT\s*$',text))!=1 or len(re.findall(r'(?m)^END_LOCAL_WORKER_REPORT\s*$',text))!=1: return None
    match=re.search(r'(?m)^LOCAL_WORKER_REPORT\s*\nStatus:\s*(COMPLETE|PARTIAL|BLOCKED)\s*\n'
        r'Findings:\s*(.+?)\n\s*Files:\s*(.+?)\n\s*Checks:\s*(.+?)\n\s*Risks:\s*(.+?)\nEND_LOCAL_WORKER_REPORT',text,re.S)
    if not match or not all(v.strip() for v in match.groups()[1:]):return None
    return {'status':match.group(1),'report':match.group(0),'findings':match.group(2).strip(),
        'files':match.group(3).strip(),'checks':match.group(4).strip(),'risks':match.group(5).strip()}

def final_report(session):
    messages=session.get('messages',[])
    last_user=max((i for i,m in enumerate(messages) if m.get('type')=='user'),default=-1)
    assistants=[m for m in messages[last_user+1:] if m.get('type')=='assistant']
    if not assistants:return None
    last=assistants[-1]
    if last.get('finish')!='stop' or session.get('info',{}).get('outcome')!='succeeded':return None
    return parse_report('\n'.join(c.get('text','') for c in last.get('content',[]) if c.get('type')=='text'))

def evidence_excerpt(session):
    rows=[]
    for m in session.get('messages',[]):
        for c in m.get('content',[]):
            if c.get('type')=='text':rows.append(c.get('text',''))
            elif c.get('type')=='tool':
                s=c.get('state',{})
                body='\n'.join(p.get('text','') for p in s.get('content',[]) if p.get('type')=='text')
                rows.append(f"Tool {c.get('name')}, status {s.get('status')}: {body}")
    cap=min(1600,10000//max(1,len(rows)))
    return '\n\n'.join(r[:cap] for r in rows)[:10000]
