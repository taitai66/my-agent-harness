# agent/dispatch.py
import sys
import threading
from config import MAX_PARALLEL_SUB_AGENTS

def can_fan_out(calls):
    if len(calls) < 2: return False
    return all(tc["function"]["name"] == "sub_agent" for tc in calls)

def dispatch_tool_calls(reg, round_no, calls, ctx):
    results = [None] * len(calls)
    if can_fan_out(calls):
        sem = threading.Semaphore(MAX_PARALLEL_SUB_AGENTS)
        log_lock = threading.Lock()
        threads = []
        for i, tc in enumerate(calls):
            sem.acquire()
            def worker(i=i, tc=tc):
                try:
                    with log_lock:
                        print(f"[round {round_no}] {tc['function']['name']}({tc['function']['arguments'][:100]})", file=sys.stderr)
                    results[i] = reg.execute(tc["function"]["name"], tc["function"]["arguments"], ctx)
                finally:
                    sem.release()
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        for t in threads: t.join()
    else:
        for i, tc in enumerate(calls):
            print(f"[round {round_no}] {tc['function']['name']}({tc['function']['arguments'][:100]})", file=sys.stderr)
            results[i] = reg.execute(tc["function"]["name"], tc["function"]["arguments"], ctx)
    return [{"role": "tool", "tool_call_id": tc["id"], "content": results[i]} for i, tc in enumerate(calls)]