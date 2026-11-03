# tools/subagent.py
import json
import sys
import threading
from config import CHILD_MAX_ROUNDS, MAX_PARALLEL_SUB_AGENTS, PLAN_SHAPE_HINT, RESULTS_PLACEHOLDER
from llm import estimate_text, send
from memory import compose_system_prompt
from runtime import AgentContext

def run_child_loop(base, api_key, model, reg, history, parent_ctx):
    # 子 agent 隔离：继承父沙箱和技能，但没有 goal、后台进程、事件、浏览器
    child_ctx = AgentContext(work_dir=parent_ctx.work_dir, session=None, registry=reg, sandbox=parent_ctx.sandbox)
    total_tokens = 0
    for _ in range(CHILD_MAX_ROUNDS):
        r = send(base, api_key, model, history, reg.definitions())
        u = r.get("usage") or {}
        total_tokens += u.get("prompt_tokens", 0) + u.get("completion_tokens", 0)
        choice = r["choices"][0]
        msg = choice["message"]
        history.append(msg)
        if choice.get("finish_reason") != "tool_calls":
            return msg.get("content") or "", total_tokens, True
        for tc in msg.get("tool_calls") or []:
            result = reg.execute(tc["function"]["name"], tc["function"]["arguments"], child_ctx)
            history.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
    return history[-1].get("content") or "", total_tokens, False

class SubAgentTool:
    def __init__(self, base, api_key, model, tools, skills):
        self.base, self.api_key, self.model = base, api_key, model
        self.tools, self.skills = tools, skills
    def definition(self):
        return {"name": "sub_agent", "description": "派生一个隔离的子 agent 去完成一个独立子任务。prompt 必须自包含。",
                "parameters": {"type": "object", "properties": {"description": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["description", "prompt"]}}
    def execute(self, args, ctx):
        try: in_ = json.loads(args) or {}
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        description, prompt = in_.get("description", ""), in_.get("prompt", "")
        if not prompt.strip(): return "错误: prompt 不能为空"
        return self.run(description, prompt, ctx)
    def run(self, description, prompt, ctx):
        from agent.registry import Registry
        child_reg = Registry(*self.tools)
        child_history = [{"role": "system", "content": compose_system_prompt(self.skills)}, {"role": "user", "content": prompt}]
        print(f"[子 agent {description!r} 开始]", file=sys.stderr)
        try: reply, tokens, complete = run_child_loop(self.base, self.api_key, self.model, child_reg, child_history, ctx)
        except Exception as e: return "错误: 子 agent 执行失败: " + str(e)
        print(f"[子 agent {description!r} 结束，内部 {tokens} tokens]", file=sys.stderr)
        return reply if complete else "[未完成]\n\n" + reply

def format_results(results):
    return "\n".join(f"【子任务 {i + 1} 的结果】\n{r}\n" for i, r in enumerate(results)).strip()

class WorkflowTool:
    def __init__(self, runner):
        self.runner = runner
    def definition(self):
        return {"name": "workflow", "description": "执行多阶段计划。",
                "parameters": {"type": "object", "properties": {"stages": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}}, "required": ["stages"]}}
    def execute(self, args, ctx):
        try: plan = json.loads(args)
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e) + "。" + PLAN_SHAPE_HINT
        stages = plan.get("stages") if isinstance(plan, dict) else None
        if not stages: return "错误: 计划里一个阶段都没有。" + PLAN_SHAPE_HINT
        prev = []
        for si, prompts in enumerate(stages):
            if not isinstance(prompts, list) or not prompts or not all(isinstance(p, str) for p in prompts):
                return f"错误: 阶段 {si + 1} 不是字符串数组。{PLAN_SHAPE_HINT}"
            results = [None] * len(prompts)
            sem = threading.Semaphore(MAX_PARALLEL_SUB_AGENTS)
            threads = []
            for i, p in enumerate(prompts):
                if prev: p = p.replace(RESULTS_PLACEHOLDER, format_results(prev))
                sem.acquire()
                def worker(i=i, p=p):
                    try: results[i] = self.runner.run(f"阶段{si + 1}-子任务{i + 1}", p, ctx)
                    finally: sem.release()
                t = threading.Thread(target=worker)
                threads.append(t)
                t.start()
            for t in threads: t.join()
            prev = results
        return prev[0] if len(prev) == 1 else format_results(prev)