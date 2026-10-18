# Mac 桥接程序和卡片的状态显示

本文是 FM-13（本项目任务清单里的第 13 项）的设计和实测记录：Claude Code（Anthropic 的 AI 编程助手）的会话状态怎样从 Mac 到达卡片屏幕。卡片上画什么见 [firmware.md](firmware.md)，蓝牙速度见 [ble-link.md](ble-link.md)，hooks（Claude Code 在特定事件发生时自动运行的命令）的实测见 [claude-code.md](claude-code.md)。

## 对照使命

按 [MISSION.md](../MISSION.md) 的“判断一个需求该不该做”逐条回答：

1. **补足的是不是电脑端和手机端都做不好的事？** 是。电脑端只有坐在 Mac 前、窗口没被挡住时才看得到 Claude Code 在干什么。手机端的 Remote Control（用手机远程操作 Mac 上的 Claude Code）要先解锁手机、打开 App 才看得到。卡片一直亮着，补的是 MISSION.md 说的第一件事：“看一眼就知道状态”。
2. **看一眼就能懂吗？** 能。屏幕只有一个像素小人和两三个字（空闲、干活中、等你批准、完成了、出错了），每种状态的背景颜色不同。
3. **按一两个键或说一句话就能完成吗？** 这一项只显示，不用按键。批准和拒绝是 FM-14。
4. **人不在电脑前、不想掏手机时还有用吗？** 有用。这正是它的使用场景：人在别处，抬头看一眼卡片，就知道该不该回电脑。

## 整体结构

```text
+-------------+   [1]    +-------------+   [2]    +-------------+
| Claude Code | -------> | bridge hook | -------> | bridge app  |
|   session   |  stdin   |   process   |  socket  |  resident   |
+-------------+          +-------------+          +------+------+
                                                         |
                                                         | [3] BLE write
                                                         v
                                                  +-------------+
                                                  |    card     |
                                                  +------+------+
                                                         |
                                                         | [4] USB serial
                                                         v
                                                  +-------------+
                                                  |  test only  |
                                                  +-------------+
```

1. Claude Code 每发生一个事件（比如用户发了指令、要用户批准、答完一轮），就运行一次配置好的 hook 命令，把事件内容用 JSON（一种常见的文本数据格式）写进它的标准输入。这个命令就是桥接程序的 `hook` 模式。
2. `hook` 模式只摘出需要的几项，通过 Unix 套接字（同一台电脑上两个程序互相传数据的通道，表现为一个文件路径）交给常驻的桥接程序，然后马上退出。
3. 常驻的桥接程序记着每个会话的状态，算出卡片该显示哪一种。变了就用 BLE（低功耗蓝牙）写给卡片。
4. 卡片的 USB 串口只在测试时用：测试从这里读回卡片收到了什么、显示了什么，平时不需要接 USB。

桥接程序是一个 Swift 写的 Mac 应用包（`.app`），没有窗口，也不在程序坞里显示。它必须由 `open`（macOS 打开应用的命令）启动，否则 macOS 会因为它用蓝牙而把它结束（见 [ble-link.md](ble-link.md) 第 4 条影响）。`hook` 模式不碰蓝牙，所以可以由 Claude Code 直接运行。FM-15 时曾有一个给 Claude Code 当频道服务器的模式，OB-16 删掉了，按住说话的文字只走下面的 `listen`。OB-8 起还有 `listen` 模式和 `said` 命令，都由插件里的 mod（插件里在 Claude Code 进程中运行的一段 JavaScript 程序）运行：`listen` 收按住说话认出的文字，`said` 交回这句话进没进会话，同样不碰蓝牙，见 [voice.md](voice.md) 的“Mac 端”。OB-11 起还有 `doctor` 命令，用来排查装好后哪里不对，见下文“doctor 自检”。

## 卡片怎么收状态

卡片开一个蓝牙服务，编号 `6d2f1001-6b0e-4a8e-9a4b-46756e4d6564`，对外广播的名字是 `FunMedal`。服务里和状态有关的是下面这个特征值（蓝牙里用来读写数据的小格子）；FM-14 加的批准用的两个特征值、以及所有特征值都要求配对加密，见 [approval.md](approval.md)：

| 名字 | 编号 | 怎么用 |
| --- | --- | --- |
| STATE | `6d2f1002-…` | Mac 写入状态名，要卡片确认收到（“有响应写入”）；Mac 也能读回卡片当前的状态。状态名是 `idle`、`working`、`waiting`、`done`、`error` 之一，其他内容一律拒绝，屏幕不变 |

卡片每收到一次写入，都从 USB 串口报一行 `BLE state <状态名>`；连上、断开时报 `BLE connected`、`BLE disconnected reason=<原因码>`。屏幕上的状态真的变了时，照旧报 `STATE <状态名>`。`info` 命令多了三项：`ble`（`connected` 或 `advertising`）、`ble_writes`（开机以来收到的有效状态写入次数）和 `uptime_s`（开机多少秒）。写入的内容必须和某个状态名一字不差，多一个字节、少一个字节、夹着空字节都拒绝，并报 `BLE reject name`；超过 16 字节报 `BLE reject length`。

卡片一次只接受一个连接，连上以后停止广播，断开后马上重新广播。

## 桥接程序怎么算状态

### 一个会话

每个会话（按 hook 输入里的 `session_id` 区分）记两样东西：

- **在等批准的操作**：一组“工具名 + 工具参数”。
- **当前在做什么**：空闲、干活中、完成了、出错了之一。

会话显示的状态：有在等批准的操作时是“等你批准”，否则是“当前在做什么”。

| 事件 | 怎么改 |
| --- | --- |
| `SessionStart`（会话开始） | 新建会话，空闲。已有的会话不变，因为压缩上下文（对话太长时 Claude Code 把前面的内容压成一段摘要）后也会触发它 |
| `UserPromptSubmit`（用户发了指令） | 干活中，清空在等批准的操作 |
| `PreToolUse`（准备用工具） | 干活中 |
| `PermissionRequest`（要用户批准） | 把这个操作加进在等批准的操作，干活中 |
| `PostToolUse` / `PostToolUseFailure`（工具用完 / 运行失败） | 把同一个操作从在等批准的操作里去掉（在卡片上批准过的，先抵掉那一次批准），干活中 |
| 卡片上答了一个请求（FM-14） | 把这个操作从在等批准的操作里去掉 |
| 终端里拒绝，等卡片的 hook 被 Claude Code 结束（FM-14） | 把这个操作从在等批准的操作里去掉；这个会话没有别的在等时改成空闲（G23） |
| `Stop`（答完一轮） | 完成了，清空在等批准的操作 |
| `StopFailure`（因为接口出错结束这一轮，比如额度用完） | 出错了，清空在等批准的操作 |
| `SessionEnd`（会话结束） | 删掉这个会话 |

其中几条的理由：

- **为什么记“一组”等批准的操作**：Claude 可能一次发起几个要批准的操作。批准一个后，别的还在等，卡片应该仍然显示“等你批准”。`PermissionRequest` 的输入里没有操作编号，所以用“工具名 + 工具参数”认出是同一个操作。
- **在终端里拒绝（按 Esc 或选“3. No”）**：这时 Claude Code 不发出任何 hook 事件（实测见下文“实测结果”第 3 条）。FM-13 时卡片因此一直显示“等你批准”，直到下一条指令。FM-14 起 `PermissionRequest` 的 hook 会一直等卡片的回答，而终端里拒绝时 Claude Code 会结束这个 hook 进程，桥接程序看到连接断开就知道了，卡片改成空闲（项目负责人 2026-10-16 选定，G23；实测见 [approval.md](approval.md)）。将来 Claude Code 不再结束 hook 进程时，退回 FM-13 的表现。
- **生成回答时按 Esc 打断，卡片停在“干活中”**：这时也没有 hook 事件，而且没有在等的 hook 可以断开，桥接程序无从得知。卡片一直显示“干活中”，直到下一条指令或结束会话。这时人就在终端前，影响小。项目负责人 2026-10-16 确认就这样处理。
- **为什么不用 `Notification` 的 `idle_prompt`**：`idle_prompt` 是 Claude Code 在输入框前闲了约 60 秒时发出的通知。原本打算用它纠正上一条，实测它只在正常答完一轮（`Stop`）之后出现，打断以后不出现，所以纠正不了。正常答完时卡片已经是“完成了”，也不该改成空闲，因为卡片的用处就是让人回来时还能看到“完成了”。
- **为什么不读会话记录文件**：打断时，Claude Code 的会话记录文件（`transcript_path` 指向的 JSONL 文件，每行一个 JSON）里会出现 `[Request interrupted by user for tool use]`。这个格式不在官方文档里，升级后可能改变，所以没有用。

收到一个没见过的会话的事件（比如桥接程序中途重启过），就当它已经存在，按事件改。

### 几个会话同时在跑

卡片只显示一种状态，按下面的顺序取最靠前的一个：等你批准 → 出错了 → 完成了 → 干活中 → 空闲。没有会话时显示空闲。

排在前面的是需要人去处理的：有一个会话等批准，就算另一个还在干活，也显示“等你批准”。“完成了”排在“干活中”前面，因为它表示有一个会话在等你下一条指令。

### 卡片断开再连上

桥接程序记着卡片上应该显示的状态。每次连上卡片（包括卡片重启后），都先把这个状态写一次，所以卡片重启后会恢复原来的显示。写入要等卡片确认；确认之前状态又变了，就在确认后再写最新的那个，中间的不补写。

### 先核对版本（OB-13）

卡片固件和桥接程序各有一个连接协议版本号（两边约定怎么收发数据的版本，是一个十进制整数），现在都是 2。卡片服务里多一个特征值 VERSION，编号 `6d2f1008-…`，和其他特征值一样要配对加密后才能读写，内容是不带空格、不以 0 开头的十进制数字，1 到 9999。

每次连上卡片，桥接程序先读 VERSION，再把自己的版本号写回去，等卡片确认后再看两边是否一样：

- **一样**：记一行 `version`（`card`、`bridge`、`match` 是 `true`），然后照旧订阅、写状态。
- **不一样**：记一行 `version`（`match` 是 `false`），断开，不再重连。`status` 里 `card` 是 `mismatch`。卡片那边收到写回的版本号后，整屏换成红底白字的“版本不配 / 卡片 2 · Mac 3”，下面写该更新哪一边：Mac 的版本大时是“请更新卡片固件”，卡片的版本大时是“请更新 Mac 上的桥接程序”，最后一行是“然后重启桥接程序”。这个画面一直保留，直到版本一样的桥接程序连上来。
- **找不到 VERSION**（OB-13 之前的固件）：同样算不一样，`version` 那一行的 `card` 是 `null`。旧固件没有这个画面，卡片停在原来的显示上。2026-10-18 把 OB-13 之前的固件只写进程序分区（配对信息不动）测过一次：桥接程序连上 1 次，记一行 `version`（`card` 是 `null`），断开时 `reconnect` 是 `false`，15 秒后 `status` 仍是 `mismatch`、`connects` 仍是 1；`doctor` 报“卡片固件太旧，没有版本号”。测完写回了新固件（`artifacts/ob13-oldfirmware-20261018-101801/`）。这一项是手动做的，没有写成自动测试，因为每次都要换两次固件。
- **读到的不是 1 到 9999 的数字**：记一行 `version_unreadable`（字节数和错误），断开后照常重连。
- **读不出，错误是“加密不够”或“认证不够”**（`CBATTErrorDomain` 的错误码 15 或 5）：说明这条连接没配对成功，比如配对框被取消。`version_unreadable` 那一行多一个 `pairing_failures`，是连续失败的次数；断开后照常重连，macOS 在新连接上会再弹配对框。连续失败 3 次后，`status` 里 `card` 变成 `pairing_failed`，桥接程序停止扫描、不再重连，免得配对框一直弹下去；重启桥接程序或蓝牙重新打开后从 0 算起，连上一次（记 `ready`）也清零。OB-14 加的，实测见 [approval.md](approval.md) 的“实测结果”第 11 条。

不重连是有意的：版本对不上时，重连多少次结果都一样，只会让卡片反复断开。想重试，就更新落后的那一边后退出再启动桥接程序；关掉再打开 Mac 的蓝牙也会让它重新搜一次。

`status` 多了两项：`card_version`（这次连接读到的卡片版本，还没读到或者卡片没有 VERSION 时是 `null`）和 `bridge_version`（桥接程序自己的版本）。`serve` 可以加 `--link-version <1-9999>`，让桥接程序假装是别的版本，只给测试用：这样不用另外编译一个桥接程序，也就不用再点一次蓝牙授权。

旧的桥接程序（不认识 VERSION）连新固件时，不读也不写 VERSION，卡片照常收状态，屏幕不出现版本画面。2026-10-18 用 OB-13 之前编译的桥接程序配新固件跑了 `test_bridge.py`、`test_card_display.py` 和 `test_approval.py`：42 项通过、3 项照常跳过，6 项因为常驻程序中途连走了卡片而失败（见下文“卡片测试期间挡住常驻程序”），加锁后重跑这 6 项全部通过。

[tests/test_link_version.py](../tests/test_link_version.py) 在真实卡片上测三种情况，2026-10-18 10:16 全部通过（`artifacts/ob13-20261018-101634/`）：版本一样时照常连上、写状态；桥接程序假装是 3 或 1 时，各只连 1 次，15 秒里不再重连，没有写状态，卡片显示对应的提示，截图里三行字都在；之后启动版本一样的桥接程序，卡片恢复正常。

## hook 模式的规矩

- **从不挡住 Claude Code**：桥接程序没在运行、套接字连不上、输入不是 JSON，都直接退出，退出码是 0。唯一的例外是 `SessionStart`：连不上时先启动桥接程序，最多等 3 秒，见下文“会话开始时启动桥接程序”。连接和读写都有 1 秒上限；`PermissionRequest` 不同，桥接程序确认收到请求最多等 2 秒，之后一直等回答，见 [approval.md](approval.md) 的“hook 模式的变化”。hook 命令本身的参数写错时（比如 `--socket` 后面没有路径），退出码是 64；按官方文档，退出码不是 0 也不是 2 的 hook 算“不挡事的出错”，操作照常进行，会话记录里会多一条 `hook error` 提示。
- **不往标准输出写任何东西**：`SessionStart` 和 `UserPromptSubmit` 的 hook 往标准输出写的内容，会被 Claude Code 当成上下文交给 Claude。
- **批准请求要等卡片**：FM-14 起，`PermissionRequest` 的 hook 不马上退出，而是等卡片上的回答，规则见 [approval.md](approval.md)。终端里的批准弹窗照常显示，哪边先答算哪边。其他事件仍然马上退出。
- **只传需要的几项**：事件名、`session_id`、工具名、`SessionStart` 的 `source`（会话怎么开始的，比如 `startup`、`compact`）、`SessionEnd` 的 `reason`（会话为什么结束）、`StopFailure` 的 `error`（出错类型），“工具名 + 工具参数”的 SHA-256 校验值（一种内容指纹，内容差一个字也不同），以及 FM-15 起的 `claude_pid`（这个 hook 属于哪个 `claude` 进程，用来把语音送进对的会话，见 [voice.md](voice.md) 的“送进哪个会话”）。不传工具参数本身、命令内容、文件内容、工作目录或用户的指令。唯一的例外是 `PermissionRequest` 多传一段要显示在卡片上的 `summary`，它不写进桥接程序的记录（见 [approval.md](approval.md)）。

## 会话开始时启动桥接程序

OB-9 起，`hook` 模式收到 `SessionStart` 时，如果套接字连不上（套接字文件不存在，或者是上次崩溃的桥接程序留下的），就自己启动桥接程序，等它就绪后再把事件交给它。负责人 2026-10-17 选定由 hook 模式启动，不由 mod 启动（`.agents/tasks/onboarding/goal.md` 的 G20），因为公司用 `allowManagedModsOnly` 只关掉 mod 时，hooks 照样能启动桥接程序和显示状态。

- **怎么启动**：运行 `open -n -g <hook 程序所在的 .app> --args serve --socket <套接字路径>`。用 `open` 是因为桥接程序用蓝牙，必须由 macOS 自己启动（见上文“整体结构”）；这样它也不是会话的子进程，会话结束后照常运行。`-n` 表示即使这个 App 已经有一份在跑（比如在别的套接字上），也再开一份。套接字路径总是写在参数里，因为 `open` 不把环境变量 `FUNMEDAL_SOCKET` 交给新程序。hook 自己带了 `--log` 时，也原样交给新程序，测试靠它看新程序的记录。`open` 的标准输出和错误输出都丢掉：`SessionStart` 的 hook 往标准输出写的内容会进 Claude 的上下文。
- **等多久**：启动后每 0.05 秒试连一次套接字，最多 3 秒。连上就只发一次事件；3 秒内没连上就不发，照常退出，退出码 0。`SessionStart` 的 hook 上限是 5 秒（`plugin/hooks/hooks.json`）。实测冷启动 1.34 秒，套接字留着旧文件时 0.14 秒（`artifacts/ob9-nocard-20261018-010607/`）。
- **两个会话同时开始**：两个 hook 可能都连不上、各启动一份。后启动的那份拿不到套接字旁边 `.lock` 文件的锁，以退出码 3 自己结束（见“怎么配置”第 3 步），两个 hook 都连到先启动的那份。实测两个同时开始各用 0.15 秒，最后只剩一份，两个事件都到了。
- **其它事件不启动**：`SessionStart` 以外的 8 种事件连不上时照旧 1 秒内退出，什么也不启动，测试里等了 3 秒也没有桥接程序出现。
- **一启动就问语音识别权限**：常驻程序启动时看语音识别授权，记一行 `speech`（`status` 是 `authorized`、`denied`、`restricted` 或 `not_determined`）；是 `not_determined` 时请 macOS 弹出授权框，得到回答后再记一行 `speech`。以前要等第一段话开始时才弹框，那一段话会失败，见 [voice.md](voice.md) 的“Mac 端”。

## 怎么配置

1. **编译**：运行下面的命令，生成 `mac/bridge/build/FunMedalBridge.app`。需要 Xcode 命令行工具里的 `swiftc`（Swift 编译器）。脚本用钥匙串里第一张 Apple Development 证书签名，一张都没有时用临时签名（`codesign --sign -`，只在本机有效、不用证书的签名），见下文“签名（OB-10）”。

   ```bash
   sh mac/bridge/build.sh
   ```

2. **启动常驻程序**：装上插件后，会话开始时会自动启动（见上文“会话开始时启动桥接程序”），这一步只在想先启动、或者不用插件时才需要。`-g` 表示在后台打开，不抢走当前窗口。不加 `--socket` 时，套接字在 `~/Library/Application Support/FunMedal/bridge.sock`。套接字旁边还有一个 `bridge.activity.json`，记每个 `claude` 进程最近一次需要你的时间（只有进程号和时间，只有你自己能读），用来决定按住说话的话送进哪个会话，见 [voice.md](voice.md) 的“送进哪个会话”。想留记录就加 `--log <文件路径>`，收到的事件、蓝牙连接和每次写入都会记成一行 JSON。第一次启动时 macOS 会问是否允许它使用蓝牙，选允许。**每次改了代码重新编译后，macOS 都会再问一次**：临时签名没有固定的身份，macOS 按程序内容的指纹记住授权，内容变了就当成新程序。2026-10-16 到 17 日系统授权日志（`tccd` 进程的日志）里有 6 次这样的询问，每次都是用户点的允许；询问没人回答时，桥接程序一直连不上卡片，`status` 里 `authorization` 是 `notDetermined`。

   ```bash
   open -g mac/bridge/build/FunMedalBridge.app --args serve
   ```

3. **查看和停止**：`funmedal-bridge` 是 `.app` 里的程序，路径是 `mac/bridge/build/FunMedalBridge.app/Contents/MacOS/funmedal-bridge`。`status` 打印一行 JSON，其中 `card` 是 `connected` 表示连上了卡片，`card_state` 是卡片确认收到的状态。`quit` 让常驻程序退出。同一个套接字上已经有常驻程序时，再启动一个会马上以退出码 3 结束，第一个不受影响；用 `open` 启动时看不到这个退出码，用 `status` 看 `pid` 有没有变就知道。

   ```bash
   mac/bridge/build/FunMedalBridge.app/Contents/MacOS/funmedal-bridge status
   ```

4. **接上 Claude Code**：运行仓库根目录的 `install.sh`，它把桥接程序复制到 `~/Applications/FunMedalBridge.app`，再从本仓库这个插件市场装上 `funmedal` 插件。插件的 [plugin/hooks/hooks.json](../plugin/hooks/hooks.json) 给 `SessionStart`、`UserPromptSubmit`、`PreToolUse`、`PermissionRequest`、`PostToolUse`、`PostToolUseFailure`、`Stop`、`StopFailure`、`SessionEnd` 这 9 个事件各配一条同样的命令 `"$HOME/Applications/FunMedalBridge.app/Contents/MacOS/funmedal-bridge" hook`；`PermissionRequest` 那一条的 `timeout` 是 600，其他是 5。命令运行固定位置的程序，不运行仓库里的文件：挪走仓库后，已经开着的会话的 hook 不会因为找不到文件而以退出码 2 结束（退出码 2 会拦下工具调用）。桥接程序没装时，shell 找不到程序，以退出码 127 结束，Claude Code 照常往下走。

   不带 `--socket` 时，各个模式先看环境变量 `FUNMEDAL_SOCKET`，有值就用它作套接字路径，没有才用默认路径。测试用 `claude --plugin-dir plugin` 加载插件，并给会话设 `FUNMEDAL_SOCKET`，让插件的 hooks 连到本次测试自己的常驻程序。

   **卡片测试期间挡住常驻程序**：卡片一次只接受一个蓝牙连接。2026-10-18 凌晨，测试跑到一半时，默认套接字上的常驻程序两次被插件的 `SessionStart` hook 启动起来，连走了卡片，测试用的桥接程序 60 秒里搜不到卡片，6 项失败（`artifacts/ob13-oldbridge-20261018-014413/`）。01:46 那次是本项目的 Claude 会话压缩上下文；01:51 那次来源没查实，测试自己开的会话不会这样，因为它们的 hook 只连测试的套接字。这是第三次因为常驻程序占着卡片而测试失败：前两次是测试开始前它就在跑，夹具（测试开始前准备卡片的那段代码）只在开始时查一次，挡不住中途被启动的。现在 [tests/bridge_harness.py](../tests/bridge_harness.py) 的 `card` 夹具在整个测试文件运行期间用 `flock`（一种文件锁，同一时间只有一个进程能拿到）占着默认套接字的 `bridge.sock.lock`，`serve` 拿不到这把锁就以退出码 3 结束。[tests/test_card_guard.py](../tests/test_card_guard.py) 照插件的真实路径送一个 `SessionStart` 事件，再看 10 秒：加锁前常驻程序起来了、卡片被连走（`artifacts/guard-before-20261018-015420/`），加锁后常驻程序起不来、卡片一直在广播（`artifacts/guard-after-20261018-015445/`）。之前失败的 6 项加锁后重跑全部通过（`artifacts/ob13-oldbridge-rerun-20261018-015518/`）。

上面第 2、3 步和默认套接字路径实际跑过一次，记录在 `artifacts/fm13-default-socket-20261016-222621/`。第 4 步的 `install.sh` 由 `tests/test_plugin.py` 用真的 `claude` 在临时 `HOME` 里装过两遍；README 里“卸载”一节的 `uninstall.sh` 由 `tests/test_uninstall.py` 在临时 `HOME` 里接着装好的插件卸过两遍，之后插件列表、插件市场列表和三个设置文件里都找不到 FunMedal；真实会话测试走 `--plugin-dir`，不经插件市场。

## 测试要覆盖的出错情况

| 编号 | 情况 | 期望 | 怎么测 |
| --- | --- | --- | --- |
| 1 | 真实会话走一遍：发指令、要批准、在终端批准、答完、退出 | 卡片依次显示干活中、等你批准、干活中、完成了、空闲 | 真实 Claude Code 会话 |
| 2 | 要批准时卡片慢了 | 从桥接程序收到 hook 送来的 `PermissionRequest` 到卡片报 `STATE waiting` 不超过 1 秒；测试在卡片显示“等你批准”之后才在终端批准 | 同上，记录两边的时间 |
| 3 | 用户在终端拒绝 | FM-14 起改为：卡片 0.5 秒内撤掉提示、变回空闲，测试移到 [approval.md](approval.md) 的第 4 条 | 见 approval.md |
| 4 | 卡片重启丢了状态 | 桥接程序重新连上，卡片恢复成应有的状态 | 模拟 hook 事件 + 让卡片重启 |
| 5 | 桥接程序启动时卡片显示着别的状态 | 连上后卡片改成应有的状态 | 同上 |
| 6 | 几个会话同时在跑 | 按上面的顺序显示；会话结束后不再算它 | 模拟 hook 事件 |
| 7 | 一个会话里同时有两个操作等批准 | 批准一个后仍显示等你批准，两个都完成后显示干活中 | 模拟 hook 事件 |
| 8 | 桥接程序没在运行，事件不是 `SessionStart` | hook 1 秒内退出，退出码 0，标准输出为空；OB-9 起还要求什么也不启动 | 直接运行 hook |
| 9 | hook 输入不是 JSON、为空、或者很大（几 MB 的工具输出） | hook 1 秒内退出，退出码 0，标准输出为空；桥接程序不受影响；记录里没有工具参数的内容 | 直接运行 hook |
| 10 | 上次桥接程序崩溃留下了套接字文件 | 新的桥接程序照常启动 | 留一个空的套接字文件再启动 |
| 11 | 同一个套接字上已经有一个桥接程序在跑 | 第二个拒绝启动，第一个不受影响 | 启动两个 |
| 12 | hook 把工具参数、命令、指令内容或工作目录传了出去 | 套接字上收到的每条消息只有“只传需要的几项”列出的字段，没有测试塞进去的那串内容 | 测试自己在套接字另一头收下 hook 发出的消息，7 种事件各一次 |
| 13 | 蓝牙让卡片上的任务栈不够用 | 跑完其余蓝牙测试后，每个任务的栈至少还剩 1024 字节 | 卡片的 `stacks` 命令；这一项排在卡片重启测试前面，因为重启会让记录从头算 |
| 14 | Mac 蓝牙关掉再打开 | 关掉后桥接程序报蓝牙已关、卡片报断开；关着时会话变成“完成了”；打开后桥接程序自己重连，卡片显示“完成了”，并且只显示这一次 | 要你本人在控制中心关开蓝牙，设环境变量 `FM_BLUETOOTH_TOGGLE_SECONDS`（每一步最多等多少秒）才运行，平时跳过；模拟 hook 事件 + 真实蓝牙 |
| 15 | 会话开始时没有桥接程序（没有套接字文件，或者留着旧的） | hook 启动 hook 程序所在的 App，5 秒内退出，退出码 0，标准输出为空；新程序的父进程是 `launchd`（进程号 1），收到这个 `SessionStart` | 直接运行 hook（`tests/test_bridge_start.py`，OB-9） |
| 16 | 两个会话同时开始，都没有桥接程序 | 最后只剩一个桥接程序，两个事件都到；之后再开始一个会话不再启动 | 同时运行两个 hook，再运行一个 |
| 17 | 桥接程序启动时语音识别授权 | 记一行 `speech`；已经定了就不再问；没定时弹框，人回答后再记一行 | 启动常驻程序看记录；“没定时”那一项要人先运行 `tccutil reset SpeechRecognition com.funmedal.bridge`、设 `FM_PERSON_RESET_SPEECH` 才运行，平时跳过 |
| 18 | 真实会话开始时没有桥接程序 | `~/Applications` 里的桥接程序被启动，会话结束后还在；卡片依次显示干活中、完成了、空闲；mod 的收话程序连到它 | 真实 Claude Code 会话 |

“模拟 hook 事件”是把和 Claude Code 格式相同的 JSON 交给真实的 `hook` 命令，经过真实的套接字、蓝牙和卡片，只是不启动 Claude Code。这样能稳定地重现几个会话、几个操作同时等批准这类真实会话里难以控制的情况。

## status 和记录里的批准信息

FM-14 起，`status` 打印的 JSON 多了几项：

- `prompt`：卡片确认收到的、正在显示的请求编号；没有时是 `null`。
- `approvals`：还在等回答的请求，每个有 `id`（编号）、`session`、`tool`（工具名），最早的排在最前，卡片显示的就是它。
- `sessions` 里每个会话的 `pending`：这个会话还有几个操作在等批准。

`--log` 记录里多了几种行，`kind` 是：

| `kind` | 什么时候记 | 字段 |
| --- | --- | --- |
| `approval` | hook 送来一个批准请求 | `id`、`session`、`tool` |
| `prompt` | 卡片该显示的请求变了 | `id`（没有时是 `null`）、`count`（一共几个） |
| `answer` | 桥接程序回答了一个 hook | `id`；`decision` 是 `allow`、`deny`，或 `released`（不回答，让 hook 退出）；`why` 是 `card`（卡片上答的），或 `post_tool_use`、`prompt`、`stop`、`stop_failure`、`session_end`（因为这些事件放走的） |
| `approval_closed` | 和 hook 的连接结束 | `id`；`how` 是 `answered`（已回答）、`gave_up`（hook 等到自己的上限）或 `hung_up`（hook 被结束，也就是终端里拒绝了） |

写给卡片的请求只记编号，不记内容。

FM-15 起 `status` 和记录里和语音有关的几项（OB-8 加了 `waiting` 和 `voice_followed`）见 [voice.md](voice.md) 的“Mac 端”。

## doctor 自检

`funmedal-bridge doctor` 依次查 9 项，每项打印一行：`<结论> <项目>：<情况>`，结论不是“好”时后面接 `。改法：<怎么做>`。结论有四种：“好”；“注意”（能用，但有你该知道的事）；“不好”；“不知道”（查不了，后面写原因）。有一项“不好”时退出码是 1，否则是 0。

| 项目 | 从哪里查 | 怎么判断 |
| --- | --- | --- |
| 桥接程序 | 向套接字（默认路径，或 `--socket` 指定的）发 `status` | 有回应是“好”，没有是“不好” |
| 蓝牙 | `status` 里的 `authorization` 和 `bluetooth` | `allowedAlways` 且 `poweredOn` 是“好”；拒绝、还没回答授权框、蓝牙关着都是“不好” |
| 语音识别 | `status` 里的 `speech` | `authorized` 是“好”；拒绝或还没问过是“不好” |
| 签名 | 对 `~/Applications/FunMedalBridge.app` 运行 `codesign --verify` 和 `codesign -dv` | 用证书签名是“好”；临时签名是“注意”，因为每次重新安装 macOS 都要再问一次授权，改法是拿一张 Apple Development 证书后重新编译、安装；没装、没签名或签名坏了是“不好” |
| 卡片 | `status` 里的 `card` | `connected` 是“好”；`mismatch`、`pairing_failed` 和其他没连上的情况是“不好”，`pairing_failed` 的改法是重启桥接程序后重新配对；`off` 是“不知道”，看蓝牙那一行 |
| 固件版本 | `status` 里的 `card_version` 和 `bridge_version`，见上文“先核对版本” | 一样是“好”；不一样、卡片没有版本号、正在跑的桥接程序太旧不报版本都是“不好”，并说该更新哪一边；卡片没连上是“不知道” |
| 插件 | `claude plugin list --json` | `funmedal@funmedal` 装着且开着是“好”；没装、关着是“不好” |
| hooks 开关 | `~/.claude/settings.json` 里的 `disableAllHooks` | 是 `true` 时“不好”，其他情况（包括没有这个文件）是“好” |
| Claude Code 版本 | `claude --version` | 不低于 2.1.292 是“好”，和 `install.sh` 的要求一样 |

蓝牙和语音识别的授权必须问正在跑的桥接程序：macOS 按程序记授权，在终端里运行的 `doctor` 自己去读，读到的是终端的授权，不是桥接程序的。所以桥接程序没在跑时，这两项和卡片、固件版本一起显示“不知道”。

**没查的**：`disableAllHooks` 只看用户设置文件，不看项目里的设置和公司统一下发的设置；`--safe-mode` 启动的会话、`allowManagedModsOnly` 限制也查不出来（见 [onboarding.md](onboarding.md) 里“mod 会被关掉”那一条）。

**测试**：[tests/test_doctor.py](../tests/test_doctor.py) 用一个假的桥接程序套接字（按测试给的内容回答 `status`）、一个假的 `claude` 命令和临时家目录，先造“全都正常”的环境，再每次只弄坏一处，一共 20 种情况，检查每一行的结论、改法和退出码（`artifacts/ob11-20261018-015646/`）。把比较卡片和桥接程序版本的判断故意写反后，相关的 2 项失败（`artifacts/ob11-bug-back-20261018-015732/`）。签名的“好”造不出来：要先定下用哪种证书签名。OB-10 之后，仓库编译的桥接程序用证书签名，假程序直接复制它，“全都正常”里签名一项就从“注意”改成了“好”；临时签名另算一种情况（`app-adhoc`），是“注意”并给出改法。2026-10-18 在本机真实环境里跑过两次（`artifacts/ob11-real-20261018-015809/`）：常驻程序没在跑时，桥接程序一项“不好”、4 项“不知道”；启动常驻程序后，只剩固件版本“不好”，因为装着的常驻程序是 OB-13 之前编译的，不报版本号，`doctor` 提示重新运行 `install.sh`。

## 签名（OB-10）

macOS 记蓝牙和语音识别授权时，记的是程序编号加签名里的“认定规则”（designated requirement，macOS 用来判断“是不是同一个程序”的条件，`codesign -d -r- <App 路径>` 能打印出来）。下次启动时，程序满足这条规则才算同一个程序，授权才照旧有效（苹果技术说明 TN3127）。

- **临时签名**：认定规则是程序内容的哈希 `cdhash`。改一个字重新编译，哈希就变，macOS 当它是新程序，再问一次授权。2026-10-18 改之前跑 `tests/test_signing.py`：仓库编译的那份是 `cdhash H"48e3…"`，只在 `Info.plist` 里多加一个键再编译的那份是 `cdhash H"5fbe…"`（`artifacts/ob10-before-20261018-143023/`）。
- **Apple Development 证书**：认定规则是“程序编号是 `com.funmedal.bridge`、证书由苹果签发、证书上的名字是 `Apple Development: <名字>`、中间证书是苹果的开发者证书”，和程序内容无关。本机钥匙串里有两张同名的证书（2027-04 和 2027-05 到期），分别签出的认定规则一字不差。
- **实测（2026-10-18）**：换成证书签名后，第一次启动常驻程序又弹了语音识别和蓝牙授权框，负责人点了允许。之后 `tests/test_signing.py` 编译一份内容不同的桥接程序（内容哈希 `42489…`，仓库那份是 `493c…`），认定规则相同，启动后直接是 `allowedAlways` 和 `authorized`，连上卡片，没有弹框（`artifacts/ob10-after-20261018-143509/`）。再用另一张同名证书签一份内容不同的，3 秒内同样直接拿到授权并连上卡片（`artifacts/ob10-second-cert-20261018-143620/record.txt`，手动做的，没写成测试）。
- **没走过的**：README 第 3 步在 Xcode 里拿证书的几步，本机没有走过，因为钥匙串里已经有证书，本机也没装 Xcode；证书到期后换新证书会不会再问授权，也没测过，同名的新证书认定规则不变，按道理不会再问。
- `build.sh` 用 `security find-identity -v -p codesigning` 列出的第一张 Apple Development 证书。名字不同的证书签出的规则不同，换用另一个 Apple ID 的证书后，第一次启动会再问一次授权。

## 实测结果

以下都在本机终端版 Claude Code 2.1.292、真实卡片和真实蓝牙上测得。

1. **真实会话的状态顺序**：发指令、要批准、在终端批准、答完、退出，卡片依次显示干活中、等你批准、干活中、完成了、空闲，和桥接程序写出的顺序一致。记录在 `artifacts/fm13-real-20261016-221449/`。
2. **等你批准的延迟**：从桥接程序收到 hook 送来的 `PermissionRequest` 到卡片报 `STATE waiting` 用了 0.05 秒。记录同上。Claude Code 弹出批准窗口到 hook 进程把事件送到桥接程序的时间不在里面；FM-8 实测弹窗和 hook 开始的时间相差不到 0.1 秒。
3. **在终端拒绝**：批准弹窗有两种拒绝方式，按 Esc 和选“3. No”，结果一样：屏幕显示 `Interrupted · What should Claude do instead?`，之后不再有 hook 事件。按 Esc 那次 93 秒里都没有，直到测试结束会话时才收到 `SessionEnd`（`artifacts/fm13-real-20261016-221642/`）；选“3. No”那次 20 秒里没有（`artifacts/fm13-reject-no-probe-20261016-224738/`，里面有探测脚本）。按这个结果定下第 3 种情况的期望后，两种方式的测试都通过（`artifacts/fm13-focused-20261016-225144/`）：拒绝后 15 秒内卡片一直是等你批准，发下一条指令后依次是干活中、完成了。生成回答时按 Esc 打断，官方文档写明 `Stop` 不会触发，FM-13 没有单独实测。
4. **`idle_prompt` 什么时候出现**：一轮正常答完（`Stop`）后 60.06 秒出现。记录和探测脚本在 `artifacts/fm13-idle-probe-20261016-221933/`。
5. **卡片重启**：卡片执行 `reboot` 后 0.44 秒打出 `READY`，0.96 秒时已重新连上并恢复成重启前的状态。记录在 `artifacts/fm13-focused-20261016-221112/`。卡片用 `esp_restart`（ESP-IDF 里让芯片从软件重启的函数）重启时 USB 串口不断开，Mac 上的串口一直能读。
6. **卡片任务的栈余量**：`stacks` 报的是开机以来每个任务栈空间最少时还剩多少。`artifacts/fm13-focused-20261016-225144/` 那次，卡片烧完固件后开机 92 秒，期间跑过两次真实会话的拒绝测试和大输入测试，收到 14 次状态写入，读到：按键 2600 字节、屏幕 3440 字节、串口命令 1740 字节、蓝牙 1452 字节（蓝牙任务一共 4096 字节）。测试要求每个任务至少剩 1024 字节，完整测试里这一项在其余蓝牙测试都跑完后检查，每次的数字记在结果目录的 `bridge/card-stacks.txt`。
7. **hook 传出去的内容**：测试在套接字另一头收下 7 种事件的消息，每条只有 `op`、`event`、`session` 和该事件对应的 `tool_name`、`tool_key`、`source`、`reason`、`error`，没有测试塞进工具参数、指令、回答内容里的那串字符（`artifacts/fm13-focused-20261016-225144/bridge/hook-captured.json`）。
8. **Mac 蓝牙关掉再打开**（编号 14），2026-10-17 15:34：你在控制中心把蓝牙关了 23.1 秒。关掉后卡片报 `BLE disconnected reason=0x213`（对方主动断开），桥接程序把卡片标成 `off`；打开后 4.1 秒重新连上，4.7 秒时卡片确认显示“完成了”（`artifacts/fm13-bluetooth-toggle-20261017-153325/`）。前一次运行里程序的表现一样，但测试在关蓝牙之前读桥接程序状态读早了：卡片刚报收到“干活中”，桥接程序还没收到卡片的确认，所以测试失败（`artifacts/fm13-bluetooth-toggle-20261017-152842/`）；之后测试改成等桥接程序收到确认。再前一次没有人关蓝牙，测试等了 2 分钟超时（`artifacts/fm13-bluetooth-toggle-20261017-135530/`）。关蓝牙时桥接程序先收到“卡片断开了”，约 0.06 秒后才收到“蓝牙已关”；这段时间里它照常马上重连，每次都失败（`Local device is powered off`，macOS 的意思是“本机蓝牙已关”），一共 56 次，收到“蓝牙已关”就停了，不影响结果。桥接程序重连失败后不等待就再连，这一点没有改。
9. **换固件后 Mac 用旧的功能表**：卡片换上带语音的固件后，Mac 一直用旧固件的功能表，桥接程序找不到语音的 3 项，连不上。原因和改法见 [firmware.md](firmware.md) 的“换固件后 Mac 还用旧的功能表”。

## 还没做的

- **打断后卡片显示过时的状态**：见上文“桥接程序怎么算状态”，项目负责人已确认这样处理。
- **卡片和 Mac 断开时屏幕上看不出来**：卡片会一直显示最后收到的状态。要显示成什么样，需要另外定。
- **认准自己的卡片**：桥接程序连第一张搜到的卡片。附近有两张卡片时会连错。没在连的那张卡片迟到的连接、断开消息会被忽略，不会打断已经连好的那张，但这一点只按代码检查过，手上只有一张卡片，没有实测。
- **一个会话答完后一直显示“完成了”**：“完成了”要等那个会话收到下一条指令或结束才会消失，而且排在“干活中”前面。所以开着一个答完的会话时，另一个会话在干活，卡片也显示“完成了”。
- **会话被强行结束**：Claude Code 被强行结束（比如直接关掉终端窗口）时可能不触发 `SessionEnd`，这个会话会一直留在桥接程序里。
- **省电的连接参数**：见 [ble-link.md](ble-link.md) 第 3 条影响。
