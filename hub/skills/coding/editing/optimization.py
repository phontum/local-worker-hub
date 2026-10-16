"""Bounded candidate reduction and causal check experiments."""
import ast
import copy
import difflib
from pathlib import Path
import re
import tempfile
import time
from .candidates import materialize,digest
from hub.skills.coding.execution.isolation import run
from hub.skills.coding.execution.testparse import parse as parse_output
from hub.skills.coding.intelligence.structural import describe,syntax
from hub.skills.coding.intelligence.codeindex import TESTISH


def check_candidate(values,checks,dependencies,deadline,execute=run):
    receipts=[]
    with tempfile.TemporaryDirectory(prefix='worker-candidate-') as tmp:
        root=materialize(values,Path(tmp)/'tree')
        for check in checks:
            remaining=deadline-time.monotonic()
            if remaining<=0:return {'passed':False,'reason':'budget_exhausted','checks':receipts}
            expected=getattr(check,'expected_test_files',[])
            if any(p not in values for p in expected):return {'passed':False,'reason':'missing_expected_tests','checks':receipts}
            receipt=execute(check.argv,root,timeout=min(check.timeout,remaining),dependencies=dependencies,cwd=check.cwd)
            receipt['name']=check.name
            count=parse_output(receipt.get('output_tail',''),check.argv)['counts']
            count['total']=count.get('total',sum(n for n in count.values() if isinstance(n,int)))
            if check.minimum_tests is not None and (count or {}).get('total',0)<check.minimum_tests:
                receipt['status']='failed';receipt['reason']='insufficient_test_count'
            receipts.append(receipt)
            if receipt['status']!='passed':return {'passed':False,'checks':receipts}
    return {'passed':bool(receipts),'checks':receipts,'candidate_hash':digest(values)}


def minimize(before,after,checks,dependencies=(),seconds=60,trials=12,protected=(),criterion_verifier=None,execute=run,final_checks=None):
    deadline=time.monotonic()+seconds;selected=dict(after);trace=[];protected=set(protected)
    units=[]
    for path in sorted(set(before)&set(after)):
        if TESTISH.search(path) or path in protected:continue
        d=describe(path,before[path],after[path])
        for edit in d['edits']:units.append((path,edit))
    groups=[[(p,e) for p,e in units if p==path] for path in sorted({p for p,e in units})]
    groups += [[u] for u in units]
    for group in groups[:max(0,trials-1)]:
        if time.monotonic()>=deadline:break
        candidate=dict(selected);valid=True
        for path,edit in group:
            if not edit['new'] or candidate[path].count(edit['new'])!=1:valid=False;break
            candidate[path]=candidate[path].replace(edit['new'],edit['old'],1)
            if syntax(path,candidate[path])[2]!='syntactic':valid=False;break
        if not valid:continue
        if criterion_verifier and not criterion_verifier(candidate):continue
        # Identical repeat is deliberate: reject flaky/unstable reduction oracles.
        first=check_candidate(candidate,checks,dependencies,deadline,execute)
        second=check_candidate(candidate,checks,dependencies,deadline,execute) if first['passed'] else None
        kept=bool(first['passed'] and second and second['passed'])
        trace.append({'edits':[e['id'] for p,e in group],'kept':kept,'checks':first,'stability':second})
        if first['passed'] and second and not second['passed']:
            return {'selected':after,'alternative':None,'trace':trace,'reason':'unstable_checks'}
        if kept:selected=candidate
    if selected==after:return {'selected':after,'alternative':None,'trace':trace}
    final=check_candidate(selected,final_checks or checks,dependencies,deadline,execute)
    if not final['passed']:return {'selected':after,'alternative':None,'trace':trace,'final':final}
    return {'selected':selected if criterion_verifier else after,'alternative':None if criterion_verifier else selected,'trace':trace,'final':final}


def mutants(path,text,limit=10):
    """Only mutate parsed changed source; invalid candidates remain explicit."""
    if syntax(path,text)[2]!='syntactic':return []
    pairs={'==':'!=','!=':'==','<':'>=','>':'<=','<=':'>','>=':'<','true':'false','false':'true','True':'False','False':'True','0':'1','1':'0'}
    spans=[]
    if Path(path).suffix=='.py':
        import tokenize,io
        lines=text.splitlines(keepends=True);offsets=[0]
        for line in lines:offsets.append(offsets[-1]+len(line))
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.string in pairs and token.type in (tokenize.OP,tokenize.NAME,tokenize.NUMBER):
                spans.append((offsets[token.start[0]-1]+token.start[1],offsets[token.end[0]-1]+token.end[1],token.string))
    else:
        from tree_sitter import Language,Parser
        from hub.skills.coding.intelligence.codeindex import language
        tree=Parser(Language(language(Path(path).suffix))).parse(text.encode())
        def visit(n):
            if not n.children and n.text.decode() in pairs:spans.append((n.start_byte,n.end_byte,n.text.decode()))
            for child in n.children:visit(child)
        visit(tree.root_node)
    result=[]
    for start,end,old in spans[:limit]:
        raw=text.encode() if Path(path).suffix!='.py' else text
        replacement=pairs[old].encode() if isinstance(raw,bytes) else pairs[old]
        value=raw[:start]+replacement+raw[end:]
        result.append({'path':path,'start':start,'old':old,'new':pairs[old],'contents':value.decode() if isinstance(value,bytes) else value})
    return result


def validate_causality(before,after,checks,fix_paths=(),required_test=None,dependencies=(),seconds=60,limit=10,execute=run):
    deadline=time.monotonic()+seconds;rows=[]
    original=check_candidate(after,checks,dependencies,deadline,execute)
    if not original['passed']:return {'baseline':original,'mutants':[],'required_passed':False,'reason':'candidate_not_green'}
    if fix_paths:
        counter=dict(after)
        for path in fix_paths:
            if TESTISH.search(path):raise ValueError('Counterfactual must retain tests')
            if path in before:counter[path]=before[path]
            else:counter.pop(path,None)
        receipt=check_candidate(counter,checks,dependencies,deadline,execute)
        failures=[f for c in receipt['checks'] for f in parse_output(c.get('output_tail',''),c.get('argv'))['failures']]
        causal=any(required_test and required_test in f.get('test_id','') and f.get('message') and not any(k in f.get('message','') for k in ('SyntaxError','ImportError','ModuleNotFoundError')) for f in failures)
        rows.append({'kind':'undo_fix','status':'killed' if causal else 'inconclusive','receipt':receipt})
    else:causal=False
    for path in sorted(after):
        if time.monotonic()>=deadline or len(rows)>=limit:break
        if TESTISH.search(path) or before.get(path)==after[path]:continue
        for mutant in mutants(path,after[path],limit-len(rows)):
            if time.monotonic()>=deadline:break
            candidate=dict(after);candidate[path]=mutant.pop('contents')
            if syntax(path,candidate[path])[2]!='syntactic':rows.append(mutant|{'status':'invalid'});continue
            receipt=check_candidate(candidate,checks,dependencies,deadline,execute)
            failures=[f for c in receipt['checks'] for f in parse_output(c.get('output_tail',''),c.get('argv'))['failures']]
            status='survived' if receipt['passed'] else 'killed' if any(f.get('message') and 'Assertion' in f['message'] for f in failures) else 'inconclusive'
            rows.append(mutant|{'status':status,'receipt':receipt})
    return {'baseline':original,'mutants':rows,'required_passed':causal if required_test else None,
            'semantic_verdict':'unknown','seconds_budget':seconds}


def validate_metamorphic(after,checks,cases,allowed,dependencies=(),seconds=60,execute=run):
    from .recipes import Operation,transform
    deadline=time.monotonic()+seconds;rows=[]
    by_name={c.name:c for c in checks}
    baseline=check_candidate(after,checks,dependencies,deadline,execute)
    if not baseline['passed']:return {'baseline':baseline,'cases':[],'required_passed':False,'reason':'candidate_not_green'}
    for case in cases:
        row={'name':case.name,'required':case.required,'expected':case.expected,'status':'inconclusive'}
        try:
            if not set(case.check_names).issubset(by_name):raise ValueError('Metamorphic checks were not approved')
            if case.expected=='assertion_failed' and not case.test_id:raise ValueError('An expected assertion failure requires a named test')
            values,_=transform(after,[Operation(kind='literal',**e.model_dump()) for e in case.edits],allowed)
            receipt=check_candidate(values,[by_name[n] for n in case.check_names],dependencies,deadline,execute)
            failures=[f for c in receipt['checks'] for f in parse_output(c.get('output_tail',''),c.get('argv'))['failures']]
            assertion=any(case.test_id==f.get('test_id') and 'Assertion' in f.get('message','') for f in failures)
            matched=receipt['passed'] if case.expected=='passed' else assertion
            row.update(status='matched' if matched else 'mismatched' if receipt['passed'] or failures else 'inconclusive',receipt=receipt)
        except (ValueError,OSError) as exc:row['reason']=str(exc)
        rows.append(row)
    required=[c for c in cases if c.required]
    return {'baseline':baseline,'cases':rows,'required_passed':all(r['status']=='matched' for r in rows if r['required']) and len(rows)==len(cases) if required else None}
