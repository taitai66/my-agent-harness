# tools/files.py
import json
import os
import time
import sys
from config import TRASH_DIR

def backup_if_exists(path):
    if not os.path.exists(path): return ""
    os.makedirs(TRASH_DIR, exist_ok=True)
    with open(path, "rb") as f: data = f.read()
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(TRASH_DIR, f"{ts}_{os.path.basename(path)}")
    with open(dest, "wb") as f: f.write(data)
    return dest

def restore(path):
    try:
        entries = os.listdir(TRASH_DIR)
    except OSError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    suffix = "_" + os.path.basename(path)
    newest = ""
    for name in entries:
        if name.endswith(suffix) and name > newest:
            newest = name
    if not newest:
        print(f"错误: 没有备份", file=sys.stderr)
        return 1
    try: backup_if_exists(path)
    except OSError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    src = os.path.join(TRASH_DIR, newest)
    try:
        with open(src, "rb") as f: data = f.read()
        with open(path, "wb") as f: f.write(data)
    except OSError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    print(f"已从 {src} 恢复到 {path}")
    return 0

class ReadFileTool:
    def definition(self):
        return {"name": "read_file", "description": "读取文件内容。",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}
    def execute(self, args, ctx):
        try: params = json.loads(args)
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        try:
            with open(params.get("path", ""), encoding="utf-8", errors="replace") as f: return f.read()
        except OSError as e: return "错误: " + str(e)

class WriteFileTool:
    def definition(self):
        return {"name": "write_file", "description": "完整写入文件。",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}
    def execute(self, args, ctx):
        try: params = json.loads(args)
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        path, content = params.get("path", ""), params.get("content", "")
        try: backup = backup_if_exists(path)
        except OSError as e: return "错误: 备份旧内容失败: " + str(e)
        try: os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        except OSError: pass
        try:
            with open(path, "w", encoding="utf-8") as f: f.write(content)
        except OSError as e: return "错误: " + str(e)
        size = len(content.encode("utf-8"))
        if backup: return f"已备份到 {backup}，写入 {path}（{size} 字节）"
        return f"已写入 {path}（{size} 字节）"

class EditFileTool:
    def definition(self):
        return {"name": "edit_file", "description": "精确替换一次。",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}}, "required": ["path", "old_string", "new_string"]}}
    def execute(self, args, ctx):
        try: params = json.loads(args)
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        path, old, new = params.get("path", ""), params.get("old_string", ""), params.get("new_string", "")
        try:
            with open(path, encoding="utf-8") as f: text = f.read()
        except OSError as e: return "错误: " + str(e)
        if old == "": return "错误: old_string 不能为空"
        n = text.count(old)
        if n == 0: return "错误: old_string 找不到"
        if n > 1: return f"错误: old_string 出现了 {n} 次"
        text = text.replace(old, new, 1)
        try:
            with open(path, "w", encoding="utf-8") as f: f.write(text)
        except OSError as e: return "错误: " + str(e)
        return "已替换"