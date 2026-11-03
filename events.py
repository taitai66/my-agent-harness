# events.py
import queue
import sys
import threading

QUEUE_PREFIX = "/q "

def start_input_reader(ctx):
    def reader():
        while True:
            try:
                line = sys.stdin.readline()
            except (OSError, ValueError):
                break
            if line == "":
                ctx.input_closed.set()
                ctx.events.put(("eof", None))
                return
            ctx.events.put(("line", line.rstrip("\n")))
    threading.Thread(target=reader, daemon=True).start()

class Inbox:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = []
    def enqueue(self, text, standalone):
        if not text.strip(): return
        with self.lock:
            self.items.append((text, standalone))
    def drain_steer(self):
        with self.lock:
            out = [text for text, standalone in self.items if not standalone]
            self.items = [item for item in self.items if item[1]]
        return out
    def drain_queued(self):
        with self.lock:
            out = [text for text, _ in self.items]
            self.items = []
        return out

class AskRequest:
    def __init__(self, prompt):
        self.prompt = prompt
        self.resp = queue.Queue(maxsize=1)

def print_ask(prompt):
    print(f"\n⚠️  {prompt}\n允许吗？(y/N) ", end="", file=sys.stderr, flush=True)