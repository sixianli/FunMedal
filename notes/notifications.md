# iPhone 和 Mac 通知汇聚到卡片

本文记录 2026-10-16 查到的做法。标“未确认”的，是没有找到苹果或乐鑫自己的资料，需要用真机实测。来源链接附在文末。

## 结论

| 来源 | 推荐做法 | 能做的操作 | 把握 |
| --- | --- | --- | --- |
| iPhone | 卡片直接用 ANCS（Apple Notification Center Service，苹果让 iPhone 把通知通过低功耗蓝牙交给配件的公开协议）连 iPhone，不用装 App | 每条通知只有“正操作”和“负操作”两种，按钮文字由 iPhone 给；不能回复文字 | 协议和乐鑫例程确定；锁屏时能不能收到、正负操作具体做什么，未确认 |
| Mac | 没有公开接口能读别的 App 的通知。只能做一个 Mac 菜单栏程序，用辅助功能接口（macOS 让程序读取和操作其他程序界面的系统接口）读屏幕上的通知横幅，再用通知数据库补漏 | 关闭通知、点通知上的按钮 | 能做，但依赖苹果没公开的内部结构，系统升级可能失效 |

卡片同时连 iPhone 和 Mac 在容量上可行：ESP32-C3 用 NimBLE（ESP-IDF 里的一套蓝牙软件）时最多可设 9 个连接，默认 3 个。这是根据乐鑫配置说明推断的，乐鑫没有专门写这个场景。

## iPhone：ANCS

- **能拿到的内容**：App 标识、标题、副标题、正文、时间、正负操作的按钮文字；还有分类（来电、社交、邮件等 12 种）和标志位，比如“静默”“重要”“连上之前就有的旧通知”。
- **操作**：只能发“正操作”或“负操作”。苹果规范要求配件不要假设它们的含义。常见情况是来电对应“接听 / 拒绝”、普通通知对应“清除”，但这是经验说法，未确认。
- **要求**：要先配对并绑定（双方保存密钥，下次自动加密重连）。iOS 13 起，第一次使用时 iPhone 会弹窗问用户是否允许共享通知，之后可以在该蓝牙设备的设置里关掉（“共享系统通知”开关）。
- **乐鑫例程**：ESP-IDF v5.5 有 `examples/bluetooth/nimble/ble_ancs` 和 `examples/bluetooth/bluedroid/ble/ble_ancs` 两个例程，README 都列出支持 ESP32-C3（已核对）。NimBLE 例程用的是旧式配对，要改成更安全的 LE Secure Connections。
- **欧盟的“通知转发”**：iOS 26 在欧盟另开了一套通知转发框架，可以按 App 选择，还能快捷回复。但它要配套 iPhone App 和苹果的专用权限，配件端还要做 HPKE（一种公钥加密方案）解密，而且只对欧盟账号生效；打开后 Apple Watch 就收不到通知了。第一版不做。
- **已知风险**：有开发者报告 iOS 26.5.2 在未读通知很多时 ANCS 连接会卡住；报告里的场景是经典蓝牙，低功耗蓝牙上会不会也这样，未确认。

## Mac：没有公开接口

- **公开接口**：`UNUserNotificationCenter` 只能拿到本 App 自己的通知。
- **辅助功能接口读横幅**：用户要在“隐私与安全性 > 辅助功能”里授权；程序不能放进沙盒（App Store 要求的权限隔离环境）。能读到屏幕上出现的横幅，也能点“关闭”或通知上的按钮。macOS 15 起，通知文字放在 `AXAttributedDescription` 里，AppleScript 读不到，要用 C 语言接口。界面结构每个大版本都可能变。
- **读通知数据库**：通知数据库是 macOS 的通知后台进程 usernoted 保存的一个 SQLite 数据库文件。macOS 15 把它搬到了 `~/Library/Group Containers/group.com.apple.usernoted/db2/db`，需要“完全磁盘访问权限”才能读（苹果安全公告 CVE-2024-40838 写的是“把敏感数据移到受保护的位置”，已核对）。表结构没有公开说明，只能读，不能关闭或回复通知。
- **iPhone 镜像**：macOS 15 以上配 iOS 18 以上（欧盟不可用），Mac 会自动收到 iPhone 的通知。这样 iPhone 的通知可能在卡片上出现两遍（ANCS 一遍、Mac 一遍），需要去重。
- 当前最新的 macOS 是 macOS 27。

## 推荐路线

1. **先做 iPhone**：以 NimBLE `ble_ancs` 例程为起点，开启绑定和安全配对；新通知到了就取标题和正文，三个键映射到正操作、负操作和“看下一条”。
2. **Mac 后做**：Mac 菜单栏桥（反正 Claude Code 也需要它）顺带用辅助功能接口抓通知横幅；每个 macOS 大版本出来都要重新测试。
3. **去重**：同一条通知从两个来源进来时，按 App、标题、时间合并。

## 要用真机实测的

1. iPhone 锁屏时 ANCS 还能不能收到通知。
2. 普通通知的正 / 负操作按钮文字是什么、按下去做了什么。
3. Mac 同时连着卡片时，iPhone 的 ANCS 连接是否稳定。
4. 本机 macOS 版本下，辅助功能接口能否读到通知横幅的文字。

## 来源

- ANCS 规范：https://developer.apple.com/library/archive/documentation/CoreBluetooth/Reference/AppleNotificationCenterServiceSpecification/Specification/Specification.html
- iOS 13 通知共享授权（WWDC 2019）：https://developer.apple.com/videos/play/wwdc2019/901/
- 欧盟通知转发：https://developer.apple.com/documentation/accessorynotifications 、https://developer.apple.com/documentation/accessorytransportextension
- ANCS 卡住的报告：https://developer.apple.com/forums/thread/839692
- 乐鑫例程：https://github.com/espressif/esp-idf/tree/release/v5.5/examples/bluetooth/nimble/ble_ancs 、https://github.com/espressif/esp-idf/tree/release/v5.5/examples/bluetooth/bluedroid/ble/ble_ancs
- ESP32-C3 蓝牙连接数配置：https://github.com/espressif/esp-idf/blob/release/v5.5/components/bt/host/nimble/Kconfig.in 、https://github.com/espressif/esp-idf/blob/release/v5.5/components/bt/controller/esp32c3/Kconfig.in
- 只能取本 App 通知：https://developer.apple.com/documentation/usernotifications/unusernotificationcenter/getdeliverednotifications(completionhandler:)
- 通知数据库搬迁：https://support.apple.com/en-us/121238 （CVE-2024-40838）、https://9to5mac.com/2024/09/01/security-bite-apple-addresses-privacy-concerns-around-notification-center-database-in-macos-sequoia/
- 辅助功能读横幅：https://forum.latenightsw.com/t/parsing-notifications-in-macos-sequoia/5001 、https://developer.apple.com/documentation/applicationservices/1459186-axisprocesstrustedwithoptions
- 第三方实现参考：https://github.com/backglance/backglance 、https://github.com/qya/macos-notification-forwarder
- iPhone 镜像：https://support.apple.com/en-us/120421 、https://support.apple.com/en-us/120684
- macOS 当前版本：https://www.apple.com/os/macos/
