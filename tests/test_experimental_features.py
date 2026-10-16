import json
from types import SimpleNamespace
from pathlib import Path
import pytest
from hub.models import JobRequest, Check
from hub.skills.coding.editing import recipes, candidates, slicing, optimization
from hub.skills.coding.execution.options import ExperimentPolicy
from hub.skills.coding.intelligence import changegraph, structural


def test_typescript_operators_are_semantic_changes():
    a='export const x = a + b;\n';b='export const x = a - b;\n'
    assert not structural.describe('x.ts',a,b)['formatting_only']
    assert not structural.compare({'x.ts':a},{'x.ts':a},{'x.ts':b})['ast_equivalent']


def test_recipe_rejects_scope_count_and_invalid_syntax():
    before={'a.py':'x = 1\n'}
    with pytest.raises(ValueError):recipes.transform(before,[recipes.Operation(kind='literal',path='a.py',old='1',new='2')],[])
    with pytest.raises(ValueError):recipes.transform(before,[recipes.Operation(kind='literal',path='a.py',old='1',new='2',expected_count=2)],before)
    with pytest.raises(ValueError):recipes.transform(before,[recipes.Operation(kind='literal',path='a.py',old='1',new='(')],before)
    assert before=={'a.py':'x = 1\n'}


def test_ast_leaf_does_not_replace_comments_or_strings():
    before={'a.py':'# 1\nx = 1\ns = "1"\n'}
    after,receipts=recipes.transform(before,[recipes.Operation(kind='ast_leaf',path='a.py',old='1',new='2',selector='Constant')],before)
    assert after['a.py']=='# 1\nx = 2\ns = "1"\n'
    assert receipts[0]['before_hash']!=receipts[0]['after_hash']


def test_approval_is_specific_to_recipe_version(tmp_path,monkeypatch):
    monkeypatch.setattr(recipes,'CONFIG',tmp_path)
    recipe=recipes.Recipe(name='Update value',operations=[recipes.Operation(kind='literal',path='a.py',old='{{old}}',new='{{new}}')],parameters=['old','new'])
    row=recipes.propose(tmp_path,recipe)
    with pytest.raises(ValueError,match='not approved'):recipes.resolve(tmp_path,row['id'],{'old':'1','new':'2'})
    recipes.review(tmp_path,row['id'],'approve','Inspected replay evidence')
    assert recipes.resolve(tmp_path,row['id'],{'old':'1','new':'2'})[1][0].new=='2'
    recipes.review(tmp_path,row['id'],'revoke','No longer valid for this repository')
    with pytest.raises(ValueError):recipes.resolve(tmp_path,row['id'],{'old':'1','new':'2'})


def test_snapshots_exclude_secrets_links_and_outside_scope(tmp_path):
    repo=tmp_path/'repo';repo.mkdir();(repo/'a.py').write_text('x=1\n');(repo/'.env').write_text('SECRET=private');(repo/'b.py').write_text('y=1\n')
    (repo/'linked.py').symlink_to(repo/'a.py')
    receipt=candidates.capture(repo,tmp_path/'snapshot',['a.py'])
    assert receipt['files'].keys()=={'a.py'}
    assert candidates.contents(tmp_path/'snapshot')=={'a.py':'x=1\n'}
    with pytest.raises(ValueError):candidates.materialize({'../outside':'x'},tmp_path/'bad')


def test_slice_keeps_transitive_imports_and_real_tests():
    values={'app.py':'from util import value\n','util.py':'value=1\n','test_app.py':'import app\n','irrelevant.py':'x=2\n'}
    result=slicing.build(values,['app.py'],['test_app.py'])
    assert result['available'] and set(result['files'])=={'app.py','util.py','test_app.py'}
    assert result['contents']['util.py']==values['util.py']
    assert not slicing.build({'app.py':'import missing_private\n'},['app.py'])['available']


def test_dynamic_import_slice_falls_back():
    assert not slicing.build({'a.py':'__import__("b")\n'},['a.py'])['available']


def green(argv,root,**kwargs):
    return {'status':'passed','exit_code':0,'argv':argv,'output_tail':'1 passed in 0.01s'}


def test_minimizer_keeps_semantic_oracle_and_protects_tests():
    before={'a.py':'x=1\n','test_a.py':'assert x==1\n'}
    after={'a.py':'x=2\n','test_a.py':'assert x==2\n'}
    checks=[Check(name='unit',argv=['pytest','test_a.py'])]
    result=optimization.minimize(before,after,checks,execute=green,criterion_verifier=lambda c:c['a.py']=='x=2\n')
    assert result['selected']==after and not result['alternative']
    result=optimization.minimize(before,after,checks,execute=green)
    assert result['selected']==after
    assert result['alternative']=={'a.py':'x=1\n','test_a.py':'assert x==2\n'}


def test_minimizer_stops_on_flaky_oracle():
    calls=[]
    def flaky(argv,root,**kwargs):
        calls.append(1)
        return {'argv':argv,'status':'passed' if len(calls)==1 else 'failed','output_tail':''}
    before={'a.py':'x=1\n'};after={'a.py':'x=2\n'}
    result=optimization.minimize(before,after,[Check(name='unit',argv=['pytest'])],execute=flaky)
    assert result['reason']=='unstable_checks' and result['selected']==after


def test_mutation_does_not_change_strings_or_comments():
    text='# x == 0\ns = "x == 0"\nif x == 0: pass\n'
    for mutant in optimization.mutants('a.py',text):
        assert mutant['contents'].startswith('# x == 0\ns = "x == 0"\n')


def test_change_graph_requires_support_and_lift():
    rows=[{'id':str(i),'source':'git','nodes':['file:a.py','file:b.py']} for i in range(5)]
    rows += [{'id':str(i),'source':'git','nodes':['file:c.py']} for i in range(5,10)]
    graph=changegraph.build(rows+rows)
    links=changegraph.explain(graph,['a.py'])
    assert graph['observations']==10
    assert links[0]['to']=='file:b.py' and links[0]['support']==5 and links[0]['lift']==2


def test_shadow_never_allows_in_place_or_continuation(repo):
    for extra in ({'in_place':True},{'workspace_from':'a'*32}):
        with pytest.raises(ValueError):JobRequest(role='editor',repo=str(repo),task='Edit',idempotency_key='shadow',allowed_paths=['app.ts'],purpose='shadow',**extra)


def test_hybrid_private_transaction_and_receipts(repo,tmp_path):
    from hub.experimental_runtime import deterministic
    request=JobRequest(role='editor',repo=str(repo),task='Update answer',idempotency_key='hybrid',allowed_paths=['app.ts'],execution_mode='hybrid',deterministic_operations=[recipes.Operation(kind='literal',path='app.ts',old='41',new='42')])
    receipts=deterministic(request,tmp_path)
    assert (repo/'app.ts').read_text()=='export const answer = 42;\n'
    assert receipts[0]['kind']=='literal'


def task_request(repo,key='task',**extra):
    from hub.taskstore import TaskDecision
    return TaskDecision(client='codex',session_id='session',task_key=key,repo=str(repo),decision='keep',
        spec={'goal':'Change answer to 42','kind':'mechanical','scope':{'read':['app.ts'],'edit':['app.ts']},
              'acceptance':[{'id':'answer','kind':'text','text':'Return 42'}]},**extra)


def test_task_sealing_sampling_queue_priority_and_apply_denial(repo,store,monkeypatch):
    from hub.taskstore import Tasks
    from hub.skills.coding.validation import profiles
    from hub.skills.coding.editing import workspace
    monkeypatch.setattr(profiles,'load_profile',lambda repo:(SimpleNamespace(experiments=ExperimentPolicy(enabled=True,shadow=True,sample_rate=1)), 'hash'))
    tasks=Tasks(store);row=tasks.record(task_request(repo))
    assert row['sampled'] and row['shadow_job']
    with pytest.raises(ValueError,match='sealed'):tasks.comparison(row['id'])
    normal=store.submit(JobRequest(role='validator',repo=str(repo),task='Check',checks=[Check(name='version',argv=['python','--version'])],idempotency_key='real'))
    assert store.next()['id']==normal['id']
    assert row['shadow_job'] not in {j['id'] for j in store.list()}
    assert tasks.record(task_request(repo))['id']==row['id']
    (repo/'app.ts').write_text('export const answer = 42;\n');tasks.finish(row['id'])
    assert tasks.comparison(row['id'])['reason']=='shadow is unfinished'
    store.finish(row['shadow_job'],'cancelled',{})
    comparison=tasks.comparison(row['id'])
    assert comparison['available'] and comparison['shadow_state']=='cancelled'
    assert 'source' not in comparison
    assert 'old' not in str(comparison['comparison'].get('frontier',[{}])[0].get('edits',[])[0])


def test_task_api_does_not_expose_shadow_results(repo,store,monkeypatch):
    from fastapi.testclient import TestClient
    from hub.service import create_app
    from hub.settings import initialize
    from hub.skills.coding.validation import profiles
    monkeypatch.setattr(profiles,'load_profile',lambda repo:(SimpleNamespace(experiments=ExperimentPolicy(enabled=True,shadow=True,sample_rate=1)), 'hash'))
    with TestClient(create_app(store,start_workers=False),base_url='http://127.0.0.1:8765') as client:
        headers={'Authorization':'Bearer '+initialize()}
        row=client.post('/api/tasks/decision',json=task_request(repo).model_dump(mode='json'),headers=headers).json()
        assert client.get('/api/jobs/'+row['shadow_job']+'/result',headers=headers).status_code==403
        assert client.get('/api/tasks/'+row['id']+'/comparison',headers=headers).status_code==409
        assert client.get('/api/jobs',headers=headers).json()==[]


def test_sampling_limit_and_disabled_profile(repo,store,monkeypatch):
    from hub.taskstore import Tasks
    from hub.skills.coding.validation import profiles
    monkeypatch.setattr(profiles,'load_profile',lambda repo:(SimpleNamespace(experiments=ExperimentPolicy(enabled=True,shadow=True,sample_rate=1,daily_shadows=1)), 'hash'))
    tasks=Tasks(store)
    assert tasks.record(task_request(repo,'first'))['sampled']
    assert not tasks.record(task_request(repo,'second'))['sampled']
    monkeypatch.setattr(profiles,'load_profile',lambda repo:(SimpleNamespace(experiments=ExperimentPolicy()), 'hash'))
    assert not tasks.record(task_request(repo,'third'))['sampled']


def test_comparison_met_claims_need_evidence(repo,store):
    from hub.taskstore import Tasks,ComparisonReview
    tasks=Tasks(store);row=tasks.record(task_request(repo));tasks.finish(row['id'])
    with pytest.raises(ValueError,match='evidence'):
        tasks.review(row['id'],ComparisonReview(notes='Reviewed the actual diff',requirements={'answer':'met'}))
    result=tasks.review(row['id'],ComparisonReview(notes='Reviewed the actual diff',requirements={'answer':'met'},evidence={'answer':'app.ts exports answer = 42'}))
    assert result['reviews'][0]['requirements']=={'answer':'met'}


def test_explicit_metamorphic_input_and_check_scope():
    from hub.skills.coding.execution.options import MetamorphicCase
    case=MetamorphicCase(name='Input scaling',edits=[{'path':'input.json','old':'1','new':'2'}],check_names=['unit'],expected='passed',required=True)
    result=optimization.validate_metamorphic({'input.json':'{"input":1}'},[Check(name='unit',argv=['pytest'])],[case],['input.json'],execute=green)
    assert result['required_passed'] and result['cases'][0]['status']=='matched'
    result=optimization.validate_metamorphic({'input.json':'{"input":1}'},[Check(name='unit',argv=['pytest'])],[case],[],execute=green)
    assert not result['required_passed'] and result['cases'][0]['status']=='inconclusive'


def test_hybrid_postconditions_cannot_be_undone_by_model():
    operation=recipes.Operation(kind='literal',path='a.py',old='1',new='2')
    assert not recipes.verify_operations({'a.py':'x=1\n'},{'a.py':'x=2\n'},[operation])['unmet']
    assert recipes.verify_operations({'a.py':'x=1\n'},{'a.py':'x=1\n'},[operation])['unmet']


@pytest.mark.asyncio
async def test_zero_model_hybrid_runs_in_private_workspace(repo,store,monkeypatch):
    from hub.runner import Runner
    from hub.settings import initialize
    from hub.skills.coding.editing import workspace
    request=JobRequest(role='editor',repo=str(repo),task='Set answer to 42',allowed_paths=['app.ts'],idempotency_key='hybrid-zero',
        execution_mode='hybrid',deterministic_only=True,deterministic_operations=[recipes.Operation(kind='literal',path='app.ts',old='41',new='42')],
        checks=[Check(name='value',argv=['python3','-c',"from pathlib import Path; assert '42' in Path('app.ts').read_text()"] )])
    job=store.submit(request);store.next();runner=Runner(store,initialize())
    async def forbidden(*args,**kwargs):raise AssertionError('Model must not run')
    monkeypatch.setattr(runner,'execute_model',forbidden)
    await runner.run(job)
    finished=store.get(job['id'])
    assert finished['state']=='completed',finished.get('result')
    assert finished['result']['worker_status']=='COMPLETE',finished['result']
    assert finished['result']['checks'][0]['status']=='passed'
    assert (workspace.STATE/'jobs'/job['id']/'experiments.json').is_file()
    assert (repo/'app.ts').read_text()=='export const answer = 41;\n'
    assert Path(workspace.read_record(job['id'])['path'],'app.ts').read_text()=='export const answer = 42;\n'


def test_repeated_config_serializer_and_fixture_pattern_promotes_once(tmp_path,monkeypatch):
    monkeypatch.setattr(recipes,'CONFIG',tmp_path/'config')
    examples=[]
    for i,(old,new) in enumerate([('timeout','deadline'),('retries','attempts'),('delay','interval'),('timeout','duration'),('limit','maximum')]):
        before={'config.py':old+' = 1\n','serializer.py':'result["'+old+'"] = config.'+old+'\n','fixture.json':'{"'+old+'": 1}'}
        after={p:text.replace(old,new) for p,text in before.items()}
        examples.append({'id':str(i),'accepted':True,'before':before,'after':after,'checks':['unit']})
    proposals=recipes.mine(tmp_path,examples)
    assert len(proposals)==1 and proposals[0]['state']=='proposed'
    assert len(proposals[0]['recipe']['operations'])==3
    assert len(proposals[0]['recipe']['evidence'])==5
    assert recipes.mine(tmp_path,examples+examples)[0]['id']==proposals[0]['id']
    assert recipes.mine(tmp_path,examples[:4])==[]


def test_graph_test_recommendations_preserve_approved_argv():
    from hub.skills.coding.validation import templates
    class Index:
        data={'a.py':{'defs':[],'words':[]},'test_other.py':{'defs':[],'words':[]}}
        def importers(self,path):return []
    rows=[{'id':str(i),'source':'git','nodes':['file:a.py','file:test_other.py']} for i in range(5)]+[{'id':str(i),'source':'git','nodes':['file:b.py']} for i in range(5,10)]
    result=templates.recommend(Index(),['a.py'],[{'name':'unit','argv':['pytest','{tests}']}],graph=changegraph.build(rows))
    assert result['tests'][0]['path']=='test_other.py'
    assert result['checks'][0]['argv']==['pytest','test_other.py']
    assert result['co_change_evidence'][0]['support']==5


def test_every_normal_submission_preempts_shadow_only_after_validation(repo,store):
    shadow=store.submit(JobRequest(role='editor',repo=str(repo),task='Shadow answer',allowed_paths=['app.ts'],purpose='shadow',idempotency_key='shadow-preempt'))
    store.next()
    with pytest.raises(ValueError,match='different task'):
        store.submit(JobRequest(role='editor',repo=str(repo),task='Different answer',allowed_paths=['app.ts'],idempotency_key='shadow-preempt'))
    assert store.get(shadow['id'])['state']=='running'
    store.submit(JobRequest(role='validator',repo=str(repo),task='Real check',checks=[Check(name='version',argv=['python3','--version'])],idempotency_key='real-preempt'))
    assert store.get(shadow['id'])['state']=='cancelled'


def test_co_change_configuration_nodes_only_name_changed_keys():
    before={'config.json':'{"timeout":1,"retries":2}'}
    after={'config.json':'{"timeout":3,"retries":2}'}
    nodes=changegraph.nodes(before,after)
    assert 'config:config.json:timeout' in nodes
    assert 'config:config.json:retries' not in nodes
