# 上手方案讨论

本文记录 2026-10-17 的一次方案讨论：用户拿到卡片后，怎样少做操作、很快用上 FunMedal。参加讨论的是 Claude 和 OpenCode（另一个 AI 编程工具，这次用的模型是 `opencode/deepseek-v4.1-flash-free`，推理级别 `high`），一共两轮。这次只讨论方案，没有改代码。

起因是项目负责人的三段原话，见 `.agents/tasks/onboarding/goal.md` 的 G1–G3：现在要敲命令启动，不优雅；做成插件或 Skill 会不会更好；除了插件，还有没有让用户少操作、快上手的办法。

## 讨论结果

### 结论

1. **Claude Code 这一侧，插件仍然是最好的装法。** 插件是 Claude Code 的扩展包，可以用一条命令装好。双方意见一致。Skill（教 Claude 做某件事的说明文件）不合适：它只在 Claude 决定用它时起作用，不会在会话开始时自己运行，也不能在后台一直运行，所以没法自动启动桥接程序（Mac 上把 Claude Code 和卡片连起来的程序）。
2. **“比只做插件更好”的改进，在插件之外的三处：**
   - 语音不再要求用开发参数（自己写的频道必须加的一个启动参数）启动 Claude Code：把语音入口从“频道”（Claude Code 把外部消息推进会话的接口）换成插件里的 mod（插件里一段在 Claude Code 内部运行的 JavaScript，详见下文）。
   - 桥接程序不再要自己编译、每次开机手动启动：发编译好的 App，装在固定位置，由插件在会话开始时自动启动。
   - 刷固件不再要装 ESP-IDF（乐鑫官方的芯片开发工具，几 GB）：发编译好的固件文件，用网页或只装 `esptool`（乐鑫的刷写工具，一个小 Python 包）来刷。
3. **按推荐做完后，每天的操作是 0 步**：打开 Claude Code，卡片的状态显示、按键批准、按住说话就都能用。一次性的操作从现在的 7 步（要装几 GB 的工具），降到“装一个 App、跑一条安装命令、刷固件、配对、允许蓝牙和语音识别各一次”。这是按文档推算的，还没实测。
4. **最大的不确定点：mod 能不能真的代替频道，还没实测。** 建议下一步先做一个不碰卡片的小实验，见“还没确认的事”第 1 条。

### 现在最费事的地方

按 `README.md` 的 7 个安装步骤：

| 步骤 | 用户现在要做的 | 一次性还是反复 |
| --- | --- | --- |
| 1 备份、2 刷固件 | 装 ESP-IDF（乐鑫官方的芯片开发工具，几 GB）；每开一个新终端先运行 `export.sh`；用 `idf.py build flash` 编译并刷进卡片 | 一次性，但最重 |
| 3 桥接程序 | 装 Xcode 命令行工具，`sh build.sh` 编译，再用 `open -g … --args serve` 启动 | 每次开机都要再启动；每次重新编译后都要重新允许蓝牙 |
| 4 配对 | Mac 上点连接，卡片上按键 | 一次性，已经够简单 |
| 5 语音授权 | 敲一条命令弹出语音识别授权框 | 一次性，重新编译后再来一次 |
| 6 hooks | 往 `~/.claude/settings.json` 手工粘 9 段 hooks（Claude Code 在特定时刻自动运行的命令，比如要你批准时） | 一次性，容易粘错 |
| 7 频道 | 写 `mcp.json`；每次要用语音，都用一长串参数启动 `claude`，还要在警告里选同意 | 每天 |

第 7 步的“频道”（channel）是 Claude Code 的一种扩展接口，能把外部消息推进会话，FunMedal 用它把语音转成的文字送进会话。自己写的频道不在官方允许名单里，必须带开发参数 `--dangerously-load-development-channels` 启动 Claude Code 才会加载。

双方都认为最费事的三处是：第 1、2 步的 ESP-IDF；第 7 步每天带开发参数启动；第 3、5、6 步的手工粘配置、重新编译后重新授权、每次开机手动启动。

### 讨论过的方案

| 编号 | 方案 | 谁提的 | 讨论后的结论 |
| --- | --- | --- | --- |
| 1 | 插件装 hooks；会话开始时自动启动桥接程序 | Claude，讨论前已向负责人提过 | 保留，是推荐组合的第一部分 |
| 2 | 插件里加 mod，语音改由 mod 送进会话，代替频道 | Claude 第一轮；OpenCode 第一轮没想到，第二轮同意，并补了 6 条风险 | 推荐，但要先做实验 |
| 3 | 安装脚本把 9 段 hooks 合并进 `settings.json`，再加一个 `fmclaude` 短命令代替长启动命令 | OpenCode 第一轮 | OpenCode 第二轮自己放弃：插件已能一条命令装好；卸载、升级、和用户已有 hooks 共存，脚本都不如插件干净 |
| 4 | 编译好、签好名的桥接 App，装在固定位置 | 双方都提了 | 推荐。要发给别人用时，需要买 Apple 开发者会员 |
| 5 | 编译好的固件文件，用网页或只装 `esptool` 来刷 | 双方都提了 | 推荐。网页上做“备份”比 Claude 原先想的难，见下文 |
| 6 | 菜单栏 App 当总入口，第一次打开是安装向导 | Claude；OpenCode 的签名 App 方案与它相近 | 等决定发给别人用时再考虑 |
| 7 | 用跨会话消息（Claude Code 会话之间互发消息的功能）当语音入口 | Claude 列为备选 | 双方都认为比 mod 差，不用 |
| 8 | 用插件的后台监视器（monitor，会话期间一直运行的命令，输出作为通知交给 Claude）当语音入口 | Claude 列为研究项 | 双方都认为不合适 |
| 9 | 登录 Mac 时自动启动桥接程序（LaunchAgent：macOS 登录后自动运行程序的配置文件） | OpenCode 第一轮 | 第二轮双方一致：默认在会话开始时启动，登录时启动只做可选项 |

方案 2 的 mod 是插件里的一段 JavaScript，在 Claude Code 内部运行。它能在会话开始时做事、定时做事、启动并读取一个一直运行的小程序，还能用 `$.prompt.submit({ text, asUser: true })` 把一段文字当作用户自己打的字送进会话。语音因此可以不走频道，也就不需要开发参数。

### 推荐的组合（按先后）

1. **插件：hooks 加 mod，不含频道。**
   - 一次性：运行一条命令 `claude plugin install funmedal --marketplace sixianli/FunMedal`。`sixianli/FunMedal` 是放插件的 GitHub 仓库，Claude Code 把它当作插件市场（可以从中安装插件的仓库）。这条命令要求 Claude Code v2.1.292 或更新，负责人的终端版正是 2.1.292。
   - 每天：0 步。mod 在会话开始时启动桥接程序，并把卡片上说的话当作你打的字送进会话。状态显示和按键批准继续由 hooks 完成。
2. **编译好的桥接 App，装在固定位置**（例如 `/Applications/FunMedalBridge.app`）。
   - 一次性：装 App；允许蓝牙、允许语音识别各一次。
   - 每天：0 步。
   - 自己用时，沿用现在的临时签名（只在本机有效的签名）就够了。要发给别人用，必须用 Developer ID（Apple 发给付费开发者的签名证书）签名并公证（交给 Apple 自动检查一遍），否则别人的 Mac 会拦下这个 App。
3. **不用 ESP-IDF 刷固件。**
   - 一次性：备份原厂固件，再刷编译好的固件文件。命令行方式只装 `esptool`；网页方式要用 Chrome 或 Edge。刷完照旧配对一次。
4. **只给旧版本用户的备用路：频道加短启动命令。** 只有 Claude Code 低于 v2.1.287 时才需要。负责人的终端版 2.1.292、桌面 App 自带的 2.1.295 都满足 mod 的版本要求（`notes/claude-code.md:14`），所以自己用时不需要这一步。

### 双方一致的地方

- 插件比改 `settings.json` 稳：卸载、升级、和你已有的 hooks 共存，都更干净。
- mod 比频道好：不需要开发参数；也不会出现“会话没有加载频道，卡片却显示已发送”的情况，FunMedal 之前把这种情况叫“假发送”。
- 跨会话消息和后台监视器都不适合当语音入口。
- 桥接程序放在插件外面的固定位置，不放进插件。
- 第 4 步配对已经够简单，不用改。

### 双方不同的地方，以及 Claude 的判断

1. **频道要不要继续维护。** OpenCode 认为要，给低版本的 Claude Code 当备用路。Claude 认为：负责人自己的两个版本都够用，备用路只在发给别人用时才需要，可以等决定发不发给别人时再定。
2. **“插件里直接带 App，每次插件升级都会重弹授权”**（OpenCode）：部分成立。macOS 按程序内容记住蓝牙和语音授权（`notes/bridge.md:113`），App 内容没变就不会重弹；但只要改了桥接程序的代码，就会重弹。所以放在插件外面仍然更好，结论不变。
3. **“跨会话消息在你人不在时正好会被扣住”**（OpenCode）：部分成立。只有接收消息的会话处在跳过所有权限确认的模式（bypassPermissions）时，消息才默认被扣住等你批准；在自动模式（Claude Code 自己判断哪些操作要你确认）等会弹确认的模式下，默认会送达（cross-session-messaging 的 “Control inbound messages”）。这不改变结论：跨会话消息到了 Claude 那里，会被标成“来自另一个会话，不是你说的”，不能当作你的指令。
4. **“网页上先备份、再刷固件”**（Claude 第一轮）：OpenCode 指出这一条写得太轻，成立。ESP Web Tools（常用的网页刷固件组件）不能把卡片里的内容读回来；要做网页备份，得用 esptool-js（乐鑫官方的 JavaScript 版刷写库，有 `readFlash` 读回功能）自己写页面。
5. **菜单栏 App 和桥接程序分成两个程序**（Claude 第一轮）：OpenCode 指出，蓝牙和语音授权跟着真正使用它们的程序走，分成两个程序就会有两套授权框。成立：要做菜单栏 App，就让桥接程序本身当菜单栏 App。

### 走 mod 这条路要注意的事

前 6 条是 OpenCode 第二轮补充的，Claude 已对照官方文档核对过；第 7、8 条是 Claude 核对时补的。

1. **10 秒限制。** mod 处理一个事件最多运行 10 秒（等待 mods 接口调用的时间不算）。所以听卡片的语音，要用 `$.process.spawn` 启动一个一直运行的小程序，不能在处理事件时一直等着。
2. **Claude 在忙时，语音要排队。** `$.prompt.submit` 会等会话空闲才开始新一轮。Claude 正在干活、或者正停在批准框上时要等多久，文档没写。卡片必须分清“已排队”和“已送进去”，否则又成了假发送。
3. **mod 会被关掉。** 用 `--safe-mode`（关掉所有自定义的安全模式）启动、在设置里写 `"disableAllHooks": true`、或者公司用 `allowManagedModsOnly` 限制时，mod 不加载，语音也就不能用。这时卡片应该显示“没有能接收的会话”。
4. **会话记录里看不出这句话来自语音。** 用 `asUser: true` 送进去的文字，和你亲手打的一样。项目规定不记录说话内容，所以识别错一句时，事后只能查到“几点向哪个会话送过一句语音”，查不到原文。
5. **公司电脑上可能被拦。** 文档写明，别的 mod 可以拦下 `$.process.spawn` 的结果。个人账号不受影响。
6. **版本要求。** 终端要 v2.1.287 或更新，桌面 App 自带的 Claude Code 要 v2.1.286 或更新。
7. **桌面 App 里也能用语音。** 文档写明 mod 在桌面 App 的 Code 标签页里运行；而频道加开发参数的方式在桌面 App 里能不能用，还没验证过（`notes/claude-code.md:139`）。如果实验成功，按住说话就能用在桌面 App 的会话里，这是现在做不到的。
8. **`/clear` 之后。** mod 的“会话开始”事件在 `/clear`（清空当前对话）、`/resume`（接着旧对话）、`/branch`（从当前对话分出一支）之后不再触发；文档写的是定时器只在 mod 重新加载时停。所以 `/clear` 之后监听语音的小程序应该还在，但要实测。桥接程序按 `claude` 进程号认会话，`/clear` 不换进程号（`notes/voice.md` “送进哪个会话”），所以一句话送进哪个会话不受影响。

### 核对过的事实

| 事实 | 出处 |
| --- | --- |
| mod 默认开启；终端要 v2.1.287+，桌面 App 自带的要 v2.1.286+；`--safe-mode`、`disableAllHooks`、`allowManagedModsOnly` 会关掉它 | https://code.claude.com/docs/en/plugins/mods/overview “Turn mods on or off” |
| mod 在终端、桌面 App 的 Code 标签页、VS Code 扩展里运行 | 同页 “Where mods run” |
| `$.prompt.submit` 等会话空闲后开始新一轮；加 `asUser: true` 就作为用户自己的话，不带 mod 的署名 | https://code.claude.com/docs/en/plugins/mods/api “Start a turn from a background job”；mods/reference 的 `$.prompt` 一行 |
| 定时器只在 mod 重新加载时停 | mods/api “Stop background work” |
| 别的 mod 可以拦下 `$.process.spawn` 的结果 | mods/api “Reach files, processes, and the network” |
| 处理一个事件最多 10 秒；`$.process.run` 默认 30 秒超时 | https://code.claude.com/docs/en/plugins/mods/reference “Limits” |
| “会话开始”事件不在 `/clear`、`/resume`、`/branch` 之后触发 | mods/reference 的 `session.start` 一行 |
| `CLAUDE_CODE_PLUGIN_DIRS` 可以像 `--plugin-dir` 一样加载插件目录，给不能传启动参数的地方用 | mods/reference “Settings and environment variables”；https://code.claude.com/docs/en/env-vars |
| 从终端一条命令装插件，会自动加上插件市场，不需要确认；要求 v2.1.292；私有仓库用本机已有的 git 凭据 | https://code.claude.com/docs/en/plugins/install “Add and install from your shell”“Add a private marketplace” |
| 只有 Anthropic 官方插件市场要在开过一次交互会话后才自动加上；自己的市场用上面那条命令就行 | plugins/install “Install a plugin”“Install from your shell” |
| `${CLAUDE_PLUGIN_DATA}` 是 `~/.claude/plugins/data/<id>/`，插件升级后还在 | https://code.claude.com/docs/en/plugins/components “Path variables and persistent data” |
| 后台监视器只在交互会话里启动，`-p`（只跑一次、不进入对话的模式）下不启动，拿不到用户配置 | plugins/components “Monitors” |
| 跨会话消息会告诉 Claude 它来自另一个会话、不是你；不能批准任何东西；接收方跳过权限确认时默认扣住 | https://code.claude.com/docs/en/cross-session-messaging “How a session treats an incoming message”“Control inbound messages” |
| esptool-js 有 `readFlash`；不能从 ELF（编译器直接产出的程序文件）生成固件文件；支持 Chrome 和 Edge 89+ 以及安卓上的 Chrome | https://github.com/espressif/esptool-js README 开头和 “Read Flash Memory” |
| ESP Web Tools 只列了安装、配 Wi-Fi、看日志，没有读回 | https://esphome.github.io/esp-web-tools/ |
| 桥接程序必须用 `open` 启动 | `README.md:68`、`notes/bridge.md:40` |
| 临时签名按程序内容记授权，重新编译就重弹 | `notes/bridge.md:113`、`mac/bridge/build.sh:9` |
| 已有桥接程序在跑时，新启动的会退出，退出码 3 | `mac/bridge/Sources/main.swift:4`、`:25-30` |
| 备份含卡片独有的身份和配对数据，不能上传 | `README.md:28` |
| Apple 开发者会员 99 美元一年，含 Developer ID 和公证 | https://developer.apple.com/programs/ |

### 还没确认的事，以及能分出对错的最小检查

1. **mod 能否代替频道。** 做一个只有 `session.start`、`$.clock.every` 和 `$.prompt.submit({ text, asUser: true })` 的小插件，不碰卡片。终端用 `claude --plugin-dir` 加载；桌面 App 用 `CLAUDE_CODE_PLUGIN_DIRS` 加载（要在 `~/.claude/settings.json` 的 `env` 里加一行，做之前先问负责人）。看四种情况：会话空闲时、Claude 在干活时、批准框挂着时、`--safe-mode` 下。
2. **`$.process.spawn` 启动的小程序。** 它的父进程号是否等于 hooks 报的 `claude_pid`（hooks 所属的 `claude` 进程号）；一直运行 5 分钟会不会被结束；`/clear` 之后还在不在。
3. **装插件后第一次加载 mod，用户要不要额外点确认。** 文档写了自己写的 mod 要批准、工作目录要信任，没写从插件市场装的 mod 是否另要确认。
4. **正式签名能否让授权跨升级保留。** 用同一个 Developer ID 签两个内容不同的版本，升级后看是否重弹蓝牙和语音授权框。这决定要不要花钱。
5. **网页备份。** 用 esptool-js 在真卡片上读整块 8 MB，看要多久、是否完整；Firefox 能不能用（ESP Web Tools 网站和 esptool-js 的说明不一致）。
6. **`SMAppService`**（macOS 13 起让 App 自己登记登录时启动的系统接口）对临时签名是否可用。只在做菜单栏 App 时需要。

### 按 MISSION.md 逐条回答

1. **补足的是不是电脑端和手机端都做不好的事？** 不直接是。上手方案本身不给卡片加新功能，它让已有的三件事（看一眼知道状态、按键批准、按住说话）更容易用上。只有一处是新增：mod 方案如果实验成功，按住说话也能用在 Claude 桌面 App 的会话里。所以按 `MISSION.md` 的规则，做不做由负责人决定。
2. **看一眼就能懂吗？** 卡片显示不变；只新增一处：卡片要分清“已排队”和“已送进去”。
3. **按一两个键或说一句话就能完成吗？** 日常从“敲一长串命令启动”降到 0 步，按键和说话之前不再需要先去终端做事。
4. **不在电脑前时仍然有用吗？** 不变。桥接程序自动启动后，不会出现走开以后才发现忘了启动、卡片没反应的情况。

### 需要负责人决定的事

1. **只给自己用，还是要发给别人用？** 只自己用时，不需要 Apple 开发者会员，不需要公开发布固件文件，也不需要频道备用路。
2. **下一步先做哪件：** mod 小实验；直接做插件；先做不用 ESP-IDF 的刷固件；或者都先不做。
3. **要发给别人用时再定：** 是否花 99 美元一年买 Apple 开发者会员；是否公开发布固件文件；刷固件走网页还是命令行；是否保留“备份原厂固件”这一步。

## 讨论记录

- 两轮的原始文件在本机 `.agents/handoff/2026-10-17-onboarding-consult/`，没有进 git，因为那里还放着 OpenCode 任务服务器的密码：
  - `2026-10-17-onboarding-oc-brief.md`：第一轮的讨论说明。
  - `2026-10-17-onboarding-claude-proposal.md`：Claude 在读到 OpenCode 意见之前写好的方案，SHA-256 文件指纹 `bf1bbe12f30d738acada3f55c8637d75b9bc3f133907cd02a925a205a068ccea`。
  - `2026-10-17-onboarding-oc-stop-1.md`：OpenCode 第一轮结论。
  - `2026-10-17-onboarding-oc-reply-1.md`：Claude 的 8 点质疑。
  - `2026-10-17-onboarding-oc-stop-2.md`：OpenCode 逐条回应和修改后的推荐。
- 时间（东京时间）：第一轮 17:13 开始，17:16 写完结论；第二轮 17:17 开始，17:19 写完结论。两轮的退出码都是 0。两轮都没有改动仓库里的文件（`git status` 只多出本任务目录）。
- 模型：两轮都用 `opencode/deepseek-v4.1-flash-free#high`。按 `goal.md` 的 G4，默认用这个免费版；两轮都没有遇到限流，所以没有换模型。
- 更正：Claude 方案文件里写的“17:20”“17:40”是估计的时间，不是实际时间。按文件修改时间，整份方案（含补充的方案 F、G）最后一次修改是 17:15:40，早于 OpenCode 第一轮结论写成的 17:16:03，所以“写于读 OpenCode 意见之前”这一点成立。
