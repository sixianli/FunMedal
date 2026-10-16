# Claude Code 对接方式

卡片本身跑不了 Claude Code，需要 Mac 上有一个桥接程序（下文简称“桥”）：它从 Claude Code 拿状态，通过蓝牙发给卡片，再把卡片上的按键和语音转回 Claude Code。本文记录 2026-10-16 查到的官方做法。依据是 code.claude.com 上的官方文档，标“未确认”的是文档没写清楚、还需要本机实测的。

## 结论

| 需求 | 推荐做法 | 要求 | 把握 |
| --- | --- | --- | --- |
| 显示任务状态 | hooks（Claude Code 在特定事件发生时自动运行你配置的命令）把事件发给桥 | 所有版本都有 hooks，但各版本支持的事件不同 | 确定 |
| 卡片上批准 / 拒绝 | 方案 A：`PermissionRequest` hook 先停住，等桥拿到卡片按键结果再回答 allow 或 deny。方案 B：Channels（官方“频道”功能：一个 MCP 服务器往运行中的会话推消息）的权限转发，终端和卡片同时能答，先到先生效 | A：版本要支持 `PermissionRequest`；B：v2.1.81 以上、研究预览阶段 | A 的“等待时终端弹窗是否也显示”未确认；B 确定 |
| 语音变成新指令 | Channels：桥作为频道服务器，把语音转出来的文字推进正在运行的会话 | v2.1.80 以上；研究预览；要用 claude.ai 登录或 API key；自定义频道要带 `--dangerously-load-development-channels` 启动；Team / Enterprise 需管理员开启 | 确定 |
| 区分多个会话 | 每个 hook 都带 `session_id`、`cwd`（工作目录），桥自己记账；新版本还可以用 `claude agents --json` | `claude agents --json` 只在新版本有 | 确定 |

**本机现状**：终端里的 `claude` 是 Homebrew 装的 **2.1.116**，运行 `claude agents --json` 报 `unknown option '--json'`。文档里最新的是 2.1.301（2026-10-15）。目标用户的版本会各不相同，所以第一版要以 hooks 为主。Channels 只在版本够新、而且用户愿意带开发参数启动时才可用。

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
- 未确认：hook 等待期间，终端里的批准弹窗会不会同时出现。要在本机实测。
- Channels 权限转发：Claude Code 发出 `notifications/claude/channel/permission_request`，里面带 5 个字母的 `request_id`、工具名、说明和输入预览；桥回 `notifications/claude/channel/permission`，带 `request_id` 和 allow/deny。终端弹窗同时保留，哪边先答算哪边。只覆盖运行命令、写文件这类工具，不覆盖“信任项目”这类对话框。

## 语音怎么变成指令

- 往**已经在运行**的交互会话里塞一条新消息，官方文档里能找到的办法只有 Channels（桥发 `notifications/claude/channel`，Claude 会把它当成一条 `<channel>` 事件收到）。
- 其他办法都不合适：Remote Control 只认 claude.ai 网页和手机 App，不是给第三方设备用的接口；`claude -p` 和 Agent SDK 只能控制桥自己启动的会话；`claude --resume <会话> "指令"` 会把终端接管过去，不适合脚本调用。
- 新版本还有跨会话消息接口（`CLAUDE_CODE_MESSAGING_SOCKET`，v2.1.224 以上），但文档没写清楚消息格式，暂不采用。

## 还要实测的

1. `PermissionRequest` hook 在本机 2.1.116 上的实际行为。静态证据：程序文件里能找到 `"PermissionRequest"`、`"Notification"`、`"Stop"` 等事件名，但没有实际运行过。
2. `PermissionRequest` hook 等待期间，终端弹窗的表现。
3. Channels 和权限转发在 2.1.116 上能不能用。静态证据：程序文件里能找到 `claude/channel/permission` 和 `dangerously-load-development-channels`，但没有实际运行过。
4. 桌面 App 里的 Claude Code 是不是另一个版本，hooks 配置和终端是否共用。
