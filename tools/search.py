# tools/search.py
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from config import BROWSER_USER_AGENT, GLOB_MAX_PATHS, GREP_MAX_LINES, WEB_FETCH_MAX_BYTES, WORK_DIR
from sandbox import network_allowed

def rg_path():
    p = shutil.which("rg")
    if not p: raise RuntimeError("找不到 ripgrep。Windows: winget install BurntSushi.ripgrep.MSVC")
    return p

def glob_to_regexp(pattern):
    out = ["^"]
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append(".*")
                i += 1
                if i + 1 < n and pattern[i + 1] == "/":
                    out.append("/?")
                    i += 1
            else: out.append("[^/]*")
        elif c == "?": out.append("[^/]")
        else: out.append(re.escape(c))
        i += 1
    out.append("$")
    return re.compile("".join(out))

_RE_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_BLANK_LINES = re.compile(r"\n{3,}")

def strip_html_to_text(s):
    s = _RE_SCRIPT_STYLE.sub("", s)
    s = _RE_TAG.sub("\n", s)
    s = html.unescape(s)
    lines = [line.strip() for line in s.split("\n") if line.strip()]
    return _RE_BLANK_LINES.sub("\n\n", "\n".join(lines))

def is_textual_content_type(ct):
    ct = (ct or "").lower()
    for prefix in ("text/", "application/json", "application/xml", "application/xhtml", "application/javascript"):
        if ct.startswith(prefix): return True
    return False

class GrepTool:
    def definition(self):
        return {"name": "grep", "description": "在文件内容里搜索。基于 ripgrep。",
                "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}, "include": {"type": "string"}}, "required": ["pattern"]}}
    def execute(self, args, ctx):
        try: in_ = json.loads(args) or {}
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        pattern = in_.get("pattern", "") or ""
        if not pattern: return "错误: pattern 不能为空"
        path, include = in_.get("path", "") or WORK_DIR, in_.get("include", "") or ""
        try: rg = rg_path()
        except RuntimeError as e: return "错误: " + str(e)
        rg_args = [rg, "--color=never", "--line-number", "--max-columns", "500", "--max-columns-preview"]
        if include: rg_args += ["-g", include]
        rg_args += [pattern, path]
        try:
            proc = subprocess.run(rg_args, cwd=WORK_DIR, capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
        except subprocess.TimeoutExpired: return "错误: grep 超时"
        if proc.returncode == 1: return "(没有匹配)"
        if proc.returncode != 0: return "错误: " + proc.stderr.decode(errors="replace")
        lines = [l for l in proc.stdout.decode(errors="replace").split("\n") if l]
        if len(lines) > GREP_MAX_LINES:
            total = len(lines)
            lines = lines[:GREP_MAX_LINES]
            lines.append(f"\n[截断：共 {total} 行，只显示前 {GREP_MAX_LINES} 行。]")
        return "\n".join(lines)

class GlobTool:
    def definition(self):
        return {"name": "glob", "description": "按文件名找文件。基于 ripgrep --files。",
                "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "root": {"type": "string"}}, "required": ["pattern"]}}
    def execute(self, args, ctx):
        try: in_ = json.loads(args) or {}
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        pattern = in_.get("pattern", "") or ""
        if not pattern: return "错误: pattern 不能为空"
        root = in_.get("root", "") or WORK_DIR
        try: rg = rg_path()
        except RuntimeError as e: return "错误: " + str(e)
        try:
            proc = subprocess.run([rg, "--files"], cwd=root, capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
        except subprocess.TimeoutExpired: return "错误: glob 超时"
        if proc.returncode != 0: return "错误: " + proc.stderr.decode(errors="replace")
        rx = glob_to_regexp(pattern)
        matches = []
        for rel in proc.stdout.decode(errors="replace").split("\n"):
            rel = rel.strip()
            if not rel or not rx.match(rel): continue
            full = os.path.join(root, rel)
            try: mtime = os.path.getmtime(full)
            except OSError: mtime = 0
            matches.append((rel, mtime))
        if not matches: return "(没有匹配)"
        matches.sort(key=lambda m: m[1], reverse=True)
        total = len(matches)
        if total > GLOB_MAX_PATHS:
            lines = [m[0] for m in matches[:GLOB_MAX_PATHS]]
            lines.append(f"\n[截断：共 {total} 个文件，只显示前 {GLOB_MAX_PATHS} 个。]")
            return "\n".join(lines)
        return "\n".join(m[0] for m in matches)

class WebFetchTool:
    def definition(self):
        return {"name": "web_fetch", "description": "抓网页返回纯文本。沙箱关网络时不可用。",
                "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}
    def execute(self, args, ctx):
        if not network_allowed(ctx.sandbox): return "错误: 沙箱关闭了网络访问，web_fetch 不可用"
        try: in_ = json.loads(args) or {}
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        url = in_.get("url", "") or ""
        if not url: return "错误: url 不能为空"
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https"): return "错误: 只支持 http/https"
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER_USER_AGENT, "Referer": f"{parsed.scheme}://{parsed.netloc}/", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if not is_textual_content_type(ctype): return f"二进制内容（{ctype}），web_fetch 只处理文本。"
                raw = resp.read(WEB_FETCH_MAX_BYTES + 1)
        except urllib.error.HTTPError as e: return f"错误: HTTP {e.code}"
        except OSError as e: return f"错误: {e}"
        truncated = len(raw) > WEB_FETCH_MAX_BYTES
        raw = raw[:WEB_FETCH_MAX_BYTES]
        text = raw.decode("utf-8", errors="replace")
        if "html" in (ctype or "").lower(): text = strip_html_to_text(text)
        if truncated: text += "\n\n[截断：只保留前 16KB。]"
        return text

class WebSearchTool:
    def definition(self):
        return {"name": "web_search", "description": "搜索网页。有 BRAVE_SEARCH_API_KEY 用 Brave，否则降级 Bing。",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}}
    def execute(self, args, ctx):
        if not network_allowed(ctx.sandbox): return "错误: 沙箱关闭了网络访问"
        try: in_ = json.loads(args) or {}
        except json.JSONDecodeError as e: return "错误: 参数不是合法 JSON: " + str(e)
        query = in_.get("query", "") or ""
        if not query: return "错误: query 不能为空"
        try: max_results = min(int(in_.get("max_results") or 5), 10)
        except (TypeError, ValueError): max_results = 5
        backends = []
        if os.environ.get("BRAVE_SEARCH_API_KEY"): backends.append(("brave", search_brave))
        backends.append(("bing", search_bing))
        last_err, provider, results = "", "", []
        for name, run in backends:
            try: candidates = run(query, max_results)
            except (OSError, urllib.error.HTTPError) as e:
                last_err = f"{name}: {e}"
                print(f"[web_search: {name} 失败（{e}），换下一个后端]", file=sys.stderr)
                continue
            if not candidates:
                last_err = f"{name}: 零结果"
                print(f"[web_search: {name} 零结果，换下一个后端]", file=sys.stderr)
                continue
            provider, results = name, candidates[:max_results]
            break
        resp = {"query": query, "provider": provider, "count": len(results), "results": results}
        if not results and last_err: resp["error"] = last_err
        return json.dumps(resp, ensure_ascii=False, indent=2)

def search_bing(query, max_results):
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=15) as resp: raw = resp.read().decode("utf-8", errors="replace")
    results = []
    for m in re.finditer(r'<li class="b_algo".*?</li>', raw, re.DOTALL):
        block = m.group(0)
        tm = re.search(r'<h2[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not tm: continue
        title = strip_html_to_text(tm.group(2))
        snippet_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        snippet = strip_html_to_text(snippet_m.group(1)) if snippet_m else ""
        results.append({"title": title, "url": tm.group(1), "snippet": snippet})
        if len(results) >= max_results: break
    return results

def search_brave(query, max_results):
    key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
    if not key: raise OSError("BRAVE_SEARCH_API_KEY 没有设置")
    url = "https://api.search.brave.com/res/v1/web/search?q=" + urllib.parse.quote(query) + f"&count={max_results}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "X-Subscription-Token": key})
    with urllib.request.urlopen(req, timeout=15) as resp: data = json.loads(resp.read())
    out = []
    for item in (data.get("web", {}).get("results") or [])[:max_results]:
        out.append({"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("description", "")})
    return out