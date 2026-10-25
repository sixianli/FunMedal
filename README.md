# FunMedal

FunMedal 把一张 ESP32-C3 电子卡片（原厂固件叫 `trae_card`，带 240×320 彩色屏幕、4 个按键和电池）变成 Claude Code（Anthropic 的 AI 编程助手）的桌面伴侣。Claude Code 照常在 Mac 上运行，卡片通过加密的蓝牙和 Mac 上的桥接程序（本项目里一个常驻后台的小程序）相连。

它只做电脑端和手机端都做不好的事，见 [MISSION.md](MISSION.md)：

- **看一眼就知道状态**：卡片上一行一个 Claude Code 会话，写着项目名和状态：在跑、等你、出错、答完了。Claude Code 要你批准一个操作时，那一行显示“等你”，你在电脑上答完它才变。

2026-10-24 起，在卡片上批准和按住说话这两个功能已经拿掉，卡片只显示状态，见产品需求文档 [docs/prd-v0.1.md](docs/prd-v0.1.md)；这两个功能以后要不要再做，记在 [docs/ideas/](docs/ideas/) 里。

## 需要什么

| 东西 | 说明 |
| --- | --- |
| 卡片 | 和本项目同款的 ESP32-C3 卡片，硬件细节和引脚见 [notes/hardware.md](notes/hardware.md)。别的板子引脚不同，不能直接用 |
| Mac | 本项目在 Apple 芯片的 Mac、macOS 27.2 上开发和测试。桥接程序用到 macOS 自带的蓝牙 |
| Xcode 命令行工具 | 提供 `swiftc`（Swift 编译器），用来编译桥接程序 |
| Apple Development 证书 | 苹果发给开发者的签名证书，用免费的 Apple ID 就能拿到，不用付费的开发者会员。编译脚本用它给桥接程序签名，这样重新编译后 macOS 不用再问授权；没有也能用，只是每次重新编译后都要再点一次允许。拿法见下面第 3 步 |
| esptool | 乐鑫的刷写工具，一个小 Python 程序，用来备份卡片和刷固件，装法见下面第 1 步 |
| ESP-IDF v5.5.5 | 乐鑫官方的 ESP32 开发环境（几 GB），只有想自己编译卡片固件时才需要，装法见[乐鑫的安装文档](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32c3/get-started/index.html) |
| Claude Code | 要 2.1.292 或更新的版本，终端版或 Claude 桌面 App 自带的都行，`install.sh` 会检查；本项目用终端版 2.1.292 和桌面 App 自带的 2.1.299 测试。显示“等你批准”要用到 `PermissionRequest` hook（Claude Code 在要你批准时运行的外部命令） |
| Python 3 | 跑测试时需要；第 1 步把 `esptool` 装进仓库的 `.venv` 时也要 |

## 安装

下面的命令都在仓库根目录运行。`<端口>` 是卡片用 USB 线连上 Mac 后出现的串口，比如 `/dev/cu.usbmodem11301`，用 `ls /dev/cu.usbmodem*` 能看到。

### 1. 备份原厂固件，刷上 FunMedal 固件

先装 `esptool`，下面两种装法选一种。用 Homebrew（Mac 上常用的软件安装工具）：

```bash
brew install esptool
```

或者装进仓库目录下的 Python 虚拟环境 `.venv`：

```bash
python3 -m venv .venv && .venv/bin/pip install esptool
```

然后运行：

```bash
sh flash.sh <端口>
```

`flash.sh` 依次做三件事，任何一步出错就停下、不写卡片：

1. **备份**：刷固件会覆盖卡片上的原厂固件，所以 `firmware-backup/` 里还没有备份时，先把整块 8 MB Flash（存程序和数据的存储芯片）读两遍，两遍完全一样才存成 `firmware-backup/factory-full8MB.bin`，旁边写一个记着 SHA-256（文件的指纹，内容变一个字节它就变）的 `.sha256` 文件。以后想用回原厂功能时写回去，见下文“恢复原厂固件”。备份里有这张卡片独有的身份和配对数据，**不要上传到任何地方**；仓库的 `.gitignore` 已经排除了 `firmware-backup/`。
2. **核对**：已经有备份时不再读卡片，只核对 `firmware-backup/` 里每个 `.bin` 都是整块 8 MB、和它的 `.sha256` 对得上。`firmware/prebuilt/` 里编译好的固件文件也先按 `SHA256SUMS` 核对一遍。
3. **刷写**：把 `firmware/prebuilt/` 里编译好的三个文件（启动程序、分区表、FunMedal 程序）写到各自的位置，`esptool` 写完自己核对一遍。存蓝牙配对的区域不动，所以以后更新固件不用重新配对。

原厂固件闲置 5 分钟会进入深度睡眠，USB 串口随之消失。连不上时按一下右侧任意一个键把它唤醒，再运行命令。

刷完卡片会重启，屏幕上出现任务板；还没连上 Mac 时写着“未连接”。编译好的文件来自哪个提交，写在 `firmware/prebuilt/SOURCE` 里。固件的结构和串口命令见 [notes/firmware.md](notes/firmware.md)。

### 2. 想自己编译固件时

不想用编译好的文件，可以装 ESP-IDF 自己编译。先照第 1 步运行一次 `sh flash.sh <端口>`，让它做好备份。下面第一条命令每开一个新终端都要先运行一次，它让当前终端能用 ESP-IDF。

```bash
export IDF_TOOLS_PATH=$HOME/esp/.espressif && . $HOME/esp/esp-idf-v5.5.5/export.sh
```

卡片上的字是编译时从三套开源字体生成的，字体文件不在仓库里。第一次编译前先下载它们（要能上网；脚本会核对每个文件的 SHA-256，已经下好的不再下载）：

```bash
sh tools/fetch-fonts.sh
```

生成字体要用 Mac 上的 `swiftc`（Swift 编译器），它随 Xcode 命令行工具一起装；没有的话先运行 `xcode-select --install`。然后编译并刷写：

```bash
cd firmware && idf.py -p <端口> build flash
```

改了固件、提交之后，用 `sh tools/update-prebuilt.sh` 重新生成 `firmware/prebuilt/`（同样要先运行上面的 `export …`），再提交它。`firmware/`、默认素材包 `assets/default/` 或生成素材的脚本有没提交的改动时，这个脚本不动手。忘了更新的话，`tests/test_flash.py` 会报编译好的文件和源码对不上。

### 3. 编译桥接程序，装上 Claude Code 插件

先看钥匙串里有没有 Apple Development 证书：

```bash
security find-identity -v -p codesigning
```

列出的行里有 `"Apple Development: …"` 就是有。没有的话，从 App Store 装上免费的 Xcode，打开它，在菜单“Xcode → Settings”的“Accounts”里点左下角的“+”加上你的 Apple ID；选中这个账号，点“Manage Certificates…”，再点“+”选“Apple Development”。

然后编译：

```bash
sh mac/bridge/build.sh
```

`build.sh` 用钥匙串里第一张 Apple Development 证书签名；一张都没有时，它说明一句，改用临时签名（只在本机有效、不用证书的签名）。

```bash
sh install.sh
```

`install.sh` 依次做三件事：

- 找 Claude Code：终端里的 `claude` 命令，和 Claude 桌面 App 自带的那份（在 `~/Library/Application Support/Claude/claude-code/` 里，桌面 App 会自己下载和升级它），用两者中版本较新的那份。低于 2.1.292 时它会停下，告诉你怎么升级：终端版运行 `claude update`；桌面 App 在菜单 Claude → Check for Updates 里升级，再开一个新的 Code 会话。另一份低于 2.1.292 时照样安装，最后打一行“注意”，说明用那一份开的会话里 FunMedal 不能正常工作。
- 把编译好的桥接程序复制到 `~/Applications/FunMedalBridge.app`。每次重新编译后都要再运行一次 `install.sh`，把新版本复制过去。
- 用找到的那份 Claude Code 把这个仓库加为插件市场（可以从中安装插件的地方），从中装上 `funmedal` 插件。终端版和桌面 App 用同一个 `~/.claude/settings.json`，所以用哪一份装，两边开的会话都会加载这个插件。插件里只有 hooks（Claude Code 在各种事件发生时运行的外部命令），它们运行 `~/Applications` 里的桥接程序，不用你改 `~/.claude/settings.json`。hooks 把每个会话的状态交给桥接程序。

**只用 Claude 桌面 App、没装终端版也行。** 上面这些命令仍然要在终端里运行：可以用桌面 App 会话里自带的终端（在会话里按 Ctrl+\` 打开），也可以用 Mac 的“终端”。`install.sh` 一份 Claude Code 都没找到时，先打开桌面 App，在 Code 页开一个会话，让它把自带的 Claude Code 准备好，再运行一次。装好后新开一个桌面会话；用下文“出问题时”的 `doctor` 看“插件”一行是不是“好”。桌面会话里运行 `/status` 能看到它用的 Claude Code 版本。

插件直接从仓库目录加载，装好后不要挪动仓库；要挪，先照“卸载”一节卸掉插件、重开会话。已经开着的 Claude Code 会话要重开，才会用上插件。

hook 只把事件名、会话编号、工具名、它属于哪个 `claude` 进程、项目目录的名字（只有最后一级，比如 `FunMedal`，不是完整路径）等几项交给桥接程序，不传命令内容、文件内容和你的指令。详见 [notes/bridge.md](notes/bridge.md)。

桥接程序不用你自己启动：Claude Code 的会话开始时，插件的 hook 发现它没在运行，就用 `open` 在后台启动 `~/Applications` 里的它，这一次会话开始要多等约 1.3 秒。它不跟着会话结束，关掉会话后照常运行，直到你让它退出或者重启 Mac。想先启动它也可以：

```bash
open -g ~/Applications/FunMedalBridge.app --args serve
```

桥接程序必须用 `open` 启动，否则 macOS 会因为它用蓝牙而把它结束。它第一次启动时，macOS 会问是否允许它使用蓝牙，选允许。macOS 认程序时看的是签名里的“认定规则”：用证书签名时，规则是程序编号加证书上的名字，和程序内容无关，所以之后重新编译、重新安装都不会再问；换一张同名的证书也不会。用临时签名时，规则是程序内容的哈希，**每次重新编译后 macOS 都会再问一次**。从临时签名换成证书签名后，第一次启动也会再问一次。

### 4. 蓝牙配对

桥接程序第一次连上卡片时，Mac 弹出蓝牙配对对话框，上面有六位数字；卡片同时显示蓝色的“蓝牙配对”画面和同一串数字。两边数字相同时，先在 Mac 上点“连接”，再按卡片右侧最上面的键。不同就按右侧最下面的键取消。

配对失败或取消后（在 Mac 上或卡片上点了取消），桥接程序自己断开重连，大约 1 秒后 Mac 再弹一个配对框，卡片也换上新的数字。连续失败 3 次后，桥接程序停下、不再弹框，`status` 里 `card` 是 `pairing_failed`。这时退出桥接程序，再照第 3 步启动它，就会重新弹出配对对话框。退出用这条命令：

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
| 看屏幕 | 一行一个会话，写着项目名和状态；某一行显示“等你”时到电脑上去答 |
| 配对画面上按右侧最上面的键 | 确认配对，见“安装”的第 4 步 |
| 配对画面上按右侧最下面的键 | 取消配对 |

其他时候按键不做任何事。左侧的键不接芯片的任何引脚，本项目的固件用不到它。

## 出问题时

卡片不显示状态时，先运行：

```bash
~/Applications/FunMedalBridge.app/Contents/MacOS/funmedal-bridge doctor
```

它依次查 9 项：桥接程序在不在跑、蓝牙是否允许、桥接程序的签名、卡片连没连上、卡片固件和桥接程序的版本配不配、插件装没装、hooks 有没有被 `disableAllHooks` 关掉、终端版和桌面 App 自带的 Claude Code 版本各够不够。只用其中一种时，另一种那行写“没找到”，仍是“好”；一种太旧、另一种够新时，太旧的那行是“注意”。每项一行，开头是“好”“注意”“不好”或“不知道”，不是“好”的行后面写改法。有一项“不好”时退出码是 1。蓝牙、卡片和版本这 3 项要问正在跑的桥接程序，它没在跑时显示“不知道”，先照第一行把它启动起来再查。

## 卸载

```bash
sh uninstall.sh
```

`uninstall.sh` 和 `install.sh` 用同样的办法找 Claude Code，所以只装了桌面 App 也能卸。它卸掉 `funmedal` 插件，再删掉这个仓库对应的插件市场。没装的部分它会跳过，所以重复运行也没关系；读不出插件列表或者卸载失败时，它会停下并报错。

卸完它会列出三步，要你自己按顺序做：

1. 重开已经开着的 Claude Code 会话，或者在会话里运行 `/reload-plugins`（让会话重新读一遍插件）。卸载只改设置和磁盘上的文件，开着的会话还带着插件，会照旧把状态发给桥接程序，会话开始或压缩上下文时还会把桥接程序重新启动起来，所以这一步要在退出桥接程序之前做。
2. 退出桥接程序（命令见第 4 步），之后可以删掉 `~/Applications/FunMedalBridge.app`。
3. 在 Mac 的“系统设置 → 蓝牙”里忽略 `FunMedal`。以后再装时，要照第 4 步重新配对。

## 恢复原厂固件

把第 1 步的备份从地址 0 整个写回，用的是第 1 步装的 `esptool`；装在 `.venv` 里的，把下面的 `esptool` 换成 `.venv/bin/esptool`。先校验备份没坏：

```bash
shasum -a 256 -c firmware-backup/factory-full8MB.bin.sha256
```

```bash
esptool --chip esp32c3 -p <端口> write_flash 0x0 firmware-backup/factory-full8MB.bin
```

写完卡片自动重启，跑回原厂固件。写回会把 FunMedal 存在卡片上的蓝牙配对一起覆盖掉。

想再换回 FunMedal：运行 `sh flash.sh <端口>`，然后先在 Mac 的“系统设置 → 蓝牙”里忽略 `FunMedal`，再退出桥接程序、重新启动它（命令见第 3、4 步），照第 4 步重新配对。不先忽略的话，Mac 还拿旧的配对去连，卡片已经不认，桥接程序连不上，它的记录里每次都是 `Peer removed pairing information`（macOS 的意思是“对方删掉了配对信息”）。

2026-10-17 在一张卡片上完整走过一遍：8 MB 写入用了约 16 秒，`esptool` 核对写入的内容无误；原厂固件的开机日志里有 `Project name: trae_card` 和 `App version: 1.0.3`，一直启动到 `99-ready`；再照第 2 步烧回 FunMedal、在 Mac 上忽略卡片、照第 4 步配对，第一次就配上。那一分钟里没有人看屏幕，原厂固件能开机的依据只有串口日志。

## 测试

测试在 [tests/](tests/)，用 pytest 运行。大部分测试要接着卡片、开着蓝牙；有几个要启动真实的 Claude Code 会话。启动真实会话的测试用插件里的 hooks 和 `~/Applications` 里的桥接程序，所以每次重新编译后先照第 3 步再运行一次 `install.sh`；它们用 `claude --plugin-dir` 加载仓库里的插件，不读你的 `~/.claude/settings.json`。

```bash
python3 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt
```

```bash
FM_ARTIFACTS=artifacts/run-$(date +%Y%m%d-%H%M%S) .venv/bin/python -m pytest tests -v
```

有两个测试要你本人在卡片旁边操作，平时自动跳过。设了环境变量才运行，值是每一步最多等多少秒：

- `FM_KEY_PRESS_SECONDS=60`：依次长按右侧上、中、下三个键，检查卡片读到的按下、长按和松开。
- `FM_BLUETOOTH_TOGGLE_SECONDS=300`：等 `FM_ARTIFACTS` 目录下的 `bridge/test_mac_bluetooth_off_and_on/ready-off.txt` 出现（单独跑这个测试时，启动后约 5 秒；跑全部测试时要等前面的测试跑完），在控制中心把 Mac 的蓝牙关掉约 10 秒再打开，检查桥接程序自己重连、补上关着时变的状态。

pytest 从开始到结束，一直占着默认套接字的锁（`~/Library/Application Support/FunMedal/bridge.sock.lock`）。这时别的会话开始或压缩上下文，插件也启动不了常驻桥接程序，不会把卡片连走。所以跑测试前要先退出常驻桥接程序，测试结束后它要等下一个会话开始才会自动启动。

每次运行的串口记录、截图和桥接程序记录存在 `FM_ARTIFACTS` 指定的目录里。默认目录 `artifacts/` 不进 git：串口记录里有卡片的蓝牙地址，结果里有本机路径。

发布或推送之前跑一遍 `tests/test_release.py`，它检查会被公开的内容里有没有不该公开的东西：

- 查哪里：git 历史（包括每个提交的作者和说明）、标签（tag）的作者和说明、暂存区（`git add` 过还没提交的内容）和待提交的文件。每个文件看内容、文件名，符号链接（指向别处的快捷方式文件）还看它指向的路径。
- 找什么：个人邮箱、本机用户名、卡片的蓝牙地址和编号、芯片唯一编号、密钥，以及原厂固件备份本身或从备份里按 4 KB 对齐截出来的整块。
- 查不出的：比 4 KB 小、没对齐或者换了写法（比如十六进制文本）的备份片段。
- 不查的：`third_party/` 下照搬上游的授权全文（`LICENSE*` 和 `LICENSES/` 里的文件）不查邮箱，里面是字体作者自己公开的联系方式；其他项照样查。
- 前提：要有第 1 步做的备份，找不到备份时检查直接失败。

## 已知的限制

- 用临时签名时，桥接程序每次重新编译后，macOS 都要你重新允许蓝牙。
- 卡片固件和桥接程序要一起更新。2026-10-24 起连接协议版本是 3（桥接程序改成一次把整张会话表写给卡片），只更新一边就会版本不配。两边的连接协议版本不一样时，桥接程序断开、不再重连，`status` 里 `card` 是 `mismatch`；卡片会显示红底的“版本不配”，并写明该更新哪一边。更新后退出再启动桥接程序就好，见 [notes/bridge.md](notes/bridge.md) 的“先核对版本”。OB-13 之前的固件没有版本号，会被当成版本不配，但卡片上不会出现这个画面。
- 卡片还不会睡眠省电，也不读电池电量；原厂的电子名片功能不保留，要用就写回原厂固件。
- 附近有两张卡片时，桥接程序会连第一张搜到的，这种情况没有实测，见 [notes/bridge.md](notes/bridge.md) 的“还没做的”。

## 许可

本项目的代码用 MIT 许可，见 [LICENSE](LICENSE)。

卡片固件里的点阵字体在编译时从三套字体生成，三套都用 SIL Open Font License 1.1（一种允许把字形嵌进程序再分发的开源字体许可）：正文用 Noto Sans SC，见 [third_party/noto-sans-sc/](third_party/noto-sans-sc/)；像素字用 Fusion Pixel，它还收了 Ark Pixel、Cubic 11、Galmuri 的字形，见 [third_party/fusion-pixel/](third_party/fusion-pixel/)；大字用站酷快乐体，见 [third_party/zcool-kuaile/](third_party/zcool-kuaile/)。各目录里有来源、SHA-256 和许可全文。
