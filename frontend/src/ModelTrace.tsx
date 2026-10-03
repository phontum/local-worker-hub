import { useEffect, useRef, useState } from 'react';

type Segment = { time: number; phase: string; step: number; kind: string; text: string };
type Page = { segments: Segment[]; next_offset: number; has_more: boolean; available: boolean; capture_limit_reached?: boolean };

export function ModelTrace({ jobId, running }: { jobId: string; running: boolean }) {
  const [open,setOpen]=useState(true), [rows,setRows]=useState<Segment[]>([]);
  const [offset,setOffset]=useState(0), [follow,setFollow]=useState(true), [more,setMore]=useState(false);
  const [nextOffset,setNextOffset]=useState(0);
  const [available,setAvailable]=useState(false), [error,setError]=useState(''), [omitted,setOmitted]=useState(false);
  const [limited,setLimited]=useState(false);
  const cursorRef=useRef(0);
  useEffect(()=>{setRows([]);setOffset(0);setFollow(true);setAvailable(false);setOmitted(false);setError('');setLimited(false);},[jobId]);
  useEffect(()=>{
    if(!open)return;
    let stopped=false;let timer:ReturnType<typeof setTimeout>;const controller=new AbortController();let cursor=cursorRef.current;
    async function load(){
      try{
        const response=await fetch(`/api/jobs/${jobId}/trace?offset=${cursor}&limit=100`,{signal:controller.signal});
        if(!response.ok)throw new Error(`Trace unavailable (${response.status})`);
        const page:Page=await response.json();if(stopped)return;
        setAvailable(page.available);setMore(page.has_more);setNextOffset(page.next_offset);setLimited(!!page.capture_limit_reached);setError('');setOmitted(follow && page.next_offset>60000);
        setRows(old=>{
          const joined=[...old,...page.segments];let chars=joined.reduce((n,s)=>n+s.text.length,0);
          while(joined.length>1 && chars>60000){chars-=joined.shift()!.text.length;}
          return joined;
        });
        cursor=page.next_offset;cursorRef.current=cursor;
        if(follow && (running || page.has_more))timer=setTimeout(load,page.has_more?100:1000);
      }catch(e){if(!stopped){setError((e as Error).message);if(follow && running)timer=setTimeout(load,2000);}}
    }
    void load();return()=>{stopped=true;clearTimeout(timer);controller.abort();};
  },[jobId,open,offset,follow,running]);
  const groups=rows.reduce<Segment[]>((all,row)=>{
    const previous=all.at(-1);
    if(previous && previous.phase===row.phase && previous.step===row.step && previous.kind===row.kind)previous.text+=row.text;
    else all.push({...row});
    return all;
  },[]);
  return <details open={open} onToggle={e=>setOpen(e.currentTarget.open)} className="model-trace">
    <summary>Local model trace</summary>
    <p className="muted">Emitted local-model thinking and answer text. Claims require tool verification. Interrupted jobs may have incomplete output.</p>
    <label><input type="checkbox" checked={follow} onChange={e=>{cursorRef.current=0;setRows([]);setOffset(0);setOmitted(false);setFollow(e.target.checked);}}/> Follow live output</label>
    {error && <p role="alert">{error}</p>}
    {!available && <p>No trace recorded yet. Older jobs do not contain a captured trace.</p>}
    {available && !rows.some(r=>r.kind==='thinking') && <p>No thinking text in this view; the phase may have thinking disabled.</p>}
    {omitted && <p>Showing the most recent 60,000 characters. Turn off live following to page through the saved trace from the start.</p>}
    {limited && <p>The private 16 MiB capture limit was reached; later text was not retained.</p>}
    <div className="trace-output" aria-label="Local model output">{groups.map((row,i)=><div key={`${offset}-${i}`}>
      <strong>{row.phase} · step {row.step+1} · {row.kind} · {new Date(row.time*1000).toLocaleTimeString()}</strong><pre>{row.text}</pre>
    </div>)}</div>
    {!follow && more && <button onClick={()=>{setRows([]);setOffset(nextOffset);}}>Next saved page</button>}
  </details>;
}
