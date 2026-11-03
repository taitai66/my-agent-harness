# main.py
import os
import sys
import threading

from config import *
from prompts import *
from llm import *
from session import *
from events import *
from permissions import *
from sandbox import *
from background import *
from skills import *
from mcp import *
from memory import *
from agent.registry import *
from agent.loop import *
from tools.files import *
from tools.bash import *
from tools.terminal import *
from tools.search import *
from tools.subagent import *
from tools.schedule import *
from tools.goal import *
from tools.browser import *
from tools.rag import RagSearchTool
from runtime import AgentContext
from dotenv import load_dotenv
load_dotenv()

def repl(base, api_key, model, ctx, window, first_task):
    print("[常驻模式：/q 排队，/goal pause|resume|clear，/exit 退出]", file=sys.stderr)
    start_input_reader(ctx)
    box = Inbox()
    start_wake_forwarder(ctx)
    start_bg_forwarder(ctx)

    try:
        if first_task:
            run_interruptible(base, api_key, model, ctx, window, first_task, box)
            run_follow_ups(base, api_key, model, ctx, window, box)

        while True:
            prompt, ok = ctx.goal.continuation()
            if ok:
                print("\n[goal 还在进行，自动续一轮]", file=sys.stderr)
                run_interruptible(base, api_key, model, ctx, window, prompt, box)
                run_follow_ups(base, api_key, model, ctx, window, box)
                continue
            print("\n> ", file=sys.stderr, end="", flush=True)
            line, eof = wait_idle(ctx)
            if eof: break
            line = line.strip()
            if not line: continue
            if handle_goal_command(line, ctx): continue
            if line in ("/exit", "/quit"): break
            if line.startswith(QUEUE_PREFIX):
                box.enqueue(line[len(QUEUE_PREFIX):].strip(), True)
            else:
                run_interruptible(base, api_key, model, ctx, window, line, box)
                run_follow_ups(base, api_key, model, ctx, window, box)
    finally:
        ctx.bg.kill_all()
        ctx.browser.close_tab()
        try:
            ctx.session.save()
        except OSError as e:
            print(f"警告: 保存会话失败: {e}", file=sys.stderr)
        print(f"[会话 ID: {ctx.session.id}，用 -c {ctx.session.id} 继续]", file=sys.stderr)
        # 强制退出，防止 Windows 终端被 stdin 卡住
        os._exit(0)
   
def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "-restore":
        if len(sys.argv) != 3:
            print("用法: python3 main.py -restore <文件路径>", file=sys.stderr)
            sys.exit(1)
        sys.exit(restore(sys.argv[2]))

    args = sys.argv[1:]
    sandbox = None
    if len(args) >= 1 and args[0] == "-sandbox":
        args = args[1:]
        if not sandbox_available():
            print("错误: 这台机器提供不了沙箱", file=sys.stderr)
            sys.exit(1)
        sandbox = default_sandbox_policy()
        print(f"[沙箱开启：网络关闭]", file=sys.stderr)

    resume_id = ""
    if len(args) >= 1 and args[0] == "-c":
        if len(args) < 2:
            print("用法: python3 main.py -c <会话ID> [任务]", file=sys.stderr)
            sys.exit(1)
        resume_id, args = args[1], args[2:]

    first_task = args[0] if args else ""

    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("MODEL", "")
    if not api_key or not model:
        print("需要环境变量 OPENAI_API_KEY 和 MODEL", file=sys.stderr)
        sys.exit(1)
    base = os.environ.get("OPENAI_BASE_URL", "") or "https://api.openai.com/v1"

    skills = discover_skills()
    tools = [ReadFileTool(), WriteFileTool(), EditFileTool(), BashTool()]
    if skills:
        tools.append(SkillTool(skills))
        manifest = skill_manifest(skills)
        print(f"[skill 清单：{len(skills)} 个，约 {estimate_text(manifest)} tokens]", file=sys.stderr)

    tools = connect_mcp_servers(tools)
    tools.extend([GrepTool(), GlobTool(), WebSearchTool(), WebFetchTool()])

    sub_agent = SubAgentTool(base, api_key, model, list(tools), skills)
    tools.append(sub_agent)
    tools.extend([
        WorkflowTool(sub_agent), ScheduleWakeupTool(),
        GetGoalTool(), CreateGoalTool(), UpdateGoalTool(),
        TerminalOutputTool(), TerminalInputTool(), BrowserTool(),
        RagSearchTool()  # <-- 注册 RAG 工具
    ])
    reg = Registry(*tools)

    if resume_id:
        try: sess = load_session(resume_id)
        except (OSError, ValueError) as e:
            print(f"错误: 恢复会话失败: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"[恢复会话 {sess.id}，已有 {len(sess.history)} 条消息]", file=sys.stderr)
    else:
        try: sess = new_session_file([{"role": "system", "content": compose_system_prompt(skills)}])
        except OSError as e:
            print(f"错误: 创建会话文件失败: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"[新建会话 {sess.id}]", file=sys.stderr)

    window = effective_context_window(model)
    pre_estimate = estimate_tokens(sess.history)
    print(f"[窗口: {model} → {window} tokens（估算值: {pre_estimate} tokens）]", file=sys.stderr)

    ctx = AgentContext(
        work_dir=WORK_DIR, session=sess, registry=reg, sandbox=sandbox
    )

    sys.exit(repl(base, api_key, model, ctx, window, first_task))

if __name__ == "__main__":
    main()