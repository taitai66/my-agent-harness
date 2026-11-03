# tools/terminal.py
import json
from config import BG_INTERACTIVE

class TerminalOutputTool:
    def definition(self):
        return {"name": "terminal_output", "description": "查看 interactive 进程输出（快照，防轮询）。",
                "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "lines": {"type": "integer"}}, "required": ["id"]}}
    def execute(self, args, ctx):
        try: in_ = json.loads(args) or {}
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        id_ = in_.get("id", "")
        mgr = ctx.bg
        if mgr is None: return "错误: 这个运行环境没有后台任务管理器。"
        p = mgr.get(id_)
        if p is None: return f"错误: 没有叫 {id_} 的后台任务。"
        if p.mode != BG_INTERACTIVE: return f"错误: {id_} 是 async 任务。"
        n = int(in_.get("lines") or 200)
        out, status, blocked = p.tail_lines(n)
        header = "[状态: " + status + "]"
        if out == "":
            msg = header + "\n（还没有输出）"
            if blocked: msg += "\n\n[停：30 秒内第三次空查了。]"
            return msg
        return header + "\n```\n" + out + "\n```"

class TerminalInputTool:
    def definition(self):
        return {"name": "terminal_input", "description": "往 interactive 进程 stdin 写内容。",
                "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "input": {"type": "string"}}, "required": ["id", "input"]}}
    def execute(self, args, ctx):
        try: in_ = json.loads(args) or {}
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        id_, text = in_.get("id", ""), in_.get("input", "") or ""
        mgr = ctx.bg
        if mgr is None: return "错误: 这个运行环境没有后台任务管理器。"
        p = mgr.get(id_)
        if p is None: return f"错误: 没有叫 {id_} 的后台任务。"
        if p.mode != BG_INTERACTIVE: return f"错误: {id_} 是 async 任务。"
        if p.done: return f"错误: {id_} 已经退出了。"
        try:
            p.stdin.write(text.encode("utf-8"))
            p.stdin.flush()
        except (BrokenPipeError, OSError) as e: return "错误: " + str(e)
        return f"已向 {id_} 写入 {len(text.encode('utf-8'))} 字节。"