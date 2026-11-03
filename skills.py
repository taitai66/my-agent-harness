# skills.py — skill 发现、解析、清单、工具。
import json
import os

from config import SKILLS_ROOT


class Skill:
    def __init__(self, name, description, body, dir):
        self.name = name
        self.description = description
        self.body = body
        self.dir = dir


def discover_skills():
    out = {}
    try:
        entries = os.scandir(SKILLS_ROOT)
    except OSError:
        return out
    with entries:
        for entry in entries:
            if not entry.is_dir():
                continue
            try:
                with open(os.path.join(entry.path, "SKILL.md"), encoding="utf-8-sig") as f:
                    data = f.read()
            except OSError:
                continue
            desc, body, ok = parse_skill_file(data)
            if not ok or not desc:
                continue
            out[entry.name] = Skill(entry.name, desc, body, entry.path)
    return out


def parse_skill_file(text):
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return "", "", False
    description = ""
    i = 1
    while i < len(lines):
        if lines[i].strip() == "---":
            break
        if ":" in lines[i]:
            key, _, val = lines[i].partition(":")
            if key.strip() == "description":
                description = val.strip()
        i += 1
    if i >= len(lines):
        return "", "", False
    body = "\n".join(lines[i + 1:]).strip()
    return description, body, True


def skill_manifest(skills):
    if not skills:
        return ""
    lines = ["# 可用的 skill", "",
             "任务匹配某条 description 时，先调用 skill 工具（参数 name）加载完整指令再动手"
             "——不要只凭这一句描述去猜正文写了什么。", ""]
    for name in sorted(skills):
        lines.append(f"- {name}: {skills[name].description}")
    return "\n".join(lines).strip()


class SkillTool:
    def __init__(self, skills):
        self.skills = skills

    def definition(self):
        return {
            "name": "skill",
            "description": "加载一个 skill 的完整指令。先看系统提示里“可用的 skill”清单，"
                           "任务匹配某条 description 时，用这个工具把对应 skill 的正文加载进来再动手。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要加载的 skill 名字，清单里“-”后面那个词"},
                },
                "required": ["name"],
            },
        }

    def execute(self, args):
        try:
            params = json.loads(args)
        except json.JSONDecodeError as e:
            return "错误: 参数不是合法 JSON: " + str(e)
        name = params.get("name", "")
        sk = self.skills.get(name)
        if sk is None:
            return "错误: 没有叫 " + name + " 的 skill——从系统提示的清单里选一个"
        return f'[skill "{sk.name}"，所在目录：{sk.dir}]\n\n{sk.body}'