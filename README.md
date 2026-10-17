# FunMedal

FunMedal 把一张 ESP32-C3 电子卡片（原厂固件叫 `trae_card`，带 240×320 彩色屏幕、4 个按键、麦克风和电池）变成 Claude Code（Anthropic 的 AI 编程助手）的桌面伴侣。Claude Code 照常在 Mac 上运行，卡片通过加密的蓝牙和 Mac 上的桥接程序（本项目里一个常驻后台的小程序）相连。

它只做电脑端和手机端都做不好的事，见 [MISSION.md](MISSION.md)：

- **看一眼就知道状态**：卡片上的像素小人显示 Claude Code 正在干活、等你批准、做完了、出错了，还是空闲。
- **在卡片上批准或拒绝**：Claude Code 要你批准一个操作时，卡片显示工具名和命令。长按右侧最上面的键批准，按一下右侧最下面的键拒绝。终端里的批准弹窗照样在，哪边先答算哪边。
- **按住说话**：按住右侧中间的键说一句短指令，松开后 Mac 用离线语音识别转成文字，送进最近需要你的那个 Claude Code 会话（只算按第 7 步带频道启动的会话）。卡片显示送进去的那句话。

## 需要什么

| 东西 | 说明 |
| --- | --- |
| 卡片 | 和本项目同款的 ESP32-C3 卡片，硬件细节和引脚见 [notes/hardware.md](notes/hardware.md)。别的板子引脚不同，不能直接用 |
| Mac | 本项目在 Apple 芯片的 Mac、macOS 27.2 上开发和测试。桥接程序用到蓝牙和离线语音识别，都是 macOS 自带的 |
| Xcode 命令行工具 | 提供 `swiftc`（Swift 编译器），用来编译桥接程序 |
| ESP-IDF v5.5.5 | 乐鑫官方的 ESP32 开发环境，用来编译和烧录卡片固件，装法见[乐鑫的安装文档](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32c3/get-started/index.html) |
| Claude Code | 本项目用终端版 2.1.292 测试。批准功能要用到 `PermissionRequest` hook（Claude Code 在要你批准时运行的外部命令）；按住说话要用到 Channels（“频道”：一个 MCP 服务器往运行中的会话推消息），要求 2.1.80 以上，并且用 claude.ai 账号登录 |
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

### 3. 编译并启动桥接程序

```bash
sh mac/bridge/build.sh
```

```bash
open -g mac/bridge/build/FunMedalBridge.app --args serve
```

桥接程序必须用 `open` 启动，否则 macOS 会因为它用蓝牙而把它结束。第一次启动时 macOS 会问是否允许它使用蓝牙，选允许。**每次重新编译后 macOS 都会再问一次**，因为脚本用的是只在本机有效的临时签名。

### 4. 蓝牙配对

桥接程序第一次连上卡片时，Mac 弹出蓝牙配对对话框，上面有六位数字；卡片同时显示蓝色的“蓝牙配对”画面和同一串数字。两边数字相同时，先在 Mac 上点“连接”，再按卡片右侧最上面的键。不同就按右侧最下面的键取消。

配对失败或取消后，退出桥接程序再启动一次，就会重新弹出配对对话框：

```bash
mac/bridge/build/FunMedalBridge.app/Contents/MacOS/funmedal-bridge quit
```

查看连接状态：输出里 `card` 是 `connected` 就连上了。

```bash
mac/bridge/build/FunMedalBridge.app/Contents/MacOS/funmedal-bridge status
```

### 5. 允许语音识别

运行下面的命令，macOS 会问是否允许 FunMedal Bridge 使用语音识别，选允许。和蓝牙一样，每次重新编译后要再允许一次。`-n` 让 macOS 另开一个桥接程序来做这件事；不加它时，第 3 步启动的桥接程序还在运行，命令会一直等着，什么也不做。

```bash
open -n -W mac/bridge/build/FunMedalBridge.app --args speech-access
```

### 6. 接上 Claude Code 的 hooks

hooks 是 Claude Code 在各种事件发生时运行的外部命令。在 `~/.claude/settings.json` 的 `hooks` 里，给 `SessionStart`、`UserPromptSubmit`、`PreToolUse`、`PermissionRequest`、`PostToolUse`、`PostToolUseFailure`、`Stop`、`StopFailure`、`SessionEnd` 这 9 个事件各加一条同样的命令。`PermissionRequest` 那一条的 `timeout` 写 600（卡片上的批准最多等 590 秒），其他写 5。`<仓库路径>` 换成这个仓库的绝对路径，下面是其中一个事件的写法：

```json
{
  "hooks": {
    "PermissionRequest": [
      {"hooks": [{"type": "command", "timeout": 600,
                  "command": "<仓库路径>/mac/bridge/build/FunMedalBridge.app/Contents/MacOS/funmedal-bridge hook"}]}
    ]
  }
}
```

hook 只把事件名、会话编号、工具名、它属于哪个 `claude` 进程等几项交给桥接程序，不传命令内容、文件内容和你的指令；唯一的例外是要显示在卡片批准画面上的那一小段内容，它不写进任何记录。详见 [notes/bridge.md](notes/bridge.md) 和 [notes/approval.md](notes/approval.md)。

### 7. 接上按住说话的频道

把频道服务器写进一个单独的 MCP 配置文件，比如 `~/.config/funmedal/mcp.json`：

```json
{"mcpServers": {"funmedal": {"command": "<仓库路径>/mac/bridge/build/FunMedalBridge.app/Contents/MacOS/funmedal-bridge",
                             "args": ["channel"]}}}
```

自己写的频道只能用开发参数加载。要用按住说话时，这样启动 Claude Code，并在警告里选“1. I am using this for local development”：

```bash
claude --mcp-config ~/.config/funmedal/mcp.json --dangerously-load-development-channels server:funmedal
```

不要把频道服务器写进 `~/.claude.json` 或项目的 `.mcp.json`：那样不带开发参数的会话也会启动它，桥接程序可能把话送进一个不接收频道消息的会话，原因见 [notes/voice.md](notes/voice.md) 的“怎么配置”。不用按住说话时照常启动 `claude`，状态显示和卡片批准照常能用。

## 怎么用

| 卡片上 | 做什么 |
| --- | --- |
| 看屏幕 | 小人的样子和下面的字告诉你 Claude Code 现在的状态 |
| 等你批准时长按右侧最上面的键 | 批准屏幕上的那个操作 |
| 等你批准时按一下右侧最下面的键 | 拒绝它，Claude 会收到“在卡片上拒绝了” |
| 按住右侧中间的键说话，松开 | 把这句话送进带频道启动的会话里最近需要你的那个：你刚给它发过指令、它刚答完、或者它刚请你批准；按住不到 0.5 秒算误触，最长 15 秒 |

左侧的键不接芯片的任何引脚，本项目的固件用不到它。

## 恢复原厂固件

把第 1 步的备份从地址 0 整个写回。先校验备份没坏：

```bash
shasum -a 256 -c firmware-backup/factory-full8MB.bin.sha256
```

```bash
python -m esptool --chip esp32c3 -p <端口> write_flash 0x0 firmware-backup/factory-full8MB.bin
```

本项目只校验过备份文件完好，还没有真正写回过一次。

## 测试

测试在 [tests/](tests/)，用 pytest 运行。大部分测试要接着卡片、开着蓝牙；有几个要启动真实的 Claude Code 会话，或者用 Mac 的扬声器朗读。

```bash
python3 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt
```

```bash
FM_ARTIFACTS=artifacts/run-$(date +%Y%m%d-%H%M%S) .venv/bin/python -m pytest tests -v
```

每次运行的串口记录、截图和桥接程序记录存在 `FM_ARTIFACTS` 指定的目录里。默认目录 `artifacts/` 不进 git：串口记录里有卡片的蓝牙地址，结果里有本机路径。

发布或推送之前跑一遍 `tests/test_release.py`：它检查 git 历史（包括每个提交的作者和说明）、暂存区（`git add` 过还没提交的内容）和待提交的文件，看文件内容和文件名里有没有个人邮箱、本机用户名、卡片的蓝牙地址和编号、芯片唯一编号、密钥，以及原厂固件备份或从备份里截出来的一段。

## 已知的限制

- 桥接程序每次重新编译后，macOS 都要你重新允许蓝牙和语音识别。
- 卡片固件和桥接程序要一起更新：新的桥接程序找不到旧固件里没有的语音特征值时，会断开重连，状态显示和批准也用不了；记录里是 `characteristic_missing`。
- 按住说话依赖 Claude Code 的开发频道参数，每次启动都要带上。
- 卡片还不会睡眠省电，也不读电池电量；原厂的电子名片功能不保留，要用就写回原厂固件。
- Mac 上的蓝牙关掉再打开、附近有两张卡片这两种情况没有实测，见 [notes/bridge.md](notes/bridge.md) 的“还没做的”。

## 许可

本项目的代码用 MIT 许可，见 [LICENSE](LICENSE)。

卡片固件里的中英文点阵字体（`firmware/main/font_large.c`、`firmware/main/font_text.c`）由 Noto Sans SC 生成，Noto Sans SC 用 SIL Open Font License 1.1 许可，全文和来源见 [third_party/noto-sans-sc/](third_party/noto-sans-sc/)。
