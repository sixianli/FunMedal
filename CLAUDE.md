# FunMedal

- 做任何需求、功能或设计方案之前，先读 [MISSION.md](MISSION.md)，按其中“判断一个需求该不该做”逐条回答，并在方案里写明它补足了 Claude Code 电脑端和手机端的哪一处不足。不符合的，先告诉用户，由用户决定。
- 任务的目标、计划和完成情况在 `.agents/tasks/funmedal/`，完成情况只看 `python3 ~/.claude/skills/long-task-planning/scripts/longtask.py status` 的输出。
- 硬件事实和引脚表在 [notes/hardware.md](notes/hardware.md)。原厂固件备份在 `firmware-backup/`，不进 git。
- 卡片闲置 5 分钟会进入深度睡眠，USB 串口随之消失，按任意功能键唤醒；打开它的 USB 串口会让它重启一次。
