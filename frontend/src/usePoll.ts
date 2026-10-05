import { useEffect, useRef } from 'react';
import type { DependencyList } from 'react';

/** Run `task` now and then `interval` ms after each run finishes, until unmount or a dependency changes.
 *  The task gets an AbortSignal that is aborted on cleanup; it should ignore results once `signal.aborted` and report its own errors. */
export function usePoll(task: (signal: AbortSignal) => Promise<void>, interval: number, deps: DependencyList = [], enabled = true) {
  const latest = useRef(task);
  latest.current = task;
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      try { await latest.current(controller.signal); } catch { /* the task reports its own errors */ }
      finally { if (!controller.signal.aborted) timer = setTimeout(run, interval); }
    };
    void run();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [interval, enabled, ...deps]);
}
