import importlib.util
import json
import os
import subprocess
import sys
import unittest

# 动态 import 23.py（避免顶层触发 main）
spec = importlib.util.spec_from_file_location("harness", "23.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def run(command, sandboxed):
    harness.ACTIVE_SANDBOX = harness.default_sandbox_policy() if sandboxed else None
    try:
        cmd_arg, use_shell = harness.shell_command(command)
        result = subprocess.run(cmd_arg, shell=use_shell, cwd=harness.WORK_DIR,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return result.stdout.decode(errors="replace"), result.returncode
    finally:
        harness.ACTIVE_SANDBOX = None


class SandboxTests(unittest.TestCase):
    def test_allows_cwd_write(self):
        out, code = run('echo hello > test_cwd.txt', sandboxed=True)
        self.assertEqual(code, 0, f"工作目录写入应当成功: out={out!r}")
        os.remove("test_cwd.txt")

    def test_allows_tmp_write(self):
        import tempfile
        target = os.path.join(tempfile.gettempdir(), "sandbox_test_tmp.txt")
        out, code = run(f'echo hello > "{target}"', sandboxed=True)
        self.assertEqual(code, 0, f"临时目录写入应当成功: out={out!r}")
        if os.path.exists(target):
            os.remove(target)

    def test_blocks_home_write(self):
        target = os.path.join(os.path.expanduser("~"), "smoke-should-fail.txt")
        try:
            out, code = run(f'echo pwned > "{target}"', sandboxed=True)
            self.assertNotEqual(code, 0, f"家目录写入应当被拦: out={out!r}")
            self.assertFalse(os.path.exists(target), "文件不应该存在")
        finally:
            if os.path.exists(target):
                os.remove(target)

    def test_no_sandbox_control(self):
        target = os.path.join(os.path.expanduser("~"), "smoke-control.txt")
        try:
            out, code = run(f'echo pwned > "{target}"', sandboxed=False)
            self.assertEqual(code, 0, f"无沙箱时家目录写入应当成功（对照组）: out={out!r}")
            self.assertTrue(os.path.exists(target), "无沙箱时文件应当存在")
        finally:
            if os.path.exists(target):
                os.remove(target)


if __name__ == "__main__":
    unittest.main(verbosity=2)