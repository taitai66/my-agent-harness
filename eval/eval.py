# eval/eval.py
import json
import os
import sys
import io
import queue
import threading
import contextlib

# 把项目根目录加入 sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

from config import WORK_DIR
from llm import send, estimate_tokens, effective_context_window
from session import new_session_file
from memory import compose_system_prompt
from skills import discover_skills
from agent.registry import Registry
from agent.loop import run_turn
from runtime import AgentContext

# 导入所有工具
from tools.files import ReadFileTool, WriteFileTool, EditFileTool
from tools.bash import BashTool
from tools.terminal import TerminalOutputTool, TerminalInputTool
from tools.search import GrepTool, GlobTool, WebFetchTool, WebSearchTool
from tools.subagent import SubAgentTool, WorkflowTool
from tools.schedule import ScheduleWakeupTool
from tools.goal import GetGoalTool, CreateGoalTool, UpdateGoalTool
from tools.browser import BrowserTool


class MockInbox:
    def drain_steer(self): return []
    def drain_queued(self): return []

def auto_approve(ctx, stop_event):
    """后台线程：自动同意所有的权限请求"""
    while not stop_event.is_set():
        try:
            tag, payload = ctx.events.get(timeout=0.1)
            if tag == "ask":
                payload.resp.put(True)  # 自动按 y
        except queue.Empty:
            pass

def run_eval():
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("MODEL", "")
    base = os.environ.get("OPENAI_BASE_URL", "") or "https://api.openai.com/v1"
    
    skills = discover_skills()
    tools = [ReadFileTool(), WriteFileTool(), EditFileTool(), BashTool(), GrepTool(), GlobTool()]
    tools.extend([WebSearchTool(), WebFetchTool(), ScheduleWakeupTool(), GetGoalTool(), CreateGoalTool(), UpdateGoalTool(), TerminalOutputTool(), TerminalInputTool()])
    
    sub_agent = SubAgentTool(base, api_key, model, list(tools), skills)
    tools.append(sub_agent)
    tools.extend([WorkflowTool(sub_agent), BrowserTool()])
    
    total = 0
    tool_hits = 0
    kw_hits = 0
    
    cases_path = os.path.join(os.path.dirname(__file__), "cases.jsonl")
    with open(cases_path, "r", encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
        
    for idx, case in enumerate(cases):
        print(f"[{idx+1}/{len(cases)}] 测试: {case['input']}")
        
        reg = Registry(*tools)
        sess = new_session_file([{"role": "system", "content": compose_system_prompt(skills)}])
        ctx = AgentContext(work_dir=WORK_DIR, session=sess, registry=reg, sandbox=None)
        box = MockInbox()
        window = effective_context_window(model)
        
        # 启动自动同意线程
        stop_event = threading.Event()
        approver = threading.Thread(target=auto_approve, args=(ctx, stop_event), daemon=True)
        approver.start()
        
        # 运行一轮（用 StringIO 拦截掉繁杂的 stderr 输出，只看结果）
        err_buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(err_buf):
                run_turn(base, api_key, model, ctx, window, case["input"], box)
        except Exception as e:
            print(f"  执行异常: {e}")
            stop_event.set()
            continue
        finally:
            stop_event.set()
            
        # 统计逻辑
        called_tools = []
        for msg in sess.history:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    called_tools.append(tc["function"]["name"])
                    
        expected_tool = case["expect_tool"]
        if expected_tool == "none" or expected_tool in called_tools:
            tool_hits += 1
            
        # 关键词命中
        final_reply = sess.history[-1].get("content") or ""
        expected_kw = case["expect_kw"]
        if expected_kw in final_reply:
            kw_hits += 1
            
        total += 1
        print(f"  调用工具: {called_tools} | 期望: {expected_tool}")
        print(f"  回复片段: {final_reply[:50].replace(chr(10), ' ')}...")
        
    print("\n" + "="*40)
    print(f"评测完成！共 {total} 条用例")
    print(f"工具选择准确率: {tool_hits}/{total} ({(tool_hits/total)*100:.1f}%)")
    print(f"关键词命中率: {kw_hits}/{total} ({(kw_hits/total)*100:.1f}%)")
    print("="*40)

if __name__ == "__main__":
    run_eval()