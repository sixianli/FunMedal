# Noto Sans SC

卡片固件里的中文、英文点阵字体由这个字体生成（见 [notes/firmware.md](../../notes/firmware.md) 的“字体”）。

- **来源**：`https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf`，2026-10-16 下载。
- **SHA-256**（文件内容的校验值，差一个字节就不同）：`faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9`。[tools/gen_fonts.py](../../tools/gen_fonts.py) 生成字体前会核对它。
- **授权**：SIL Open Font License 1.1，全文在同目录的 [LICENSE](LICENSE)。它允许把字形嵌进程序再分发；固件里的点阵是从这个字体生成的，所以随固件一起分发时附上这份授权。
- **不进 git**：字体文件约 8 MB，只有重新生成点阵时才需要，所以 `.gitignore` 排除了 `*.otf`。仓库里只放生成好的 `firmware/main/font_large.c`、`firmware/main/font_text.c` 和这份授权。
