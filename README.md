# FunMedal

FunMedal 把一张 ESP32-C3 电子卡片（原厂固件叫 `trae_card`，带 240×320 彩色屏幕、4 个按键、麦克风和电池）变成 Claude Code（Anthropic 的 AI 编程助手）的桌面伴侣。Claude Code 照常在 Mac 上运行，卡片通过加密的蓝牙和 Mac 上的桥接程序（本项目里一个常驻后台的小程序）相连。

它只做电脑端和手机端都做不好的事，见 [MISSION.md](MISSION.md)：

- **看一眼就知道状态**：卡片上的像素小人显示 Claude Code 正在干活、等你批准、做完了、出错了，还是空闲。
- **在卡片上批准或拒绝**：Claude Code 要你批准一个操作时，卡片显示工具名和命令。长按右侧最上面的键批准，按一下右侧最下面的键拒绝。终端里的批准弹窗照样在，哪边先答算哪边。
- **按住说话**：按住右侧中间的键说一句短指令，松开后 Mac 用离线语音识别转成文字，送进最近需要你的那个 Claude Code 会话，就像你在电脑上打了这句话。卡片先显示“已排队”和认出的文字，送进去后显示“已送进去”，没送进去时说明原因。

## 需要什么

| 东西 | 说明 |
| --- | --- |
| 卡片 | 和本项目同款的 ESP32-C3 卡片，硬件细节和引脚见 [notes/hardware.md](notes/hardware.md)。别的板子引脚不同，不能直接用 |
| Mac | 本项目在 Apple 芯片的 Mac、macOS 27.2 上开发和测试。桥接程序用到蓝牙和离线语音识别，都是 macOS 自带的 |
| Xcode 命令行工具 | 提供 `swiftc`（Swift 编译器），用来编译桥接程序 |
| ESP-IDF v5.5.5 | 乐鑫官方的 ESP32 开发环境，用来编译和烧录卡片固件，装法见[乐鑫的安装文档](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32c3/get-started/index.html) |
| Claude Code | 要 2.1.292 或更新的版本，`install.sh` 会检查；本项目用终端版 2.1.292 测试。批准功能要用到 `PermissionRequest` hook（Claude Code 在要你批准时运行的外部命令）；按住说话靠插件里的 mod（插件里在 Claude Code 进程中运行的一段 JavaScript 程序）把话送进会话 |
| Python 3 | 只有跑测试时需要 |

## 安装

下面的命令都在仓库根目录运行。`<端口>` 是卡片用 USB 线连上 Mac 后出现的串口，比如 `/dev/cu.usbmodem11301`，用 `ls /dev/cu.usbmodem*` 能看到。

### 1. 备份原厂固件

烧录本项目的固件会覆盖卡片上的原厂固件。先把整块 8 MB Flash（存程序和数据的存储芯片）读出来存好，以后想用回原厂功能时写回去。备份里有这张卡片独有的身份和配对数据，**不要上传到任何地方**；仓库的 `.gitignore` 已经排除了 `firmware-backup/`。

```bash
export IDF_TOOLS_PATH=$HOME/esp/.espressif && . $HOME/esp/esp-idf-v5.5.5/export.sh
```

```bash
mkdir -p firmware-backup && python -m esptool --chip esp32c3 -p <端口> read_flash 0 0x800000 firmware-backup/factory-full8MB.bin
```

```bash
shasum -a 256 firmware-backup/factory-full8MB.bin > firmware-backup/factory-full8MB.bin.sha256
```

原厂固件闲置 5 分钟会进入深度睡眠，USB 串口随之消失。连不上时按一下右侧任意一个键把它唤醒，再运行命令。

### 2. 编译并烧录卡片固件

第一条命令每开一个新终端都要先运行一次，它让当前终端能用 ESP-IDF。

```bash
export IDF_TOOLS_PATH=$HOME/esp/.espressif && . $HOME/esp/esp-idf-v5.5.5/export.sh
```

```bash
cd firmware && idf.py -p <端口> build flash
```

烧录完卡片会重启，屏幕上出现像素小人。固件的结构和串口命令见 [notes/firmware.md](notes/firmware.md)。

### 3. 编译桥接程序，装上 Claude Code 插件

```bash
sh mac/bridge/build.sh
```

```bash
sh install.sh
```

`install.sh` 依次做三件事：

- 查 Claude Code 的版本。低于 2.1.292 时它会停下，让你先把 Claude Code 升级到最新版。
- 把编译好的桥接程序复制到 `~/Applications/FunMedalBridge.app`。每次重新编译后都要再运行一次 `install.sh`，把新版本复制过去。
- 把这个仓库加为 Claude Code 的插件市场（可以从中安装插件的地方），从中装上 `funmedal` 插件。插件里是 hooks（Claude Code 在各种事件发生时运行的外部命令）和一个 mod（在 Claude Code 进程里运行的一段 JavaScript 程序），它们都运行 `~/Applications` 里的桥接程序，不用你改 `~/.claude/settings.json`。hooks 把会话的状态和批准请求交给桥接程序；mod 在每个会话开始时接上桥接程序，把卡片上说的话当作你打的字送进会话。

插件直接从仓库目录加载，装好后不要挪动仓库；要挪，先卸载插件、重开会话。已经开着的 Claude Code 会话要重开，才会用上插件。

hook 只把事件名、会话编号、工具名、它属于哪个 `claude` 进程等几项交给桥接程序，不传命令内容、文件内容和你的指令；唯一的例外是要显示在卡片批准画面上的那一小段内容，它不写进任何记录。详见 [notes/bridge.md](notes/bridge.md) 和 [notes/approval.md](notes/approval.md)。

桥接程序不用你自己启动：Claude Code 的会话开始时，插件的 hook 发现它没在运行，就用 `open` 在后台启动 `~/Applications` 里的它，这一次会话开始要多等约 1.3 秒。它不跟着会话结束，关掉会话后照常运行，直到你让它退出或者重启 Mac。想先启动它也可以：

```bash
open -g ~/Applications/FunMedalBridge.app --args serve
```

桥接程序必须用 `open` 启动，否则 macOS 会因为它用蓝牙而把它结束。它第一次启动时，macOS 会先后问是否允许它使用语音识别和蓝牙，都选允许。**每次重新编译后 macOS 都会再问一次**，因为脚本用的是只在本机有效的临时签名，macOS 把内容变了的程序当成新程序。

### 4. 蓝牙配对

桥接程序第一次连上卡片时，Mac 弹出蓝牙配对对话框，上面有六位数字；卡片同时显示蓝色的“蓝牙配对”画面和同一串数字。两边数字相同时，先在 Mac 上点“连接”，再按卡片右侧最上面的键。不同就按右侧最下面的键取消。

配对失败或取消后，退出桥接程序，再照第 3 步启动它（或者开一个新的 Claude Code 会话），就会重新弹出配对对话框。退出用这条命令：

```bash
~/Applications/FunMedalBridge.app/Contents/MacOS/funmedal-bridge quit
```

查看连接状态：输出里 `card` 是 `connected` 就连上了。

```bash
~/Applications/FunMedalBridge.app/Contents/MacOS/funmedal-bridge status
```

## 怎么用

| 卡片上 | 做什么 |
| --- | --- |
| 看屏幕 | 小人的样子和下面的字告诉你 Claude Code 现在的状态 |
| 等你批准时长按右侧最上面的键 | 批准屏幕上的那个操作 |
| 等你批准时按一下右侧最下面的键 | 拒绝它，Claude 会收到“在卡片上拒绝了” |
| 按住右侧中间的键说话，松开 | 把这句话送进最近需要你的那个会话：你刚给它发过指令、它刚答完、或者它刚请你批准；按住不到 0.5 秒算误触，最长 15 秒 |

左侧的键不接芯片的任何引脚，本项目的固件用不到它。

## 恢复原厂固件

把第 1 步的备份从地址 0 整个写回。新开的终端要先运行第 1 步那条 `export …` 命令，才能用 `esptool`（乐鑫的烧录工具）。先校验备份没坏：

```bash
shasum -a 256 -c firmware-backup/factory-full8MB.bin.sha256
```

```bash
python -m esptool --chip esp32c3 -p <端口> write_flash 0x0 firmware-backup/factory-full8MB.bin
```

写完卡片自动重启，跑回原厂固件。写回会把 FunMedal 存在卡片上的蓝牙配对一起覆盖掉。

想再换回 FunMedal：照第 2 步烧录，然后先在 Mac 的“系统设置 → 蓝牙”里忽略 `FunMedal`，再退出桥接程序、重新启动它（命令见第 3、4 步），照第 4 步重新配对。不先忽略的话，Mac 还拿旧的配对去连，卡片已经不认，桥接程序连不上，它的记录里每次都是 `Peer removed pairing information`（macOS 的意思是“对方删掉了配对信息”）。

2026-10-17 在一张卡片上完整走过一遍：8 MB 写入用了约 16 秒，`esptool` 核对写入的内容无误；原厂固件的开机日志里有 `Project name: trae_card` 和 `App version: 1.0.3`，一直启动到 `99-ready`；再照第 2 步烧回 FunMedal、在 Mac 上忽略卡片、照第 4 步配对，第一次就配上。那一分钟里没有人看屏幕，原厂固件能开机的依据只有串口日志。

## 测试

测试在 [tests/](tests/)，用 pytest 运行。大部分测试要接着卡片、开着蓝牙；有几个要启动真实的 Claude Code 会话，或者用 Mac 的扬声器朗读。启动真实会话的测试用插件里的 hooks 和 `~/Applications` 里的桥接程序，所以每次重新编译后先照第 3 步再运行一次 `install.sh`；它们用 `claude --plugin-dir` 加载仓库里的插件，不读你的 `~/.claude/settings.json`。

```bash
python3 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt
```

```bash
FM_ARTIFACTS=artifacts/run-$(date +%Y%m%d-%H%M%S) .venv/bin/python -m pytest tests -v
```

有四个测试要你本人在卡片旁边操作，平时自动跳过。设了环境变量才运行，值是每一步最多等多少秒：

- `FM_KEY_PRESS_SECONDS=60`：依次长按右侧上、中、下三个键，检查卡片读到的按下、长按和松开。
- `FM_BLUETOOTH_TOGGLE_SECONDS=300`：等 `FM_ARTIFACTS` 目录下的 `bridge/test_mac_bluetooth_off_and_on/ready-off.txt` 出现（单独跑这个测试时，启动后约 5 秒；跑全部测试时要等前面的测试跑完），在控制中心把 Mac 的蓝牙关掉约 10 秒再打开，检查桥接程序自己重连、补上关着时变的状态。
- `FM_PERSON_AT_CARD_SECONDS=90`：在真实会话里长按确认键批准一次、按下键拒绝一次；再按住中间的键说“把测试跑一遍”3 次，检查认出的文字。测试记录会存下认出的文字，所以只说这句测试话。

每次运行的串口记录、截图和桥接程序记录存在 `FM_ARTIFACTS` 指定的目录里。默认目录 `artifacts/` 不进 git：串口记录里有卡片的蓝牙地址，结果里有本机路径。

发布或推送之前跑一遍 `tests/test_release.py`，它检查会被公开的内容里有没有不该公开的东西：

- 查哪里：git 历史（包括每个提交的作者和说明）、标签（tag）的作者和说明、暂存区（`git add` 过还没提交的内容）和待提交的文件。每个文件看内容、文件名，符号链接（指向别处的快捷方式文件）还看它指向的路径。
- 找什么：个人邮箱、本机用户名、卡片的蓝牙地址和编号、芯片唯一编号、密钥，以及原厂固件备份本身或从备份里按 4 KB 对齐截出来的整块。
- 查不出的：比 4 KB 小、没对齐或者换了写法（比如十六进制文本）的备份片段。
- 前提：要有第 1 步做的备份，找不到备份时检查直接失败。

## 已知的限制

- 桥接程序每次重新编译后，macOS 都要你重新允许蓝牙和语音识别。
- 卡片固件和桥接程序要一起更新：新的桥接程序找不到旧固件里没有的语音特征值时，会断开重连，状态显示和批准也用不了；记录里是 `characteristic_missing`。
- 在电脑上按过停止（终端里按 Esc，桌面 App 里点停止）之后，Claude Code 2.1.299 起不收插件送来的话，卡片显示“没送进去 / 你按过停止”，要先在电脑上打一句话。停止时还排着的话会被清掉，卡片显示“没送进去 / 刚才那句被停止清掉了”。
- 按住说话时，Mac 把结果写给卡片如果连续两次蓝牙出错（桥接程序只重写一次），文字其实已经送进会话，卡片却会显示“没送进去 / Mac 没有回音”。看到这句时先看终端里会话有没有收到，再决定要不要重说，见 [notes/voice.md](notes/voice.md) 的“还没做的”。
- 卡片还不会睡眠省电，也不读电池电量；原厂的电子名片功能不保留，要用就写回原厂固件。
- 附近有两张卡片时，桥接程序会连第一张搜到的，这种情况没有实测，见 [notes/bridge.md](notes/bridge.md) 的“还没做的”。

## 许可

本项目的代码用 MIT 许可，见 [LICENSE](LICENSE)。

卡片固件里的中英文点阵字体（`firmware/main/font_large.c`、`firmware/main/font_text.c`）由 Noto Sans SC 生成，Noto Sans SC 用 SIL Open Font License 1.1 许可，全文和来源见 [third_party/noto-sans-sc/](third_party/noto-sans-sc/)。
