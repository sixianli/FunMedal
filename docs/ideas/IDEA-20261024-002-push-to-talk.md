---
status: "active"
document_type: "idea"
record_id: "IDEA-20261024-002"
record_state: "captured"
date: "2026-10-24"
updated: "2026-10-24"
project: "FunMedal"
promoted_to: ""
supersedes: ""
superseded_by: ""
---
# 按住说话，把一句短指令送进会话

**Context/Project:** FunMedal

## Core Ideas

按住卡片中间键说一句话，Mac 用离线语音识别转成文字，送进“最近需要你的”那个 Claude Code 会话。2026-10-24 从产品范围里拿掉：负责人决定卡片只做状态显示。代码已经做出来并测过（firmware/main/voice.c、mac/bridge/Sources/Voice.swift、plugin/hooks/voice.js、notes/voice.md）。

## Thought Trajectory

原来的定位是“按住说一句短指令”，补人不在电脑前时下不了指令的空缺。已知缺口：在电脑上按过 Stop 之后，Claude Code 2.1.299 会拒收插件送进来的话，必须先回电脑打一句字，这和“人不在电脑前也有用”冲突；多个会话同时开着时可能送错会话；依赖的 $.prompt.submit 是 mod 接口，不在官方文档里，升级后可能失效。手机上的 Remote Control 已经能发新指令，卡片的优势只是不用掏手机。

## Verbatim Quotes

- None recorded.

## Open Questions

状态显示用起来后，负责人多久会遇到一次“想在卡片上说一句话”的情况？如果恢复，先要解决 Stop 之后拒收的问题。
