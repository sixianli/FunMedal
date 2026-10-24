---
status: active
document_type: lessons
supersedes: ""
superseded_by: ""
date: "2026-10-24"
---

# 写代码的 AI 反复犯的错

本文只记写代码的 AI（Claude Code 等）在这个项目里反复犯的同一种错，以及挡住它的硬性规则。项目进度和一次性的问题不记在这里。

## 测试跑到一半，常驻桥接程序把卡片连走

**反复出现的情况**：卡片一次只接受一个蓝牙连接。跑接卡片的测试时，你别的 Claude Code 会话一开始或者压缩上下文，插件的 `SessionStart` hook（会话开始时 Claude Code 运行的命令）就会在默认套接字上启动常驻桥接程序，把卡片连走，后面接卡片的测试全都失败。2026-10-18 记过三次，见 [notes/bridge.md](../notes/bridge.md) 的“卡片测试期间挡住常驻程序”。那次的办法是让 `card` 夹具（测试开始前准备卡片的那段代码）占着默认套接字旁边的 `bridge.sock.lock`，可它是按测试文件拿锁的：一个文件跑完就放开，下一个用卡片的文件开始时再拿。2026-10-24 跑整套测试时，常驻程序就在这段空当里（21:10:28）被启动起来，9 项接卡片的测试在准备阶段失败（`artifacts/sc16-full-20261024-210814.log`）。

**为什么要紧**：这些失败和被测的代码无关，却和真的出错长得一样，容易让人去改不该改的地方，或者把真失败当成“又是常驻程序”放过去。

**挡住它的规则**：

- [tests/conftest.py](../tests/conftest.py) 在整轮 pytest 一开始就拿这把锁，一直拿到进程结束，中间不放。常驻程序已经在跑、拿不到锁时不报错，接卡片的测试到用卡片时再失败，并提示先让它退出。
- [tests/test_card_guard.py](../tests/test_card_guard.py) 的第一项不用卡片，模拟“两个卡片测试文件之间”的空当：送一个 `SessionStart`，10 秒内默认套接字上不能有桥接程序。按文件拿锁时它失败（`artifacts/guard-gap-before-20261024-211318/`），改成整轮拿锁后通过（`artifacts/guard-gap-after-20261024-211417/`）。
- 新写接卡片的测试时，用 `bridge_harness.py` 的 `card` 夹具或 `require_resident_locked_out()`，不要自己另开一把锁：同一个进程里对同一个文件再 `flock` 一次会拿不到。
