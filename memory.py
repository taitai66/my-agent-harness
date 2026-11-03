# memory.py — 读取项目规则、跨会话记忆，拼装最终的 system prompt。
import os

from config import PROJECT_RULES_FILE, MEMORY_FILE
from prompts import (
    BASE_PROMPT,
    MEMORY_GUIDANCE,
    SKILL_AUTHORING_GUIDANCE,
)
from skills import skill_manifest


def read_project_rules():
    try:
        with open(PROJECT_RULES_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def read_memory():
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def compose_system_prompt(skills):
    prompt = BASE_PROMPT
    rules = read_project_rules()
    if rules:
        prompt += "\n\n---\n\n# 项目约定 (" + PROJECT_RULES_FILE + ")\n\n" + rules
    manifest = skill_manifest(skills)
    if manifest:
        prompt += "\n\n---\n\n" + manifest
    prompt += "\n\n---\n\n" + SKILL_AUTHORING_GUIDANCE
    prompt += "\n\n---\n\n" + MEMORY_GUIDANCE
    mem = read_memory()
    if mem:
        prompt += "\n\n## 你目前记下的内容\n\n" + mem
    else:
        prompt += "\n\n## 你目前记下的内容\n\n（还是空的——这是这个项目第一次有你可读的记忆）"
    return prompt