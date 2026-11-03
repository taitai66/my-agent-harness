# tools/bash.py
import json
import subprocess
from config import BG_ASYNC, BG_INTERACTIVE, DEFAULT_BASH_TIMEOUT, MAX_BASH_OUTPUT, MAX_BASH_TIMEOUT, WORK_DIR
from sandbox import shell_command

def tail(data, max_bytes):
    if len(data) <= max_bytes: return data.decode("utf-8", errors="replace")
    cut = data[-max_bytes:]
    i = cut.find(b"\n")
    if i >= 0: cut = cut[i + 1:]
    skipped = len(data) - len(cut)
    return f"[... 前面 {skipped} 字节被截断 ...]\n" + cut.decode("utf-8", errors="replace")

class BashTool:
    def definition(self):
        return {"name": "bash", "description": "运行 shell 命令。Windows 用 dir/type/findstr。",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}, "run_in_background": {"type": "string", "enum": ["async", "interactive"]}}, "required": ["command"]}}
    
    def execute(self, args, ctx):
        try: params = json.loads(args)
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        command = params.get("command", "") or ""
        if not command.strip(): return "错误: command 不能为空"
        
        run_bg = params.get("run_in_background") or ""
        if run_bg:
            if run_bg not in (BG_ASYNC, BG_INTERACTIVE):
                return '错误: run_in_background 只能是 "async" 或 "interactive"'
            if ctx.bg is not None:
                id_ = ctx.bg.start(command, run_bg, ctx.sandbox)
                return f"已在后台启动，id={id_}，模式={run_bg}。"
                
        d = DEFAULT_BASH_TIMEOUT
        timeout_arg = params.get("timeout") or 0
        if timeout_arg > 0:
            d = timeout_arg
            if d > MAX_BASH_TIMEOUT: return f"错误: timeout 最大 {MAX_BASH_TIMEOUT} 秒。"
            
        cmd_arg, use_shell = shell_command(command, ctx.sandbox)
        try:
            result = subprocess.run(cmd_arg, shell=use_shell, cwd=WORK_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=d)
        except subprocess.TimeoutExpired as e:
            text = tail(e.stdout or b"", MAX_BASH_OUTPUT)
            return f"错误: 命令超过 {d} 秒被终止。\n{text}"
        text = tail(result.stdout, MAX_BASH_OUTPUT)
        if result.returncode != 0: return f"{text}\n[exit status {result.returncode}]"
        if text == "": return "(命令成功，无输出)"
        return text