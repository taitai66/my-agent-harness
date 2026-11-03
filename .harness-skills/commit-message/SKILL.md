---
name: commit-message
description: 当用户要求写一条 git commit message 时使用。
---

# Commit message 规范

- 格式：<type>(<scope>): <subject>，type 从 feat/fix/docs/refactor/chore/test
  里选最贴切的一个，scope 是改动涉及的模块名，subject 是一句话概述，不写句号。
- 只输出这一行 commit message，不要额外的正文说明，除非用户明确要求写详细描述。
