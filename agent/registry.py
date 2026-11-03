# agent/registry.py
import json
import os
import sys

from config import DECISION_ASK, DECISION_DENY, SKILLS_ROOT
from llm import estimate_text
from permissions import ask_approval, classify_bash, command_of, confirm

class Registry:
    def __init__(self, *tools):
        self.tools = {t.definition()["name"]: t for t in tools}
        self.has_read = set()

    def definitions(self):
        return [{"type": "function", "function": t.definition()} for t in self.tools.values()]

    def execute(self, name, args, ctx):
        t = self.tools.get(name)
        if t is None: return "错误: 未知工具 " + name

        if name in ("write_file", "edit_file"):
            path = path_of(args)
            if path and os.path.exists(path) and path not in self.has_read:
                return "错误: " + path + " 已存在但这个会话里还没读过它。"
            if path.startswith(SKILLS_ROOT + "/"):
                if not confirm("模型想把一份 skill 写进生效目录：" + path, ctx):
                    return "错误: 权限拒绝"

        if name == "bash":
            cmd = command_of(args)
            if cmd:
                decision = classify_bash(cmd)
                if decision == DECISION_DENY: return "错误: 权限拒绝"
                if decision == DECISION_ASK and not ask_approval(cmd, ctx):
                    return "错误: 权限拒绝——用户没有批准"

        result = t.execute(args, ctx)
        path = path_of(args)
        if path and not result.startswith("错误:"): self.has_read.add(path)
        if name == "skill" and not result.startswith("错误:"):
            print(f"[skill 正文进入对话：约 {estimate_text(result)} tokens]", file=sys.stderr)
        return result

def path_of(args):
    try: return (json.loads(args) or {}).get("path", "") or ""
    except json.JSONDecodeError: return ""