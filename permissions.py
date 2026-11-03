# permissions.py
import json
import queue

from config import BASH_RULES, DECISION_ASK, DECISION_DENY, DECISION_ALLOW
from events import AskRequest, print_ask

def classify_bash(cmd):
    for pattern, decide in BASH_RULES:
        if decide == DECISION_DENY and pattern in cmd:
            return DECISION_DENY
    for pattern, decide in BASH_RULES:
        if decide == DECISION_ASK and pattern in cmd:
            return DECISION_ASK
    trimmed = cmd.lstrip(" \t")
    for pattern, decide in BASH_RULES:
        if decide != DECISION_ALLOW or pattern not in cmd:
            continue
        if trimmed.startswith(pattern) and not contains_shell_chain(cmd):
            return DECISION_ALLOW
    return DECISION_ASK

def contains_shell_chain(cmd):
    return any(ch in cmd for ch in ";|&$()`\n")

def confirm(prompt, ctx):
    req = AskRequest(prompt)
    ctx.events.put(("ask", req))
    while True:
        try:
            return req.resp.get(timeout=0.05)
        except queue.Empty:
            if ctx.cancel_event.is_set():
                return False

def ask_approval(cmd, ctx):
    return confirm(f"模型想执行: {cmd}", ctx)

def command_of(args):
    try:
        return (json.loads(args) or {}).get("command", "") or ""
    except json.JSONDecodeError:
        return ""