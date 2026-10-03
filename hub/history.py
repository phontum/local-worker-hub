"""Import legacy summaries once, without changing or accepting historical runs."""
import hashlib
import json
from .settings import STATE

def import_history(store,root=STATE):
    count=0
    for directory in root.iterdir():
        meta=directory/'metadata.json'
        if not directory.is_dir() or directory.is_symlink() or not meta.is_file():continue
        key=str(directory.resolve())
        try:
            data=json.loads(meta.read_text())
            task=(directory/'task.txt').read_text()[:12000] if (directory/'task.txt').is_file() else 'Legacy task brief unavailable'
            report=(directory/'report.txt').read_text()[:24000] if (directory/'report.txt').is_file() else None
        except (OSError,ValueError):continue
        ident='legacy-'+hashlib.sha256(key.encode()).hexdigest()[:24]
        request={'role':'editor' if data.get('write') else 'investigator','task':task,
            'repo':data.get('cwd'),'context':'','caller':'legacy-import','allowed_paths':[], 'checks':[]}
        result={'report':report,'report_valid':False,'worker_status':'LEGACY',
            'model':data.get('model'),'legacy_path':key,'review':'unreviewed','usage':{},
            'workspace_verified':False,'note':'Historical report retained as unverified evidence; token accounting unavailable.'}
        with store.connect() as db:
            if db.execute('SELECT 1 FROM imports WHERE path=?',(key,)).fetchone():continue
            db.execute('INSERT OR IGNORE INTO jobs (id,key,request,state,created,started,ended,result,review) VALUES (?,?,?,?,?,?,?,?,?)',
                (ident,ident,json.dumps(request),'completed',meta.stat().st_mtime,None,meta.stat().st_mtime,json.dumps(result),None))
            db.execute('INSERT INTO imports VALUES (?)',(key,));count+=1
    return count
