# Claude Code 对接方式

卡片本身跑不了 Claude Code，需要 Mac 上有一个桥接程序（下文简称“桥”）：它从 Claude Code 拿状态，通过蓝牙发给卡片，再把卡片上的按键和语音转回 Claude Code。本文记录 2026-10-16 查到的官方做法。依据是 code.claude.com 上的官方文档，标“未确认”的是文档没写清楚、还需要本机实测的。

## 结论

| 需求 | 推荐做法 | 要求 | 把握 |
| --- | --- | --- | --- |
| 显示任务状态 | hooks（Claude Code 在特定事件发生时自动运行你配置的命令）把事件发给桥 | 所有版本都有 hooks，但各版本支持的事件不同 | 确定 |
| 卡片上批准 / 拒绝 | 方案 A：`PermissionRequest` hook 先停住，等桥拿到卡片按键结果再回答 allow 或 deny。方案 B：Channels（官方“频道”功能：一个 MCP 服务器往运行中的会话推消息）的权限转发，终端和卡片同时能答，先到先生效 | A：版本要支持 `PermissionRequest`；B：v2.1.81 以上、研究预览阶段 | A 和 B 都在本机终端版 2.1.292 上实测过，见“本机实测结果” |
| 语音变成新指令 | Channels：桥作为频道服务器，把语音转出来的文字推进正在运行的会话 | v2.1.80 以上；研究预览；要用 claude.ai 登录或 API key；自定义频道要带 `--dangerously-load-development-channels` 启动；Team / Enterprise 需管理员开启 | 确定 |
| 区分多个会话 | 每个 hook 都带 `session_id`、`cwd`（工作目录），桥自己记账；新版本还可以用 `claude agents --json` | `claude agents --json` 只在新版本有 | 确定 |

**本机现状**：终端里的 `claude` 原来是 Homebrew 装的 2.1.116，运行 `claude agents --json` 报 `unknown option '--json'`；2026-10-16 用户把它升级到了 **2.1.292**（升级后没再试 `claude agents --json`）。Claude 桌面 App 自带的 Claude Code 是 **2.1.295**（装在 `~/Library/Application Support/Claude/claude-code/2.1.295/`），它也会运行 `~/.claude/settings.json` 里配置的 hooks：本项目的会话在桌面 App 里开始时，用户在这个文件里配置的 `SessionStart` hook 照常运行了。文档里最新的是 2.1.301（2026-10-15）。目标用户的版本会各不相同，所以第一版要以 hooks 为主。Channels 只在版本够新、而且用户愿意带开发参数启动时才可用。

## 状态从哪里来

- **hooks**：文档列了 33 种事件。这个项目要用的有：
  - `SessionStart` / `SessionEnd`：会话开始和结束。
  - `UserPromptSubmit`：用户发了一条指令。
  - `PreToolUse` / `PostToolUse`：Claude 准备用工具（比如运行命令、改文件）和用完了。
  - `PermissionRequest`：需要用户批准，**立刻**触发。
  - `Notification`：带 `notification_type` 字段，比如 `permission_prompt`（等批准）、`idle_prompt`（做完后闲了约 60 秒）、`agent_needs_input`、`agent_completed`。其中 `permission_prompt` 要等提示挂了约 6 秒才触发，所以要即时提醒就用 `PermissionRequest`。
  - `Stop`：Claude 这一轮回答完了。
- 每个事件都会通过标准输入收到 JSON，里面有 `session_id`、`cwd`、`transcript_path`、`permission_mode` 等字段。
- **statusline**（状态栏命令）：每次回答后都会收到模型、费用、上下文用量等信息，但不会告诉你“正在等批准”，等批准时它也不刷新。适合在卡片上显示用量，不适合判断状态。
- **OpenTelemetry**（一种标准的监控数据导出格式）：默认关闭，要设环境变量才开，只能往外发。第一版不需要。

## 卡片怎么回答“要不要批准”

- `PermissionRequest` hook 的回答格式是 `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}`，拒绝时把 `allow` 换成 `deny`，还可以附一段 `message`。
- 命令类 hook 是同步运行的，所以桥可以一直等到卡片按键再回答。默认超时 600 秒；文档没写上限。
- hook 等待期间，终端里的批准弹窗会同时出现，哪边先答算哪边，见“本机实测结果”。
- Channels 权限转发：Claude Code 发出 `notifications/claude/channel/permission_request`，里面带 5 个字母的 `request_id`、工具名、说明和输入预览；桥回 `notifications/claude/channel/permission`，带 `request_id` 和 allow/deny。终端弹窗同时保留，哪边先答算哪边。只覆盖运行命令、写文件这类工具，不覆盖“信任项目”这类对话框。

## 语音怎么变成指令

- 往**已经在运行**的交互会话里塞一条新消息，做 FM-15 时官方文档里能找到的办法只有 Channels（桥发 `notifications/claude/channel`，Claude 会把它当成一条 `<channel>` 事件收到）。2026-10-17 起还有插件里的 mod（在 Claude Code 内部运行的一段 JavaScript）：它的 `$.prompt.submit({ text, asUser: true })` 能把文字当作用户的话送进会话，不需要开发参数，见 [onboarding.md](onboarding.md) 的“mod 实验”。在 Claude 桌面 App 里也能用，但按过 Stop 之后，这个会话里 mod 送的话都被丢掉，见同一文件的“桌面 App 里的 mod 实验”。这是 Claude Code 2.1.299 引擎自己的规则（2.1.292 还没有）：用户亲手打一句话之后才恢复；桌面 App 里的 `/clear` 还会结束整个 Claude Code 进程，mod 要等新进程启动才重新运行。见同一文件的“按 Stop 之后丢话的复查”。
- 其他办法都不合适：Remote Control 只认 claude.ai 网页和手机 App，不是给第三方设备用的接口；`claude -p` 和 Agent SDK 只能控制桥自己启动的会话；`claude --resume <会话> "指令"` 会把终端接管过去，不适合脚本调用。
- 新版本还有跨会话消息接口（`CLAUDE_CODE_MESSAGING_SOCKET`，v2.1.224 以上），但文档没写清楚消息格式，暂不采用。

## 本机实测结果

2026-10-16 在本机终端版 Claude Code 2.1.292 上实测了两件事：`PermissionRequest` hook（测试程序和原始记录在 [experiments/claude-hooks/](../experiments/claude-hooks/)），以及 Channels 推消息和权限转发（在 [experiments/claude-channels/](../experiments/claude-channels/)，见下面“Channels”一节）。

### PermissionRequest hook 怎么测的

- [drive.py](../experiments/claude-hooks/drive.py) 在一个伪终端（程序模拟出来的终端窗口，`claude` 以为有人在用）里启动 `claude`，用 Haiku 模型，权限模式是 `default`（每次运行命令都要用户批准）。它让 Claude 运行一条 `touch <标记文件>` 命令：标记文件出现，就说明命令真的运行了。它还能在批准弹窗里替用户按 `1`（是）和回车。
- 它给这次运行单独配了 4 个 hook：`PermissionRequest`（只管 Bash 命令，超时 60 秒）、`Notification`、`PreToolUse`、`PostToolUse`。每个 hook 都运行 [hook_probe.py](../experiments/claude-hooks/hook_probe.py)，记下触发时间和收到的内容；`PermissionRequest` hook 按场景先等若干秒，再回答 allow（允许）、deny（拒绝），或者什么都不答直接退出。
- 测试时关掉了沙箱（sandbox，限制命令能读写哪些文件、连哪些网络的隔离环境）。用户的全局设置开着沙箱，并允许沙箱里的命令自动运行；开着它时，测试命令不弹窗就直接运行了，测不到批准流程。测试程序还去掉了从当前会话继承来的 `CLAUDE*`、`ANTHROPIC*` 等环境变量，否则被测的 `claude` 会把自己当成别的会话的子会话。
- [analyze.py](../experiments/claude-hooks/analyze.py) 从终端录屏里找出各段文字第一次出现的时间，结果在 [results/analysis.json](../experiments/claude-hooks/results/analysis.json)。

### PermissionRequest hook 的 5 个场景

时间都是从 `claude` 启动算起的秒数。

| 场景 | hook 怎么答 | 终端里怎么答 | 实测经过 |
| --- | --- | --- | --- |
| `wait-allow` | 等 20 秒后答 allow | 不答 | 11.2 秒弹窗出现，hook 同时开始；17.2 秒触发 `Notification`（`permission_prompt`）；31.4 秒 hook 答 allow，32.2 秒命令运行，屏幕显示 `Allowed by PermissionRequest hook`，弹窗消失 |
| `wait-deny` | 等 10 秒后答 deny | 不答 | 17.3 秒弹窗和 hook 同时开始；23.4 秒触发 `Notification`；27.5 秒 hook 答 deny，屏幕显示 `Denied by PermissionRequest hook`，命令没有运行 |
| `no-decision` | 什么都不答，马上退出 | 4 秒后答“是” | 11.7 秒弹窗和 hook 同时开始，hook 马上结束，弹窗继续等；15.8 秒终端答“是”，16.1 秒命令运行 |
| `wait-then-terminal-yes` | 等 20 秒后答 allow | 6 秒后答“是” | 16.4 秒弹窗和 hook 同时开始；22.4 秒触发 `Notification`；22.5 秒终端答“是”，22.8 秒命令运行并触发 `PostToolUse`；hook 进程没有被结束，一直到 35.3 秒测试程序关掉会话时才收到结束信号 |
| `terminal-yes-then-hook-deny` | 等 12 秒后答 deny | 3 秒后答“是” | 11.7 秒弹窗和 hook 同时开始；14.7 秒终端答“是”，15.0 秒命令运行；23.9 秒 hook 才答 deny，屏幕上没有任何拒绝提示，这个迟到的回答不起作用 |

### PermissionRequest hook 的结论

1. **弹窗和 hook 同时出现**：5 个场景里两者开始的时间都相差不到 0.1 秒。hook 在等的时候，终端照样能批准。
2. **哪边先答算哪边**：hook 先答，终端弹窗自动消失，屏幕上写明是 hook 允许或拒绝的；终端先答，命令马上运行，hook 之后再答会被忽略。
3. **终端先答时，hook 进程不会被结束**：它会一直跑到自己回答、超时或会话结束。所以桥要自己发现“终端已经答了”，比如收到同一条命令的 `PostToolUse`，就把卡片上的“等你批准”撤掉。
4. **hook 不给答案就退出时**，弹窗照常等终端回答。
5. **`Notification` 的 `permission_prompt` 在弹窗挂了约 6 秒后才触发**（触发过的 3 次都在 6.0–6.1 秒之间）；6 秒内有人答了就不会触发。卡片要即时显示“等你批准”，应该用 `PermissionRequest`。
6. **开着沙箱并允许自动运行时，沙箱里的命令不会弹窗**，卡片上也就不会出现要批准的事。
7. **在终端拒绝时，Claude Code 不发出任何 hook 事件**：在批准弹窗里按 Esc 后，93 秒里没有 `Stop`、`PostToolUse`、`PostToolUseFailure` 或 `Notification`；选“3. No”后 20 秒里也没有。桥只能等用户发出下一条指令。这是 FM-13 实测的，见 [bridge.md](bridge.md) 的“实测结果”第 3 条。
8. **但在终端拒绝时，正在等的 `PermissionRequest` hook 会被结束**：hook 等着没有回答时，在终端按 Esc，0.11 秒后 hook 进程收到 SIGTERM（请程序结束的信号）；选“3. No”，0.03 秒后收到。它之后再答 allow 也不起作用，命令不运行。所以桥看到 hook 的连接断开，就知道是终端里拒绝了（和第 3 条对照：终端里批准时 hook 不会被结束）。这是 FM-14 实测的，官方文档没写，见 [approval.md](approval.md) 的“实测结果”第 1 条。

**对卡片设计的影响（需要用户决定）**：第一版计划里有一个“离开开关”，打开时才让 hook 等卡片回答，原因是担心 hook 等待时会挡住终端。实测说明不会挡住：hook 一直等着，人在电脑前就在终端答，不在就在卡片上答，哪边先答算哪边。所以“离开开关”可能不需要。代价是终端先答时，桥要按第 3 条自己撤掉卡片上的提示。

### PermissionRequest hook 怎么重跑

在自己的终端里运行（会真的启动 `claude` 并消耗少量 Haiku 用量）；`<场景>` 是上表第一列之一，结果写进 `--out` 指定的目录：

```bash
python3 experiments/claude-hooks/drive.py <场景> --workdir "$TMPDIR/hookrun" --out experiments/claude-hooks/results/<场景>
```

```bash
python3 experiments/claude-hooks/analyze.py experiments/claude-hooks/results/* > experiments/claude-hooks/results/analysis.json
```

终端录屏里有 `claude` 的启动画面，上面显示了模型名和账户的订阅类型；记录里的路径带有本机的用户名。没有邮箱、密钥或登录凭据。

### Channels

**怎么测的**：[channel_probe.py](../experiments/claude-channels/channel_probe.py) 是一个最小的频道服务器（用标准输入输出和 Claude Code 交换 JSON 消息的 MCP 服务器），声明自己支持 `claude/channel`（推消息）和 `claude/channel/permission`（权限转发），把收发的每条消息都记下来。[run_channel.py](../experiments/claude-channels/run_channel.py) 在伪终端里用下面的参数启动 `claude`，只加载这一个服务器：

```bash
claude --settings <关掉沙箱的设置文件> --strict-mcp-config --mcp-config <只含这个服务器的配置> --dangerously-load-development-channels server:fmprobe --model haiku --permission-mode default
```

等会话空闲后，服务器推一条消息，内容是“请用 Bash 运行 `touch <标记文件>`，然后回复 done”。Claude 照做时会弹出批准请求，这个请求会转发给服务器，服务器按场景回答。

**启动时的三道关**：

1. “信任这个文件夹吗”：2.1.292 默认选中的是“No, exit”，直接回车会退出。
2. 开发频道警告：`WARNING: Loading development channels`，默认选中“1. I am using this for local development”，回车继续。
3. 之后屏幕提示频道消息会直接进入这个会话（`inject directly in this session`）。

**连接过程**：2.1.292 先发 `server/discover`，带新协议版本 2026-07-28。服务器回“不支持这个方法”后，它改用旧的 `initialize`，请求版本 2025-11-25。服务器回 2025-06-18 并声明两个能力后，频道就注册上了。文档说用 2026-07-28 协议连接的服务器不会被当成频道，所以频道服务器不要实现 `server/discover`。

**3 个场景**（时间从 `claude` 启动算起）：

| 场景 | 经过 |
| --- | --- |
| `push-allow` | 7.1 秒服务器推消息；12.0 秒服务器收到批准请求 `notifications/claude/channel/permission_request`，终端弹窗在 0.1 秒内同时出现；17.0 秒服务器回 allow，18.0 秒命令运行 |
| `push-deny` | 8.9 秒推消息；13.1 秒收到批准请求；18.1 秒回 deny，屏幕显示 `Tool use rejected`，命令没有运行 |
| `push-terminal-first` | 6.5 秒推消息；9.7 秒收到批准请求；12.8 秒终端答“是”，13.5 秒命令运行；24.7 秒服务器才回 allow，没有任何作用 |

批准请求里有 5 个小写字母的 `request_id`、工具名 `Bash`、一句说明（有时是固定的 `Run shell command`，有时是 Claude 自己写的，比如 `Create marker file as requested`），以及 `input_preview`（命令内容的预览，卡片上可以显示）。

**结论**：

1. 推送的消息能让正在运行、正在等输入的会话接着干活，从推送到 Claude 发起命令约 3–5 秒。语音转成文字后用这条路送进会话可行。
2. 权限转发和 `PermissionRequest` hook 一样：弹窗同时出现，哪边先答算哪边，迟到的回答没有作用。
3. 终端先答时，Claude Code 不会通知服务器“这个请求已经处理了”。卡片上的提示同样要靠别的信号撤掉，比如 `PostToolUse` hook。
4. 用 Channels 必须每次带 `--dangerously-load-development-channels` 启动，并在警告里确认一次。`--channels` 只能加载 Anthropic 认可列表里的插件。所以批准操作用 `PermissionRequest` hook 更省事（只要配置一次 hooks，不用改启动方式），Channels 只在需要往会话里送语音指令时用。这是建议，用不用由 FM-14、FM-15 设计时定。

**怎么重跑**：在自己的终端里运行（会真的启动 `claude`，消耗少量 Haiku 用量）；`<场景>` 是上表第一列之一：

```bash
python3 experiments/claude-channels/run_channel.py <场景> --workdir "$TMPDIR/chanrun" --out experiments/claude-channels/results/<场景>
```

每个场景的结果目录里有 `summary.json`（时间线和服务器收发的全部消息）、`channel.jsonl`（服务器的原始记录）、`terminal.raw` 和 `terminal.txt`（终端录屏）、`settings.json` 和 `mcp.json`（这次用的设置）。终端录屏的启动画面上有模型名和账户的订阅类型，记录里的路径带本机用户名；没有邮箱、密钥或登录凭据。

## 状态卡片开工实测（2026-10-24，终端版 2.1.308）

这是状态卡片实施计划 Task 0 的终端部分。`experiments/hook-probe/probe.py` 是一个只记日志的 hook 脚本：10 种事件（`SessionStart`、`SessionEnd`、`UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`PermissionRequest`、`Notification`、`Stop`、`StopFailure`）都指向它，它把事件名、`session_id`、`source`、`reason`、工具名、`notification_type`、`cwd`、环境变量 `CLAUDE_PROJECT_DIR` 和父进程链写成一行 JSON。`experiments/hook-probe/drive.py` 用伪终端（程序模拟出来的终端窗口）启动真的 `claude`，按场景打字或发信号，最后把这次收到的全部事件写进 `experiments/hook-probe/results/<场景>/summary.json`。

| 要回答的问题 | 结果 | 场景 |
| --- | --- | --- |
| `AskUserQuestion`（Claude 给用户出选择题的工具）会不会触发 `PermissionRequest` | 会。`PreToolUse` 之后 0.07 秒触发 `PermissionRequest`，约 6 秒后再来一条 `Notification`（`permission_prompt`）；用户选完后有 `PostToolUse`，然后是 `Stop` | `ask` |
| `/clear` 之后 `session_id` 变不变 | 变。旧 id 先收到 `SessionEnd`（`reason: clear`），0.1 秒内新 id 收到 `SessionStart`（`source: clear`） | `clear` |
| Claude Code 被杀掉时有没有 `SessionEnd` | 发 SIGTERM 或 SIGHUP（普通的“请退出”信号，关终端窗口时就是 SIGHUP）：有，`reason: other`，约 2 秒内到。发 SIGKILL（系统直接结束进程，程序来不及做任何事）：没有，最后一条事件是之前的 `Stop` | `kill-term`、`kill-hup`、`kill-kill` |
| 正常退出 | `SessionEnd`，`reason: prompt_input_exit` | 全部 |
| hook 进程里 `CLAUDE_PROJECT_DIR` 有没有值 | 有，等于启动 `claude` 的目录 | 全部 |
| hook 的父进程是谁 | 就是 `claude` 进程本身，所以现有的 `claude_pid`（hook 父进程号）在终端里指的是对的进程 | 全部 |
| 权限弹窗等满 600 秒会怎样 | hook 设的超时是 600 秒、脚本故意等 900 秒。第 600 秒 Claude Code 发信号结束了 hook 进程，但终端里的权限弹窗没有消失；又过 15 秒在终端选“是”，命令照常执行，随后收到这个工具的 `PostToolUse`。所以 hook 超时不等于用户答了，“等你”要一直留到 `PostToolUse`、`Stop` 或下一句话 | `timeout` |

对状态卡片设计的影响：

- 选择题和权限请求走同一条 `PermissionRequest`，“等你”不需要另开一条路。
- `/clear` 自己就发 `SessionEnd`，按 `SessionEnd` 删旧行即可，设计规格里“同一 pid 换了 `session_id` 就删旧行”的规则不需要。
- 只有 SIGKILL 不发 `SessionEnd`，这种情况靠桥接程序每 15 秒检查一次 pid 兜底。
- `PermissionRequest` 的 hook 进程被超时结束时，不能把“等你”去掉，设计规格里“超时标记保留记录”的做法和实测一致。

重跑（每个场景约 30 秒，`timeout` 约 11 分钟）：

```bash
python3 experiments/hook-probe/drive.py <ask|clear|kill-term|kill-hup|kill-kill|timeout> --workdir "$TMPDIR/fm-probe" --out experiments/hook-probe/results/<场景>
```

`results/<场景>/` 里还有 `hooks.jsonl`（原始日志）、`terminal.raw`（终端录屏）和 `settings.json`（这次用的 hooks 设置）。日志里的路径带本机用户名；终端录屏的启动画面上有模型名和账户的订阅类型；没有邮箱、密钥或登录凭据。

## 状态卡片开工实测：桌面 App（2026-10-24，2.1.308）

这是 Task 0 的桌面 App 部分。`experiments/hook-probe/make_app_project.py` 建了一个独立的小项目 `experiments/hook-probe/app-project/`（不进 git），它的 `.claude/settings.json` 把 10 种事件都指向同一个 `probe.py`。负责人在桌面 App 里打开这个项目，权限模式选“每次都问”，先开一个普通会话、再开一个工作副本会话（git worktree，App 给会话单独复制出来的一份代码目录），每个会话都让 Claude 写一个文件、再出一道选择题，答完后关掉会话。原始日志和整理后的时间线在 `experiments/hook-probe/results/app/`（`hooks.jsonl`、`report.txt`、这次用的 `settings.json`）。

| 要回答的问题 | 结果 |
| --- | --- |
| 桌面 App 里 `PermissionRequest` 触发不触发 | 触发。写文件（`Write`）弹批准框时，`PreToolUse` 之后 0.1 秒触发；选择题（`AskUserQuestion`）同样触发。沙箱里自动放行的 `Bash` 命令不弹框，也没有 `PermissionRequest` |
| hook 的父进程是谁 | 是会话自己的 claude 命令行进程（`~/Library/Application Support/Claude/claude-code/2.1.308/…/claude`，带 `--output-format stream-json`），它的上一级是 `disclaimer`，再上一级是 Claude.app。每个会话一个独立的 claude 进程，所以 `claude_pid` 在桌面 App 里也指向对的进程 |
| 工作副本会话里 `cwd` 和 `CLAUDE_PROJECT_DIR` 是什么 | `cwd` 是工作副本目录 `app-project/.claude/worktrees/write-text-to-file-cd0950`；`CLAUDE_PROJECT_DIR` 是主项目 `app-project`。普通会话里两者都是 `app-project` |
| 关掉会话后有没有 `SessionEnd`、claude 进程退不退出 | 都没有。两个会话关掉后日志里最后一条是 `Stop`；最后一条事件之后 3 到 4 分钟，两个 claude 进程（23520、24312）还活着。App 的会话列表里这两个会话仍在，只是“没在跑” |
| `Notification` 有没有 | 这次没有。每个批准框都在 6 秒内答掉了，终端实测里 `Notification` 要等约 6 秒才发 |

对状态卡片设计的影响：

- 桌面 App 和终端一样，批准框和选择题都走 `PermissionRequest`，`AskUserQuestion` 不需要再靠 `PreToolUse` 记“等你”。
- 桌面 App 里“关掉会话”既不发 `SessionEnd`、也不结束进程，所以桥接程序的 pid 检查删不掉这一行，答完了的会话会一直留在卡片上。什么操作才会让桌面会话真正结束（归档会话、退出 App），这次没测。
- 工作副本会话的项目名直接取 `CLAUDE_PROJECT_DIR` 的最后一级就是主项目名，不用读 `.git` 文件。

## 还要实测的

1. 桌面 App 里归档会话、退出 App 时，有没有 `SessionEnd`，claude 进程退不退出。
2. 同一个会话里接连有几条命令等批准时，hook 和弹窗的顺序。
3. 桌面 App 里能不能加载开发频道。
