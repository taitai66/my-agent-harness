# background.py
import platform
import queue
import signal
import subprocess
import sys
import threading
import time
import os

from config import (
    BG_ASYNC, MAX_BG_OUTPUT_LINES, MAX_EMPTY_POLLS, POLL_WINDOW,
    SHORT_ASYNC_DURATION, WORK_DIR,
)
from sandbox import shell_command

class BgProc:
    def __init__(self, id_, command, mode, pid, stdin):
        self.id = id_
        self.command = command
        self.mode = mode
        self.pid = pid
        self.stdin = stdin
        self.start = time.monotonic()
        self.lock = threading.Lock()
        self.lines = []
        self.read_pos = 0
        self.done = False
        self.exit_code = None
        self.empty_poll_count = 0
        self.first_empty_poll = 0.0

    def append(self, raw):
        with self.lock:
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            self.lines.append(text)
            if len(self.lines) > MAX_BG_OUTPUT_LINES:
                drop = len(self.lines) - MAX_BG_OUTPUT_LINES
                self.lines = self.lines[drop:]
                self.read_pos = max(0, self.read_pos - drop)

    def finish(self, exit_code):
        with self.lock:
            self.done = True
            self.exit_code = exit_code

    def status_locked(self):
        return "exited(" + str(self.exit_code) + ")" if self.done else "running"

    def tail_lines(self, n):
        with self.lock:
            chunk = self.lines[-n:] if n > 0 else self.lines[-200:]
            out = "\n".join(chunk)
            blocked = False
            if not self.done and out == "":
                now = time.monotonic()
                if self.empty_poll_count == 0 or now - self.first_empty_poll > POLL_WINDOW:
                    self.first_empty_poll = now
                    self.empty_poll_count = 1
                else:
                    self.empty_poll_count += 1
                    if self.empty_poll_count >= MAX_EMPTY_POLLS:
                        blocked = True
            else:
                self.empty_poll_count = 0
                self.first_empty_poll = 0.0
            return out, self.status_locked(), blocked

    def read_new(self):
        with self.lock:
            new = self.lines[self.read_pos:]
            self.read_pos = len(self.lines)
            return "\n".join(new), self.status_locked()

def format_bg_note(p):
    out, status = p.read_new()
    parts = ["<system-reminder>\n[后台任务完成]\n", f"后台进程 {p.id}（`{p.command}`）{status}。"]
    if out:
        parts.append(f"\n新输出（{len(out)} 字节）：\n```\n{out}\n```")
    else:
        parts.append("\n（没有新输出）")
    if p.mode == BG_ASYNC:
        d = time.monotonic() - p.start
        if d < SHORT_ASYNC_DURATION:
            parts.append(f"\n\n[注意：这个任务 {d:.1f}s 就跑完了——这么快的命令根本不需要放后台。]")
    parts.append("\n</system-reminder>")
    return "".join(parts)

class BgManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.seq = 0
        self.procs = {}
        self.done = queue.Queue()

    def start(self, command, mode, sandbox):
        sc = shell_command(command, sandbox)
        if isinstance(sc, tuple):
            cmd_arg, use_shell = sc
            proc = subprocess.Popen(cmd_arg, shell=use_shell, cwd=WORK_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=(platform.system() != "Windows"))
        else:
            proc = subprocess.Popen(sc, cwd=WORK_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=(platform.system() != "Windows"))
        with self.lock:
            self.seq += 1
            id_ = f"bg_{self.seq}"
            p = BgProc(id_, command, mode, proc.pid, proc.stdin)
            self.procs[id_] = p

        reader_done = threading.Event()
        def reader():
            try:
                while True:
                    line = proc.stdout.readline()
                    if line == b"": return
                    p.append(line)
            finally:
                try: proc.stdout.close()
                except OSError: pass
                reader_done.set()

        def finisher():
            proc.wait()
            try: proc.stdin.close()
            except OSError: pass
            reader_done.wait()
            p.finish(proc.returncode)
            self.done.put(format_bg_note(p))

        threading.Thread(target=reader, daemon=True).start()
        threading.Thread(target=finisher, daemon=True).start()
        return id_

    def get(self, id_):
        with self.lock: return self.procs.get(id_)

    def kill(self, p):
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else: os.killpg(p.pid, signal.SIGKILL)
        except Exception: pass

    def kill_all(self):
        with self.lock: procs = list(self.procs.values())
        for p in procs: self.kill(p)

def start_bg_forwarder(ctx):
    def forwarder():
        while True:
            note = ctx.bg.done.get()
            ctx.events.put(("bg", note))
    threading.Thread(target=forwarder, daemon=True).start()