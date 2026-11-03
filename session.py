# session.py — 会话持久化。JSONL 追加写，persisted 账本，容错加载。
import json
import os
import secrets
import time
from datetime import datetime

from config import SESSION_DIR


class Session:
    def __init__(self, id, created_at="", history=None):
        self.id = id
        self.created_at = created_at
        self.history = history if history is not None else []
        self.persisted = 0
        self.force_rewrite = False

    def save(self):
        if self.force_rewrite:
            return self.rewrite_all()
        if len(self.history) == self.persisted:
            return
        return self.append_delta()

    def append_delta(self):
        with open(session_path(self.id), "a", encoding="utf-8") as f:
            for msg in self.history[self.persisted:]:
                f.write(encode_record({"type": "message", "message": msg}))
        self.persisted = len(self.history)

    def rewrite_all(self):
        with open(session_path(self.id), "w", encoding="utf-8") as f:
            f.write(encode_record({"type": "meta", "id": self.id, "created_at": self.created_at}))
            for msg in self.history:
                f.write(encode_record({"type": "message", "message": msg}))
        self.persisted = len(self.history)
        self.force_rewrite = False


def encode_record(rec):
    return json.dumps(rec, ensure_ascii=False) + "\n"


def new_session_id():
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)


def session_path(id):
    return os.path.join(SESSION_DIR, id + ".jsonl")


def new_session_file(history):
    os.makedirs(SESSION_DIR, exist_ok=True)
    s = Session(new_session_id(), datetime.now().astimezone().isoformat(), history)
    with open(session_path(s.id), "w", encoding="utf-8") as f:
        f.write(encode_record({"type": "meta", "id": s.id, "created_at": s.created_at}))
        for msg in history:
            f.write(encode_record({"type": "message", "message": msg}))
    s.persisted = len(history)
    return s


def load_session(id):
    with open(session_path(id), "rb") as f:
        data = f.read()
    n = data.rfind(b"\n")
    data = data[:n + 1] if n >= 0 else b""

    s = Session(id)
    for line in data.split(b"\n"):
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"会话文件损坏: {e}") from None
        if rec.get("type") == "meta":
            s.created_at = rec.get("created_at", "")
        elif rec.get("type") == "message":
            if rec.get("message") is not None:
                s.history.append(rec["message"])
    s.persisted = len(s.history)
    return s


def safe_save(sess):
    try:
        sess.save()
    except OSError as e:
        import sys
        print(f"警告: 保存会话失败: {e}", file=sys.stderr)