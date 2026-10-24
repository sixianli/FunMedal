# Fusion Pixel

卡片固件里的像素字体（12 像素高、每个像素只有亮和不亮两种）由这个字体生成（见 [notes/firmware.md](../../notes/firmware.md) 的“字体”）。Fusion Pixel 是 TakWolf 做的开源中文像素字体。

- **来源**：发布版 2026.09.25 的压缩包 `https://github.com/TakWolf/fusion-pixel-font/releases/download/2026.09.25/fusion-pixel-font-12px-proportional-bdf-v2026.09.25.zip`，里面的 `fusion-pixel-12px-proportional-zh_hans.bdf`（BDF 是一种按点阵存字形的文本格式，`proportional` 表示每个字宽度不同，`zh_hans` 表示简体字形）。2026-10-25 下载。没有用更新的 2026.10.16 版，因为它发布还不到两周。
- **SHA-256**（文件内容的校验值，差一个字节就不同）：
  - BDF 文件：`9d69fe7a51e928dbb0689a70bed8eec28666713d8c57410fba0f0aa69c0005c6`。[tools/gen_fonts.py](../../tools/gen_fonts.py) 生成字体前会核对它。
  - 压缩包：`7cae5ea83b46086f146002d194d46bbba70ec7264ac3a309219f5ad491626cef`，只用来对照发布页，[tools/fetch-fonts.sh](../../tools/fetch-fonts.sh) 核对的是解出来的 BDF 文件。
- **授权**：SIL Open Font License 1.1，全文在同目录的 [LICENSE](LICENSE)。Fusion Pixel 里还收了别的像素字体的字形，它们的授权在 [LICENSES/](LICENSES/)：Ark Pixel（[OFL](LICENSES/ark-pixel/OFL.txt)）、Cubic 11（[OFL](LICENSES/cubic-11/OFL.txt)）、Galmuri（[OFL](LICENSES/galmuri/LICENSE.txt)）。这些授权都允许把字形嵌进程序再分发；固件里的点阵是从这个字体生成的，所以随固件一起分发时附上这些授权。
- **不进 git**：BDF 文件约 5 MB，`.gitignore` 排除了 `third_party/*/*.bdf`。编译固件前运行 `sh tools/fetch-fonts.sh` 下载它。仓库里只放这份说明和授权。
