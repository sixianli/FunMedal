---
status: "active"
document_type: "idea"
record_id: "IDEA-20261024-001"
record_state: "captured"
date: "2026-10-24"
updated: "2026-10-24"
project: "FunMedal"
promoted_to: ""
supersedes: ""
superseded_by: ""
---
# 在卡片上按键批准或拒绝

**Context/Project:** FunMedal

## Core Ideas

卡片显示“等你批准”时，长按一个键批准、短按另一个键拒绝，不用走回电脑。2026-10-24 从产品范围里拿掉：负责人决定卡片只做一件事，显示所有会话的状态，要处理的事去电脑或手机上处理。代码已经做出来并测过（mac/bridge/Sources/Hook.swift、notes/approval.md），拿掉后要么删，要么留在代码里不接到新界面上。

## Thought Trajectory

原来 MISSION.md 把“按一个键就处理掉”列为两件核心价值之一。实际用起来发现 Claude Code 等人的情况不只是“跑这条命令行不行”，还有选择题（AskUserQuestion）、计划确认（ExitPlanMode），卡片只有批准/拒绝两个动作，对不上。负责人到 2026-10-24 还没真正用起来卡片，“按一键批准”是不是真需求没有验证过，而它引出的复杂度最大：多会话时批准哪一个、电脑和卡片抢答、选择题怎么显示。所以先拿掉，做好状态显示后再看。

## Verbatim Quotes

> "这个卡片只需要做一件事：显示 Claude Code 当前所有在跑任务的状态。"

## Open Questions

负责人用上状态显示之后，有没有出现“总想在卡片上按一下”的时刻？如果有，是哪一类等待（权限请求、选择题、计划确认）？
