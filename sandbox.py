# sandbox.py
import json
import os
import platform
import sys

from config import WORK_DIR

def default_sandbox_policy():
    import tempfile
    tmp = tempfile.gettempdir()
    if platform.system() == "Windows":
        read_roots = [WORK_DIR, tmp, os.environ.get("SystemRoot", r"C:\Windows")]
        write_roots = [WORK_DIR, tmp]
    else:
        read_roots = [WORK_DIR, tmp, "/usr", "/bin", "/sbin", "/etc", "/var"]
        write_roots = [WORK_DIR, tmp]
    return {"read_roots": read_roots, "write_roots": write_roots,
            "allow_network": False, "work_dir": WORK_DIR, "tmp_dir": tmp}

def sandbox_available():
    if platform.system() == "Darwin":
        return os.path.exists("/usr/bin/sandbox-exec")
    if platform.system() == "Windows":
        return os.path.exists(os.path.join(WORK_DIR, "sandbox_policy.py"))
    return False

def build_sandbox_profile(p):
    def resolve(path):
        try: return os.path.realpath(path)
        except OSError: return path
    def subpaths(roots):
        return " ".join(f'(subpath "{resolve(r)}")' for r in roots)
    lines = ["(version 1)", "(allow default)", "(deny file-write*)",
             f"(allow file-write* {subpaths(p['write_roots'])})",
             '(allow file-write* (literal "/dev/null") (literal "/dev/tty") '
             '(literal "/dev/stdout") (literal "/dev/stderr"))']
    home = os.path.expanduser("~")
    if home:
        lines.append(f'(deny file-read* (subpath "{resolve(home)}"))')
        lines.append(f"(allow file-read* {subpaths(p['read_roots'])})")
    if not p["allow_network"]:
        lines.append("(deny network*)")
    return "\n".join(lines) + "\n"

def shell_command(command, sandbox):
    if sandbox is None:
        if platform.system() == "Windows":
            return command, True
        return ["sh", "-c", command], False
    if platform.system() == "Darwin" and os.path.exists("/usr/bin/sandbox-exec"):
        profile = build_sandbox_profile(sandbox)
        return ["/usr/bin/sandbox-exec", "-p", profile, "/bin/sh", "-c", command], False
    if platform.system() == "Windows":
        policy_json = json.dumps(sandbox, ensure_ascii=False)
        return [sys.executable, os.path.join(WORK_DIR, "sandbox_policy.py"), policy_json, command], False
    return ["sh", "-c", command], False

def network_allowed(sandbox):
    return sandbox is None or sandbox.get("allow_network", False)