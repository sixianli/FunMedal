# 卡片固件

本文说明 [firmware/](../firmware/) 里的自制卡片固件（烧在卡片芯片里的程序）第一版：它做什么、怎么编译烧录、怎么测试，以及做的过程中查到的问题。硬件事实（引脚、按键电压）在 [hardware.md](hardware.md)。

固件用 ESP-IDF v5.5.5（乐鑫官方的开发环境）编写。烧上它会覆盖卡片上的原厂固件；原厂固件可以用 `firmware-backup/` 里的完整备份恢复，见“怎么编译和烧录”。

## 对照使命

按 [MISSION.md](../MISSION.md) 的“判断一个需求该不该做”逐条回答：

| 问题 | 回答 |
| --- | --- |
| 1. 补足的是不是电脑端和手机端都做不好的事 | 是。电脑端要走回电脑前看终端；手机端的 Remote Control（用手机远程操作 Mac 上的 Claude Code）要掏手机、解锁、打开 App，才能知道 Claude Code 是不是在等你批准。这一版固件让卡片能显示 5 种状态，是“看一眼就知道状态”的显示部分；还能认出 3 个功能键，是之后“按一个键就处理掉”的输入部分。这一版还没有和 Claude Code 连上（FM-13 做），单独用它还看不到真实状态 |
| 2. 是不是看一眼就懂 | 是。每种状态有自己的背景色、小人动作和一个两到四个字的标签：空闲、干活中、等你批准、完成了、出错了。等你批准时背景一明一暗地闪 |
| 3. 是不是按一两个键就能完成 | 是。3 个功能键都能分出“按一下”和“按住 0.7 秒以上” |
| 4. 不在电脑前、不想掏手机时是否有用 | 是，卡片自己显示，不用手机。前提是 FM-13 把状态通过蓝牙送到卡片 |

## 现在能做什么

- **显示**：5 种状态，每种两帧画面，每 0.5 秒换一帧，看起来是动的。画面布局是上方一个 32×32 像素的小人（每个像素放大成 6×6 的方块），下方一行中文标签。
- **按键**：每 10 ms 读一次 GPIO0 的电压，按电压认出是哪个键；连续 3 次读到同一个键才算数，用来滤掉抖动（按键接触瞬间电压来回跳）。按下、按住 0.7 秒、松开各报一次。
- **USB 串口命令**：Mac 上的测试程序通过 USB 串口（卡片用 USB 线连 Mac 时出现的文字通道）切换状态、截取画面、读按键电压，见“USB 串口命令”。
- **蓝牙收状态**：卡片是 BLE（低功耗蓝牙）外设，Mac 上的桥接程序连上后写入状态名，卡片就切到那个状态。服务编号、怎么连和桥接程序见 [bridge.md](bridge.md)（FM-13）。

还不能做的：按键触发批准或拒绝（FM-14，现在按键只从串口报出来）；录音（FM-15）；睡眠省电；读电池电量；原厂的电子名片功能。

## 怎么编译和烧录

先在终端里启用 ESP-IDF，再编译烧录（卡片要接着 USB）：

```bash
export IDF_TOOLS_PATH=$HOME/esp/.espressif && . $HOME/esp/esp-idf-v5.5.5/export.sh
```

```bash
cd firmware && idf.py -p /dev/cu.usbmodem11301 build flash
```

恢复原厂固件：备份是整块 8 MB Flash（存程序和数据的存储芯片）的完整拷贝，从地址 0 整个写回即可。先用 `shasum -a 256 -c` 校验备份没坏。下面这条写回命令还没有真正跑过：

```bash
esptool.py --chip esp32c3 -p /dev/cu.usbmodem11301 write_flash 0x0 firmware-backup/trae_card_<id>_fw1.0.3_full8MB_2026-10-16.bin
```

## 编译好的固件文件（OB-15）

使用者不装 ESP-IDF 也能刷固件（负责人 2026-10-17 定，见 [onboarding.md](onboarding.md)）：仓库里放着编译好的固件，只要装 `esptool` 就能刷。

- **放在哪里**：`firmware/prebuilt/`，跟着仓库走（负责人 2026-10-18 选的，每更新一次，仓库历史多约 0.8 MB）。里面有启动程序 `bootloader.bin`、分区表 `partition-table.bin`、程序 `funmedal.bin`，写到哪个地址记在 `flash_args`，SHA-256 记在 `SHA256SUMS`，编译时所在的提交记在 `SOURCE`。
- **怎么生成**：`sh tools/update-prebuilt.sh`，要先运行 ESP-IDF 的 `export.sh`。它只在 `firmware/` 下没有没提交的改动时才动手，用 `idf.py build` 编译，按 ESP-IDF 生成的 `build/flash_args` 复制三个文件，再写 `SHA256SUMS` 和 `SOURCE`。脚本放在 `tools/` 而不是 `firmware/`，因为 `firmware/` 下任何文件改了都算固件变了。
- **怎么刷**：仓库根目录的 `flash.sh <端口>`，依次找 `esptool`、`esptool.py`、`.venv/bin/esptool`。先按 `SHA256SUMS` 核对编译好的文件；`firmware-backup/` 里有 `.bin` 时，核对每个都是整块 8 MB、和同名 `.sha256` 文件里的第一个字段对得上，没有时读两遍整块 Flash，两遍一样才存成 `firmware-backup/factory-full8MB.bin` 并写 `.sha256`；最后用 `write_flash` 把三个文件写到 `flash_args` 里的地址。任何一步失败都停下、退出码不是 0，读备份那一步失败或两遍不一样时不留下备份文件。
- **为什么不合成一个文件**：`esptool merge_bin` 能把三个文件合成一个从地址 0 开始的文件，但中间的空隙会填满 0xFF。分区表里 NVS 区（存蓝牙配对的地方）在 0x9000–0xEFFF，正好在分区表（0x8000）和程序（0x10000）之间，合成的文件会把它擦掉，配对就没了。分开写只碰三个文件各自的位置。
- **怎么保证和源码对得上**：`tests/test_flash.py` 的 `test_prebuilt_is_current` 检查三个文件和 `SHA256SUMS` 一致、`flash_args` 的地址对，并且 `SOURCE` 那个提交之后 `firmware/`（除了 `prebuilt/`）没有改动、也没有新文件。改了固件忘了重新生成，这一项就失败。编译结果不是逐字节可重现的（ESP-IDF 没开 `CONFIG_APP_REPRODUCIBLE_BUILD`），所以测试不重新编译来比对。
- **会不会带出本机信息**：2026-10-18 生成的三个文件里搜不到用户名、`/Users`、仓库目录名和 ESP-IDF 的安装路径：ESP-IDF 默认把源码路径写成 `/IDF/…`。`tests/test_release.py` 发布前还会再查一遍仓库里的所有文件。
- **实测**（2026-10-18）：用 ESP-IDF 自带的 esptool 4.12.0（不经过 `idf.py`）在真卡片上跑 `flash.sh`，已有备份所以只核对没读卡片，三个文件各报一次 `Hash of data verified`，0.8 MB 用了约 3 秒；卡片开机后报 `fw=funmedal-0.1`，配对还在（`sec=encrypted,authenticated,bonded`），桥接程序第一次就连上，版本对得上（`artifacts/ob15-after-20261018-123530/`）。备份、核对、出错停下的各种情况用假的 `esptool` 测（同一个测试文件）。
- **没测的**：Homebrew 的 esptool 5.5.0 和用 `pip` 装的 esptool。esptool 5 把命令和选项改成 `write-flash`、`--flash-mode` 这种带短横线的写法；按乐鑫的[迁移说明](https://docs.espressif.com/projects/esptool/en/latest/esp32c3/migration-guide.html)，`flash.sh` 用的带下划线的旧写法在 v5 里还能用、会打出警告，下一个大版本才删掉。这一点没有在本机用 v5 验证。没有备份时真读两遍 8 MB 的那条路，也只用假的 `esptool` 测过。

## 程序结构

固件里有 4 个并行的任务（FreeRTOS 里同时运行的程序单元，优先级数字越大越先运行）：

| 任务 | 优先级 | 做什么 | 代码 |
| --- | --- | --- | --- |
| 按键 | 6 | 每 10 ms 读一次电压，认出按键，报按键事件；批准画面和配对画面里的按键也在这里处理（见 [approval.md](approval.md)）；串口 `key` 命令注入的按键同样在这个任务里处理。栈 4096 字节（FM-14 从 3072 加大，见 approval.md 的“实测结果”第 5 条） | [key_input.c](../firmware/main/key_input.c)、[keys.c](../firmware/main/keys.c)、[key_levels.c](../firmware/main/key_levels.c) |
| 显示 | 5 | 状态变了就重画；没变时每 0.5 秒换一帧 | [main.c](../firmware/main/main.c)、[scene.c](../firmware/main/scene.c)、[display.c](../firmware/main/display.c) |
| 串口命令 | 4 | 读 USB 串口上的命令并回复 | [console.c](../firmware/main/console.c) |
| 蓝牙（`nimble_host`） | 21 | ESP-IDF 自带的 NimBLE 蓝牙协议栈建的任务，优先级由它定（`configMAX_PRIORITIES - 4`）；收到状态写入、批准请求写入和配对过程的消息时，都在这个任务里处理并报一行 | [ble_link.c](../firmware/main/ble_link.c) |

- **画面怎么送到屏幕**：一帧 240×320 像素，每像素 2 字节（RGB565，一种红 5 位、绿 6 位、蓝 5 位的颜色格式）。程序每次只算 40 行，放进两块轮流使用的内存，用 DMA（不占 CPU、由硬件自己搬数据的传输方式）经 40 MHz 的 SPI 送给屏幕。
- **小人和字是生成的**：[tools/gen_pet.py](../tools/gen_pet.py) 用代码画出 5 种状态各两帧的小人，生成 [pet_art.c](../firmware/main/pet_art.c)。改了图案后要重新生成。2026-10-16 用下面的命令重新生成过一次，和仓库里的逐字节相同：

  ```bash
  .venv/bin/python tools/gen_pet.py firmware/main/pet_art.c
  ```

- **字体**：卡片上的字用 Noto Sans SC（Google 和 Adobe 做的开源中文字体，SIL Open Font License 授权，允许把字形嵌进程序再分发，G22）生成两套点阵：大字 `font_large.c`（30 像素，用于标签、标题和配对数字）和正文 `font_text.c`（16 像素，用于批准内容和按键提示）。每个像素有 16 级透明度。[tools/gen_fonts.py](../tools/gen_fonts.py) 从 [scene.c](../firmware/main/scene.c) 的字符串里找出所有汉字，再调用 [tools/gen_font.swift](../tools/gen_font.swift) 画成点阵；正文字体另外包括全部英文字母、数字、符号和省略号 `…`。改了屏幕上的文字后要重新生成。字体文件本身不进 git：先把 `https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf` 下载到 `third_party/noto-sans-sc/`，脚本会核对它的 SHA-256，对不上就停下：

  ```bash
  .venv/bin/python tools/gen_fonts.py
  ```

  Mac 上的测试 `[FM-14] both fonts hold every character the screens draw` 会检查两套字体里有没有屏幕要画的每一个字。
- **蓝牙设置**写在 [sdkconfig.defaults](../firmware/sdkconfig.defaults)（ESP-IDF 的编译选项）：只允许 LE Secure Connections 配对（`CONFIG_BT_NIMBLE_SM_LEGACY=n`、`CONFIG_BT_NIMBLE_SM_SC_ONLY=1`），绑定信息存进 NVS（`CONFIG_BT_NIMBLE_NVS_PERSIST=y`），每次传输的上限 512 字节（`CONFIG_BT_NIMBLE_ATT_PREFERRED_MTU=512`），蓝牙任务的栈 6144 字节（`CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE=6144`）。为什么要配对加密见 [approval.md](approval.md)。
- **按键电压表**单独放在 [key_levels.c](../firmware/main/key_levels.c)，这样 Mac 上的测试能直接用同一份表。

## USB 串口命令

每条命令一行。卡片对每条命令回一行，开头是 `OK <命令名>` 表示成功，`ERR <命令名>` 表示失败并说明原因；不认识的命令回 `ERR unknown command <命令名>`。Mac 端按命令名对回复，不会把上一条命令迟到的回复当成这一条的。

另外卡片会主动报这些行：开机时 `READY funmedal-0.1`，状态变化时 `STATE <状态>`，按键时 `KEY <press|long|release> <ok|up|down>`，`adc` 命令期间的 `ADC …`，以及蓝牙的 `BLE state <状态>`（收到一次状态写入）、`BLE reject length|name`（写入的内容超过 16 字节，或者和状态名不是一字不差，被拒绝）、`BLE connected`、`BLE disconnected reason=<原因码>`、`BLE connect failed status=<码>`，以及 Mac 写来连接协议版本时的 `LINK peer=<Mac 的版本> own=<卡片的版本> match=<1 或 0>`（版本格式不对时是 `BLE reject version`，见 [bridge.md](bridge.md) 的“先核对版本”）。FM-14 加的批准和配对相关的行（`PROMPT`、`DECISION`、`BLE sec`、`BLE subscribe`、`PAIR`）见 [approval.md](approval.md) 的“蓝牙协议”。开机时还会报 `GATT layout same` 或 `GATT layout changed subscribers=<数>`，给 Mac 发“功能表变了”的通知时报 `GATT changed sent range=<起>-<止>`，Mac 确认收到时报 `GATT changed acked`，新功能表记好时报 `GATT layout saved`（失败时是 `GATT layout unsaved err=<错误码>`），见下文“换固件后 Mac 还用旧的功能表”。

两类输出的发送规则不同：

- **回复**（`OK`、`ERR`、截图）：Mac 刚发了命令，一般在读，所以发送缓冲区满时会等，每次最多等 20 ms，等不到就重试，一行累计等了 2 秒还发不出去才放弃。截图中途放弃时改回 `ERR snap write timed out`。如果连这一行也发不出去，Mac 就收不到这条命令的结尾：前面的行也都没发出去时什么都收不到，发出去了一部分时只收到半张截图。两种情况都记一次 `info` 里的 `replies_dropped`，Mac 端等不到结尾会报超时。
- **主动报的行**：USB 没接电脑时直接丢掉；接着电脑时，等发送权（同一时间只能有一个任务往驱动里写）最多 5 ms、等缓冲区空位最多 5 ms，所以一行最多约 10 ms，还发不出去就整行丢掉，不会只发半行，丢掉的行数记在 `info` 的 `events_dropped` 里。这样没人读串口时，按键任务和显示不会被串口拖住。

| 命令 | 作用 |
| --- | --- |
| `info` | 回报固件版本、当前状态、帧设置、屏幕设置，以及几项运行情况：`shown_frame`（最近一次画到屏幕上的是第几帧）、`draws` / `draw_errors`（开机以来画成功和失败的次数）、`key_gap_ms`（上次 `info` 以来，按键任务两次读电压之间最长隔了多少毫秒，正常是 10）、`events_dropped`（主动报的行丢了多少）、`replies_dropped`（有几条命令的回复最后没发出去）、`adc_errors`（读按键电压出错了几次；出错的那一次不判断按键，也不报 `ADC` 行）、`ble`（`connected` 表示有 Mac 连着，`advertising` 表示在等连接）、`ble_writes`（开机以来收到几次有效的状态写入）、`prompt`（正在显示的批准请求编号，没有时是 `none`）、`sec`（蓝牙连接的加密状态）、`gatt_changed`（`Service Changed` 通知的句柄，固定是 3，见下文“换固件后 Mac 还用旧的功能表”）、`link`（和 Mac 核对连接协议版本的结果：`none` 还没核对，`match` 一样，`update_firmware` Mac 的版本大，`update_bridge` 卡片的版本大；收到一次状态写入就从后两种变回 `none`）、`uptime_s`（开机多少秒）。语音加的两项见 [voice.md](voice.md) |
| `state <idle\|working\|waiting\|done\|error>` | 切换状态 |
| `frame <0\|1\|auto>` | 固定显示某一帧，或恢复自动换帧 |
| `pattern` | 显示测试图（四角四色、白边框、尖朝上的三角形），用来确认屏幕方向和颜色 |
| `fill <rrggbb>` | 整屏填一种颜色 |
| `snap` | 截取当前画面：先回 `SNAP BEGIN 240 320 …`，再回 320 行 `SNAP <行号> <这一行像素的十六进制>`，最后回 `SNAP END max_wait_ms=<数> write_timeouts=<数>` |
| `lcd invert\|bgr <0\|1>`、`lcd mirror <0\|1> <0\|1>`、`lcd gap <x> <y>` | 调屏幕的颜色反转、颜色顺序、镜像、偏移；会按新设置重建屏幕驱动，成功回 `OK lcd <全部屏幕设置>`。重建失败时退回原来的设置；退回也失败时串口上会多一行 ESP-IDF 的错误日志，之后每次画屏都失败，`info` 的 `draw_errors` 会一直增加 |
| `adc <1-120>` | 在给定秒数内每 20 ms 回一行 `ADC <毫伏> <原始读数> <判断>`，结束时回 `ADC END` |
| `backlight <0-100>` | 调背光亮度 |
| `stacks` | 回 `OK stacks keys=<字节> display=<字节> console=<字节> nimble_host=<字节> voice=<字节>`：每个任务开机以来栈空间最少时还剩多少字节，找不到某个任务时是 -1 |
| `key <ok\|up\|down> <press\|long\|hold <毫秒>>` | 模拟一次按键，走和真按键相同的处理，测试用它代替人按键，见 [approval.md](approval.md) |
| `ble unpair` | 清掉卡片上的全部蓝牙绑定，见 [approval.md](approval.md) |
| `ble disconnect` | 卡片主动断开当前的蓝牙连接，回 `OK ble disconnect`；没有连接时回 `ERR ble disconnect rc=<码>`。测试用它让桥接程序重连，不用重启卡片（重启会取消还没用掉的 `ble reject`） |
| `ble decision <编号> allow\|deny` | 不按键，直接发一个回答给 Mac，测试用，见 [approval.md](approval.md) |
| `ble layout forget` | 忘掉记下的功能表指纹，下次开机按“功能表变了”处理，测试用，见下文“换固件后 Mac 还用旧的功能表” |
| `ble reject image\|voice\|version [<1-9>]`、`ble reject none` | `version` 让卡片回绝 Mac 接下来对 VERSION 的读取：每回绝一次回 ATT 错误 15（“加密不够”，和没配对成功时 Mac 收到的一样），并报 `BLE reject version read`，测试用它模拟配对失败，见 [approval.md](approval.md) 实测第 11 条。`image`、`voice` 让卡片回绝 Mac 接下来写来的文字图片块（`image`）或语音结果（`voice`），次数不写时是 1 次：每回绝一次，回 ATT 错误 14（蓝牙写入时对方回的错误码，这个码的意思是“不太可能发生的错误”），并报 `BLE reject image injected` 或 `BLE reject voice injected`，成功时回 `OK ble reject <image\|voice> <次数>`，`none` 成功时回 `OK ble reject none`；种类写错、次数不是 1 到 9 的整数、`none` 后面带了次数时，回 `ERR ble usage: …`，原来记着的回绝不变。同一时间只记一种回绝，后输入的替换先输入的。`none` 取消还没用掉的回绝，卡片重启也会取消。测试用，见 [voice.md](voice.md) 编号 17 |
| `reboot` | 回 `OK reboot`，0.1 秒后重启卡片。重启时 USB 串口不断开，Mac 上打开着的串口会接着收到开机日志和 `READY` |

Mac 端用 [tools/cardlink.py](../tools/cardlink.py) 收发，例如 `.venv/bin/python tools/cardlink.py info`。

`snap` 截的是固件按当前状态重新算出的画面，不是显示任务送给屏幕的那份数据，更不是用摄像头拍屏幕。显示任务有没有真的在画，看 `info` 里的 `draws`、`draw_errors` 和 `shown_frame`。屏幕本身显示得对不对，2026-10-16 由用户看实物确认过一次（见 [hardware.md](hardware.md) 的“结论”）。

## 串口截图丢数据的问题

**现象**：第一次跑测试时，10 张截图里有 1 张中间少了一段，下一行直接接在半行后面。后来专门测：连续截 30 张，3 张少数据；按字节比对，一次少了 4,215 字节。

**原因（从代码确认）**：ESP-IDF v5.5.5 里 `printf` 经过的串口输出函数 `usbjtag_tx_char_via_driver`（`components/esp_driver_usb_serial_jtag/src/usb_serial_jtag_vfs.c`）在发送缓冲区满时最多等 50 ms，还发不出去就把之后的字符悄悄丢掉，直到缓冲区又有空位。这是为了没人读串口时 `printf` 不会卡住。一张截图有约 31 万字节，只要中途有一次超过 50 ms 发不出去，就会丢数据。少掉的 4,215 字节不是 64 的整数倍（64 字节是一个 USB 数据包），说明丢在逐个字符写入的这一层，不是整包丢。

**排除了的猜测**：

- 不是 Mac 读得慢：故意让 Mac 停 0.5 秒、2 秒再读，截图都完整；一次排队 4 张、8 张截图不读，macOS 先替程序存下了约 240 万字节，卡片照样发得出去。（macOS 能存的量有上限，见下面“没人读串口时”，但这两次都没到上限。）
- 不是重画屏幕引起的：不切换状态、不重画时，12 张里也有 2 张少数据。

**修法**：所有协议输出都不再走 `printf`，而是整行一次交给 USB 串口驱动，规则见“USB 串口命令”里的两类输出。截图写不出去超过 2 秒时回 `ERR snap write timed out`，Mac 端会报错，而不是收到少了一段的数据。整行一次写入还有两个好处：不同任务同时输出时不会把一行插到另一行中间；截图从每张约 2.8 秒变成约 0.5 秒。ESP-IDF 自己的诊断日志仍走 `printf`。

**修好以后**：测试里连续 30 张、单独比对 60 张，共 90 张全部完整。每张截图的 `SNAP END` 行会报出这张截图里单次写入等得最久的毫秒数和超时次数，这 90 张里最久 7 ms，超时 0 次。

**还不知道的**：修之前是什么让 USB 发送停了 50 ms 以上。修之后再也没有出现超过 20 ms 的等待，所以很可能和旧的逐字符写法本身有关，但没有查实。

## 没人读串口时

**问题（代码审核发现，已复现）**：上面的修法刚做完时，主动报的行也用“最多等 2 秒”的规则。卡片用电池、或者接着电脑但没有程序在读串口时，发送缓冲区满了就再也空不出来，于是每报一行 `KEY` 或 `STATE` 都要白等 2 秒。按键任务在这 2 秒里不读电压，期间的按键会丢。

**怎么复现的**：macOS 替程序存的串口数据有上限，这次约 290 万字节。测试先让卡片持续报 `ADC` 行，再一次排队 16 张截图、16 秒不读，存满以后卡片就发不出去了（有截图因此超时，证明确实卡住了）。之后看 `info` 的 `key_gap_ms`：改之前是 2020，也就是按键任务停了约 2 秒；改成上面“主动报的行”的规则后是 10，和正常时一样，同时 `events_dropped` 是 692，说明那段时间报的行被整行丢掉了，没有拖住按键任务。

**同一测试里的另一个现象**：排队的 16 张截图只回来了 13 个结尾（12 张完整，1 张 `ERR snap write timed out`），三次运行都是这样。串口记录里只有 13 个 `SNAP BEGIN`，也没有一行是坏的，所以不是 Mac 收的时候丢了数据。为了查清，固件加了 `replies_dropped`：再跑一次，它从 0 变成 3，正好补上差的 3 张。也就是卡片 16 条命令都执行了，只是有 3 张截图连第一行 `SNAP BEGIN` 和之后的 `ERR` 都在 2 秒内发不出去，于是什么也没回。测试现在要求“回来的结尾数 + `replies_dropped` 增加的数 = 16”，每条命令都要有着落。

**没有测到的**：卡片完全不接电脑（用电池）时的表现。这时 `usb_serial_jtag_is_connected()` 会报告“没连接”，主动报的行直接丢掉；要等 FM-13 有了蓝牙，才能在不接 USB 的情况下读回卡片的状态来验证。屏幕驱动重建失败时退回原设置的那段代码，也没法用串口命令触发，只做了代码检查。

## 真实按键测试两次没认出按键

**结论（部分确认）**：第 4 次失败查清了，是等待期间没有人按键。第 1 次失败的原因没查清：那次没有录电压，分不出是“没按”还是“按了没认出”。固件里没有找到会让按键认不出来的问题，但这只说明没找到，不能排除。

**经过**：2026-10-16 代码审核改完后，用户在卡片上按了 5 次键：

| 次序 | 结果目录 | 结果 | 说明 |
| --- | --- | --- | --- |
| 1 | [fm12-keys-20261016-204447](../artifacts/fm12-keys-20261016-204447/) | 失败，3 分钟里一个按键事件也没有 | 用户记得是马上按的。这次没有录电压，测试结束后也没有读 `info` |
| 2 | [fm12-keys-diagnose-20261016-205419](../artifacts/fm12-keys-diagnose-20261016-205419/) | 3 个键全部认出 | 一边录电压一边按，电压和 [hardware.md](hardware.md) 里的一样 |
| 3 | [fm12-keys-20261016-205904](../artifacts/fm12-keys-20261016-205904/) | 通过 | 和第 1 次是同一个固件、同一次开机，在第 1 次之后约 15 分钟 |
| 4 | [fm12-keys-20261016-210635](../artifacts/fm12-keys-20261016-210635/) | 失败，10 分钟里一个按键事件也没有 | 这次固件已经加了 `adc_errors`，测试也一直在录电压：29997 次电压全是“没按”，读电压出错 0 次。用户事后说看到请求时测试已经结束 |
| 5 | [fm12-keys-after-suite-20261016-213608](../artifacts/fm12-keys-after-suite-20261016-213608/) | 通过 | 第 1、4 次之前都刚跑过完整测试，所以这次也先跑完整测试，再请用户看到请求立即按，排除“完整测试让按键失效”的猜测 |

第 1–3 次是同一个固件、同一次开机：开测时的 `info` 里都没有 `adc_errors` 这一项，画屏次数从 2605 涨到 4206。第 4、5 次是加了 `adc_errors` 的新固件，第 4 次开测时画屏次数是 336，刚烧完。

**能确定的**：

- 第 4 次等待的 10 分钟里，引脚电压一次也没变过，读电压也没出错，所以那段时间里没有按键。按键任务一直在按时读电压（测后 `key_gap_ms` 是 10）。
- 第 4 次期间 `events_dropped` 从 1417 变成 1418，多丢了 1 行主动报的行，原因没查。Mac 那时一直在读串口。`KEY` 行走的也是这条发送路径，所以以后可能偶尔少一个按键事件；真实按键测试把测前测后的 `info` 都存进 `key-adc.json`，失败时能从这里看出等待期间有没有丢过主动报的行，但分不出丢的是 `KEY` 行还是 `ADC` 行。
- 第 1 次和第 3 次开测时 `events_dropped` 都是 3631，所以第 1 次期间和之后到第 3 次之前，卡片没有丢主动报的行。第 1 次期间的 `key_gap_ms` 没有记录。
- 串口关着时卡片报的行会怎样，2026-10-16 实测过（[fm12-port-closed-20261016-214823](../artifacts/fm12-port-closed-20261016-214823/)，重跑：`.venv/bin/python tools/port_closed_probe.py --closed-seconds 10 --out artifacts/fm12-port-closed-<时间>`）：卡片持续报电压时把串口关 10 秒，这 10 秒应报约 500 行，其中 284 行被丢掉并记进 `events_dropped`，剩下的约 216 行（约 4 KB，和卡片的发送缓冲区一样大）积在卡片里，重新打开串口后马上送到。但送到的第一行只有后半截：pyserial 3.5 打开串口时会清掉 Mac 已经收到、还没读的数据（`serialposix.py` 的 `open()` 调用 `_reset_input_buffer()`），积着的行里最早的一段就这样丢了：按 3 秒内收到的行数推算，约 50 行、1 KB。

**没能确定的**：

- 第 1 次是没按还是按了没认出。
- 如果有人在测试结束后才按键，那些 `KEY` 行去了哪里。推测：按得早的，在下一次打开串口时被 pyserial 清掉；按得晚的，被我之后用 `tools/cardlink.py` 读状态时跳过了，因为这个命令会悄悄跳过不是回复的行。两次失败后，我都在测试之外手动用它读过卡片状态，但没有留下读到的行，没法核对。

**做了的改动**：固件加了 `adc_errors` 计数；真实按键测试在等待期间一直录电压，并要求 `adc_errors` 不变。第 4 次失败已撤回（证据记录 `E20261016T214001-9c8342`，理由是用户确认按键不在等待时间里）；第 1 次的失败记录在旧版本上，保留。

**以后跑真实按键测试**：先确认用户就在卡片旁边、现在就能按，再启动测试。

## 换固件后 Mac 还用旧的功能表

GATT 是蓝牙设备对外公布的功能表：卡片有哪些可以读写的项，比如状态、批准请求、录音。2026-10-17 早上，卡片换上带语音的固件后，桥接程序 3 分钟里连了 358 次，每次都只找到旧固件的 3 项（状态、批准请求、回答），找不到语音的 3 项，于是断开重连。记录在 `artifacts/fm13-gatt-cache-before-20261017-101731/bridge.jsonl`。

**原因**：macOS 把配对过的蓝牙设备的功能表记在本机，下次连接直接用记下的，不再问设备。设备的功能表变了，要由设备发一个 `Service Changed` 通知（蓝牙标准里“我的功能表变了，请重新读”的通知），Mac 收到后才会重新读。卡片用的 NimBLE 蓝牙库带这个通知，但旧固件从没发过。

**改法**：卡片开机时把自己的功能表算成一个指纹，和上次存在 NVS（芯片上存设置的一小块存储）里的比较。指纹包括每一项的编号（UUID，蓝牙用来认项的标识）、读写方式和句柄（handle，Mac 读写某一项时用的位置编号），也包括 NimBLE 自带的 GAP 服务（设备名一类的基本信息）和 GATT 服务（放 `Service Changed` 的那一组）的编号和起始句柄，但不包括这两组里面各项的读写方式：比如只改设备名能不能写，指纹不变，不会发通知。

- 一样：报 `GATT layout same`，什么也不发。
- 不一样或者没存过：调用 NimBLE 的 `ble_svc_gatt_changed` 发通知，报 `GATT layout changed subscribers=<数>`。`subscribers` 是订阅了这个通知、还等着收的已配对设备数，本机的 Mac 是 1。

新指纹不是马上存，而是等每个订阅的设备都确认收到才存，存好时报 `GATT layout saved`，存失败时报 `GATT layout unsaved err=<错误码>`。没有设备订阅时马上存。如果卡片还和一台再也不连的设备配对着、它也订阅过这个通知，新指纹就一直存不下，卡片每次开机都会再发一次；按代码推断，连着的 Mac 只是每次多读一遍功能表，没有实测过。通知真的发出去时报 `GATT changed sent range=<起>-<止>`，范围是 `0001-ffff`，意思是“全部重新读”；Mac 确认时报 `GATT changed acked`。

**为什么要等确认再存**：第一版改法是开机发现变了就马上存新指纹，以为 NimBLE 会替没连着的 Mac 记下“还欠一个通知”。读 ESP-IDF v5.5.5 的代码后发现，这个“还欠”的标记只在内存里：`ble_store_config_persist_cccds`（`ble_store_nvs.c`）只在订阅记录的条数变了时才写 NVS，标记变了不写。所以 Mac 确认之前卡片重启一次，标记就没了，新指纹却已经存下，下次开机报 `same`，Mac 永远收不到通知。用旧固件复现过：忘掉指纹后连着重启两次，第二次就报 `same`，测试等不到 `GATT changed sent`（`artifacts/fm13-unconfirmed-before-20261017-110935/`）。

**为什么 GATT 服务要排在最前**：Mac 记的订阅是按句柄记的。各组服务按注册的先后分句柄，旧固件先注册 GAP 服务，GAP 服务里多一项，后面所有句柄都往后挪，`Service Changed` 自己的句柄也跟着挪。Mac 订阅的还是旧位置，卡片找不到这个订阅，于是 `subscribers=0`，通知发不出去。实验在 `artifacts/fm13-handle-shift-20261017-111512/`，都是用 ESP-IDF 的 `CONFIG_BT_NIMBLE_SVC_GAP_CAR_SUPP=y`（让 GAP 服务多一项“中心设备地址解析”）把句柄挪动。重跑：把 `firmware/` 复制一份、在副本里打开这个配置，再运行 `.venv/bin/python artifacts/fm13-handle-shift-20261017-111512/experiment.py <阶段名> <副本目录>`，结果会写进脚本所在目录下以阶段名命名的子目录。各阶段的结果：

| 阶段 | 固件 | 结果 |
| --- | --- | --- |
| b | 旧固件 | 报 `same`，不发通知；桥接程序 32 次写入全部出错，卡片状态没变 |
| c、c2 | 指纹不含句柄 | 报 `same`；忘掉指纹再重启，报 `changed subscribers=0`，还是发不出去 |
| e | GATT 服务排在最前，指纹含句柄 | 报 `changed subscribers=1`，发出并收到确认；桥接程序 2 次写入都成功，卡片变成空闲 |
| f | 同 e，换回平常的配置 | 同 e |

现在 `ble_link_start` 先注册 GATT 服务、再注册 GAP 服务，`Service Changed` 的句柄固定是 3，不管 GAP 服务或我们自己的服务怎么变。`info` 里的 `gatt_changed` 报这个句柄。这次挪动本身也让 Mac 记的订阅失效，所以 2026-10-17 用户决定固定这个句柄（任务目标记录 `goal.md` 的 G33），并重新配对了一次（`artifacts/fm13-repair-20261017-112321/`）。

**结果**：

- [test_bridge.py](../tests/test_bridge.py) 的 `test_layout_change_announced_once`：先用 `ble layout forget` 忘掉指纹，再在 Mac 确认前连着重启两次，两次都要报 `changed` 且订阅数至少 1；然后启动桥接程序，要求报 `GATT changed sent range=0001-ffff`、`GATT changed acked`、`GATT layout saved`；第三次重启要报 `same`，之后不再发通知，桥接程序连上。记录在 `artifacts/fm13-unconfirmed-after-20261017-112018/`。
- 同一文件的 `test_changed_notice_position_fixed`：要求 `info` 的 `gatt_changed` 是 3。改之前 `info` 里没有这一项（`artifacts/fm13-fixed-position-before-20261017-112244/`），改之后通过（`artifacts/fm13-fixed-position-after-20261017-112501/`）。

**以后改功能表**：只要固件里的功能表变了，指纹就会变，不用另外做什么。如果某次换固件后开机报 `subscribers=0`，说明 Mac 没订阅这个通知或者订阅的位置不对，只能在卡片上运行 `ble unpair`、在 Mac 的“系统设置 → 蓝牙”里忽略卡片，再照 README 第 4 步重新配对。用本项目 2026-10-17 之前的固件配对过的 Mac，换到现在的固件时都要这样重新配对一次。

## 测试

| 测试 | 查什么 | 要不要卡片 |
| --- | --- | --- |
| [test_firmware_host.py](../tests/test_firmware_host.py) 的按键逻辑 | 在 Mac 上编译固件的按键代码：电压怎么归到键、判断范围的边界、抖动、长按只报一次、计时器绕回、电压表里每个键只出现一次且相邻两档不重叠 | 不要 |
| 同一文件的录音回放 | 把 [hardware.md](hardware.md) 里录的真实按键电压交给按键代码，要求正好得到确认、上、下各一次“按下、长按、松开”。录音是每 20 ms 一个点，固件实际每 10 ms 读一次，所以回放时防抖要的 3 个点是 60 ms 而不是 30 ms；电压表又是从这份录音定出来的，回放通过只说明代码和表配得上，换一次按键能不能认对要看下面的真实按键测试 | 不要 |
| 同一文件的蓝牙状态名（FM-13） | 在 Mac 上编译固件的状态名检查：5 个状态名原样写入才接受；空内容、少字、多字、夹着或结尾带空字节、带换行、大小写不同都拒绝 | 不要 |
| [test_card_display.py](../tests/test_card_display.py) 的状态画面 | 5 种状态 × 2 帧：四角是该状态的背景色；小人区域里有足够多的非背景像素；标签是该状态的文字颜色、宽度符合字数、水平居中；同一状态两帧不同；不同状态画面不同 | 要 |
| 同一文件的测试图 | 白边框、四角颜色、三角形位置 | 要 |
| 同一文件的显示任务 | 自动换帧时，重建屏幕驱动之前和之后各看 2 秒：每段里两帧都真的画到了屏幕上，并且各画了至少 3 次；画失败次数不变 | 要 |
| 同一文件的连续截图 | 连续 30 张截图全部完整且完全相同 | 要 |
| 同一文件的串口堵住 | Mac 不读串口、卡片发不出去时，按键任务读电压的间隔仍不超过 100 ms，报不出去的行被丢掉而不是等着；排队的 16 条截图命令每条都有着落：完整、回 `ERR`，或者记进 `replies_dropped`；读按键电压一次也没出错 | 要 |
| 同一文件的错误命令 | 5 种写错的命令都回 `ERR`，之后卡片照常回应 | 要 |
| 同一文件的真实按键 | 用户依次按住上、中、下三个键，卡片报出的事件正好是三个键各一次“按下、长按、松开”，并且读按键电压一次也没出错。等按键期间卡片一直报电压，所有不是“没按”的读数存进 `key-adc.json`，没认出按键时能看出按下时电压变没变 | 要，还要有人按键 |

`tests/` 下还有 FM-13 的 [test_bridge.py](../tests/test_bridge.py)，查蓝牙和 Mac 桥接程序，它查什么见 [bridge.md](bridge.md) 的“测试要覆盖的出错情况”。它要求先运行 `sh mac/bridge/build.sh` 编好桥接程序、蓝牙已允许，还会启动三次真实的终端版 Claude Code 会话，所以终端里的 `claude` 要已登录，每次会消耗少量 Haiku（Anthropic 最便宜的一档模型）用量。

跑法（卡片接着 USB；真实按键那一项默认跳过）：

```bash
.venv/bin/python -m pytest tests/ -v
```

要留存结果时，指定结果目录和报告文件：

```bash
A=artifacts/fm12-suite-$(date +%Y%m%d-%H%M%S); FM_ARTIFACTS=$A .venv/bin/python -m pytest tests/ -v -rs --junitxml=$A/junit.xml
```

真实按键那一项要加 `FM_KEY_PRESS_SECONDS=90`（90 秒内按完），只跑它时再加 `-k physical`。

每次运行的结果目录里有：每个画面的 PNG 图片、`snapshots.jsonl`（每张截图的状态和 SHA-256 校验值，SHA-256 是一种文件“指纹”，内容差一个字节就不同）、`back-to-back.jsonl`（连续截图的结果）、`display-task.json`（显示任务测试前后的 `info`）、`unread-backlog.json`（串口堵住测试里每张截图的结束行和之后的 `info`）、`card-info.txt`（开测时卡片的 `info`）、`serial.log`（串口往来记录；完整的截图行已经省略，只留一行计数，解析不了的行原样保留）、`junit.xml`（测试报告）。2026-10-16 留下的几次：

- [fm12-suite-20261016-202334](../artifacts/fm12-suite-20261016-202334/)：两轮代码审核的问题都改完后的完整一轮（固件 `funmedal.bin` 的 SHA-256 开头 `cd65b5d6`），12 项通过、真实按键跳过。
- [fm12-review2-focused-20261016-202237](../artifacts/fm12-review2-focused-20261016-202237/)：第二轮审核后只跑改过的显示任务和串口堵住两项，都通过。
- [fm12-suite-20261016-201242](../artifacts/fm12-suite-20261016-201242/)：第二轮审核前的完整一轮（固件 SHA-256 开头 `2cbc4f83`），12 项通过、真实按键跳过。
- [fm12-suite-20261016-200520](../artifacts/fm12-suite-20261016-200520/)：加 `replies_dropped` 之前的完整一轮，全部通过；从它的串口记录里发现 16 张截图只回来 13 个结尾。
- [fm12-suite-20261016-193716](../artifacts/fm12-suite-20261016-193716/)：修好截图丢数据后、代码审核前的完整一轮，10 项通过、真实按键跳过；`contact-sheet.png` 把 10 张状态画面拼在一张图里。
- [fm12-backlog-before-fix-20261016-195859](../artifacts/fm12-backlog-before-fix-20261016-195859/)：主动报的行还会等 2 秒时跑串口堵住测试，`key_gap_ms` 是 2020，失败。
- [fm12-backlog-after-fix-20261016-200051](../artifacts/fm12-backlog-after-fix-20261016-200051/)：改成整行丢掉后同一测试，`key_gap_ms` 是 10、`events_dropped` 是 692，通过；同一次也跑了显示任务测试，通过。
- [fm12-unanswered-before-fix-20261016-201048](../artifacts/fm12-unanswered-before-fix-20261016-201048/)：串口堵住测试加上“每条命令都有着落”的检查后，在还没有 `replies_dropped` 的固件上跑，失败；结尾数仍是 13。
- [fm12-unanswered-after-fix-20261016-201143](../artifacts/fm12-unanswered-after-fix-20261016-201143/)：加了 `replies_dropped` 后同一测试，13 个结尾 + 3 条没发出的回复 = 16，通过。
- [fm12-keys-20261016-193813](../artifacts/fm12-keys-20261016-193813/)：用户按真实按键，通过（代码审核前的固件）。
- [fm12-adcerr-after-20261016-210352](../artifacts/fm12-adcerr-after-20261016-210352/)：加了 `adc_errors` 后跑串口堵住测试，通过，`adc_errors` 前后都是 0。
- [fm12-backtoback-before-fix-20261016-193008](../artifacts/fm12-backtoback-before-fix-20261016-193008/)：修之前连续 30 张，3 张少数据，失败的行原样留在 `serial.log` 里。
- [fm12-backtoback-after-fix-20261016-193333](../artifacts/fm12-backtoback-after-fix-20261016-193333/)：同一测试修之后，30 张全部完整。
- [fm12-20261016-192125](../artifacts/fm12-20261016-192125/)：第一次完整运行，第 9 张截图少数据。
- [fm12-slowreader-before-fix-20261016-192338](../artifacts/fm12-slowreader-before-fix-20261016-192338/)：“Mac 读得慢”的猜测，修之前就通过，这个猜测因此被排除，测试随后删掉了。

这些目录里原始的完整串口记录改名为 `serial.full.log`，只留在本机，不进 git。这些目录原来放在 `tests/artifacts/`，2026-10-16 移到了项目根目录的 `artifacts/`：放在 `tests/` 下时，任务进度工具 `longtask.py` 会把报告 `junit.xml` 当成测试文件。所以旧报告里写的路径还是 `tests/artifacts/…`。
