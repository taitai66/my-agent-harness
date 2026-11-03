# sandbox_policy.py —— Windows 上的"策略沙箱"
# 明确标注：这不是 OS 级沙箱，是 Python 层的字符串检查。
# 它能拦住明显的越界，但防不住精心构造的命令。真沙箱要 OS 支持（macOS sandbox-exec / Linux Landlock）。
import json
import os
import re
import subprocess
import sys


def main():
    if len(sys.argv) < 3:
        print("用法: sandbox_policy.py <policy_json> <command>", file=sys.stderr)
        sys.exit(2)
    policy = json.loads(sys.argv[1])
    command = sys.argv[2]

    home = os.path.realpath(os.path.expanduser("~"))
    work = os.path.realpath(policy.get("work_dir", os.getcwd()))
    tmp = os.path.realpath(policy.get("tmp_dir", os.environ.get("TEMP", "/tmp")))
    write_roots = [os.path.realpath(p) for p in policy.get("write_roots", [])] or [work, tmp]
    read_roots = [os.path.realpath(p) for p in policy.get("read_roots", [])] or [work, tmp]

    # 1. 检查明显的越界写：> 或 >> 后面跟着家目录路径
    #    这是一个非常粗糙的检查——真实沙箱在内核层，跟字符串无关
    write_pattern = re.compile(r'(?:>>?|tee)\s+("([^"]+)"|\'([^\']+)\'|([^\s|&;<>]+))')
    for m in write_pattern.finditer(command):
        target = m.group(2) or m.group(3) or m.group(4) or ""
        target = target.strip()
        if not target:
            continue
        # 展开 ~
        expanded = os.path.expanduser(target)
        rp = os.path.realpath(expanded)
        if any(rp == w or rp.startswith(w + os.sep) for w in write_roots):
            continue
        print(f"sh: {target}: Operation not permitted (policy sandbox: write outside {write_roots})",
              file=sys.stderr)
        sys.exit(1)

    # 2. 检查读家目录：cat ~/xxx 或 cat /Users/xxx 或 type C:\Users\xxx
    #    只在命令里直接出现绝对路径时检查
    read_pattern = re.compile(r'(?:^|\s)(cat|type|more|head|tail)\s+("([^"]+)"|\'([^\']+)\'|([^\s|&;<>]+))')
    for m in read_pattern.finditer(command):
        target = m.group(3) or m.group(4) or m.group(5) or ""
        target = target.strip()
        if not target or target.startswith("-"):
            continue
        expanded = os.path.expanduser(target)
        rp = os.path.realpath(expanded)
        # 家目录下的文件，且不在 read_roots 里
        if rp.startswith(home + os.sep):
            if any(rp == r or rp.startswith(r + os.sep) for r in read_roots):
                continue
            print(f"sh: {target}: Operation not permitted (policy sandbox: read outside {read_roots})",
                  file=sys.stderr)
            sys.exit(1)

    # 3. 检查网络命令
    if not policy.get("allow_network", False):
        for net_cmd in ("curl", "wget", "nc ", "ncat", "ssh ", "scp ", "ftp ", "Invoke-WebRequest", "iwr "):
            if net_cmd in command:
                print(f"sh: {net_cmd.strip()}: Operation not permitted (policy sandbox: network disabled)",
                      file=sys.stderr)
                sys.exit(1)

    # 4. 检查通过
    result = subprocess.run(command, shell=True, cwd=policy.get("work_dir", os.getcwd()),
                            stdout=sys.stdout, stderr=sys.stderr)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()