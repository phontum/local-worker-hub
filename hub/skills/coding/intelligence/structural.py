"""Structural patch evidence, deliberately distinct from a semantic verdict."""
import ast
import difflib
import hashlib
import json
import re
from pathlib import Path
from .codeindex import analyze, language


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def syntax(path,text):
    try:
        if Path(path).suffix=='.py':
            tree=ast.parse(text)
            literals=[repr(n.value) for n in ast.walk(tree) if isinstance(n,ast.Constant)]
            return sha(ast.dump(tree,include_attributes=False)),literals,'syntactic'
        grammar=language(Path(path).suffix)
        if grammar:
            from tree_sitter import Language,Parser
            tree=Parser(Language(grammar)).parse(text.encode())
            if tree.root_node.has_error:raise ValueError('Invalid syntax')
            literals=[]
            def walk(n):
                if not n.named_children:
                    token=n.text.decode()
                    if n.type in ('string','string_fragment','number','integer','float'):literals.append(token)
                    return [n.type,token]
                return [n.type,*[walk(c) for c in n.children]]
            return sha(json.dumps(walk(tree.root_node))),literals,'syntactic'
    except (SyntaxError,ValueError,ImportError):pass
    return None,re.findall(r'''["'][^"']*["']|\b\d+\b''',text),'lexical'


def describe(path,before,after):
    old=before.splitlines(keepends=True);new=after.splitlines(keepends=True)
    old_ast,old_literals,old_precision=syntax(path,before)
    new_ast,new_literals,new_precision=syntax(path,after)
    defs=analyze(path,before)['defs'];new_defs=analyze(path,after)['defs']
    edits=[];symbols=set();added=removed=0
    for tag,a,b,c,d in difflib.SequenceMatcher(None,old,new,autojunk=False).get_opcodes():
        if tag=='equal':continue
        previous=''.join(old[a:b]);replacement=''.join(new[c:d]);added+=d-c;removed+=b-a
        touched={name for name,kind,start,end in defs if start<=max(a+1,b) and end>=a+1}
        touched|={name for name,kind,start,end in new_defs if start<=max(c+1,d) and end>=c+1}
        symbols.update(touched)
        fingerprint=sha(previous+'\0'+replacement)
        edits.append({'id':sha(path+'\0'+fingerprint)[:16],'fingerprint':fingerprint,
                      'before_range':[a+1,b],'after_range':[c+1,d],'old':previous,'new':replacement,
                      'symbols':sorted(touched)})
    tests=re.findall(r'^\+?\s*(?:async\s+)?def\s+(test\w+)|\b(?:it|test)\(["\']([^"\']+)', ''.join(e['new'] for e in edits),re.M)
    return {'path':path,'precision':'syntactic' if old_precision==new_precision=='syntactic' else 'lexical',
            'added':added,'removed':removed,'formatting_only':before!=after and old_ast is not None and old_ast==new_ast,
            'before_sha256':sha(before),'after_sha256':sha(after),'ast_before':old_ast,'ast_after':new_ast,
            'changed_symbols':sorted(symbols),'added_tests':sorted({a or b for a,b in tests}),
            'literal_changes':{'removed':sorted(set(old_literals)-set(new_literals)), 'added':sorted(set(new_literals)-set(old_literals))},
            'edits':edits}


def compare(baseline,worker,frontier):
    paths=sorted(set(baseline)|set(worker)|set(frontier))
    w=[describe(p,baseline.get(p,''),worker.get(p,'')) for p in paths if baseline.get(p)!=worker.get(p)]
    f=[describe(p,baseline.get(p,''),frontier.get(p,'')) for p in paths if baseline.get(p)!=frontier.get(p)]
    def edit_set(arm):return {(file['path'],e['fingerprint']) for file in arm for e in file['edits']}
    we,fe=edit_set(w),edit_set(f)
    def sets(a,b):return {'matching':sorted(a&b),'missing':sorted(b-a),'extra':sorted(a-b)}
    facts=sets(we,fe)
    symbols=lambda arm:{file['path']+':'+s for file in arm for s in file['changed_symbols']}
    equivalence=True
    for p in paths:
        if (p in worker)!=(p in frontier):equivalence=False;break
        a,_,ap=syntax(p,worker.get(p,''));b,_,bp=syntax(p,frontier.get(p,''))
        if ap!=bp or ap!='syntactic' or a!=b:equivalence=False;break
    return {'schema':2,'worker':w,'frontier':f,'edits':facts,'files':sets({i['path'] for i in w},{i['path'] for i in f}),
            'symbols':sets(symbols(w),symbols(f)), 'structural_coverage':len(we&fe)/len(fe) if fe else None,
            'exact_match':all(worker.get(p)==frontier.get(p) for p in paths),'ast_equivalent':equivalence,'semantic_verdict':'unknown'}


def review_packet(baseline,after,criteria=(),provenance=None):
    files=[describe(p,baseline.get(p,''),after.get(p,'')) for p in sorted(set(baseline)|set(after)) if baseline.get(p)!=after.get(p)]
    ids=[c.get('id') or str(i+1) for i,c in enumerate(criteria)]
    hunks=[]
    for file in files:
        for edit in file['edits']:
            linked=[ids[i] for i,c in enumerate(criteria) if c.get('path')==file['path'] or c.get('symbol') in edit['symbols']]
            hunks.append({'id':edit['id'],'path':file['path'],'symbols':edit['symbols'],'criteria':linked,
                          'rationale':'Changes '+(', '.join(edit['symbols']) or file['path']),
                          'origin':(provenance or {}).get(edit['id'],'semantic'),'rationale_verified':False})
    score=[sum(not h['criteria'] for h in hunks),sum(f['added']+f['removed'] for f in files if f['formatting_only']),
           len(files),len(hunks),sum(f['added']+f['removed'] for f in files)]
    return {'hunks':hunks,'review_score':score,'estimated_review_seconds':None,'basis':'structural heuristic; no measured time estimate'}
