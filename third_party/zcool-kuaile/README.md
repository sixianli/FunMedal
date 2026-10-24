# 站酷快乐体

卡片固件里的大字（30 像素，用于标签、标题和配对数字）由这个字体生成（见 [notes/firmware.md](../../notes/firmware.md) 的“字体”）。站酷快乐体（ZCOOL KuaiLe）是站酷网发起的开源中文字体，笔画圆润活泼。

- **来源**：Google Fonts 仓库里固定的一次提交 `a606d6f3181c62b792c8c1463d309c6a8abb8a12`，文件地址 `https://raw.githubusercontent.com/google/fonts/a606d6f3181c62b792c8c1463d309c6a8abb8a12/ofl/zcoolkuaile/ZCOOLKuaiLe-Regular.ttf`。地址里写死了提交号，以后下载到的永远是同一个文件。2026-10-25 下载。
- **SHA-256**（文件内容的校验值，差一个字节就不同）：`812a6fc1fe54b6d73a419245c32dfeba8aa33104d5be90d1cf6af082007cb71d`。[tools/gen_fonts.py](../../tools/gen_fonts.py) 生成字体前会核对它。
- **授权**：SIL Open Font License 1.1，全文在同目录的 [LICENSE](LICENSE)。它允许把字形嵌进程序再分发；固件里的点阵是从这个字体生成的，所以随固件一起分发时附上这份授权。
- **缺字**：这个字体里没有“□”，所以大字字库里也没有。大字只画固件源代码里写死的文字和数字，这些字都在字库里，用不到“□”。
- **不进 git**：字体文件约 1.5 MB，`.gitignore` 排除了 `third_party/*/*.ttf`。编译固件前运行 `sh tools/fetch-fonts.sh` 下载它。仓库里只放这份说明和授权。
