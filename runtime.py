# runtime.py — 每个实例的状态容器
import queue
import threading
from dataclasses import dataclass, field

from session import Session
from agent.registry import Registry
from background import BgManager
from tools.goal import GoalBox
from tools.schedule import Waker
from tools.browser import BrowserSession

@dataclass
class AgentContext:
    work_dir: str
    session: Session
    registry: Registry
    sandbox: dict | None = None
    
    goal: GoalBox = field(default_factory=GoalBox)
    bg: BgManager = field(default_factory=BgManager)
    waker: Waker = field(default_factory=Waker)
    browser: BrowserSession = field(default_factory=BrowserSession)
    events: queue.Queue = field(default_factory=queue.Queue)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    input_closed: threading.Event = field(default_factory=threading.Event)