# agent/loop.py
import signal
import sys
import threading
import time
import os

from config import BUDGET_FRACTION, COMPACT_KEEP_FRACTION, MAX_ROUNDS, QUEUE_PREFIX
from llm import send, send_stream, estimate_tokens
from session import safe_save
from events import Inbox, AskRequest, print_ask
from prompts import format_loop_tick, COMPRESSION_PROMPT
from tools.goal import handle_goal_command
from agent.dispatch import dispatch_tool_calls

def check_budget(used_tokens, window):
    pct = used_tokens / window * 100
    print(f"[预算：{used_tokens}/{window} tokens，{pct:.1f}%]", file=sys.stderr)
    over = used_tokens >= window * BUDGET_FRACTION
    if over: print(f"⚠️  已用掉窗口的 {pct:.0f}%，接近上限——开始压缩", file=sys.stderr)
    return over

def compact_keep_budget(window, trigger):
    budget = int(window * COMPACT_KEEP_FRACTION)
    if trigger > 0 and budget > trigger // 2: budget = trigger // 2
    return budget

def safe_split_index(history, keep_budget):
    user_turns = [i for i, m in enumerate(history) if m.get("role") == "user"]
    if len(user_turns) <= 1: return 0
    kept_from = user_turns[-1]
    for k in range(len(user_turns) - 2, -1, -1):
        if estimate_tokens(history[user_turns[k]:]) > keep_budget: break
        kept_from = user_turns[k]
    return kept_from

def summarize(base, api_key, model, msgs):
    req = list(msgs) + [{"role": "user", "content": COMPRESSION_PROMPT}]
    r = send(base, api_key, model, req, None)
    return r["choices"][0]["message"].get("content") or ""

def compact(base, api_key, model, history, keep_budget):
    split = safe_split_index(history, keep_budget)
    if split <= 1: return history, 0
    summary = summarize(base, api_key, model, history[:split])
    rebuilt = [history[0], {"role": "user", "content": "[更早对话的摘要]\n\n" + summary}]
    rebuilt.extend(history[split:])
    return rebuilt, split

def heal_turn(history, note):
    if not history: return history
    last = history[-1]
    if last.get("role") == "assistant" and not last.get("tool_calls"): return history
    if last.get("role") == "assistant" and last.get("tool_calls"):
        for tc in last["tool_calls"]:
            history.append({"role": "tool", "tool_call_id": tc["id"], "content": "错误: 这一轮中断了，这个工具没有执行。"})
    history.append({"role": "assistant", "content": note})
    return history

def heal(ctx, note):
    ctx.session.history = heal_turn(ctx.session.history, note)
    try: ctx.session.save()
    except OSError as e: print(f"警告: 保存会话失败: {e}", file=sys.stderr)

def run_turn(base, api_key, model, ctx, window, input_text, box):
    ctx.session.history.append({"role": "user", "content": input_text})
    for round_no in range(1, MAX_ROUNDS + 1):
        if ctx.cancel_event.is_set(): raise InterruptedError("cancelled")
        steers = box.drain_steer()
        if steers:
            for text in steers: ctx.session.history.append({"role": "user", "content": text})
            print(f"[插话进入这一轮：{len(steers)} 条]", file=sys.stderr)
        use_stream = os.environ.get("STREAM", "0") == "1"
        if use_stream:
            print("\n> ", end="", file=sys.stderr, flush=True)
            def on_delta(s):
                print(s, end="", flush=True)
            r = send_stream(base, api_key, model, ctx.session.history, ctx.registry.definitions(), on_delta)
            print() # 流结束后换行
        else:
            r = send(base, api_key, model, ctx.session.history, ctx.registry.definitions())
        choice = r["choices"][0]
        msg = choice["message"]
        ctx.session.history.append(msg)
        u = r.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        ctx.goal.account(u.get("prompt_tokens", 0) - cached + u.get("completion_tokens", 0))
        steer, ok = ctx.goal.consume_budget_steer()
        if ok:
            print("[goal 预算用完]", file=sys.stderr)
            box.enqueue(steer, False)
        if check_budget(u.get("prompt_tokens", 0), window):
            trigger = int(window * BUDGET_FRACTION)
            keep_budget = compact_keep_budget(window, trigger)
            try:
                rebuilt, folded = compact(base, api_key, model, ctx.session.history, keep_budget)
            except Exception as e:
                print(f"警告: 压缩失败: {e}", file=sys.stderr)
            else:
                if folded > 0:
                    print(f"[压缩：{len(ctx.session.history)} → {len(rebuilt)}]", file=sys.stderr)
                    ctx.session.history = rebuilt
                    ctx.session.force_rewrite = True
        if choice.get("finish_reason") != "tool_calls":
            print(msg.get("content") or "", flush=True)
            print(f"\n[本轮 {round_no} 次请求 · {u.get('prompt_tokens', 0)} tokens · finish_reason={choice.get('finish_reason')}]", file=sys.stderr)
            safe_save(ctx.session)
            return
        print(f"[round {round_no} 输入 {u.get('prompt_tokens', 0)} tokens，命中缓存 {cached}]", file=sys.stderr)
        tool_calls = msg.get("tool_calls") or []
        ctx.session.history.extend(dispatch_tool_calls(ctx.registry, round_no, tool_calls, ctx))
        safe_save(ctx.session)
        if ctx.cancel_event.is_set(): raise InterruptedError("cancelled")
    raise RuntimeError(f"跑满 {MAX_ROUNDS} 轮还没收敛")

def run_interruptible(base, api_key, model, ctx, window, input_text, box):
    ctx.cancel_event.clear()
    outcome = {}

    def worker():
        try: run_turn(base, api_key, model, ctx, window, input_text, box)
        except InterruptedError: outcome["interrupted"] = True
        except Exception as e: outcome["error"] = e
        finally: ctx.events.put(("done", None))

    def handler(signum, frame):
        ctx.cancel_event.set()
        ctx.waker.cancel()
        ctx.goal.suppress()

    old_handler = signal.signal(signal.SIGINT, handler)
    t = threading.Thread(target=worker)
    t.start()

    pending = None
    ask_backlog = []
    input_closed = ctx.input_closed.is_set()

    try:
        while True:
            tag, payload = ctx.events.get()
            if tag == "done": break
            elif tag == "eof":
                input_closed = True
                if pending is not None:
                    print("[输入已关闭，按 N 处理]", file=sys.stderr)
                    pending.resp.put(False)
                    pending = None
                for req in ask_backlog: req.resp.put(False)
                ask_backlog = []
            elif tag == "line":
                line = payload
                if pending is not None:
                    answer = line.strip().lower()
                    pending.resp.put(answer in ("y", "yes"))
                    pending = None
                    if ask_backlog:
                        pending = ask_backlog.pop(0)
                        print_ask(pending.prompt)
                    continue
                if input_closed: continue
                if handle_goal_command(line.strip(), ctx): continue
                if line.startswith(QUEUE_PREFIX):
                    box.enqueue(line[len(QUEUE_PREFIX):].strip(), True)
                    print("[已排队]", file=sys.stderr)
                elif line.strip():
                    box.enqueue(line, False)
                    print("[已收下]", file=sys.stderr)
            elif tag == "ask":
                req = payload
                if input_closed:
                    req.resp.put(False)
                    continue
                if pending is not None: ask_backlog.append(req)
                else:
                    pending = req
                    print_ask(req.prompt)
            elif tag == "tick":
                box.enqueue(format_loop_tick(payload), False)
                print("[定时唤醒到点，当插话塞进去]", file=sys.stderr)
            elif tag == "bg":
                box.enqueue(payload, False)
                print("[后台任务完成，当插话塞进去]", file=sys.stderr)
    finally:
        signal.signal(signal.SIGINT, old_handler)

    t.join()
    if outcome.get("interrupted"):
        heal(ctx, "[这一轮被用户打断]")
        print("\n[已打断这一轮。对话还在，接着说]", file=sys.stderr)
    elif "error" in outcome:
        print("错误:", outcome["error"], file=sys.stderr)
        heal(ctx, f"[这一轮没跑完：{outcome['error']}]")

def run_follow_ups(base, api_key, model, ctx, window, box):
    while True:
        late = box.drain_steer()
        if late:
            print(f"[插话来晚了：折成跟进的对话]", file=sys.stderr)
            run_interruptible(base, api_key, model, ctx, window, "\n\n".join(late), box)
            continue
        queued = box.drain_queued()
        if not queued: return
        for q in queued:
            print("[开始排队的那一句]", file=sys.stderr)
            run_interruptible(base, api_key, model, ctx, window, q, box)

def wait_idle(ctx):
    def handler(signum, frame): ctx.events.put(("sigint", None))
    old_handler = signal.signal(signal.SIGINT, handler)
    try:
        while True:
            tag, payload = ctx.events.get()
            if tag == "line": return payload, False
            if tag == "eof":
                ctx.input_closed.set()
                print(file=sys.stderr)
                return "", True
            if tag == "tick":
                print("\n[定时唤醒到点，自动开始新的一轮]", file=sys.stderr)
                return format_loop_tick(payload), False
            if tag == "bg":
                print("\n[后台任务完成，自动开始新的一轮]", file=sys.stderr)
                return payload, False
            if tag == "sigint":
                if ctx.waker.armed():
                    ctx.waker.cancel()
                    print("\n[循环已停。进程还在，接着说]", file=sys.stderr)
                    print("\n> ", file=sys.stderr, end="", flush=True)
                    continue
                print(file=sys.stderr)
                return "", True
    finally:
        signal.signal(signal.SIGINT, old_handler)