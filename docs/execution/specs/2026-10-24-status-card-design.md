---
status: active
document_type: spec
supersedes: ""
superseded_by: ""
date: "2026-10-24"
---

# 状态卡片设计规格

本文是把 [SOURCE: docs/prd-v0.1.md] 做出来的设计：Claude Code（Anthropic 公司的 AI 编程助手）的每个会话在卡片上占一行，四套主题，一个写实角色，提醒声音，电池下熄屏。实施顺序和验证在配套的实施计划里。写法上，每个设计决定都写“怎么做、为什么不用别的、最可能怎么坏、依据”。依据里的 `文件:行` 指 2026-10-24 提交 `3e149b3` 时的代码。

成稿过程：本文先由 Claude 独立写成，同时 OpenCode（开源的命令行编程助手）里三个免费模型在看不到本文的工作区里各自独立出方案，再读本文挑错；Claude 逐条核对后修订成现在这版，最后由项目负责人认可。采纳与否记在文末“头脑风暴的采纳情况”。

## Goal

卡片只做一件事：显示 Claude Code 当前所有在跑会话的状态，有会话“等你”或“出错”时用角色和声音把人叫过来。做完后，PRD 的验收第一层 10 条都能当着负责人演示（第 4 条只靠自动测试）。

## Source Context

- PRD: [SOURCE: docs/prd-v0.1.md#business-rules]
- 术语: [SOURCE: docs/domain/glossary.md]
- 拿掉的功能: [SOURCE: docs/ideas/IDEA-20261024-001-card-approve-deny.md]、[SOURCE: docs/ideas/IDEA-20261024-002-push-to-talk.md]

### 对照 MISSION.md

按 `MISSION.md`“判断一个需求该不该做”四条：

1. 补的是电脑端和手机端都做不好的事：人不在电脑前、或电脑屏幕被别的窗口占着时，哪个项目在等人、哪个出错了，电脑端看不到，手机端要掏手机解锁。卡片放在手边，抬眼就看到。
2. 看一眼就懂：一行一个会话，四种状态用颜色和动法区分，“等你”永远在最上面，角色冒头加声音。
3. 按一两个键完成：卡片上只剩一个键有用，换主题。要处理的事去电脑或手机上处理，这是 PRD 的决定。
4. 不在电脑前也有用：这正是它的场景。

## Scope

- Included：hooks 事件采集、桥接程序的会话表和排序、新的蓝牙消息格式、固件的会话列表画法、四套主题和换主题键、角色动画和提醒、提醒声音、电池下熄屏和唤醒、拆掉批准和按住说话、素材包、按验收条目的测试。
- Excluded：卡片上批准或拒绝；按住说话；省电到几天的续航（第一版只熄屏不睡眠，见“电源”）；无线升级固件；配套功能（安装卸载、doctor 自检、版本互报、配对加密、预编译固件、原厂备份恢复）只保留不改动。

## Acceptance Criteria

照抄 PRD“Acceptance Boundaries”第一层 10 条，每条对应长任务 `.agents/tasks/status-card/` 的 SC-3 到 SC-12。第 11、12 条是真实使用一周，对应 SC-13、SC-14，不在本文设计范围内。

## 待负责人决定的事

设计时发现 PRD 没写死、又会改画面或行为的点：

1. 已定（负责人 2026-10-24）：只接充电器、不接电脑时按电池处理，5 分钟没变化会熄屏。PRD:64 原来只说“插着 USB 时常亮”，现在写明是接着电脑时常亮。固件只认“USB 接着电脑”这一个信号，不再查电量计的充电状态。
2. 待定：7 个会话都在“等你”时第 7 个不显示，顶栏写“+1”。PRD:49 定了只显示 6 行，本文加的只是“+N”这个提示；负责人 2026-10-24 定在 Task 3d 第一次出 PNG 效果图时看图再定。

## Design

### 1. 整体结构

```text
Claude Code 会话 ──hooks(stdin JSON)──▶ funmedal-bridge hook ──Unix 套接字──▶ funmedal-bridge serve
                                                                                   │ 会话表、排序、6 行、答完多久
                                                                                   ▼ BLE 写 ROWS 特征值（≤ 226 字节）+ 每 30 秒心跳
                                                                             卡片固件：按主题画 6 行 + 角色 + 声音 + 熄屏
```

分工原则：**规则在 Mac，画法在卡片。** 排序、6 行上限、每行的“已过分钟数”都在 Mac 算好再发，卡片收到什么画什么，只在本地把分钟数往上加。理由：PRD 说这些规则要靠实际使用验证、可能改；Mac 程序改起来快、能用 pytest 测，卡片固件改一次要刷机。放弃的做法是卡片自己排序，这样 Mac 只发原始表，但同一套规则就要在 Swift 和 C 里各写一遍、各测一遍。

### 2. Claude Code 这一侧：hooks

**事件和状态的对应**（术语表的四种状态）：

| 收到的 hook 事件 | 会话状态变成 | 说明 |
| --- | --- | --- |
| `SessionStart` | 不建可见行，只记 `session_id`、`project`、`pid` | `source: compact`、`clear`、`resume` 都会触发，不能当新会话。同一 `pid` 已经有另一个 `session_id` 的行（`/clear` 换了 id）就删掉旧行 |
| `UserPromptSubmit` | 在跑；第一次收到时才建可见行 | 清掉该会话所有“等你”记录 |
| `PreToolUse`（任何工具） | 在跑 | 若工具是 `AskUserQuestion` 或 `ExitPlanMode`，同时记一条“等你”，键是 `tool_use_id` |
| `PermissionRequest` | 等你 | 记一条“等你”，键是 `tool_name` 加 `tool_input` 的哈希（现有 `toolKey`） |
| `Notification`（matcher `permission_prompt`） | 等你 | 记一条“等你”，键固定为 `notification`，是沙箱命令网络请求的兜底（见下） |
| `PostToolUse` / `PostToolUseFailure` | 在跑 | 去掉同一 `tool_use_id` 和同一 `toolKey` 的“等你”记录，以及 `notification` 记录 |
| `Stop` | 答完了 | 记下答完时刻；清掉“等你” |
| `StopFailure` | 出错 | 记下出错时刻；清掉“等你” |
| `SessionEnd` | 会话删除 | |

显示状态 = 有“等你”记录就是等你，否则是上表的状态。

**“等你”怎么结束。** 权限请求在终端里被允许或拒绝时没有任何 hook 事件（`notes/bridge.md:81`，2026-10-16 实测）。现有做法是让 `PermissionRequest` 的 hook 进程一直等着，桥接程序看着这条连接（`mac/bridge/Sources/Bridge.swift:241-251` 的 `ended`）。实测过的行为（`notes/approval.md:45,51,53`）：终端里**拒绝**，Claude Code 约 0.1 秒内结束 hook 进程，连接断开；终端里**允许**，hook 进程不会被结束、也没有事件，直到工具跑完发 `PostToolUse`；hook 进程自己在 590 秒时退出，`hooks.json` 这一项的超时是 600 秒。所以“等你”的结束信号分三路：

1. 连接断开且 hook 存活不到 590 秒 → 终端拒绝了 → 删这条记录。
2. hook 存活到 590 秒 → hook 退出前先发一个“超时”标记 → 桥接程序**保留**这条记录，因为权限弹窗还在等；之后只能靠第 3 路或 `UserPromptSubmit`、`Stop`、`StopFailure`、`SessionEnd` 清掉。
3. 同一个 `tool_use_id`（`PreToolUse` 记的）或同一个 `toolKey`（`PermissionRequest` 记的）来了 `PostToolUse` / `PostToolUseFailure` → 终端允许了、工具跑完了 → 删记录，桥接程序随即主动关掉那条 hook 连接。`PermissionRequest` 的输入里没有 `tool_use_id`，所以 hook 进程要对 `PostToolUse`、`PostToolUseFailure` 也算一遍 `tool_key`（它们带同样的 `tool_name` 和 `tool_input`，hooks 参考页 PostToolUse input 一节），现在 `Hook.swift` 只对 `PermissionRequest` 算。

hook 进程连上、报一声“等你”、然后只等不答，**永远不回答决定**：去掉 `Hook.swift:85-98` 的两个 `answer` 和卡片按键那一路。选择题和计划确认走 `PreToolUse` → `PostToolUse`。

- **沙箱命令的网络请求**：官方 hooks 参考页写明，沙箱里的命令要访问网络时弹出的权限请求不运行 `PermissionRequest` hook。这类弹窗只能靠 `Notification` 事件的 `permission_prompt` 类型捕获，它在弹窗出现约 6 秒后才发。所以 `Notification` 作为兜底：收到就记 `notification` 这条“等你”，被 `PostToolUse`、`UserPromptSubmit`、`Stop` 清掉。不把 `Notification` 当主信号的原因就是那 6 秒。`idle_prompt` 类型要等 60 秒且后台有代理时不发，不用。
- **已知限制**：终端允许了一条要跑很久的命令，卡片会一直显示“等你”直到命令跑完（没有事件能区分“允许了在跑”和“还在等”）；用户按 Esc 打断生成时 `Stop` 不触发（hooks 参考页 Stop 一节原文），会话停在“在跑”直到下一句话。两条 PRD 都没要求处理，记为已知限制。
- 最可能怎么坏：`AskUserQuestion` 弹出时会不会也触发 `PermissionRequest`，官方文档没写；`ExitPlanMode` 有官方示例说会（hooks 指南“Auto-approve specific permission prompts”）。两条路都记“等你”、键不同，所以重复触发只会多一条记录，各自的结束信号去掉各自的，不会漏报。桌面 App 里 `PermissionRequest` 是否触发、hook 进程里 `CLAUDE_PROJECT_DIR` 有没有值、600 秒后权限弹窗还在不在，都在开工前用一个只记日志的最小插件实测（计划里的 T0）。
- 依据：事件和字段来自 hooks 参考页 `https://code.claude.com/docs/en/hooks` 的 Hook lifecycle、PermissionRequest、PreToolUse、Notification、Stop、StopFailure、SessionEnd 各节和“沙箱网络请求不运行 PermissionRequest hook”一句；`PermissionRequest` 的输入里没有 `tool_use_id`，所以它只能用 `toolKey` 配对。

**项目名。** hook 进程读环境变量 `CLAUDE_PROJECT_DIR`（会话启动时的项目根目录，Claude 在会话里 `cd` 或进工作副本也不变，hooks 参考页 Hook input 一节），取最后一级目录名作为 `project` 字段发给桥接程序；没有这个变量时用 `SessionStart` 事件里的 `cwd` 的最后一级。只发目录名、不发完整路径，所以 `tests/test_bridge.py:18` 的白名单 `HOOK_FIELDS` 加一项 `project`，`notes/bridge.md:123`“不传工作目录”改成“只传目录名”。为什么不用每个事件的 `cwd`：它跟着 Claude 的 `cd` 走，同一会话会在卡片上换名字。**工作副本**（git worktree，桌面 App 给每个会话单开的一份代码副本）：hooks 参考页说会话中途进工作副本时 `CLAUDE_PROJECT_DIR` 不变；但桌面 App 一开始就在工作副本里启动的会话，这个变量指向哪里文档另有一页，T0 实测。规则定为“项目名 = 主仓库的目录名”：目录里的 `.git` 是文件而不是目录时，读它的 `gitdir:` 一行（指向 `<主仓库>/.git/worktrees/<名字>`），取 `<主仓库>` 的目录名；不用跑 `git` 子进程。两个不同目录同名（比如两处都叫 `app`）就显示两行一样的名字，和 PRD“同一项目两个会话显示两行一样的名字”一致，不加父目录区分。

**幽灵会话。** Claude Code 被强杀时有没有 `SessionEnd`，文档没写（T0 实测）。现有每条消息都带 `claude_pid`（`Hook.swift:49-54`，hook 进程的父进程号）；桌面 App 里它是哪个进程、关掉会话后它会不会退出，T0 一起实测，否则 pid 检查会盯错对象。桥接程序每 15 秒对表里每个会话的 pid 做一次 `kill(pid, 0)`，进程不在就删会话。Mac 睡眠醒来、桥接程序重启也靠这个：桥接程序每次表变化都把会话表写到 `<套接字路径>.sessions.json`，启动时读回来再用 pid 核对一遍。为什么不用“多久没事件就删”：答完了的会话按 PRD 要一直显示到关掉，可能几小时没事件。

**hooks.json 的改动**：删掉 `modules: ["./voice.js"]` 和 `voice.js`；事件为 `SessionStart`、`UserPromptSubmit`、`PreToolUse`、`PermissionRequest`、`Notification`（matcher `permission_prompt`）、`PostToolUse`、`PostToolUseFailure`、`Stop`、`StopFailure`、`SessionEnd` 十个，`PreToolUse` 不加 matcher（所有工具都要当“在跑”的心跳）。`plugin/plugin.json` 的 description 改成状态卡片的说明，`tests/test_plugin.py` 对应断言跟着改。

### 3. Mac 桥接程序

**会话表**（改 `mac/bridge/Sources/Sessions.swift`）：每个会话记 `rowId`（1 字节，桥接程序分配，从 1 到 255 循环、跳过 0，会话删除后它的号不马上复用）、`project`、`activity`（在跑 / 答完了 / 出错）、`waiting`（“等你”记录的键的集合，每条记录带 hook 连接和建立时刻）、`since`（进入当前状态的时刻）、`pid`、`visible`（收到过 `UserPromptSubmit` 才为真）。去掉 `pending`、`allowedOnCard`、`answeredOnCard`、`rejectedInTerminal`、`followsCardAllow`（`Sessions.swift:5-23、74-85`）。

**排序和截断**在 Mac：等你 → 出错 → 在跑 → 答完了；同状态按项目名的 Unicode 码位顺序；取前 6 行。连同总数一起发，总数大于 6 时卡片顶栏显示“+N”。

**“新进入”标记。** 每行带一个标记位“本帧新进入等你或出错”，只在桥接程序看到该会话从别的显示状态变成等你或出错时置位一次；重连、重启后重发整表、因为别的行变化而重发时都不置位。卡片只用这个位决定要不要提醒，不自己比较前后两帧。为什么不让卡片比较：卡片按“项目名 + 状态”比较时，两个同名会话一个已经在等你、另一个新进入，看起来和上一帧一样，会漏提醒（PRD:46 允许两行同名）。

**“答完了多久”**：Mac 发每行“发送这一帧时已经过了多少分钟”（0–44640，44640 = 31 天，再久也显示“31 天前”；65535 = 不显示，用于非答完了的行）。卡片收到后用自己的单调时钟（`esp_timer_get_time()`，固件里 `key_input.c:45,119`、`console.c:66,71` 已在用）往上加，文字分档在卡片上算：不到 1 分钟“刚刚”，1–59“N 分钟前”，60–1439“N 小时前”，之后“N 天前”。Mac **只在会话表变化和重连时发**，不再因为时间流逝重发。为什么不让 Mac 定时重发：Mac 睡眠时发不出来，卡片上的分钟数会停住；卡片本地累加没有这个问题，重连时 Mac 重发一次整表就把误差归零。

**心跳。** Mac 睡眠时蓝牙连接不一定断（未实测，T0 补一条“合盖 2 分钟看卡片串口有没有断开事件”）。所以桥接程序每 30 秒写一帧 4 字节的 ROWS（`shown = 0`，标志位 heartbeat 置位），卡片收到心跳不改画面、不算“变化”；卡片 90 秒没收到任何写入（心跳或整表）就当断开，显示“未连接”。整表写入也重置这 90 秒计时。

**整屏状态**：卡片自己判断“未连接”（BLE 断开事件，或 90 秒没写入）；Mac 发一张空表（总数 0、`shown = 0`、不带 heartbeat 位）时卡片显示“没有会话”。桥接程序正常退出前主动断开蓝牙。

**要删的**：`Bridge.swift` 里批准（`Approval`、`approvals`、`approve/ended/gaveUp/send/finish/answer`、PROMPT 和 DECISION 两个特征值）和语音（`import Speech`、`Listener`、`CardWrite`、`lastActive`、`listen/said/follow/beginClip…conclude`、AUDIO/VOICE/VOICE_IMAGE 特征值）两组；`Voice.swift`、`Listen.swift`、`TextImage.swift` 整个删；`main.swift` 的 `listen`、`said`、`speech-access` 三个子命令和 `--wait-limit`、`--voice-locale`、`--clip`、`--result` 选项；`Doctor.swift:102-117` 的语音识别检查和 `:212` 的文案；`mac/bridge/Info.plist:19` 的语音权限说明。`claude_pid` 保留（幽灵会话要用）。`push()` 的三级优先队列（`Bridge.swift:536-548`）简化成“只有一份 ROWS 要写，写失败 1 秒后重试”，重试机制沿用 `writeRetrySeconds`。`tests/test_no_channel.py:22` 和 `tests/test_release.py:293` 断言 usage 文本含 `listen`，改成断言新的子命令集合。

### 4. 蓝牙协议

**特征值**：删 STATE（1002）、PROMPT（1003）、DECISION（1004）、AUDIO（1005）、VOICE（1006）、VOICE_IMAGE（1007）六个；保留 VERSION（1008）；新增 ROWS（`6D2F1009-6B0E-4A8E-9A4B-46756E4D6564`），Mac 写、带响应，权限和现在一样要求加密加认证（`firmware/main/ble_link.c:33-40` 的 `SECURE_FLAGS`）。`LINK_VERSION` 从 2 升到 3（`firmware/main/link_version.h`、`Bridge.swift:90`），两边不等时现有的“版本不配”画面照旧。固件里现有的“功能表指纹”（`ble_link.c:305` 的 `layout_fingerprint`，存 NVS 键 `gatt_layout` `:346`，不同就发 Service Changed `:378`）会在特征值表变化时通知 Mac。Mac 这边 `Bridge.swift` 现在只有连上后的 `discoverServices`（`:644`）和 `didDiscoverServices`（`:656`），没有实现接收这个通知的 `didModifyServices` 回调；刷固件时蓝牙必然断开、重连后本来就会重新发现，所以旧版能用，但要补上这个回调（收到就重新 `discoverServices`），并在 Task 2 的真机检查里加一条“刷新固件后 Mac 不重启也能重连并写 ROWS”。为什么不复用 1002 的 UUID 换内容：旧版 Mac 程序会把新内容当旧格式解析；新 UUID 加版本号让不配的两边一眼看出来。

**ROWS 的内容**：二进制，一次写完，不分包。

```text
偏移  长度  内容
0     1     格式版本，固定 1
1     1     总会话数（0 表示“没有会话”）
2     1     本帧行数 shown，0–6
3     1     标志位：bit0 = heartbeat（心跳帧，只有这 4 字节，不改画面）；其余写 0
4…    每行  行号(1, 1–255) + 状态(1: 低 4 位 1 等你 2 出错 3 在跑 4 答完了，bit7 = 本帧新进入等你或出错) + 已过分钟(2, 小端, 65535 表示不显示) + 名字字节数(1, ≤ 32) + 名字(UTF-8)
```

最长 4 + 6 × (1 + 1 + 2 + 1 + 32) = 4 + 6 × 37 = 226 字节。依据：本机实测 ATT MTU 517，Mac 每次最多写 512 字节（`notes/ble-link.md:32`；固件 `CONFIG_BT_NIMBLE_ATT_PREFERRED_MTU=512`），226 远低于此，所以不分包。名字按 UTF-8 字符边界截到 32 字节，卡片按像素宽再截并加“…”。为什么不用文本加制表符：要转义名字里的制表符和换行，二进制定长头更好校验；为什么不分包、不加序号确认：分包要处理丢包和乱序，蓝牙带响应写本身保证送达和顺序，226 字节一次写完就都不需要。

- 最可能怎么坏：某台 Mac 协商出的 MTU 比实测小。开工时在 `Bridge.swift` 里打印 `maximumWriteValueLength(for: .withResponse)`，小于 226 就把名字上限降到 24 字节（4 + 6 × 29 = 178）。
- 配对加密、绑定、数字比较确认都不动（`ble_link.c:627-637` 的 `ble_link_confirm_pairing`，上键接受、下键拒绝）：配对时按卡片键确认不是“批准 Claude 的请求”，PRD 验收第 9 条不受影响。

### 5. 卡片固件：会话列表

**版面**（240 × 320 竖屏）。四套主题分两类几何：

| 类型（主题） | 顶栏 | 会话区 | 底部 |
| --- | --- | --- | --- |
| 列表型（任务板、街机厅） | y 0–23，24 px：标题、连接状态、总数超 6 时“+N” | y 24–239，6 行 × 36 px = 216 px，每行写项目名、状态词、答完多久 | y 240–319，80 px：角色常驻位 56 × 72 在右下（街机厅的“INSERT COIN”在左下） |
| 格子型（漫画、控制塔） | 同上 | y 24–275，2 列 × 3 行，每格 114 × 80 px，列间距 6（114 × 2 + 6 = 234，左右各留 3），行间距 6（80 × 3 + 6 × 2 = 252） | y 276–319，44 px：会话不满 6 个时只放主题装饰；满 6 个时角色退到这里，只露上半身（56 × 72 的图从顶部裁 44 px） |

格子型主题会话少于 6 个时，角色住在最后一格（PRD:69-70）。这张表里的每个数字都要先用下面说的“Mac 上渲染 PNG”出图给负责人看过再写进代码，这是负责人定的规则：画面选择看图不看字。出图要包含“6 个会话”和“7 个会话（有 +1、角色退位）”两张。

**每套主题画什么**（照 PRD:67-70，主题只改画法不改信息）：

| 主题 | 等你 | 出错 | 在跑 | 答完了 | 提醒样式 | 角色平时 |
| --- | --- | --- | --- | --- | --- | --- |
| 游戏任务板 | 粗圆角色块，整条呼吸（亮度 1 秒一周期） | 红色块 | 色块上斜纹流动 | 灰色块，右侧“N 分钟前” | 圆角卡片，角色从卡片底边冒出 | 右下角 |
| 街机厅 | 黑底绿字像素体，整行像“PRESS START”那样闪（0.5 秒亮灭） | 红字 | 绿字加右侧跳动的三个点 | 暗绿字加时间 | 黄色双线框卡片（出错红色），角色在卡片左边；扫描线不盖角色 | 右下角，“INSERT COIN”左下 |
| 漫画分格 | 一格里大字“！！” | “啊！” | “哒哒哒” | “呼～”加时间 | 黄色爆炸星（出错红色），角色和大字一起撞出 | 最后一格，满 6 个退到底边 |
| 控制塔 | 模块黄灯呼吸 | 红灯急闪（0.25 秒） | 雷达扇形转（100 毫秒一格） | 绿灯常亮加时间 | 整屏泛黄光（出错红光），角色从发光边框实心卡片冒出 | 最后一个模块，满 6 个退到底边 |

**字库**：现有字库只有 107 个固定汉字，碰到项目名里的其他汉字会静默跳过（`firmware/main/scene.c:215-217`、`font.c:73`）。改成三套字：

| 字库 | 字号 | 字符集 | 字体 | 估算大小 |
| --- | --- | --- | --- | --- |
| 正文 | 16 px，4 位灰度 | ASCII 95 + GB2312 一级汉字 3755 + 界面用到的符号 | Noto Sans SC（OFL 许可；仓库只有 `third_party/noto-sans-sc/` 的 LICENSE 和 README，字体文件被 `.gitignore:13` 排除，按 README 的地址下载并核对 SHA-256） | 3850 × 128 B ≈ 490 KB |
| 像素 | 12 px，1 位 | 同上 | 缝合像素字体 Fusion Pixel 12px（OFL 许可；下载地址、版本、SHA-256 和许可证全文在实施时记进 `third_party/`，和 Noto 同一写法） | 3850 × 18 B ≈ 70 KB |
| 大字 | 30 px，4 位 | 数字、拟声词、状态词、整屏提示共约 40 字 | 站酷快乐体 ZCOOL KuaiLe（OFL；同样记来源和校验值） | 40 × 450 B ≈ 18 KB |

生成仍用 `tools/gen_fonts.py`，字符来源从“扫 scene.c 字面量”改成“ASCII + GB2312 一级表 + 扫源码字面量”。**输出改成二进制文件加索引**（每个字的码位、宽、位图偏移），由 CMake 的 `EMBED_FILES`（ESP-IDF 把文件原样嵌进程序的机制）放进固件，生成到 `build/` 下，不进仓库；现有 `font.c`、`font_text.c`（203,726 字节）、`font_large.c`（475,051 字节）删掉。为什么不再生成 C 数组：三套字库会是约 6 MB 的源码，编译慢、diff 不可读。宿主测试要断言两套全字库能画出 GB2312 一级表的每一个字。项目名里一级表以外的字（繁体、日文、emoji）显示成“□”，这是接受的限制。放弃“Mac 把项目名渲染成图发过来”：换主题在卡片上完成，Mac 不知道当前主题用哪种字体，就得每个名字发四份图。

**主题的代码组织**：一个 `theme_t` 结构体，含颜色表、`draw_header`、`draw_slot`（画一个会话格子，参数是会话、格子序号、动画相位）、`draw_empty`、`draw_footer`、角色常驻位和提醒卡片的位置尺寸、动画节拍。四套主题是四个结构体实例，画线、圆角矩形、文字、斜纹、扫描线、精灵贴图是公用的绘图原语。为什么不写四份渲染函数：PRD 说主题只改画法不改信息，公用的“会话 → 格子”的循环写一份，主题只填怎么画一格。

**局部重画**。现在每 0.5 秒整屏重画 320 行（`main.c:16、107-130`），没有帧缓冲，按 40 行一条乒乓发送（`display.c:14-15`）。改成脏区重画：每次会话表变化、动画相位变化、角色换帧，各自按行号登记自己的行范围，画屏任务只渲染并发送有脏区的行段（`scene_render_rows` 已经接受任意 `y0` 和行数，`scene.c:419`）。列表型一行 36 px = 240 × 36 × 2 = 17,280 字节，格子型一格 80 px 高、整宽发送 = 240 × 80 × 2 = 38,400 字节，SPI 40 MHz 下分别约 3.5 ms 和 8 ms。动画节拍 100 ms；“等你”那行呼吸、“在跑”斜纹流动都只重画自己那行或那格。街机厅的整屏扫描线不做成动画（静止的暗线叠在背景上，渲染时按行号判断），只有文字闪烁。

- 最可能怎么坏：CPU 渲染时间没有实测（notes 里没有任何画屏毫秒数）。Task 0 先在真机上测“整屏一帧多少毫秒”并记进 `notes/firmware.md`；3c 做完后再测“一行 36 px、一格 80 px 各多少毫秒”，超过预算就把动画节拍放到 200 ms。

**换主题键**：中间的键（现有固件叫 `up`，301 mV，`notes/hardware.md` 功能键电压表）按一下换下一套，顺序任务板 → 街机厅 → 漫画 → 控制塔 → 任务板。最上面的键（`ok`，0 mV）保留给配对确认接受；最下面的键（`down`，597 mV）保留给配对拒绝，平时不用。主题号用 NVS（芯片里掉电不丢的小存储区）保存：命名空间 `funmedal`、键 `theme`、`nvs_set_u8` 后 `nvs_commit`，和 `ble_link.c:346-359` 存功能表指纹同一套写法；读不到就是 0 = 任务板，所以第一次开机和恢复出厂都是任务板。换主题后 1 秒才写 NVS，连按不反复写 Flash。

**串口命令**（`console.c`，供测试和演示）：`rows` 打印当前会话表，每行“行号 状态字 分钟 名字”；`theme [n]` 读或设主题；`idle <秒>` 把熄屏的 5 分钟改短；`alert <等你|出错>` 直接触发一次提醒（测声音和动画，不用等真会话）；`power` 打印供电判断和熄屏计时；现有 `snap` 截屏、`backlight <百分比>`（`console.c:496-498`）保留。

### 6. 角色、提醒和声音

**角色图**。负责人已有十张 PNG（`private-assets/character/real/01-lying.png` 到 `10-cheer.png`，270–390 × 300–445 px，已带透明通道，2026-10-24 用 `sips -g hasAlpha` 核对）。构建脚本 `tools/gen_assets.py` 把每张裁掉透明边、等比缩进两个框：常驻小图 56 × 72，提醒大图 84 × 108；输出 RGB565 颜色加 4 位透明度（每像素 2.5 字节），和字库一样写成二进制文件加索引、`EMBED_FILES` 嵌入。十张 × 两种 ≈ 10 × (56×72 + 84×108) × 2.5 ≈ 330 KB。输入图没有透明通道时脚本报错退出，不默默生成白方块。放弃调色板压缩：写实照片色彩多，调色板会出色带；Flash 够用。

**动作时间线**（PRD:57）：发呆 5 s → 拍桌子（举起、拍下交替，共 3 下，每张 0.3 s）→ 歪头 + 问号 3 s → 自言自语 + 气泡 3 s → 打瞌睡 + zZ 5 s → 发呆。问号、气泡、zZ 由固件用主题颜色画在角色旁边，zZ 每 0.5 s 上飘一档。各段时长放在一张表里，调起来不用改逻辑；时间线用同一个单调时钟驱动，宿主测试喂虚拟时钟就能断言每段的起止。

**事件优先级**：提醒（等你/出错）> 答完了小条 > 连上了挥手 > 日常循环。高优先级进来时打断低的，结束后回到日常循环的发呆段。

**提醒**：收到的 ROWS 里有行带“新进入”位就触发（见第 3 节，由 Mac 判定，卡片不比较前后帧）：画主题的提醒卡片，角色大图从卡片底边分 8 帧、每帧 50 ms 冒出来，文字是 PRD 的两句话，同时放声音；卡片停留 6 秒后收起。同一帧里多行带新进入位只提醒一次，有出错就用出错的那句。“卡片刚连上 Mac”时挥手配“连上了”（第一次收到非心跳 ROWS 时）；“一个会话答完了”时底部弹小条、角色欢呼 3 秒（同一行号上一帧在跑、这一帧答完了）。

**声音**。现有固件只建了 I2S 录音通道，放音通道从没用过（`firmware/main/voice.c:97-118`，TX 传 `NULL`；放音数据线 GPIO2 在引脚表里有、固件里没配）。做法：

1. 构建时 `gen_assets.py` 把 `wait.mp3`、`error.mp3`（24 kHz 单声道，实测 3.24 s 和 2.90 s）和笑声录音转成 16 kHz 单声道 16 位 PCM，把“笑声 + 那句话”拼成一段，再编成 IMA ADPCM（每个采样 4 位）。编码器用 Python 在脚本里写，现有 `voice_codec.c/h` 整个删（`voice_codec.h:56-65` 只有编码和打包，没有解码可复用）。每段约 5 s × 16000 × 0.5 B ≈ 40 KB，两段 80 KB，同样 `EMBED_FILES` 嵌入。
2. 固件新增 `audio.c`：IMA ADPCM 解码（几十行，宿主测试和 Python 编码器对拍）和 I2S 放音通道：`i2s_new_channel` 只建 TX，`dout = GPIO2`，ES8311 的 DAC 寄存器现有初始化已经写了上电（`voice.c:68-95`），只差把数据送进去和音量寄存器。
3. 为什么不在卡片上解 mp3：ESP32-C3 单核 160 MHz 解 mp3 可行但要引入解码库，而两段固定的话构建时转一次就够；PRD 也说以后换真人录音别的不动，换素材重新构建即可。

- 最大的风险：放音通路从未验证过，GPIO2 是 ESP32-C3 的启动配置引脚，喇叭功放有没有单独的开关也不确定（`notes/hardware.md`“没有找到的”）。实施计划把“在真机上放出一声 1 kHz 测试音”放在所有声音工作之前，放不出来就先查原厂固件的寄存器写法。

**素材包**（SC-15）：目录结构固定为 `pack/character/01-lying.png … 10-cheer.png` 和 `pack/voice/laugh.wav、wait.mp3、error.mp3`。仓库里 `assets/default/` 放默认素材：十张用脚本画的简笔人形和两段用 macOS `say` 合成的声音，不含负责人家人的任何形象和声音；负责人本机的是 `private-assets/pack/`，由现有的 `private-assets/character/real/` 十张图和 `private-assets/voice/` 两段话加笑声按这个结构整理一次（Task 5 的第一步，原目录不动）。CMake 变量 `FUNMEDAL_ASSET_PACK` 指定目录，默认 `assets/default`，构建时跑 `gen_assets.py` 生成到 `build/` 下，生成物不进仓库（`build/` 已在 `.gitignore`）。`tests/test_release.py` 的隐私扫描加一条：仓库里任何文件都不和 `private-assets` 下的文件字节相同；本机没有 `private-assets` 时这条测试报 skipped 而不是 passed。这条扫描只能挡住原文件被误放进仓库，挡不住转换后的位图和 ADPCM（哈希已经变了）；真正的保障是生成物只落在 `build/`，加上 `tests/test_assets.py` 断言默认素材包构建出来的位图和私有包的不同。

**删掉像素小人**：`pet_art.c/h`、`scene.c` 的 `render_pet`（`:393`、`:445`）、`scene.h` 的引用、`tools/gen_pet.py`。

### 7. 电源：熄屏和唤醒

**供电判断**：USB 接着电脑时 ESP32-C3 的 USB 串口外设能报告主机是否连上（ESP-IDF 的 `usb_serial_jtag_is_connected`）；只接充电器不接电脑时它报“没连上”，这时按电池模式处理（负责人 2026-10-24 定，见“待负责人决定的事”第 1 条）。所以供电判断只用这一个信号，不读电量计 CW2017；放弃读电量计的原因是它的 I2C 地址和寄存器仓库里没记录，查了也只为一个负责人已经不要的区分。

**熄屏**：只用电池、连续 5 分钟“没有变化”就把背光调到 0（`display_backlight(0)`，`display.c:64-73`）并停止画屏任务；蓝牙保持连接，CPU 不睡。“变化”指：ROWS 的内容变了（心跳和本地分钟数增加不算）、按了键、来了提醒、正在放声音。唤醒条件：带“新进入”位的等你或出错行（先重画整屏、再开背光，避免闪出旧画面，然后照常提醒）、按任意键、USB 插上。熄屏期间收到“答完了”不亮屏、不排队欢呼。醒来后重新计 5 分钟。

**为什么第一版不进 light sleep**：ESP-IDF v5.5 文档里，蓝牙保持连接的自动 light sleep 需要开 modem sleep、tickless idle、并选主晶振在睡眠时保持供电，`power_management` 页又写蓝牙会一直持有不许 light sleep 的锁，文档之间有冲突，只能实机验证（`https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32c3/api-reference/system/sleep_modes.html`、`…/power_management.html`、NimBLE `power_save` 示例 README）。PRD 只要求熄屏，没要求续航时长；只关背光，520 mAh 电池按芯片约 40 mA 估算能撑 10 小时以上，够一个工作日。light sleep 记进 Backlog，等第一版用起来再做。**不用 deep sleep**：它会断掉蓝牙，醒来要重新广播、Mac 重连，“等你”到了也收不到，和原厂固件 5 分钟深睡的做法不能照搬的原因就是这个。

### 8. 拆掉批准和按住说话

固件：删 `prompt.c/h`、`voice.c/h` 里的录音部分（ES8311 和 I2S 初始化挪进 `audio.c`）、`voice_codec.c/h`、`pet_art.c/h`、`scene.c` 的 `render_prompt`、`render_voice`、`draw_voice_image`、`render_pet` 和相关字符串、`main.c` 的 `decide`、`held_approval`、`on_ok_key` 的批准分支、`voice_press/release`、`ble_link.c` 的六个旧特征值和 `ble_link_send_decision/send_audio/incoming_image`、`console.c` 的 `mic` 和 `ble decision` 命令、`font.c`/`font_text.c`/`font_large.c`；`tests/host/test_prompt.c`、`test_voice_codec.c` 删，解码测试新写在 `tests/host/test_audio.c`。宿主测试加一条：固件源码里不存在任何从按键到蓝牙写 allow/deny 的路径（grep 级断言，SC-11）。
Mac 和插件：见第 2、3 节。
测试：`tests/test_approval.py`、`test_voice.py`、`test_voice_mod.py`、`voice_audio.py` 整文件删；`bridge_harness.py` 删 `approve`、`press`、`wait_prompt`；`test_bridge.py`、`test_bridge_start.py`、`test_plugin.py`、`test_doctor.py`、`test_signing.py`、`test_release.py`、`test_no_channel.py`、`test_card_display.py`、`test_firmware_host.py`、`test_link_version.py` 删改涉及批准、语音、五种旧状态、旧特征值的用例（`test_link_version.py:34-40`、`test_bridge_start.py:181`、`test_card_display.py:49` 直接依赖 STATE 特征值）。
保留并要继续通过的：带 `[OB-7]`、`[OB-9]`、`[OB-11]`、`[OB-12]`、`[OB-13]`、`[OB-14]`、`[OB-15]`、`[OB-17]` 标签的测试（SC-16）。**顺序**：先加 ROWS、新协议在真机上通了再删旧特征值，中间不让 OB 测试变红；`[OB-15]` 预编译固件在每个改固件的批次收尾时重新生成。

**分区表**：现在是 IDF 的单应用大分区（`factory` 1500 KB，`firmware/sdkconfig.defaults:3`），字库、角色、声音加起来约 1 MB，加上现有 763 KB 的程序放不进去。换成自定义 `firmware/partitions.csv`：`nvs` 24 KB、`phy_init` 4 KB、`factory` 4 MB；其余留空。`flash.sh`、`tools/update-prebuilt.sh`、`firmware/prebuilt/partition-table.bin` 跟着更新，`tests/test_flash.py` 验证，改完真机刷一次、画面不变再往下做。为什么不把素材放独立分区：第一版素材和程序一起改，一个分区一次刷完最省事；代价是预编译固件从约 0.8 MB 涨到约 1.8 MB，每次更新都进 git 历史（`notes/firmware.md:49` 记过每次约 0.8 MB），所以预编译只在批次收尾生成，“素材独立分区”记进 Backlog。

## Error Handling

| 情况 | 卡片上看到 | 怎么恢复 |
| --- | --- | --- |
| 桥接程序没在跑、Mac 睡眠、蓝牙关了 | 整屏“未连接” | 固件在 BLE 断开事件或 90 秒没写入时切换；Mac 回来后重连，收到 ROWS 即恢复 |
| Mac 和卡片固件版本不配 | 整屏“版本不配”（现有画面） | 升级两边之一 |
| Claude Code 被强杀，没有 `SessionEnd` | 该行最多 15 秒后消失 | 桥接程序的 pid 检查 |
| `/clear` 换了 `session_id` | 旧行立刻消失、新行在下一句话出现 | 同 pid 不同 id 删旧行 |
| 权限弹窗等了超过 590 秒 | 该行一直“等你” | hook 超时标记让记录保留；终端答掉后由 `PostToolUse` 或下一句话清掉 |
| 终端允许了一条很长的命令 | 该行“等你”直到命令结束 | 已知限制 |
| 用户按 Esc 打断生成 | 该行停在“在跑” | 已知限制，下一句话或会话关掉时恢复 |
| ROWS 写失败 | 画面不变 | 1 秒后重试，直到写成功 |
| 项目名含一级汉字表以外的字 | 那个字显示“□” | 已知限制 |
| 喇叭放音失败（I2S 或功放） | 角色照常冒头，没声音 | 开工第一步在真机验证放音，失败时不进入提醒的开发 |
| 熄屏期间 Mac 断开 | 屏幕不亮 | 不唤醒；下次按键或 USB 插上时看到“未连接” |

## Verification Strategy

未来的实施计划必须包含这些检查：

1. **Mac 上渲染 PNG 的宿主渲染器**：`tests/host/` 现在已经能用 `cc` 在 Mac 上编译固件的 C 代码（`tests/test_firmware_host.py:9-15`）。加一个 `render_png.c`，输入一份会话表、主题号和虚拟时刻，用固件同一套 `scene_render_rows` 输出 PNG。它同时服务三件事：给负责人看版面（代替 HTML 效果图）、自动测试第 4 条（7 个会话只画 6 格，被挤掉的是在跑或答完了的，SC-6）和第 10 条（连续两帧角色区像素不同，SC-12）、四套主题各渲染一张作为回归基线。
2. **桥接程序的 pytest**（`tests/bridge_harness.py` 用假 hooks 喂真桥接程序）：会话表状态机（含 590 秒超时保留、`PostToolUse` 清除、`Notification` 兜底、同 pid 换 id）、排序、6 行截断和总数、ROWS 编码的字节级断言、“新进入”位只置一次（含两个同名会话先后进入等你）、只在表变化时重发、30 秒心跳、pid 检查删会话、重启后读回会话表、20 条权限请求连发不丢（SC-3、SC-4、SC-5、SC-6、SC-7、SC-10 的 Mac 侧）。
3. **固件宿主测试**（`tests/host/`）：ROWS 解析、分钟数本地累加和 59→60 分钟的档位边界、心跳 90 秒超时、角色时间线各段起止（虚拟时钟）、ADPCM 解码对拍、两套全字库覆盖 GB2312 一级表、按键到蓝牙 allow/deny 路径不存在。
4. **真机串口测试**（现有 `tools/cardwatch.py` 读串口）：`rows`、`theme`、`idle`、`alert`、`power`、`snap` 命令；测空表“没有会话”、7 行只画 6 行、断开重连不闪旧表、主题切换和断电保持（SC-8）、熄屏唤醒（SC-9）、提醒触发和声音（SC-5）。
5. **真实 Claude Code 会话测试**（`tools/claude_session.py`）：开三个项目各一个会话看三行（SC-3）；同项目第二个会话、关掉一个（SC-4）；进入权限请求 3 秒内变“等你”并在终端答掉后恢复（SC-5）；按卡片所有键，终端里的权限弹窗仍然等着（SC-11）。
6. **开工前的实测 T0**：一个只记日志的最小插件，在终端和桌面 App 各跑一遍，回答：`AskUserQuestion` 是否触发 `PermissionRequest`；桌面 App 里 `PermissionRequest` 是否触发；强杀 Claude Code 有没有 `SessionEnd`；`/clear` 之后 `session_id` 变不变；hook 进程里 `CLAUDE_PROJECT_DIR` 有没有值、桌面 App 的工作副本会话里它指向哪；`claude_pid` 在桌面 App 里是哪个进程、关会话后退不退出；600 秒后权限弹窗还在不在；Mac 合盖 2 分钟卡片的蓝牙连接断不断。结果记进 `notes/claude-code.md` 和 `notes/ble-link.md`，状态机按结果调整。
7. **放音验证 T1**：真机放一声 1 kHz 测试音，录进 `notes/hardware.md`。
8. **性能实测**：Task 0 测整屏一帧多少毫秒；3c 后测一行 36 px、一格 80 px 各多少毫秒，记进 `notes/firmware.md`。
9. 配套功能回归：SC-16 列的 OB 标签测试在每批结束时全部通过；分区表改动后做一次刷写往返（刷预编译、读版本）。

## 头脑风暴的采纳情况

2026-10-24 两轮。第一轮三个模型在没有本文的工作区（仓库 `3e149b3` 的导出，不含 `.agents/`、`device-scan/`、私有素材）里各自独立出方案；第二轮读本文和实施计划挑错。每个模型的 jsonl 都审计过：只读了允许的文件，没有读别的模型的文件，没有在网上查这个项目（GLM 第一轮往自己的停止文件写了一次 shell，内容是它自己的方案，不违规）。原始文件在负责人本机 `.agents/handoff/2026-10-24-sc2/`，不进仓库。

### DeepSeek V4.1 Flash

- 采纳：`SessionStart` 不建可见行、第一句话才建（它第一轮和第二轮都提）；等你记录的三路结束信号（它指出 600 秒超时会擦掉等你，是本轮最严重的问题）；每行带 Mac 分配的行号；Mac 发已过分钟、卡片本地累加、不再定时重发；心跳和 90 秒超时（它指出 Mac 睡眠时蓝牙未必断）；同 pid 换 `session_id` 删旧行；字库和素材改成二进制嵌入（它算出 C 数组源码约 6 MB）；`display_backlight` 行号、格子 258 > 252、底条 44 px 放不下角色、拍桌子 3 下、`voice_codec` 删除和保留矛盾、`prompt.c` 引用、`test_no_channel` 的 usage 断言、Noto 字体文件不在 git、站酷快乐体要记来源、每套主题画法要照 PRD 写表、虚拟时钟测时间线、熄屏时收到答完了不亮屏、只接充电器算什么要问负责人、Task 1 删旧特征值会让 OB 测试变红、Task 3 拆四步、Task 2 真机标准加空表和 7 行、T0 补四项实测。
- 否定：“十张图可能没抠白底”（本机 `sips` 核对两张都有透明通道，但它建议的“脚本检查透明通道”留下了）；素材独立分区（第一版一次刷完更简单，代价记在第 8 节和 Backlog）。
- 它第一轮方案里和本文不同、本文没采纳的：`PermissionRequest` hook 立即退出靠 `Notification` 判定（6 秒延迟，且允许路径没信号）。

### GLM-5.3-Flash

- 采纳：`Notification permission_prompt` 作为沙箱网络请求的兜底（它从官方文档查到这一句）；MTU 用实测 517/512 作依据而不是 247；`display_backlight` 行号；格子 258 > 252；Task 1 先加 ROWS 再删旧的。
- 否定：“字库和素材的大小只是估算、不可信”（字形字节数来自现有 `font_text.c` 的实际大小推算，表里写的就是估算并标注）；“设计规格引用的文档路径不存在”（`docs/ideas/IDEA-20261024-00{1,2}` 两个文件在仓库里，它的工作区导出时没包含 `docs/ideas/`）。

### MiMo-V2.6-Flash

- 采纳（第一轮）：行带“新进入”标记位由 Mac 置位，卡片据此提醒和脏区重画（解决两个同名会话漏提醒）；事件优先级 提醒 > 答完小条 > 挥手 > 日常循环；正在放声音时不熄屏。
- 否定（第一轮）：每 60 秒定时重发整表（Mac 睡眠时停住，改成本地累加 + 心跳）；用“60 秒内有没有串口流量”判断 USB 接着电脑（`usb_serial_jtag_is_connected` 直接可查）；第一版进 light sleep（见第 7 节）；用父目录区分同名项目（PRD:46 已定不区分）；“十张 PNG 没有透明通道”（核对过有）。
- 采纳（第二轮）：Mac 侧补 `didModifyServices` 回调并加“刷固件后不重启 Mac 程序也能重连”的检查（它查出 `Bridge.swift` 没有这个回调）；`PostToolUse` 要能对上 `PermissionRequest` 的记录，hook 进程得对 `PostToolUse` 也算 `tool_key`（它指出两边键不同，照原表实现会写出永远不生效的删除）；T0 加“`claude_pid` 在桌面 App 里是哪个进程”和“工作副本会话里 `CLAUDE_PROJECT_DIR` 指向哪”，项目名规则补了工作副本的处理；隐私扫描挡不住转换后的素材，改成“生成物只在 `build/`、无私有素材时测试报 skipped”；`private-assets/character/real/` 和 `pack/` 两套路径写清关系；Task 2 的交付物写明升级窗口内旧固件显示“版本不配”是预期；`voice_codec` 去留矛盾（已改为整个删、解码新写）。
- 否定（第二轮）：“4 + 6 × 36 = 218”（是 220；现在帧长已改为 226）；“私有素材的分辨率和时长是编的”（本机实测，它的工作区没有这些文件）；“Source Spec 路径不存在”（工作区导出不含 `docs/execution/`）；升级期保留 8 个旧特征值只升版本号（空壳特征值让两边代码都留一堆死路，补回调比保留旧表便宜）；它采纳的“档位变化时重发”已被 DeepSeek 的本地累加方案取代。
