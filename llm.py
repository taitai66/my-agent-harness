# llm.py — 发请求、token 估算、上下文窗口。不碰业务逻辑。
import json
import os
import urllib.error
import urllib.request

from config import WORK_DIR


def context_window(model):
    m = model.lower()
    if "deepseek" in m:
        return 1_000_000
    if "gpt-4" in m:
        return 128_000
    if "claude" in m:
        return 200_000
    return 128_000


def effective_context_window(model):
    v = os.environ.get("CONTEXT_WINDOW", "")
    if v:
        try:
            n = int(v)
        except ValueError:
            n = 0
        if n > 0:
            return n
    return context_window(model)


def estimate_text(s):
    ascii_count, multi = 0, 0
    for ch in s:
        if ord(ch) < 128:
            ascii_count += 1
        else:
            multi += len(ch.encode("utf-8"))
    return ascii_count // 4 + int(multi / 1.5 + 0.5)


def estimate_tokens(msgs):
    total = 0
    for m in msgs:
        total += estimate_text(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            total += estimate_text(tc["function"]["name"]) + estimate_text(tc["function"]["arguments"])
    return total


def send(base, api_key, model, history, tools):
    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": history,
    }
    if tools:
        payload["tools"] = tools
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')}") from None
    except OSError as e:
        raise RuntimeError(f"请求失败: {e}") from None

    try:
        r = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"解析失败: {e}\n原始响应: {raw.decode(errors='replace')}") from None
    if r.get("error"):
        raise RuntimeError(f"API 错误 [{r['error'].get('type')}]: {r['error'].get('message')}")
    if not r.get("choices"):
        raise RuntimeError(f"空响应: {raw.decode(errors='replace')}")
    return r

def send_stream(base, api_key, model, history, tools, on_delta):
    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": history,
        "stream": True,
        "stream_options": {"include_usage": True}
    }
    if tools:
        payload["tools"] = tools
        
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )
    
    collected = {}
    usage = {}
    full_content = []  # 修复：用来收集流式文本
    
    with urllib.request.urlopen(req) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
                
            if chunk.get("usage"):
                usage = chunk["usage"]
                
            for ch in chunk.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content"):
                    on_delta(d["content"])
                    full_content.append(d["content"])  # 收集文本
                # 合并流式的 tool_calls
                for tc in d.get("tool_calls") or []:
                    i = tc.get("index", 0)
                    slot = collected.setdefault(i, {
                        "id": "", "type": "function",
                        "function": {"name": "", "arguments": ""}
                    })
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]

    # 修复：把拼接好的文本放进 content
    reply_content = "".join(full_content) if full_content else None

    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": reply_content,
                "tool_calls": [collected[i] for i in sorted(collected)] or None
            },
            "finish_reason": "tool_calls" if collected else "stop"
        }],
        "usage": usage
    }