"""Core orchestration for opt-in candidate experiments; skills stay independent."""
import json
from pathlib import Path
import shutil
import time

from .models import Check
from .skills.coding.editing import candidates,recipes,optimization,slicing,workspace
from .skills.coding.delegation.spec import DelegationSpec,verify
from .skills.coding.intelligence.structural import review_packet
from .skills.coding.execution.testparse import parse


def deterministic(request,directory):
    before={p:(Path(request.repo)/p).read_text() for p in request.allowed_paths if (Path(request.repo)/p).is_file()}
    operations=list(request.deterministic_operations)
    if request.literal_mappings:
        operations += [recipes.Operation(kind='literal',path=m.path,old=m.old,new=m.new,expected_count=m.expected_count)
                       for m in request.literal_mappings]
    after,receipts=recipes.transform(before,operations,request.allowed_paths)
    # Use the existing fresh scoped transaction, not direct real-tree writes.
    from .scoped import ScopedFiles
    from .skills.coding.editing import textedit
    files=ScopedFiles(request)
    snapshots={p:textedit.snapshot(files,p) for p in before}
    planned=textedit.Plan({p:text for p,text in after.items() if before[p]!=text},[],{})
    written,errors=textedit.commit(files,planned.contents,snapshots)
    if errors:raise ValueError('; '.join(errors))
    (directory/'deterministic.json').write_text(json.dumps(receipts))
    return receipts


def dependencies(request,origin):
    root=Path(origin or request.repo)
    return [str(root/p) for p in ('.venv','venv','node_modules') if (root/p).is_dir() and not (root/p).is_symlink()]


def prepare_slice(request,directory,label,origin):
    if not request.experiments.enabled or not request.experiments.slicing or request.role!='editor':return None
    values=candidates.contents(request.repo)
    tests=sorted({p for c in request.checks for p in c.expected_test_files})
    external=set()
    try:
        package=json.loads(values.get('package.json','{}'))
        external.update(package.get('dependencies',{}));external.update(package.get('devDependencies',{}))
    except ValueError:pass
    manifest=slicing.build(values,request.allowed_paths,tests,request.experiments.stub_files,external)
    path=directory/(label+'.slice.json')
    if not manifest['available']:
        path.write_text(json.dumps(manifest));return None
    selected=manifest.pop('contents')
    # Slice admission requires comparable baseline collection, with recognized receipts.
    deadline=time.monotonic()+request.experiments.seconds
    dep=dependencies(request,origin)
    full=optimization.check_candidate(values,request.checks,dep,deadline)
    sliced=optimization.check_candidate(selected,request.checks,dep,deadline)
    def signature(receipt):
        return [(c['name'],parse(c.get('output_tail',''),c.get('argv'))['counts'],
                 [f['test_id'] for f in parse(c.get('output_tail',''),c.get('argv'))['failures']]) for c in receipt['checks']]
    full_sig=signature(full);slice_sig=signature(sliced)
    if not full_sig or not all(row[1] for row in full_sig) or full_sig!=slice_sig:
        manifest.update(available=False,fallback='Baseline collection is unverified or differs',full=full,slice=sliced)
        path.write_text(json.dumps(manifest));return None
    root=directory/(label+'.slice');candidates.materialize(selected,root)
    manifest.update(baseline=candidates.digest(selected),baseline_checks=sliced)
    path.write_text(json.dumps(manifest))
    return {'root':root,'before':selected,'manifest':manifest,'original':request.repo}


def finish_slice(request,prepared):
    if not prepared:return
    after=candidates.contents(prepared['root']);before=prepared['before']
    for path in set(before)|set(after):
        if before.get(path)!=after.get(path) and path not in request.allowed_paths:
            raise ValueError('Slice modified a read-only dependency or stub: '+path)
    for path in request.allowed_paths:
        target=Path(request.repo)/path
        if path in after:
            target.parent.mkdir(parents=True,exist_ok=True);target.write_text(after[path])
        elif target.exists():
            if path not in request.delete_paths:raise ValueError('Slice contains undeclared deletion')
            target.unlink()


def postprocess(request,directory,originals,checks,origin):
    after={p:(Path(request.repo)/p).read_text() for p in request.allowed_paths if (Path(request.repo)/p).is_file()}
    criteria=[*request.spec.get('acceptance',[]),*request.spec.get('invariants',[])] if request.spec else []
    provenance={}
    if request.deterministic_operations:
        try:
            mechanical,_=recipes.transform(originals,request.deterministic_operations,request.allowed_paths)
            from .skills.coding.intelligence.structural import describe
            pure={edit['id'] for path in request.allowed_paths for edit in describe(path,originals.get(path,''),mechanical.get(path,''))['edits']}
            touched={op.path for op in request.deterministic_operations}
            for path in request.allowed_paths:
                for edit in describe(path,originals.get(path,''),after.get(path,''))['edits']:
                    provenance[edit['id']]='deterministic' if edit['id'] in pure else 'mixed' if path in touched else 'semantic'
        except ValueError:pass
    evidence={'review':review_packet(originals,after,criteria,provenance)}
    policy=request.experiments
    if policy.enabled and policy.graph:
        from .skills.coding.intelligence.changegraph import load,explain
        evidence['review']['co_change_suggestions']=explain(load(origin or request.repo),[p for p in request.allowed_paths if originals.get(p)!=after.get(p)])
    if not policy.enabled or not request.checks or any(c.get('status')!='passed' for c in checks):
        (directory/'experiments.json').write_text(json.dumps(evidence));(directory/'experiments.json').chmod(0o600)
        return evidence
    dep=dependencies(request,origin)
    # Checks require a coherent full project, even though minimization units are scoped.
    full=candidates.contents(request.repo);before=dict(full)
    for p in request.allowed_paths:
        if p in originals:before[p]=originals[p]
        else:before.pop(p,None)
    if policy.minimize:
        selected_checks=[c for c in request.checks if c.name in policy.cheap_check_names]
        protected=[m.path for m in request.literal_mappings if m.path]
        protected += [c.get('path') for c in criteria if c.get('path')]
        known=bool(request.spec) and bool(criteria) and all(c.get('kind')!='text' for c in criteria)
        def oracle(values):
            report=verify(DelegationSpec.model_validate(request.spec),originals,values,checks)
            return not report['unmet'] and not report['unknown']
        if selected_checks:
            # Include all approved checks for final validity within the same bounded budget.
            result=optimization.minimize(before,full,selected_checks,dep,policy.seconds,policy.trials,
                                         protected,oracle if known else None,final_checks=request.checks)
            alternative=result.pop('alternative');chosen=result.pop('selected')
            if alternative:
                candidates.materialize(alternative,directory/'minimized-candidate')
                result['alternative_hash']=candidates.digest(alternative)
                result['original_hash']=candidates.digest(full)
            if chosen!=full:
                for p in request.allowed_paths:
                    if p in chosen:(Path(request.repo)/p).write_text(chosen[p])
                result['selected_hash']=candidates.digest(chosen)
            evidence['minimization']=result
        else:evidence['minimization']={'reason':'No approved cheap checks selected'}
    causal_started=time.monotonic()
    if policy.metamorphic_cases:
        evidence['metamorphic']=optimization.validate_metamorphic(candidates.contents(request.repo),request.checks,policy.metamorphic_cases,request.allowed_paths,dep,policy.seconds)
    if policy.mutation or policy.required_causal_test:
        evidence['causal']=optimization.validate_causality(before,candidates.contents(request.repo),request.checks,
            policy.fix_paths,policy.required_causal_test,dep,max(0,policy.seconds-(time.monotonic()-causal_started)),policy.mutants)
    (directory/'experiments.json').write_text(json.dumps(evidence));(directory/'experiments.json').chmod(0o600)
    return evidence


def mine_accepted(repo,jobs,examples=()):
    rows=list(examples)
    for job in jobs:
        if job['request'].get('repo')!=repo or (job.get('review') or {}).get('decision')!='accepted':continue
        try:
            record=workspace.read_record(job['id']);base=workspace.ROOT/job['id']/'base'
            directory=workspace.STATE/'jobs'/job['id']
            frontier=json.loads((directory/'snapshots'/'frontier.json').read_text())
            before=candidates.contents(base,record['allowed'])
            rows.append({'id':job['id'],'accepted':True,'before':before,'after':frontier,
                         'checks':[c['name'] for c in job['request'].get('checks',[])]})
        except (OSError,ValueError,workspace.WorkspaceError):continue
    return recipes.mine(repo,rows)


def accepted_change_rows(repo,jobs):
    from .skills.coding.intelligence.changegraph import nodes
    from .skills.coding.delegation.outcomes import is_real
    rows=[]
    for job in jobs:
        if not is_real(job) or job['request'].get('repo')!=repo or (job.get('review') or {}).get('decision')!='accepted':continue
        try:
            record=workspace.read_record(job['id'])
            baseline=candidates.contents(workspace.ROOT/job['id']/'base',record['allowed'])
            frontier=json.loads((workspace.STATE/'jobs'/job['id']/'snapshots'/'frontier.json').read_text())
            rows.append({'id':job['id'],'source':'accepted','nodes':list(nodes(baseline,frontier))})
        except (OSError,ValueError,workspace.WorkspaceError):continue
    return rows
