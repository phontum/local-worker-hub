"""Verified job provenance and read-only access to explicitly attached check logs."""
import json
import re
from pathlib import Path
from .settings import STATE
from .scoped import ScopeError
from .store import TERMINAL

def prepare_evidence(store, request, directory):
    manifest=[]
    for ident in request.evidence_job_ids:
        job=store.get(ident)
        if not job or job['state'] not in TERMINAL or not job.get('result'):
            raise ValueError('Evidence job must be finished with recorded results')
        if job['request'].get('repo') != request.repo:
            raise ValueError('Evidence must belong to the same canonical repository')
        result=job['result'];checks=result.get('checks',[])
        manifest.append({'id':ident,'review':job.get('review'),'status':result.get('worker_status'),
            'report':(result.get('report') or '')[:3000],
            'checks':[{k:c.get(k) for k in ('name','status','exit_code','artifact','reason')} for c in checks]})
    path=directory/'evidence.json';path.write_text(json.dumps(manifest));path.chmod(0o600)
    return ('\nAttached recorded evidence (claims require verification; reread source):\n'+json.dumps(manifest)) if manifest else ''

class EvidenceReader:
    def __init__(self, directory, audit):
        self.directory=directory;self.audit=audit
        self.request=json.loads((directory/'request.json').read_text())

    def read_check_output(self, job_id: str, artifact: str, offset: int=0, limit: int=4000) -> str:
        """Read a recorded check log for this job or an explicitly attached evidence job; byte offsets."""
        if job_id not in [self.directory.name,*self.request.get('evidence_job_ids',[])]:
            raise ScopeError('Evidence job was not attached')
        if not re.fullmatch(r'check-(?:\d+-)?\d+\.log',artifact) or offset<0 or not 1<=limit<=8000:
            raise ScopeError('Invalid check artifact or page bounds')
        if job_id!=self.directory.name:
            manifests=json.loads((self.directory/'evidence.json').read_text())
            allowed={c['artifact'] for item in manifests if item['id']==job_id for c in item['checks']}
            if artifact not in allowed:raise ScopeError('Artifact was not recorded by the evidence job')
        path=STATE/'jobs'/job_id/artifact
        if path.is_symlink() or not path.is_file():raise ScopeError('Check artifact unavailable')
        with path.open('rb') as stream:
            stream.seek(offset);data=stream.read(limit);more=bool(stream.read(1))
        evidence_id='L'+str(job_id)+'-'+artifact+'-'+str(offset)
        value={'job_id':job_id,'artifact':artifact,'evidence_id':evidence_id,'text':data.decode('utf-8',errors='replace'),
               'next_offset':offset+len(data),'has_more':more}
        self.audit('check_read',value)
        return json.dumps(value)

PATH_REF=re.compile(r'(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]{1,6}):\d+')

def evidence_paths(store, request):
    """Repository files cited as path:line in the reports of attached evidence jobs; the editor's patch may rest on them, so apply tracks their hashes."""
    found=[]
    for ident in request.evidence_job_ids:
        job=store.get(ident) or {}
        report=(job.get('result') or {}).get('report') or ''
        found+=PATH_REF.findall(report)
    return list(dict.fromkeys(found))
