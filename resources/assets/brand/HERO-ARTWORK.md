# CPlus Cleaner 静态立体插画

2026-10-26，用户确认方案A并明确不需要动效。两张图使用内置image_gen分别生成，提示词完整保存在[hero-prompts.json](hero-prompts.json)。

- C盘清理：[../hero-clean.png](../hero-clean.png)，浅青色磁盘、薄荷绿扫描环和少量缓存方块。
- 大文件搬家：[../hero-large.png](../hero-large.png)，双磁盘、文件夹与绿色传输箭头。

两张均为1196×1315的静态RGBA PNG，含真实透明通道，未在图中加入文字、按钮或品牌标志。生成结果直接复制入项目，保留高清像素及Alpha，没有对图像内容做二次绘制或手工抠图。原始尺寸、文件大小与SHA-256见[hero-assets.json](hero-assets.json)。

运行时用Tk PhotoImage按整数比例缩小，最多占用300×330的显示区域，缩放版本按比例缓存，不依赖Pillow或图像生成服务。C盘画布按可用高度居中；大文件搬家插画放在待扫描页右下方，磁盘信息区域保留独立宽度。窗口变更时更新显示尺寸，没有帧动画或定时动效。

显示不透明度为60%：缩小后仅在显示层将原Alpha乘以3/5（取最近整数），最大Alpha为153/255，RGB保持不变。使用标准库PNG处理并缓存显示结果，原始高清文件的像素和摘要不变；没有新增运行依赖。

980×650与1160×760页面预览位于`../../previews/static-hero-{cdrive,large}-{980,1160}.png`，容量数字使用隔离测试样本，未扫描或处理真实磁盘文件。

<!-- Purpose: Static illustration behavior and rendering notes -->
