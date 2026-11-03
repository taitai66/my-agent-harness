# tools/goal.py
import json
import sys
import threading
from config import GOAL_ACTIVE, GOAL_BLOCKED, GOAL_BUDGET_LIMITED, GOAL_COMPLETE, GOAL_PAUSED
from prompts import BUDGET_STEER_TEMPLATE, format_goal_continuation

class Goal:
    def __init__(self, objective, token_budget=0):
        self.objective = objective
        self.status = GOAL_ACTIVE
        self.token_budget = token_budget
        self.tokens_used = 0
    def remaining(self):
        if self.token_budget <= 0: return -1
        return max(self.token_budget - self.tokens_used, 0)
    def to_dict(self):
        return {"objective": self.objective, "status": self.status, "token_budget": self.token_budget, "tokens_used": self.tokens_used, "remaining": self.remaining()}

class GoalBox:
    def __init__(self):
        self.lock = threading.Lock()
        self.g = None
        self.cont_pending = False
        self.cont_tokens_at = 0
        self.cont_suppressed = False
        self.budget_steer = ""
        self.skip_next_delta = False
    def _reset_runtime_locked(self):
        self.cont_pending = False
        self.cont_tokens_at = 0
        self.cont_suppressed = False
        self.budget_steer = ""
    def create(self, objective, budget):
        objective = objective.strip()
        if not objective: raise ValueError("objective 不能为空")
        if budget < 0: raise ValueError("token_budget 给了就得是正数")
        with self.lock:
            if self.g is not None: raise ValueError("这个会话已经有一个 goal 了")
            self.g = Goal(objective, budget)
            self._reset_runtime_locked()
            self.skip_next_delta = True
            return self.g.to_dict()
    def set_status(self, status):
        with self.lock:
            if self.g is None: raise ValueError("现在没有 goal")
            if status == GOAL_ACTIVE and self.g.status == GOAL_COMPLETE: raise ValueError("goal 已经完成了")
            if status == GOAL_ACTIVE and self.g.remaining() == 0: status = GOAL_BUDGET_LIMITED
            self.g.status = status
            self._reset_runtime_locked()
            return self.g.to_dict()
    def account(self, delta):
        delta = max(delta, 0)
        with self.lock:
            if self.skip_next_delta:
                self.skip_next_delta = False
                delta = 0
            if delta == 0 or self.g is None or self.g.status not in (GOAL_ACTIVE, GOAL_BUDGET_LIMITED): return
            self.cont_suppressed = False
            self.g.tokens_used += delta
            if self.g.status == GOAL_ACTIVE and self.g.remaining() == 0:
                self.g.status = GOAL_BUDGET_LIMITED
                self.budget_steer = BUDGET_STEER_TEMPLATE.format(used=self.g.tokens_used, budget=self.g.token_budget)
    def consume_budget_steer(self):
        with self.lock:
            s = self.budget_steer
            self.budget_steer = ""
            return s, s != ""
    def continuation(self):
        with self.lock:
            if self.g is None or self.g.status != GOAL_ACTIVE:
                self.cont_pending = False
                return "", False
            if self.cont_pending:
                self.cont_pending = False
                if self.g.tokens_used == self.cont_tokens_at: self.cont_suppressed = True
            if self.cont_suppressed: return "", False
            self.cont_pending = True
            self.cont_tokens_at = self.g.tokens_used
            return format_goal_continuation(self.g), True
    def suppress(self):
        with self.lock:
            self.cont_pending = False
            self.cont_suppressed = True
    def snapshot(self):
        with self.lock: return None if self.g is None else self.g.to_dict()
    def clear(self):
        with self.lock:
            self.g = None
            self._reset_runtime_locked()
            self.skip_next_delta = False

class GetGoalTool:
    def definition(self):
        return {"name": "get_goal", "description": "查看当前 goal。", "parameters": {"type": "object", "properties": {}, "required": []}}
    def execute(self, args, ctx):
        snap = ctx.goal.snapshot()
        return "现在没有 goal。" if snap is None else json.dumps(snap, ensure_ascii=False)

class CreateGoalTool:
    def definition(self):
        return {"name": "create_goal", "description": "立新 goal。", "parameters": {"type": "object", "properties": {"objective": {"type": "string"}, "token_budget": {"type": "integer"}}, "required": ["objective"]}}
    def execute(self, args, ctx):
        try: in_ = json.loads(args) or {}
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        objective = in_.get("objective", "") or ""
        try: budget = int(in_.get("token_budget") or 0)
        except (TypeError, ValueError): return "错误: token_budget 必须是整数"
        try: snap = ctx.goal.create(objective, budget)
        except ValueError as e: return "错误: " + str(e)
        print(f"[goal 已立：{snap['objective']}]", file=sys.stderr)
        return f"goal 已立：{snap['objective']}"

class UpdateGoalTool:
    def definition(self):
        return {"name": "update_goal", "description": "更新 goal 状态，只有 complete 或 blocked。", "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["complete", "blocked"]}}, "required": ["status"]}}
    def execute(self, args, ctx):
        try: in_ = json.loads(args) or {}
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        status = in_.get("status", "")
        if status not in (GOAL_COMPLETE, GOAL_BLOCKED): return '错误: status 只能是 "complete" 或 "blocked"'
        try: snap = ctx.goal.set_status(status)
        except ValueError as e: return "错误: " + str(e)
        print(f"[goal → {snap['status']}]", file=sys.stderr)
        return f"goal → {snap['status']}"

def handle_goal_command(line, ctx):
    if not line.startswith("/goal"): return False
    parts = line.split()
    if len(parts) == 1 or parts[1] == "status":
        snap = ctx.goal.snapshot()
        if snap is None: print("[没有 goal]", file=sys.stderr)
        else: print(f"[goal {snap['status']}：{snap['objective']}（已用 {snap['tokens_used']} tokens）]", file=sys.stderr)
        return True
    cmd = parts[1]
    if cmd == "pause":
        try:
            snap = ctx.goal.set_status(GOAL_PAUSED)
            print(f"[goal 已暂停]", file=sys.stderr)
        except ValueError as e: print(f"[错误: {e}]", file=sys.stderr)
        return True
    if cmd == "resume":
        try:
            snap = ctx.goal.set_status(GOAL_ACTIVE)
            print(f"[goal 已恢复]", file=sys.stderr)
        except ValueError as e: print(f"[错误: {e}]", file=sys.stderr)
        return True
    if cmd == "clear":
        ctx.goal.clear()
        print("[goal 已删除]", file=sys.stderr)
        return True
    return True