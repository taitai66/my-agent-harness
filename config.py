# config.py — 所有常量。谁用谁 import，这里不做逻辑。
import os

WORK_DIR = os.getcwd()

# 目录/文件
SESSION_DIR = ".sessions"
TRASH_DIR = ".trash"
SKILLS_ROOT = ".harness-skills"
SKILLS_PROPOSED_ROOT = ".harness-skills-proposed"
PROJECT_RULES_FILE = ".harnessrules"
MEMORY_FILE = "MEMORY.md"
MCP_CONFIG_FILE = "mcp.json"

# 预算/压缩
BUDGET_FRACTION = 0.75
COMPACT_KEEP_FRACTION = 0.30
MAX_ROUNDS = 30
CHILD_MAX_ROUNDS = 6

# bash
DEFAULT_BASH_TIMEOUT = 30
MAX_BASH_TIMEOUT = 120
MAX_BASH_OUTPUT = 8 * 1024

# 权限三档
DECISION_ALLOW = 0
DECISION_ASK = 1
DECISION_DENY = 2

BASH_RULES = [
    ("rm -rf /", DECISION_DENY),
    ("rm -rf ~", DECISION_DENY),
    ("rm -rf", DECISION_ASK),
    ("sudo ", DECISION_ASK),
    ("git push --force", DECISION_ASK),
    ("curl ", DECISION_ASK),
    ("ls", DECISION_ALLOW),
    ("cat ", DECISION_ALLOW),
    ("pwd", DECISION_ALLOW),
    ("echo ", DECISION_ALLOW),
    ("git status", DECISION_ALLOW),
]

# 事件/收件箱
QUEUE_PREFIX = "/q "

# 后台任务
BG_ASYNC = "async"
BG_INTERACTIVE = "interactive"
POLL_WINDOW = 30
MAX_EMPTY_POLLS = 3
SHORT_ASYNC_DURATION = 5.0
MAX_BG_OUTPUT_LINES = 2000

# 定时唤醒
MAX_LOOP_LIFETIME = 30 * 60
MIN_WAKEUP_DELAY = 5

# goal 状态
GOAL_ACTIVE = "active"
GOAL_PAUSED = "paused"
GOAL_BLOCKED = "blocked"
GOAL_BUDGET_LIMITED = "budget_limited"
GOAL_COMPLETE = "complete"

# 并发/编排
MAX_PARALLEL_SUB_AGENTS = 4
RESULTS_PLACEHOLDER = "{{results}}"
PLAN_SHAPE_HINT = '{"stages": [["阶段1 prompt", "..."], ["阶段2 prompt，可写 {{results}}"]]}'

# 检索/联网
BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
GREP_MAX_LINES = 200
GLOB_MAX_PATHS = 200
WEB_FETCH_MAX_BYTES = 16 * 1024

# 浏览器
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
CLICK_MOVE_SETTLE = 0.05
BROWSER_ACTION_TIMEOUT = 45.0
OBSERVE_MAX_ITEMS = 60

NAMED_KEYS = {
    "enter": ("Enter", "Enter", 13),
    "escape": ("Escape", "Escape", 27),
    "tab": ("Tab", "Tab", 9),
    "backspace": ("Backspace", "Backspace", 8),
    "space": (" ", "Space", 32),
    "arrowdown": ("ArrowDown", "ArrowDown", 40),
    "arrowup": ("ArrowUp", "ArrowUp", 38),
}

BROWSER_SETUP_GUIDE = (
    "找不到可连接的 Chrome 调试端口。两种方式：\n"
    "A) 用你日常的 Chrome（带登录态）：地址栏打开 chrome://inspect/#remote-debugging，\n"
    "   勾选 'Allow remote debugging for this browser instance'，重启 Chrome，\n"
    "   之后第一次拨号时 Chrome 会弹授权提示，点允许。\n"
    "B) 独立实例（干净、无登录态，适合练手）：\n"
    "   Windows: chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\\tmp\\harness-chrome\n"
    "   macOS:   /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/harness-chrome"
)