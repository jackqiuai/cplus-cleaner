# Image and rule resources / 图片与规则资源

[`runtime-assets.json`](runtime-assets.json) lists every retained image or editable vector with its purpose in Chinese and English. It contains 116 entries. Runtime PNG/ICO files are distinguished from editable SVG sources. The documentation screenshot lives separately at `docs/images/home-en.png`.

| Path | Purpose / 用途 |
| --- | --- |
| `app-icon.png`, `tray.ico`, `brand/sidebar.png` | 程序、窗口、托盘和侧栏品牌 / Application, window, tray and sidebar brand |
| `brand/logo.svg` | 可编辑品牌源文件 / Editable brand source |
| `hero-clean.png`, `hero-large.png` | C盘清理与大文件搬家静态插图 / Drive Cleanup and Large File Mover illustrations |
| `home/background.png`, `home/search.*` | 首页背景和扫描图标 / Home background and scan icon |
| `nav-*.png`, `nav-system*.svg` | 导航普通、选中状态与系统管理可编辑源文件 / Navigation states and editable system icon sources |
| `check-*.png` | 全项目统一的勾选、未选、部分选中素材 / Shared checked, unchecked and mixed states |
| `categories/*.png`, `category-icons.json` | 清理类别图标及映射 / Cleanup category icons and mappings |
| `privacy/*.png` | 隐私分类及结果图标 / Privacy group and result icons |
| `large-c-*.png` | 大文件分类图标 / Large-file category icons |
| `icon-*.png` | 软件与清理类别的备用图标，包含微信资源 / Application and category fallback icons, including WeChat |
| `status-warning.png` | 扫描结果感叹号 / Scan-result warning icon |
| `chat-rules.json` | 聊天软件缓存及附件目录规则 / Chat cache and attachment location rules |
| `popup-huorong.json` | 弹窗定义和匹配规则 / Popup definitions and matching rules |

旧首页动效、已替换按钮、旧 QQ 导航等 40 张未引用图片或矢量已移到本地 `dev/archive/assets/`，不随源码上传。微信相关的现用图标保留。项目回归测试使用的图标也属于项目资源。

Source/provenance records (`asset-sources.json`, `home/*manifest.json`, `privacy/sources.json`, and `brand/` metadata) document retained artwork and its history. Historical archive/member paths are research references, not files required to run the program. `SOURCE-MANIFEST.json` at the project root covers the current published bytes. See [`brand/HERO-ARTWORK.md`](brand/HERO-ARTWORK.md) for the static illustration behavior.

第三方资源的来源记录应保留；目录整理不会改变其权属或分发条件。新增图片时应同步更新用途清单、映射和源码完整性清单，避免将预览截图或未使用实验素材放入运行目录。
