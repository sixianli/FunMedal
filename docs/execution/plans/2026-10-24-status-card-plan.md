---
status: active
document_type: plan
supersedes: ""
superseded_by: ""
date: "2026-10-24"
---

# 状态卡片实施计划

**Source Spec:** [SOURCE: docs/execution/specs/2026-10-24-status-card-design.md]

**Goal:** 按设计规格把 PRD v0.1 做出来，验收第一层 10 条都能当着负责人演示（第 4 条只靠自动测试）。

**Architecture:** 规则在 Mac（会话表、排序、6 行、已过分钟、“新进入”标记），画法在卡片（四套主题、角色、声音、熄屏）；两边之间一条 226 字节以内的 ROWS 消息加 30 秒心跳，协议版本升到 3。顺序：开工实测 → 拆掉批准和按住说话但不动蓝牙功能表 → 新协议并存、通了再删旧的 → 分区表 → 字库 → 渲染框架 → 第一套主题出图 → 其余主题 → 角色和声音 → 熄屏。每个改固件的批次收尾时重新生成预编译固件，配套功能的回归测试全部通过。

做没做完只看 `python3 ~/.claude/skills/long-task-planning/scripts/longtask.py status` 的输出，本文不写进度。

---

## File Boundaries

- Create：`firmware/partitions.csv`、`firmware/main/theme.h`、`firmware/main/theme_*.c`（四套）、`firmware/main/rows.c/h`（ROWS 解析和会话表）、`firmware/main/character.c/h`、`firmware/main/audio.c/h`、`firmware/main/power.c/h`、`tools/gen_assets.py`、`assets/default/`、`third_party/fusion-pixel/` 和 `third_party/zcool-kuaile/`（只放 LICENSE 和 README，字体文件不进 git）、`tests/host/render_png.c`、`tests/host/test_rows.c`、`tests/host/test_audio.c`、`tests/host/test_character.c`、`tests/test_rows.py`、`tests/test_assets.py`、`experiments/hook-probe/`（T0 的最小插件和记录）。
- Modify：`plugin/hooks/hooks.json`、`plugin/plugin.json`、`mac/bridge/Sources/{main,Bridge,Hook,Sessions,Doctor}.swift`、`mac/bridge/Info.plist`、`firmware/main/{main,scene,display,ble_link,console,link_version}.c`、`firmware/main/CMakeLists.txt`、`firmware/sdkconfig.defaults`、`tools/gen_fonts.py`、`tools/update-prebuilt.sh`、`flash.sh`、`tests/bridge_harness.py`、`tests/test_bridge.py`、`tests/test_bridge_start.py`、`tests/test_plugin.py`、`tests/test_doctor.py`、`tests/test_signing.py`、`tests/test_release.py`、`tests/test_no_channel.py`、`tests/test_card_display.py`、`tests/test_firmware_host.py`、`tests/test_flash.py`、`tests/test_link_version.py`、`README.md`、`notes/bridge.md`、`notes/firmware.md`、`notes/claude-code.md`、`notes/hardware.md`、`notes/ble-link.md`、`.gitignore`。
- Delete：`mac/bridge/Sources/{Voice,Listen,TextImage}.swift`、`plugin/hooks/voice.js`、`firmware/main/{prompt,voice,voice_codec}.c/h`（`voice.c` 的 ES8311 和 I2S 初始化先挪进 `audio.c` 再删）；`firmware/main/pet_art.c/h`、`firmware/main/{font,font_text,font_large}.c`、`tools/gen_pet.py` 等新画面换掉旧画面（Task 3c、3d）后再删，因为在那之前现有画面和 `test_card_display.py` 还要用小人和字库；`tests/{test_approval,test_voice,test_voice_mod,voice_audio}.py`、`tests/host/{test_prompt,test_voice_codec}.c`；`notes/approval.md` 和 `notes/voice.md` 留在 `notes/`，只在开头加一行“2026-10-24 起功能已拿掉”。
- Test：带 `[SC-3]` 到 `[SC-12]`、`[SC-15]` 标签的新测试；带 `[OB-7]`、`[OB-9]`、`[OB-11]`、`[OB-12]`、`[OB-13]`、`[OB-14]`、`[OB-15]`、`[OB-17]` 标签的现有测试继续通过。

## Implementation Tasks

每个任务里的测试先写、先跑出预期的失败，再实现，再跑通；任务内的检查项在实施时按 `~/.claude/skills/verification-discipline/SKILL.md` 补全。改了固件的任务收尾时跑 `tools/update-prebuilt.sh` 重新生成预编译固件并提交（`[OB-15]`）。

### Task 0: 开工前的实测（T0、T1、性能）

- [x] 写最小插件 `experiments/hook-probe/`，只把 hooks 事件和环境变量记到文件；在终端和桌面 App（含一个工作副本会话）各跑一遍，回答：`AskUserQuestion` 是否触发 `PermissionRequest`；桌面 App 里 `PermissionRequest` 是否触发；强杀 Claude Code 有没有 `SessionEnd`；`/clear` 后 `session_id` 变不变；hook 进程里 `CLAUDE_PROJECT_DIR` 有没有值、工作副本会话里指向哪；`claude_pid` 在桌面 App 里是哪个进程、关会话后退不退出；权限弹窗等满 600 秒后还在不在。结果写进 `notes/claude-code.md`。
- [x] Mac 合盖 2 分钟，看卡片串口有没有蓝牙断开事件，写进 `notes/ble-link.md`。
- [x] 真机放一声 1 kHz 测试音（I2S TX 走 GPIO2，ES8311 DAC），结果写进 `notes/hardware.md`。放不出来就先查原厂固件对 ES8311 和功放的写法，再决定声音这一块的做法。
- [x] 真机测整屏一帧多少毫秒，写进 `notes/firmware.md`。
- [x] 按结果修订设计规格里的状态机、项目名和声音各节。
- [x] 桌面 App 里归档会话，看有没有 `SessionEnd`、claude 进程退不退出，结果写进 `notes/claude-code.md`。退出整个 App 不测：两种可能的结果设计里都已能处理（理由见笔记）。
- [x] 负责人确认设计规格“待负责人决定的事”第 3 条：桌面会话的行留到归档为止（2026-10-24 确认）。

### Task 1: 拆掉批准和按住说话，不动蓝牙功能表（SC-16 的回归）

- [ ] 删 Mac、插件、固件、测试里批准和语音的代码（设计规格第 3、8 节的清单），**保留** `ble_link.c` 的六个旧特征值定义和 STATE 的写入路径，让 `[OB-*]` 测试和现有 `test_card_display.py` 继续通过；旧特征值的删除放到 Task 2。
- [ ] `voice.c` 的 ES8311 和 I2S 初始化挪进 `audio.c`（只初始化，不放音）；`voice_codec.c/h` 删。`pet_art.c/h`、`gen_pet.py` 留到 Task 3c、3d：现有画面还要画小人，`test_card_display.py` 也要看它（Task 1 动手时发现，原计划写的是这一步删）。
- [ ] 配对确认的按键保留，`tests/test_pairing_retry.py` 仍通过。
- [ ] 跑 SC-16 的全部 OB 标签测试和 `tests/test_release.py` 的隐私扫描，全部通过；`test_no_channel.py`、`test_release.py` 的 usage 断言改成新子命令集合。
- [ ] 更新 `README.md`、`notes/bridge.md` 的功能描述；`notes/approval.md`、`notes/voice.md` 开头加“已拿掉”说明；`plugin.json` 的 description 改成状态卡片，`test_plugin.py` 跟着改。

### Task 2: 新协议和桥接程序（SC-3、SC-4、SC-6、SC-7、SC-10 的 Mac 侧）

- [ ] `tests/test_bridge.py` 新增（标签 `[SC-3]`、`[SC-4]`、`[SC-5]`、`[SC-6]`、`[SC-7]`、`[SC-10]`）：事件序列 → 会话表状态，含 `SessionStart` 不建行、第一句话建行、`SessionEnd`（`reason: clear`）删旧行、`PermissionRequest` 连接 590 秒内断开清记录、超时标记保留记录、同 `tool_key` 的 `PostToolUse` 清记录并关连接、`Notification permission_prompt` 兜底、`UserPromptSubmit`/`Stop`/`StopFailure` 清全部；排序；7 个会话截 6 行且被挤掉的不是“等你”、总数为 7；ROWS 字节级编码（行号、状态、新进入位、分钟、名字截断）；“新进入”位只置一次，两个同名会话先后进入等你各提醒一次，重连重发不置位；只在表变化和重连时发，30 秒心跳帧为 4 字节；`project` 字段来自 `CLAUDE_PROJECT_DIR`，`.git` 是文件时取主仓库名；pid 不在时会话 15 秒内消失；重启读回会话表；20 条权限请求连发不丢。
- [ ] `Hook.swift`：加 `project` 字段；`PermissionRequest` 只等不答，590 秒退出前发超时标记；`PostToolUse`、`PostToolUseFailure` 也算 `tool_key`。`hooks.json` 删 mod、加 `Notification` matcher `permission_prompt`。`HOOK_FIELDS` 白名单加 `project`。
- [ ] `Sessions.swift` 重写会话表（行号、新进入位、`visible`）；`Bridge.swift` 改成只写 ROWS 加心跳，`linkVersion` 升 3，补 `didModifyServices` 回调，蓝牙变成可用时先用 `retrieveConnectedPeripherals(withServices:)` 取回已经连着的卡片再扫描（T0 合盖实测发现醒来后桥接程序一直停在 `scanning`），启动时打印 `maximumWriteValueLength`；会话表落盘到 `<套接字>.sessions.json`。
- [ ] 固件：`link_version.h` 升 3，`ble_link.c` 加 ROWS 特征值、`rows.c` 解析成会话表并本地累加分钟、心跳和 90 秒超时，串口 `rows` 命令打印“行号 状态字 分钟 名字”；旧画面暂时用新表里的第一行顶替 STATE 的作用，让画面不黑。新协议通了之后删六个旧特征值和 STATE 路径；`tests/test_link_version.py`、`test_bridge_start.py:181`、`test_card_display.py:49` 改到 ROWS。
- [ ] 真机：三个假会话 → `rows` 看到三行、项目名和状态对；空表 → “没有会话”；7 个会话 → 6 行；断开重连不闪旧表；刷新固件后不重启 Mac 程序也能重连并写 ROWS；Mac 合盖睡 2 分钟醒来后 1 分钟内卡片重新收到 ROWS。升级窗口内旧固件显示“版本不配”是预期，不算回归。
- [ ] 重新生成预编译固件（`[OB-15]`）。

Task 2 动手时和上面不同的几处：新测试放进 `tests/test_rows.py`（`test_bridge.py` 只跟着改旧断言）；桥接程序每 5 秒核对一次进程号（测试仍要求 15 秒内消失）；卡片 90 秒没收到 ROWS 时主动断开，计时从版本核对一样那一刻起算；hook 多传 `notification_type`，桥接程序只认 `permission_prompt`；空表在旧画面上显示空闲，“没有会话”要等 Task 3 的新画面。

### Task 3a: 分区表、刷写脚本、预编译

- [ ] `firmware/partitions.csv`：`nvs` 24 KB、`phy_init` 4 KB、`factory` 4 MB；`sdkconfig.defaults` 指向它；`flash.sh`、`tools/update-prebuilt.sh`、`firmware/prebuilt/partition-table.bin` 跟着改，`tests/test_flash.py` 通过。
- [ ] 真机刷一次，画面不变、版本互报正常；做一次刷写往返（刷预编译、读版本）。

### Task 3b: 字库

- [ ] `third_party/` 补 Fusion Pixel 和站酷快乐体的 README（下载地址、版本、SHA-256、许可证），`.gitignore` 排除字体文件；按 Noto 的 README 下载并核对 SHA-256。
- [ ] `gen_fonts.py` 改字符来源为 ASCII + GB2312 一级 + 源码字面量，输出三套二进制字库加索引到 `build/`，`CMakeLists.txt` 用 `EMBED_FILES` 嵌入；删 `font.c`、`font_text.c`、`font_large.c`，`scene.c` 改用新索引取字。
- [ ] 宿主测试：两套全字库能画出 GB2312 一级表的每一个字；一级表外的字画“□”。
- [ ] 真机：现有画面用新字库显示正常。

### Task 3c: 渲染框架和脏区重画

- [ ] `theme.h` 结构体和公用绘图原语（线、圆角矩形、文字、斜纹、扫描线、精灵贴图）；`rows.c` 的会话表接到渲染；脏区登记和按行段发送，替代 0.5 秒整屏重画；动画节拍 100 ms。
- [ ] `tests/host/render_png.c`：输入会话表、主题号、虚拟时刻，输出 PNG；`tests/test_firmware_host.py` 的用例：6 行、空表、“未连接”、7 个会话只画 6 格且顶栏有“+1”（`[SC-6]`）。
- [ ] 真机测一行 36 px、一格 80 px 各多少毫秒，写进 `notes/firmware.md`；超预算就把节拍放到 200 ms。

### Task 3d: 第一套主题（任务板）出图定版面

- [ ] 任务板主题 `theme_board.c`；用宿主渲染器出 0、1、3、6、7 个会话和“未连接”“没有会话”的 PNG 给负责人看，版面数字按意见改定后再往下做。
- [ ] 真机：`tests/test_card_display.py` 改为新画面的断言（`[SC-3]`、`[SC-10]`）。
- [ ] 重新生成预编译固件（`[OB-15]`）。

### Task 4: 其余三套主题和换主题键（SC-8）

- [ ] 街机厅、漫画分格、控制塔三套 `theme_*.c`，按设计规格第 5 节“每套主题画什么”表；每套用宿主渲染器出 6 个和 7 个会话两张图给负责人看过。
- [ ] 中间键换主题；NVS 保存和读回；串口 `theme` 命令。
- [ ] `tests/host` 加主题轮换顺序和默认值用例；真机断电重启保持上次主题（`[SC-8]`）。
- [ ] 重新生成预编译固件（`[OB-15]`）。

### Task 5: 角色、素材包、动作时间线（SC-12、SC-15）

- [ ] 本机把 `private-assets/character/real/` 和 `private-assets/voice/` 整理成 `private-assets/pack/` 的结构（原目录不动）。
- [ ] `tools/gen_assets.py`：核对透明通道（没有就报错退出）、裁边、缩放到 64×74 和 84×108、RGB565 + 4 位透明度、输出二进制加索引到 `build/`；`assets/default/` 默认素材；CMake 变量 `FUNMEDAL_ASSET_PACK`。
- [ ] `tests/test_assets.py`：默认包能构建；换成另一个目录构建出不同的位图；仓库里没有任何文件和 `private-assets` 下的文件字节相同，本机没有 `private-assets` 时报 skipped（`[SC-15]`）。
- [ ] `character.c`：动作时间线（拍桌子 3 下）、问号气泡 zZ、事件优先级、每套主题的常驻位；格子型主题第 6 格固定留给角色，最多显示 5 个会话（PRD Business Rules，负责人 2026-10-25 定）；`tests/host/test_character.c` 用虚拟时钟断言各段起止；宿主渲染器连续两帧角色区像素不同（`[SC-12]`）。
- [ ] 四套主题各出一张带角色的 PNG 给负责人看。
- [ ] 重新生成预编译固件（`[OB-15]`）。

### Task 6: 提醒和声音（SC-5、SC-11）

- [ ] `gen_assets.py` 加声音：转 16 kHz PCM、拼“笑声 + 话”、Python 编 IMA ADPCM；`audio.c` 解码和 I2S 放音；`tests/host/test_audio.c` 解码和 Python 编码器对拍。
- [ ] 提醒由“新进入”位触发、提醒卡片动画、“连上了”挥手、“答完了”欢呼；串口 `alert` 命令。
- [ ] `tests/test_bridge.py` 加真实会话：权限请求 3 秒内变等你、终端答掉后恢复（`[SC-5]`）；真机听到声音、看到角色冒头。
- [ ] `[SC-11]`：按卡片所有键，终端权限弹窗仍在等；宿主测试断言固件源码里没有从按键到蓝牙写 allow/deny 的路径。
- [ ] 重新生成预编译固件（`[OB-15]`）。

### Task 7: 电源（SC-9）

- [ ] `power.c`：USB 接着电脑的判断（只接充电器算电池）、5 分钟计时、“变化”的定义（心跳和本地分钟增加不算，放声音算）、熄屏（背光 0 + 停画屏）、唤醒（先重画再开背光）、熄屏时收到答完了不亮屏；串口 `idle <秒>`、`power` 命令。
- [ ] 真机：只用电池、`idle 30` 后熄屏；喂一个带新进入位的“等你”行，屏幕亮、角色冒头、有声音（`[SC-9]`）。
- [ ] light sleep 和“素材独立分区”记进 `docs/backlog/`。
- [ ] 重新生成预编译固件（`[OB-15]`）。

### Task 8: 文档和交付

- [ ] `README.md`、`notes/firmware.md`、`notes/bridge.md`、`notes/claude-code.md`、`notes/ble-link.md`、`notes/hardware.md` 更新到新设计；`python3 ~/.claude/skills/document-governance/scripts/validate_docs.py . --strict` 通过。
- [ ] 全套测试通过，最终预编译固件提交（`[OB-15]`）。
- [ ] 按 PRD 第 11、12 条开始一周真实使用（SC-13、SC-14）。

## Verification

- `FM_ARTIFACTS=artifacts/run-$(date +%Y%m%d-%H%M%S) .venv/bin/python -m pytest tests -v`（真机接着 USB、桥接程序已编译安装、常驻桥接程序已退出）
- `.venv/bin/python -m pytest tests/test_firmware_host.py -v`（不接卡片，宿主渲染器、ROWS 解析、角色时间线、ADPCM 解码、字库覆盖）
- `python3 ~/.claude/skills/document-governance/scripts/validate_docs.py . --strict`
- 每条验收的证据用 `python3 ~/.claude/skills/long-task-planning/scripts/longtask.py record` 记录

## Closure Checklist

- [ ] Verification has been run and recorded.
- [ ] Affected current-truth documents are reconciled.
- [ ] Remaining future work is recorded in Backlog when needed.
- [ ] This Plan is moved to `docs/archive/plans/` when closed.
