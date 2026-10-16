"""Repository-local co-change evidence; recommendations never grant scope."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
from hub.settings import STATE
from .structural import describe


def build(observations):
    totals=defaultdict(int);pairs=defaultdict(list);seen=set();count=0
    for row in observations:
        if row['id'] in seen:continue
        seen.add(row['id']);nodes=set(row['nodes'])
        if not nodes or len(nodes)>200:continue
        count+=1
        for a in nodes:
            totals[a]+=1
            for b in nodes-{a}:pairs[a,b].append({'id':row['id'],'source':row['source']})
    links=[]
    for (a,b),evidence in pairs.items():
        frequency=len(evidence)/totals[a];lift=frequency/(totals[b]/count)
        links.append({'from':a,'to':b,'support':len(evidence),'frequency':frequency,'lift':lift,'evidence':evidence[-20:]})
    return {'schema':1,'observations':count,'links':sorted(links,key=lambda e:(-e['frequency'],-e['support'],e['to']))}


def nodes(before,after):
    found=set()
    for path in sorted(set(before)|set(after)):
        if before.get(path)==after.get(path):continue
        found.add('file:'+path)
        d=describe(path,before.get(path,''),after.get(path,''))
        found.update('symbol:'+path+':'+s for s in d['changed_symbols'])
        if Path(path).suffix in ('.json','.toml','.yaml','.yml'):
            def keys(text):
                try:
                    if Path(path).suffix=='.json':data=json.loads(text)
                    elif Path(path).suffix=='.toml':
                        import tomllib
                        data=tomllib.loads(text)
                    else:
                        import re
                        return {key:value for key,value in re.findall(r'^\s*["\']?([\w.-]+)["\']?\s*:\s*(.*)$',text,re.M)}
                    result={}
                    def visit(value,prefix=''):
                        if isinstance(value,dict):
                            for key,child in value.items():visit(child,prefix+'.'+key if prefix else key)
                        else:result[prefix]=value
                    visit(data);return result
                except (ValueError,TypeError):return {}
            old,new=keys(before.get(path,'')),keys(after.get(path,''))
            found.update('config:'+path+':'+key for key in set(old)|set(new) if (key in old)!=(key in new) or old.get(key)!=new.get(key))
    return found


def git_rows(repo,limit=1000):
    from hub.scoped import sensitive
    def git(*args):return subprocess.run(['git','-C',str(repo),*args],capture_output=True,text=True,timeout=30,check=True).stdout
    try:commits=git('log','--no-merges','--format=%H','-n',str(limit)).splitlines()
    except (OSError,subprocess.SubprocessError):return []
    rows=[]
    for commit in commits:
        paths=[p for p in git('diff-tree','--root','--no-commit-id','--name-only','-r',commit).splitlines()
               if not sensitive(p) and not any(s in p for s in ('lock.json','pnpm-lock','uv.lock','dist/','generated/'))]
        if 1<len(paths)<=20:rows.append({'id':'git:'+commit,'source':'git','nodes':['file:'+p for p in paths]})
    return rows


def explain(graph,changed):
    keys=set(changed)|{'file:'+p for p in changed}
    return [e for e in graph['links'] if e['from'] in keys and e['to'] not in keys and e['support']>=5 and e['frequency']>=.8 and e['lift']>1]


def save(repo,graph):
    path=STATE/'graphs'/(hashlib.sha256(str(Path(repo).resolve()).encode()).hexdigest()+'.json')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);path.write_text(json.dumps(graph));path.chmod(0o600)
    return graph


def load(repo):
    path=STATE/'graphs'/(hashlib.sha256(str(Path(repo).resolve()).encode()).hexdigest()+'.json')
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {'schema':1,'observations':0,'links':[]}
