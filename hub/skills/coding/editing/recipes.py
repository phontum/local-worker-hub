"""Versioned, reviewed deterministic transformations; no executable recipe code."""
import ast
import hashlib
import json
from pathlib import Path
import re
import time
from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from hub.settings import CONFIG
def sensitive(path):
    from hub.scoped import sensitive as guarded
    return guarded(path)
from hub.skills.coding.intelligence.codeindex import language


class Operation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind: Literal['literal','ast_leaf','import']
    path: str
    old: str=''
    new: str
    expected_count: int=Field(default=1,ge=1,le=1000)
    selector: str | None=None
    criterion_ids: list[str]=Field(default_factory=list,max_length=20)


class Recipe(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(min_length=1,max_length=120)
    operations: list[Operation]=Field(min_length=1,max_length=80)
    parameters: list[str]=Field(default_factory=list,max_length=20)
    evidence: list[str]=Field(default_factory=list,max_length=100)
    check_names: list[str]=Field(default_factory=list,max_length=12)
    invariants: list[dict]=Field(default_factory=list,max_length=40)


def version(recipe):
    return hashlib.sha256(recipe.model_dump_json().encode()).hexdigest()[:24]


def path_for(repo):
    key=hashlib.sha256(str(Path(repo).resolve()).encode()).hexdigest()
    return CONFIG/'recipes'/key


def list_recipes(repo):
    rows=[]
    for path in path_for(repo).glob('*.json'):
        rows.append(json.loads(path.read_text()))
    return rows


def propose(repo,recipe):
    directory=path_for(repo);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    ident=version(recipe);path=directory/(ident+'.json')
    if path.exists():return json.loads(path.read_text())
    data={'id':ident,'state':'proposed','recipe':recipe.model_dump(),'reviews':[],'created':time.time()}
    path.write_text(json.dumps(data,indent=2));path.chmod(0o600)
    return data


def review(repo,ident,decision,notes):
    if not re.fullmatch(r'[a-f0-9]{24}',ident):raise ValueError('Invalid recipe id')
    if decision not in ('approve','reject','revoke','suspend'):raise ValueError('Invalid recipe decision')
    if len(notes)<10:raise ValueError('Recipe reviews need explanatory notes')
    path=path_for(repo)/(ident+'.json');data=json.loads(path.read_text())
    data['state']={'approve':'approved','reject':'rejected','revoke':'revoked','suspend':'suspended'}[decision]
    data['reviews'].append({'decision':decision,'notes':notes,'created':time.time()})
    path.write_text(json.dumps(data,indent=2));path.chmod(0o600)
    return data


def bind(operation,parameters):
    data=operation.model_dump()
    for key in ('old','new','selector'):
        if data[key] is not None:
            data[key]=re.sub(r'\{\{([a-zA-Z_][a-zA-Z_0-9]*)\}\}',lambda m:parameters[m[1]],data[key])
    return Operation.model_validate(data)


def leaf_spans(path,text,selector):
    if Path(path).suffix=='.py':
        tree=ast.parse(text);lines=text.splitlines(keepends=True)
        offsets=[0]
        for line in lines:offsets.append(offsets[-1]+len(line.encode()))
        return [(offsets[n.lineno-1]+n.col_offset,offsets[n.end_lineno-1]+n.end_col_offset)
                for n in ast.walk(tree) if type(n).__name__==selector and isinstance(n,(ast.Constant,ast.Name,ast.arg))]
    grammar=language(Path(path).suffix)
    if not grammar:raise ValueError('AST transform language unsupported')
    from tree_sitter import Language,Parser
    tree=Parser(Language(grammar)).parse(text.encode())
    if tree.root_node.has_error:raise ValueError('Invalid original syntax')
    found=[]
    def visit(n):
        if n.type==selector and not n.named_children:found.append((n.start_byte,n.end_byte))
        for child in n.named_children:visit(child)
    visit(tree.root_node)
    return found


def transform(before,operations,allowed):
    result=dict(before);receipts=[]
    for op in operations:
        if op.path not in allowed or Path(op.path).is_absolute() or '..' in Path(op.path).parts or sensitive(op.path):
            raise ValueError('Operation outside authorized scope')
        if op.path not in result:raise ValueError('Operations require existing files')
        text=result[op.path]
        if op.kind=='literal':
            if not op.old or text.count(op.old)!=op.expected_count:raise ValueError('Literal count mismatch')
            text=text.replace(op.old,op.new)
        elif op.kind=='ast_leaf':
            raw=text.encode();spans=[(a,b) for a,b in leaf_spans(op.path,text,op.selector) if raw[a:b].decode()==op.old]
            if len(spans)!=op.expected_count:raise ValueError('AST selector/count mismatch')
            for a,b in reversed(spans):raw=raw[:a]+op.new.encode()+raw[b:]
            text=raw.decode()
        else:
            if '\n' in op.new.strip():raise ValueError('Import insertion requires one statement')
            if op.new.strip() in [line.strip() for line in text.splitlines()]:continue
            if Path(op.path).suffix=='.py':
                node=ast.parse(op.new).body
                if len(node)!=1 or not isinstance(node[0],(ast.Import,ast.ImportFrom)):raise ValueError('Expected import statement')
                tree=ast.parse(text);position=0
                for item in tree.body:
                    if isinstance(item,(ast.Import,ast.ImportFrom)) or isinstance(item,ast.Expr) and isinstance(item.value,ast.Constant) and isinstance(item.value.value,str):
                        position=item.end_lineno
                    else:break
                lines=text.splitlines(keepends=True);lines.insert(position,op.new.rstrip()+'\n');text=''.join(lines)
            else:
                if not re.fullmatch(r"import\s+[^\n]+\s+from\s+['\"][^'\"]+['\"];?",op.new.strip()):raise ValueError('Expected explicit JS/TS import')
                lines=text.splitlines(keepends=True);position=0
                while position<len(lines) and (lines[position].lstrip().startswith('import ') or lines[position].strip() in ('\"use strict\";','\"use client\";',"'use strict';","'use client';")):position+=1
                lines.insert(position,op.new.rstrip()+'\n');text=''.join(lines)
        from hub.skills.coding.intelligence.structural import syntax
        if Path(op.path).suffix in ('.py','.ts','.tsx','.js','.jsx') and syntax(op.path,text)[2]!='syntactic':
            raise ValueError('Transformation produced invalid syntax')
        result[op.path]=text;receipts.append({'path':op.path,'kind':op.kind,'criterion_ids':op.criterion_ids,
                                          'before_hash':hashlib.sha256(before[op.path].encode()).hexdigest(),
                                          'after_hash':hashlib.sha256(text.encode()).hexdigest()})
    return result,receipts


def resolve(repo,ident,parameters):
    if not re.fullmatch(r'[a-f0-9]{24}',ident):raise ValueError('Invalid recipe id')
    data=json.loads((path_for(repo)/(ident+'.json')).read_text())
    if data['state']!='approved':raise ValueError('Recipe is not approved')
    recipe=Recipe.model_validate(data['recipe'])
    if set(parameters)!=set(recipe.parameters) or any(not isinstance(v,str) or len(v)>1000 for v in parameters.values()):
        raise ValueError('Recipe parameter bindings do not match')
    return recipe,[bind(op,parameters) for op in recipe.operations]


def mine(repo,examples):
    """Conservative literal anti-unification, with exact reconstruction and held-out replay."""
    groups=defaultdict(list);seen=set()
    for example in examples:
        if not example.get('accepted') or example['id'] in seen:continue
        seen.add(example['id']);old=example['before'];new=example['after']
        from hub.skills.coding.intelligence.structural import describe
        changes=[]
        for path in sorted(set(old)|set(new)):
            if path not in old or path not in new:changes=[];break
            if old[path]==new[path]:continue
            # Infer one exact token mapping, then demand complete raw-text replay.
            # This handles identifiers and quoted config keys with the same binding.
            import difflib
            tokens=lambda text:re.findall(r'[A-Za-z_]\w*|\d+',text)
            left,right=tokens(old[path]),tokens(new[path])
            pairs=set()
            for tag,a,b,c,d in difflib.SequenceMatcher(None,left,right,autojunk=False).get_opcodes():
                if tag=='equal':continue
                if tag!='replace' or b-a!=1 or d-c!=1:pairs=set();break
                pairs.add((left[a],right[c]))
            if len(pairs)!=1:changes=[];break
            a,b=next(iter(pairs))
            if old[path].replace(a,b)!=new[path]:changes=[];break
            changes.append((path,a,b,old[path].count(a)))
        if not changes:continue
        pairs={(a,b) for p,a,b,n in changes}
        if len(pairs)!=1:continue
        shape=tuple((p,n) for p,a,b,n in changes)
        groups[shape].append((example,changes))
    proposals=[]
    for shape,rows in groups.items():
        bindings={(changes[0][1],changes[0][2]) for ex,changes in rows}
        if len(rows)<5 or len(bindings)<3:continue
        ops=[Operation(kind='literal',path=p,old='{{old}}',new='{{new}}',expected_count=n) for p,n in shape]
        valid=True
        for example,changes in rows:
            bound=[bind(op,{'old':changes[0][1],'new':changes[0][2]}) for op in ops]
            try:actual,_=transform(example['before'],bound,example['before'])
            except ValueError:valid=False;break
            if actual!=example['after']:valid=False;break
        if valid:
            proposals.append(propose(repo,Recipe(name='Repeated literal update',operations=ops,parameters=['old','new'],
                evidence=[ex['id'] for ex,ch in rows],check_names=sorted(set.intersection(*[set(ex.get('checks',[])) for ex,ch in rows])))))
    return proposals


def verify_operations(before,after,operations):
    """Conservative postconditions for the declared deterministic portion."""
    unmet=[];checked=0
    for op in operations:
        original=before.get(op.path,'');final=after.get(op.path,'');checked+=1
        if op.kind=='literal':
            expected_old=original.count(op.old)-op.expected_count+op.new.count(op.old)*op.expected_count
            okay=final.count(op.old)==expected_old and (not op.new or final.count(op.new)>=original.count(op.new)+op.expected_count)
        elif op.kind=='ast_leaf':
            try:
                def count(text,value):
                    raw=text.encode();return sum(raw[a:b].decode()==value for a,b in leaf_spans(op.path,text,op.selector))
                okay=count(final,op.old)==count(original,op.old)-op.expected_count and count(final,op.new)>=count(original,op.new)+op.expected_count
            except (ValueError,SyntaxError):okay=False
        else:
            try:
                if Path(op.path).suffix=='.py':
                    wanted=ast.dump(ast.parse(op.new).body[0],include_attributes=False)
                    okay=any(ast.dump(n,include_attributes=False)==wanted for n in ast.parse(final).body)
                else:okay=op.new.strip() in [line.strip() for line in final.splitlines()]
            except (ValueError,SyntaxError,IndexError):okay=False
        if not okay:unmet.append({'path':op.path,'kind':op.kind,'reason':'Declared deterministic postcondition no longer holds'})
    return {'checked':checked,'unmet':unmet}
