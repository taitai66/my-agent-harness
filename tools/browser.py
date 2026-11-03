# tools/browser.py
import base64
import json
import os
import platform
import sys
import threading
import time
import urllib.request
import queue

try:
    from websockets.sync.client import connect as ws_connect
    from websockets.exceptions import InvalidStatus
except ImportError:
    print("需要 websockets: pip install websockets", file=sys.stderr)
    sys.exit(1)

from config import (
    CDP_PORT,
    BROWSER_SETUP_GUIDE,
    CLICK_MOVE_SETTLE,
    BROWSER_ACTION_TIMEOUT,
    OBSERVE_MAX_ITEMS,
    NAMED_KEYS,
)


class CDPClient:
    def __init__(self, ws):
        self.ws = ws
        self.write_lock = threading.Lock()
        self.next_id_lock = threading.Lock()
        self._next_id = 0
        self.pending_lock = threading.Lock()
        self.pending = {}
        self.closed = threading.Event()
        self.close_err = None
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
        try:
            while True:
                data = self.ws.recv()
                try:
                    msg = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    continue
                msg_id = msg.get("id")
                if not msg_id:
                    continue
                with self.pending_lock:
                    q = self.pending.pop(msg_id, None)
                if q is not None:
                    q.put(msg)
        except Exception as e:
            self._shutdown(e)

    def _shutdown(self, err):
        self.closed.set()
        self.close_err = err
        with self.pending_lock:
            pending = list(self.pending.values())
            self.pending.clear()
        for q in pending:
            try:
                q.put({"error": {"message": f"连接已断开: {err}"}})
            except Exception:
                pass

    def call(self, session_id, method, params, timeout=BROWSER_ACTION_TIMEOUT):
        if self.closed.is_set():
            raise RuntimeError(f"CDP 连接已断开: {self.close_err}")
        with self.next_id_lock:
            self._next_id += 1
            req_id = self._next_id
        q = queue.Queue(maxsize=1)
        with self.pending_lock:
            self.pending[req_id] = q
        msg = {"id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        if session_id:
            msg["sessionId"] = session_id
        try:
            with self.write_lock:
                self.ws.send(json.dumps(msg))
        except Exception as e:
            with self.pending_lock:
                self.pending.pop(req_id, None)
            raise RuntimeError(f"发送 CDP 命令失败: {e}")
        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            with self.pending_lock:
                self.pending.pop(req_id, None)
            raise RuntimeError(f"CDP 命令超时（{timeout}s）: {method}")
        if resp.get("error"):
            e = resp["error"]
            raise RuntimeError(f"CDP 错误: {e.get('message', e)}")
        return resp.get("result")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def chrome_profile_dirs():
    home = os.path.expanduser("~")
    if platform.system() == "Windows":
        local = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
        return [os.path.join(local, "Google", "Chrome", "User Data")]
    if platform.system() == "Darwin":
        return [os.path.join(home, "Library", "Application Support", "Google", "Chrome")]
    return [os.path.join(home, ".config", "google-chrome")]


def cdp_endpoint(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as resp:
            v = json.loads(resp.read())
        if v.get("webSocketDebuggerUrl"):
            return v["webSocketDebuggerUrl"]
    except (OSError, ValueError):
        pass
    for d in chrome_profile_dirs():
        try:
            with open(os.path.join(d, "DevToolsActivePort"), encoding="utf-8") as f:
                lines = f.read().strip().split("\n", 1)
        except OSError:
            continue
        if len(lines) == 2:
            return "ws://127.0.0.1:" + lines[0].strip() + lines[1].strip()
    raise RuntimeError(BROWSER_SETUP_GUIDE)


def connect_chrome():
    ws_url = cdp_endpoint(CDP_PORT)
    try:
        ws = ws_connect(ws_url, open_timeout=10, max_size=64 << 20)
    except InvalidStatus as e:
        if e.response.status_code == 403:
            raise RuntimeError(
                f"Chrome 拒绝了这次调试连接（403）。在 Chrome 弹出的授权提示里点允许，再试一次\n{BROWSER_SETUP_GUIDE}"
            ) from None
        raise RuntimeError(f"拨号 {ws_url}: {e}") from None
    except OSError as e:
        raise RuntimeError(f"拨号 {ws_url}: {e}") from None
    return CDPClient(ws)


class Page:
    def __init__(self, cli, session_id, target_id):
        self.cli = cli
        self.session_id = session_id
        self.target_id = target_id


def new_tab(cli):
    created = cli.call("", "Target.createTarget", {"url": "about:blank"})
    attached = cli.call("", "Target.attachToTarget", {
        "targetId": created["targetId"],
        "flatten": True,
    })
    page = Page(cli, attached["sessionId"], created["targetId"])
    for domain in ("Page.enable", "Runtime.enable"):
        cli.call(page.session_id, domain, None)
    return page


def js_str(s):
    return json.dumps(s)


def _page_eval(page, expr):
    result = page.cli.call(page.session_id, "Runtime.evaluate", {
        "expression": expr,
        "returnByValue": True,
        "awaitPromise": True,
    })
    if result.get("exceptionDetails"):
        exc = result["exceptionDetails"]
        msg = exc.get("exception", {}).get("description", "") or exc.get("text", "")
        msg = (msg or "JS 异常").split("\n")[0]
        raise RuntimeError(f"页面 JS 报错: {msg}")
    return (result.get("result") or {}).get("value")


def page_navigate(page, url):
    try:
        start = _page_eval(page, "location.href")
    except RuntimeError:
        start = None
    if not start or start == "about:blank":
        _page_eval(page, f"(()=>{{location.replace({js_str(url)}); return true}})()")
    else:
        page.cli.call(page.session_id, "Page.navigate", {"url": url})
    deadline = time.monotonic() + BROWSER_ACTION_TIMEOUT
    while time.monotonic() < deadline:
        try:
            href = _page_eval(page, "location.href")
            if href and href != "about:blank":
                break
        except RuntimeError:
            pass
        time.sleep(0.1)
    while time.monotonic() < deadline:
        try:
            ready = _page_eval(page, "document.readyState")
            if ready == "complete":
                return
        except RuntimeError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"导航超时: {url}")


OBSERVE_JS = r"""
(() => {
  const sels = "a,button,input,select,textarea,[role=button],[role=link],[role=tab]";
  const els = Array.from(document.querySelectorAll(sels));
  const items = [];
  function visible(el) {
    const s = window.getComputedStyle(el);
    if (s.visibility === "hidden" || s.display === "none") return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function selector(el) {
    if (el.id) return "#" + el.id;
    for (const a of ["data-testid","name","aria-label"]) {
      const v = el.getAttribute(a);
      if (v) return el.tagName.toLowerCase() + "[" + a + "=" + JSON.stringify(v) + "]";
    }
    const parts = [];
    let cur = el;
    while (cur && cur.tagName && parts.length < 5) {
      let part = cur.tagName.toLowerCase();
      const p = cur.parentElement;
      if (p) {
        const sibs = Array.from(p.children).filter(c => c.tagName === cur.tagName);
        if (sibs.length > 1) part += ":nth-of-type(" + (sibs.indexOf(cur) + 1) + ")";
      }
      parts.unshift(part);
      cur = cur.parentElement;
    }
    return parts.join(" > ");
  }
  for (const el of els) {
    if (!visible(el)) continue;
    const text = (el.innerText || el.value || el.getAttribute("aria-label") || el.title || "").trim().slice(0, 80);
    items.push({
      tag: el.tagName.toLowerCase(),
      selector: selector(el),
      text: text,
      href: el.tagName === "A" ? el.href : "",
    });
    if (items.length >= 200) break;
  }
  return {
    url: location.href,
    title: document.title,
    total: items.length,
    items: items,
  };
})()
"""


def page_observe(page):
    digest = _page_eval(page, OBSERVE_JS)
    lines = [f"URL: {digest.get('url')}", f"标题: {digest.get('title')}", ""]
    items = digest.get("items") or []
    for it in items[:OBSERVE_MAX_ITEMS]:
        line = f"- [{it['tag']}] {it['selector']}"
        if it.get("text"):
            line += f"  «{it['text']}»"
        if it.get("href"):
            line += f"  → {it['href']}"
        lines.append(line)
    total = digest.get("total", 0)
    if total > len(items[:OBSERVE_MAX_ITEMS]):
        lines.append(f"（页面上共有 {total} 个可交互元素，这里只列了前 {len(items[:OBSERVE_MAX_ITEMS])} 个——要数总量或读正文，用 eval）")
    return "\n".join(lines)


ELEMENT_CENTER_JS = r"""
(() => {
  const sel = %s;
  let el;
  try { el = document.querySelector(sel); } catch (e) { return {bad: "selector: " + e.message}; }
  if (!el) return {bad: "not-found"};
  el.scrollIntoView({block: "center", inline: "center"});
  const r = el.getBoundingClientRect();
  const x = r.left + r.width / 2;
  const y = r.top + r.height / 2;
  const hit = document.elementFromPoint(x, y);
  const hittable = hit && (hit === el || el.contains(hit) || hit.contains(el));
  return {x: x, y: y, hittable: !!hittable, bad: ""};
})()
"""


def element_center(page, selector):
    res = _page_eval(page, ELEMENT_CENTER_JS % js_str(selector))
    if res.get("bad") == "not-found":
        raise RuntimeError(f"选择器 {selector!r} 没有匹配到元素——先用 observe 看看页面上有什么")
    if res.get("bad", "").startswith("selector:"):
        raise RuntimeError(f"选择器 {selector!r} 语法不合法: {res['bad']}")
    if not res.get("hittable"):
        raise RuntimeError(
            f"元素 {selector!r} 找到了，但它所在的点当前点不到（在视口外，或被别的元素挡着）——"
            "它可能藏在折叠的侧栏/菜单里，先点 observe 清单里 toggle 类的开关把它展开，再点它"
        )
    return res["x"], res["y"]


def page_click(page, selector):
    x, y = element_center(page, selector)
    page.cli.call(page.session_id, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    time.sleep(CLICK_MOVE_SETTLE)
    for ev in (
        {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1},
        {"type": "mouseReleased", "x": x, "y": y, "button": "left", "buttons": 0, "clickCount": 1},
    ):
        page.cli.call(page.session_id, "Input.dispatchMouseEvent", ev)


def page_type(page, selector, text):
    focus_expr = f"(() => {{ const el = document.querySelector({js_str(selector)}); if (!el) return false; el.focus(); return true; }})()"
    if not _page_eval(page, focus_expr):
        raise RuntimeError(f"选择器 {selector!r} 没有匹配到元素——先用 observe 看看页面上有什么")
    page.cli.call(page.session_id, "Input.insertText", {"text": text})


def page_press_key(page, name):
    k = NAMED_KEYS.get(name.strip().lower())
    if k is None:
        raise RuntimeError(f"不认识的按键 {name!r}（支持 enter/escape/tab/backspace/space/arrowdown/arrowup）")
    key, code, vk = k
    text = "\r" if key == "Enter" else (" " if key == " " else "")
    for typ in ("keyDown", "keyUp"):
        params = {"type": typ, "key": key, "code": code, "windowsVirtualKeyCode": vk}
        if text and typ == "keyDown":
            params["text"] = text
        page.cli.call(page.session_id, "Input.dispatchKeyEvent", params)


def page_screenshot(page):
    res = page.cli.call(page.session_id, "Page.captureScreenshot", {"format": "png"})
    data = base64.b64decode(res.get("data", ""))
    os.makedirs(".screenshots", exist_ok=True)
    name = f"screenshot_{int(time.time())}.png"
    path = os.path.join(".screenshots", name)
    with open(path, "wb") as f:
        f.write(data)
    return path


class BrowserSession:
    def __init__(self):
        self.lock = threading.Lock()
        self.cli = None
        self.page = None

    def get_page(self):
        with self.lock:
            if self.cli is not None and self.cli.closed.is_set():
                self.cli = None
                self.page = None
            if self.cli is None:
                self.cli = connect_chrome()
                print("[浏览器已连接]", file=sys.stderr)
            if self.page is None:
                try:
                    self.page = new_tab(self.cli)
                    print("[浏览器已连接，开了自己的 tab——不动你已经打开的任何页面]", file=sys.stderr)
                except Exception as e:
                    raise RuntimeError(f"打开新 tab 失败: {e}")
            return self.page

    def close_tab(self):
        with self.lock:
            if self.cli and self.page:
                try:
                    self.cli.call("", "Target.closeTarget", {"targetId": self.page.target_id})
                except Exception:
                    pass
            self.page = None
            if self.cli:
                try:
                    self.cli.close()
                except Exception:
                    pass
            self.cli = None


class BrowserTool:
    def definition(self):
        return {
            "name": "browser",
            "description": (
                "驱动本机一个真实的 Chrome 完成网页任务：导航、查看页面、点击、输入、执行 JS、截图。"
                "连接用户自己的浏览器，可能带着登录态。只在任务真的需要操作网页界面时用；"
                "已知 URL 的公开内容，web_fetch 更便宜。动手之前先用 observe 看清页面上有什么。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["navigate", "observe", "click", "type", "key", "eval", "screenshot"],
                        "description": "要执行的动作",
                    },
                    "url": {"type": "string", "description": "navigate 的目标 URL（只属于 navigate）"},
                    "selector": {"type": "string", "description": "click/type 用的 CSS 选择器，从 observe 清单里取"},
                    "text": {"type": "string", "description": "type 要输入的文本"},
                    "keys": {"type": "string", "description": "key 要按的键名：enter/escape/tab/backspace/space/arrowdown/arrowup"},
                    "js": {"type": "string", "description": "eval 要执行的 JS 表达式（返回值会被 JSON 化）"},
                },
                "required": ["action"],
            },
        }

    def execute(self, args, ctx):
        try:
            in_ = json.loads(args) or {}
        except json.JSONDecodeError as e:
            return "错误: 参数不是合法 JSON: " + str(e)
        action = in_.get("action", "")
        url = in_.get("url", "") or ""
        if url and action != "navigate":
            return f'错误: url 参数只属于 navigate——先 {{"action":"navigate","url":...}} 打开页面，再单独调 {action}'
        try:
            page = ctx.browser.get_page()
        except Exception as e:
            return "错误: " + str(e)
        try:
            if action == "navigate":
                if not url:
                    return "错误: navigate 需要 url"
                page_navigate(page, url)
                return f"已导航到 {url}"
            if action == "observe":
                return page_observe(page)
            if action == "click":
                sel = in_.get("selector", "")
                if not sel:
                    return "错误: click 需要 selector"
                page_click(page, sel)
                return f"已点击 {sel}"
            if action == "type":
                sel = in_.get("selector", "")
                text = in_.get("text", "")
                if not sel:
                    return "错误: type 需要 selector"
                page_type(page, sel, text)
                return f"已向 {sel} 输入 {len(text)} 个字符"
            if action == "key":
                keys = in_.get("keys", "")
                if not keys:
                    return "错误: key 需要 keys"
                page_press_key(page, keys)
                return f"已按键 {keys}"
            if action == "eval":
                js = in_.get("js", "")
                if not js:
                    return "错误: eval 需要 js"
                val = _page_eval(page, js)
                return json.dumps(val, ensure_ascii=False, default=str)
            if action == "screenshot":
                path = page_screenshot(page)
                return f"截图已保存到 {path}（当前模型看不了图片，用 observe 了解页面）"
            return f"错误: 未知 action {action!r}"
        except Exception as e:
            return "错误: " + str(e)