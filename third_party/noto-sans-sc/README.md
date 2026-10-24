# Noto Sans SC

卡片固件里的正文字体（16 像素，每个像素有 16 级透明度，用于批准内容、项目名和按键提示）由这个字体生成（见 [notes/firmware.md](../../notes/firmware.md) 的“字体”）。

- **来源**：`https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf`，2026-10-16 下载。
- **SHA-256**（文件内容的校验值，差一个字节就不同）：`faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9`。[tools/gen_fonts.py](../../tools/gen_fonts.py) 生成字体前会核对它。
- **授权**：SIL Open Font License 1.1，全文在同目录的 [LICENSE](LICENSE)。它允许把字形嵌进程序再分发；固件里的点阵是从这个字体生成的，所以随固件一起分发时附上这份授权。
- **不进 git**：字体文件约 8 MB，`.gitignore` 排除了 `third_party/*/*.otf`。编译固件时点阵才从它生成，生成的文件放在编译目录里，也不进 git。编译固件前运行 `sh tools/fetch-fonts.sh` 下载它。仓库里只放这份说明和授权。
