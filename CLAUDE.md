# FunMedal

- 项目文档按 Document Governance（`~/.claude/skills/document-governance/`，一套管理项目文档的规则）管理，放在 `docs/` 下。产品需求写在 `docs/prd-v*.md`；新建、修改、作废 `docs/` 下的文档都按这套规则做，改完运行 `python3 ~/.claude/skills/document-governance/scripts/validate_docs.py . --strict` 检查。
- 做任何需求、功能或设计方案之前，先读 [MISSION.md](MISSION.md)，按其中“判断一个需求该不该做”逐条回答，并在方案里写明它补足了 Claude Code 电脑端和手机端的哪一处不足。不符合的，先告诉用户，由用户决定。
- 任务的目标、计划和完成情况在 `.agents/tasks/status-card/`（旧任务 `funmedal/`、`onboarding/` 已关），完成情况只看 `python3 ~/.claude/skills/long-task-planning/scripts/longtask.py status` 的输出。
- 硬件事实和引脚表在 [notes/hardware.md](notes/hardware.md)。原厂固件备份在 `firmware-backup/`，不进 git。
- 原厂固件下，卡片闲置 5 分钟会进入深度睡眠，USB 串口随之消失，按任意功能键唤醒；自制固件（`firmware/`，说明在 [notes/firmware.md](notes/firmware.md)）还不睡眠。原厂固件下打开卡片的 USB 串口会让它重启一次，自制固件下不会。
