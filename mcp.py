# mcp.py — MCP 客户端、工具适配器、连接服务器。
import json
import shutil
import subprocess
import sys
import threading

from config import MCP_CONFIG_FILE
from llm import estimate_text


def load_mcp_config():
    try:
        with open(MCP_CONFIG_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
        return data.get("mcpServers", {}) or {}
    except OSError:
        return {}
    except json.JSONDecodeError as e:
        print(f"警告: {MCP_CONFIG_FILE} 不是合法 JSON，忽略: {e}", file=sys.stderr)
        return {}


class MCPClient:
    def __init__(self, name, command, args, env):
        import os
        exe = shutil.which(command) or command
        full_env = {**os.environ, **(env or {})}
        self.name = name
        self.proc = subprocess.Popen(
            [exe] + list(args or []),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=full_env,
            bufsize=0,
        )
        self.lock = threading.Lock()
        self.next_id = 0

    def _send(self, obj):
        self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def call(self, method, params):
        with self.lock:
            self.next_id += 1
            req_id = self.next_id
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError(f"MCP 服务 {self.name!r} 进程退出了")
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    m = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if m.get("method") or m.get("id") != req_id:
                    continue
                if m.get("error"):
                    e = m["error"]
                    raise RuntimeError(f"MCP 错误 {e.get('code')}: {e.get('message')}")
                return m.get("result")

    def notify(self, method):
        self._send({"jsonrpc": "2.0", "method": method})

    def initialize(self):
        res = self.call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "learnharness", "version": "0.1"},
        })
        self.notify("notifications/initialized")
        info = (res or {}).get("serverInfo", {})
        print(f"[MCP 服务 {self.name!r} 握手完成：{info.get('name')} v{info.get('version')}，"
              f"协议 {(res or {}).get('protocolVersion')}]", file=sys.stderr)

    def list_tools(self):
        res = self.call("tools/list", {})
        return (res or {}).get("tools", []) or []


class MCPTool:
    def __init__(self, client, remote):
        self.client = client
        self.remote = remote

    def definition(self):
        return {
            "name": "mcp__" + self.client.name + "__" + self.remote["name"],
            "description": "[来自 MCP 服务 " + self.client.name + "] " + self.remote.get("description", ""),
            "parameters": self.remote.get("inputSchema", {"type": "object", "properties": {}}),
        }

    def execute(self, args):
        try:
            arguments = json.loads(args) if args else {}
        except json.JSONDecodeError as e:
            return "错误: 参数不是合法 JSON: " + str(e)
        try:
            res = self.client.call("tools/call", {
                "name": self.remote["name"],
                "arguments": arguments,
            })
        except Exception as e:
            return "错误: " + str(e)
        parts = []
        for c in (res or {}).get("content") or []:
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            else:
                parts.append(f"[未处理的内容类型 {c.get('type')!r}]")
        text = "\n".join(parts)
        if (res or {}).get("isError"):
            return "错误: 工具执行失败: " + text
        return text


def connect_mcp_servers(tools):
    cfg = load_mcp_config()
    for name in sorted(cfg.keys()):
        sc = cfg[name]
        try:
            client = MCPClient(name, sc.get("command", ""), sc.get("args"), sc.get("env"))
            client.initialize()
            remotes = client.list_tools()
        except Exception as e:
            print(f"警告: MCP 服务 {name!r} 接入失败，跳过: {e}", file=sys.stderr)
            continue
        cost = 0
        for rt in remotes:
            tools.append(MCPTool(client, rt))
            raw_schema = json.dumps(rt.get("inputSchema", {}), ensure_ascii=False)
            cost += estimate_text(rt.get("name", "") + rt.get("description", "") + raw_schema)
        print(f"[MCP 服务 {name!r}：接入 {len(remotes)} 个工具，声明约 {cost} tokens，随 tools 数组每轮都算钱]",
              file=sys.stderr)
    return tools