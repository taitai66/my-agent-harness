# smoke_test.py — 不调 LLM，只验证工具与模块装配
import json
import os
import tempfile

import harness_v0 as h


def main():
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "a.txt")

    reg = h.Registry(
        h.ReadFileTool(), h.WriteFileTool(), h.EditFileTool(),
        h.BashTool(), h.GrepTool(), h.GlobTool(),
    )

    # ① 文件不存在 → read_file 报错
    r = reg.execute("read_file", json.dumps({"path": p}))
    assert r.startswith("错误"), r

    # ② 文件不存在 → write_file 可以直接创建（不触发 read-before-write）
    r = reg.execute("write_file", json.dumps({"path": p, "content": "v0"}))
    assert "写入" in r and not r.startswith("错误"), r

    # ③ 测 read-before-write：用 Python 直接创建一个文件（绕过工具），
    #    注册表不知道它存在 → write_file 必须拒绝
    p2 = os.path.join(tmp, "b.txt")
    with open(p2, "w", encoding="utf-8") as f:
        f.write("external")
    r = reg.execute("write_file", json.dumps({"path": p2, "content": "hello"}))
    assert "还没读过" in r, r

    # ④ 读 → 写 → 编辑（p 在 ② 里已被记为“读过”，所以下面 write/edit 放行）
    reg.execute("read_file", json.dumps({"path": p}))
    r = reg.execute("write_file", json.dumps({"path": p, "content": "v1"}))
    assert "写入" in r and not r.startswith("错误"), r

    r = reg.execute("edit_file", json.dumps({"path": p, "old_string": "v1", "new_string": "v2"}))
    assert r == "已替换", r

    # ⑤ bash
    r = reg.execute("bash", json.dumps({"command": "echo hi"}))
    assert "hi" in r, r

    # ⑥ grep
    r = reg.execute("grep", json.dumps({"pattern": "v2", "path": p}))
    assert "v2" in r, r

    # ⑦ 会话
    s = h.new_session_file([{"role": "system", "content": "x"}])
    s.history.append({"role": "user", "content": "y"})
    s.save()
    s2 = h.load_session(s.id)
    assert len(s2.history) == 2, s2.history

    # ⑧ 预算 / 压缩
    assert h.context_window("gpt-4") == 128_000
    assert h.estimate_text("hello") >= 1
    hist = [{"role": "system", "content": "s"}] + [
        {"role": "user", "content": "u%d" % i} for i in range(20)
    ]
    assert 0 <= h.safe_split_index(hist, 10) <= len(hist)
    assert h.compact_keep_budget(1000, 750) > 0

    # ⑨ skill（没有 .harness-skills 时返回空字典，不报错）
    sk = h.discover_skills()
    if sk:
        st = h.SkillTool(sk)
        name = next(iter(sk))
        r = st.execute(json.dumps({"name": name}))
        assert name in r, r

    print("SMOKE OK")


if __name__ == "__main__":
    main()