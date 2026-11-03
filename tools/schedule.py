# tools/schedule.py
import json
import queue
import sys
import threading
import time
from config import MAX_LOOP_LIFETIME, MIN_WAKEUP_DELAY
from prompts import format_loop_tick

class Waker:
    def __init__(self):
        self.lock = threading.Lock()
        self.timer = None
        self.start = 0.0
        self.ticks = queue.Queue(maxsize=1)
    def loop_expired(self):
        return self.start != 0.0 and (time.monotonic() - self.start) >= MAX_LOOP_LIFETIME
    def _stop_locked(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self.start = 0.0
    def cancel(self):
        with self.lock: self._stop_locked()
    def armed(self):
        with self.lock: return self.timer is not None
    def arm(self, delay, prompt, repeat):
        with self.lock:
            if self.start == 0.0: self.start = time.monotonic()
            if self.loop_expired():
                self._stop_locked()
                raise RuntimeError(f"循环跑满 {MAX_LOOP_LIFETIME}s 上限")
            if self.timer is not None: self.timer.cancel()
            def on_fire():
                with self.lock: self.timer = None
                if repeat:
                    try: self.arm(delay, prompt, repeat)
                    except RuntimeError: pass
                self.fire(prompt, repeat)
            self.timer = threading.Timer(delay, on_fire)
            self.timer.daemon = True
            self.timer.start()
    def fire(self, prompt, repeat):
        if repeat:
            try: self.ticks.put_nowait(prompt)
            except queue.Full: pass
            return
        self.ticks.put(prompt)

class ScheduleWakeupTool:
    def definition(self):
        return {"name": "schedule_wakeup", "description": "安排一次定时唤醒：到点后系统自动开始新的一轮。",
                "parameters": {"type": "object", "properties": {"delay_seconds": {"type": "integer"}, "prompt": {"type": "string"}, "reason": {"type": "string"}, "repeat": {"type": "boolean"}, "cancel": {"type": "boolean"}}, "required": []}}
    def execute(self, args, ctx):
        try: in_ = json.loads(args) or {}
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        w = ctx.waker
        if w is None: return "错误: 这个运行环境不会有下一轮。"
        if in_.get("cancel"):
            w.cancel()
            return "已取消。"
        prompt = in_.get("prompt", "") or ""
        if not prompt.strip(): return "错误: prompt 不能为空"
        delay = max(int(in_.get("delay_seconds") or 0), MIN_WAKEUP_DELAY)
        repeat = bool(in_.get("repeat"))
        try: w.arm(delay, prompt, repeat)
        except RuntimeError as e: return "错误: " + str(e)
        kind = "每隔这么久响一次" if repeat else "只响一次"
        print(f"[已安排唤醒：{delay}s 后，{kind}]", file=sys.stderr)
        return f"已安排唤醒：{delay}s 后，{kind}。"

def start_wake_forwarder(ctx):
    def forwarder():
        while True:
            prompt = ctx.waker.ticks.get()
            ctx.events.put(("tick", prompt))
    threading.Thread(target=forwarder, daemon=True).start()