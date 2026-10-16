# 卡片硬件

这张卡片是一块 ESP32-C3 开发板（乐鑫出的一种带 Wi-Fi 和蓝牙的单核芯片），原厂固件（烧在芯片里的程序）叫 `trae_card`，版本 1.0.3。本文记录 2026-10-16 查到的硬件事实和查法。原始输出在 [device-scan/](../device-scan/)。

## 结论

| 部件 | 结果 | 把握 | 依据 |
| --- | --- | --- | --- |
| 主控 | ESP32-C3，QFN32 封装（芯片外形规格），版本 v1.1，单核 160 MHz | 确定 | `esptool flash-id` 读芯片 |
| 存储 | 8 MB Flash（存程序和数据的存储芯片），封装在主控里，厂商 XMC | 确定 | `esptool flash-id` 和 eFuse（芯片里一次性写入的配置位） |
| 屏幕 | 驱动芯片 ST7789，走 SPI（一种串行接口），分辨率很可能是 240×320 竖屏 | 驱动确定；分辨率是推断 | 驱动名在固件字符串里；程序里有 4 处成对使用 240 和 320，图片分区 152 KB 正好放得下一帧 240×320 的 RGB565（每像素 2 字节）画面 |
| 按键 | 一共 4 个。右侧从上到下：确认、上、下，这 3 个是功能键，共用 GPIO0 这一根 ADC 引脚（把电压换算成数字的引脚），靠电压不同区分。左侧 1 个，用户认为是电源键，它不接任何 GPIO | 位置来自用户看实物；功能键接法确定；左键用途是推断 | 用户描述（goal.md G11）；固件字符串 `press UP DOWN OK within 10s`、`按键句柄不全(prev next enter)`；引脚表 |
| 音频芯片 | ES8311，同时有放音和录音通道，采样率 16 kHz 单声道 | 确定 | 开机日志和 `AT+TEST=I2C` 里 TX/RX 两个通道都打开了 |
| 麦克风 | 有 | 确定 | 用户确认（goal.md G8）；`AT+TEST=AUDIO` 回环测试的强度比为 194 倍；引脚表里录音数据线接在 GPIO4 |
| 喇叭 | 有 | 确定 | 开机放音乐、`AT+TEST=AUDIO` 能放测试音 |
| 电池 | 4.2 V / 520 mAh 锂电池，电量计芯片 CW2017；查时电量 82%、电压 4.035 V | 确定 | `AT+TEST=BATT` |
| 无线 | Wi-Fi 2.4G、蓝牙 5 低功耗版（BLE）；原厂只用 BLE | 确定 | `esptool` 和开机日志 |
| 背光 | 接 GPIO21（芯片的 21 号通用引脚） | 确定 | 开机日志 `背光 LEDC 就绪 gpio=21` |
| 其余引脚 | 见下面的“引脚表” | 大部分确定 | 原厂固件运行时用 USB 调试接口读出的寄存器 |

## 引脚表

2026-10-16 在原厂固件运行时，用乐鑫 OpenOCD（开源调试工具，v0.12.0-esp32-20260831）通过芯片自带的 USB 调试接口（JTAG）暂停 CPU，读出 GPIO 映射寄存器（记录每个外设信号接到哪根引脚的硬件设置），再按 ESP-IDF v5.5 的信号编号表翻译。整个过程没有写 Flash。

| 引脚 | 接的是什么 | 把握 | 依据 |
| --- | --- | --- | --- |
| GPIO0 | 3 个功能键的 ADC 输入（ADC1 第 0 通道），也是深度睡眠的唤醒引脚 | 确定 | 引脚的数字输入被关掉（模拟用法）；ADC 最后一次采样的通道是 0；固件要求唤醒引脚编号 ≤ 5 |
| GPIO1 | 屏幕 SPI 片选（CS） | 确定 | `FSPICS0` 输出 |
| GPIO8 | 屏幕 SPI 时钟（SCLK） | 确定 | `FSPICLK` 输出 |
| GPIO9 | 屏幕 SPI 数据（MOSI，主控发往屏幕） | 确定 | `FSPID` 输出 |
| GPIO20 | 屏幕的数据 / 命令切换线（D/C） | 推断，把握高 | 普通 GPIO 功能，开了上拉，最后一次输出是高电平（发图像数据时的电平）；ESP-IDF v5.5 的屏幕驱动只在每次传输时打开这根线的输出，传完就关，所以暂停时看到“输出关闭” |
| GPIO21 | 屏幕背光，PWM 调亮度（LEDC 第 0 通道） | 确定 | `LEDC_LS_SIG_OUT0` 输出；开机日志 `gpio=21` |
| GPIO7 | I2C 时钟（SCL），连 ES8311 和 CW2017 | 确定 | `I2CEXT0_SCL` 输入输出 |
| GPIO10 | I2C 数据（SDA） | 确定 | `I2CEXT0_SDA` 输入输出 |
| GPIO6 | I2S 主时钟（MCLK），给 ES8311 | 确定 | `I2S_MCLK` 输出 |
| GPIO5 | I2S 位时钟（BCLK），放音和录音共用 | 确定 | `I2SO_BCK` 输出、`I2SI_BCK` 输入都在这根 |
| GPIO3 | I2S 左右声道时钟（WS），放音和录音共用 | 确定 | `I2SO_WS` 输出、`I2SI_WS` 输入都在这根 |
| GPIO2 | I2S 放音数据（主控 → ES8311 → 喇叭） | 确定 | `I2SO_SD` 输出 |
| GPIO4 | I2S 录音数据（麦克风 → ES8311 → 主控） | 确定 | `I2SI_SD` 输入 |
| GPIO12–17 | 内置 Flash 占用 | 确定 | ESP32-C3 带内置 Flash 时的固定用法 |
| GPIO18 / 19 | USB | 确定 | 固定用法 |
| GPIO11 | 未使用 | 确定 | 没有任何设置 |

没有找到的：

- **屏幕复位线**：没有引脚被设成输出，屏幕可能靠上电复位或软件复位命令。
- **喇叭功放开关**：没有单独的引脚，功放可能一直通电，或者由 ES8311 控制。
- **左侧电源键**：所有 GPIO 都有了用途，它不接 GPIO，应该是直接控制供电的硬件开关。

重跑方法：卡片醒着时运行下面的命令，再用 `python3 tools/decode_gpio_dump.py device-scan/esp32c3_gpio_sig_map_idf-v5.5.h device-scan/gpio-dump.txt` 解码。原始读数在 [device-scan/gpio-dump.txt](../device-scan/gpio-dump.txt)，解码结果在 [device-scan/gpio-decoded.txt](../device-scan/gpio-decoded.txt)。

```bash
openocd -s <openocd 目录>/share/openocd/scripts -f board/esp32c3-builtin.cfg -c "init; halt; echo {== OUT_SEL GPIO0-21}; echo [mdw 0x60004554 22]; echo {== IN_SEL signals 0-127}; echo [mdw 0x60004154 128]; echo {== IO_MUX GPIO0-21}; echo [mdw 0x60009004 22]; echo {== ENABLE OUT IN}; echo [mdw 0x60004020]; echo [mdw 0x60004004]; echo [mdw 0x6000403c]; echo {== SARADC_ONETIME a}; echo [mdw 0x60040020]; resume; shutdown"
```

## 使用注意

- 闲置 5 分钟后进入深度睡眠，USB 串口会消失；按任意功能键唤醒。
- 打开 USB 串口会让卡片重启一次。
- 安全启动和 Flash 加密都没开，可以读出、重新烧录。
- 原厂固件完整备份在 `firmware-backup/`（不进 git，含本机独有的身份和配对数据），SHA-256 为 `03d6dfc67996615c47cd0ad96bce2da354e06db185fc8d88377d6162900529db`。

## 原厂固件里的产线指令

通过 USB 串口发送，以 `\r\n` 结尾。指令和作用来自固件字符串；只有标“跑过”的实际发送过，结果在 [device-scan/at-test.txt](../device-scan/at-test.txt)。

| 指令 | 作用 | 是否改动设备 | 跑过 |
| --- | --- | --- | --- |
| `AT+TEST=INFO` / `I2C` / `BATT` | 读版本、芯片连接、电池 | 不改 | 跑过 |
| `AT+TEST=AUDIO` | 放一声测试音并录音测量 | 不改，会响 | 跑过 |
| `AT+TEST=BLE` / `ID` / `DISP` / `BTN` | 蓝牙、身份、屏幕测试图、10 秒内按键测试 | 推断不改 | 没跑 |
| `AT+CARDID=` | 写入设备身份 | **改** | 没跑 |
| `at+config=common,volume,<0-100>` / `standby_time,<秒>` | 改音量、改自动睡眠时间 | **改** | 没跑 |
| `at+command=binding,reset` | 清除绑定 | **改，不要随便用** | 没跑 |
| `at+reboot` / `at+command=restart,now` | 重启 | 只重启 | 没跑 |

## 还没查清的

1. 屏幕分辨率（推断 240×320）和 D/C 线是不是 GPIO20：在自制固件里点亮屏幕时确认。
2. 3 个功能键各自对应的 ADC 电压范围：在自制固件里读 GPIO0 的电压，依次按键测出来。
3. 右侧最上面那个键是不是确认键：用户也不太确定，按键测电压时一起确认。
