import json
import pytest
from hub.llama_backend import request_body, normalized, stream

def test_request_preserves_budget_schema_and_disables_thinking():
    result=request_body({'model':'gemma','messages':[{'role':'user','content':'Hi'}], 'stream':True, 'think':False,'options':{'num_predict':123,'temperature':.1},'format':{'type':'object'}})
    assert result['max_tokens']==123
    assert result['reasoning_effort']=='none'
    assert result['response_format']['schema']=={'type':'object'}
    assert result['stream_options']=={'include_usage':True}

def test_usage_and_reasoning_normalization():
    result=normalized({'choices':[{'message':{'content':'OK','reasoning_content':'Check'},'finish_reason':'length'}], 'usage':{'prompt_tokens':12,'completion_tokens':5}})
    assert result['done_reason']=='length'
    assert result['message']['thinking']=='Check'
    assert result['eval_count']==5

@pytest.mark.asyncio
async def test_stream_reassembles_tool_arguments_before_emitting():
    class Response:
        async def aiter_lines(self):
            for value in [{'choices':[{'delta':{'content':'OK','tool_calls':[{'index':0,'id':'c','function':{'name':'read','arguments':'{"p":'}}]}}]}, {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'"a"}'}}]},'finish_reason':'tool_calls'}]}, {'choices':[], 'usage':{'completion_tokens':7}}]:
                yield 'data: '+json.dumps(value)
            yield 'data: [DONE]'
    rows=[row async for row in stream(Response())]
    assert rows[0]['message']['content']=='OK'
    assert 'tool_calls' not in rows[0]['message']
    assert rows[-1]['message']['tool_calls'][0]['function']['arguments']=={'p':'a'}
    assert rows[-1]['eval_count']==7

@pytest.mark.asyncio
async def test_truncated_stream_cannot_report_success():
    class Response:
        async def aiter_lines(self):
            yield 'data: {"choices":[]}'
    with pytest.raises(RuntimeError,match='ended before completion'):
        [row async for row in stream(Response())]
