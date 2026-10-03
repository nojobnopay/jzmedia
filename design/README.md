# jzmedia 设计资产

`design/` 管理跨端设计的唯一母版与定义；`requirements.json`、`catalog.json` 和导出清单由生成器维护。功能图标用 24×24 视框、1.7 描边、圆端点和连接，播放、评分和收藏的实心状态单独登记；颜色跟随所在控件。J 与播放标记延续仓库既有品牌，16px/32px 标记保留光学校正版。固定字标已转路径，不依赖浏览器或电视字体。

## 维护入口

| 文件 | 负责的内容 |
| --- | --- |
| `assets.json`、`icons/*.svg`、`brand/*.svg` | 稳定语义 ID、母版、网格、来源、修订、主题适配和废弃替代关系 |
| `tokens.json` | Web 语义令牌与 Android 颜色引用、尺寸、焦点规则；Android 颜色引用同一语义令牌而非复制色值；CSS px、Android dp 和目标显示分辨率分开记录 |
| `contracts.json` | 动态图标的取值范围与来源、原生/专用控件的逐用途例外；新增未登记 SVG、动态名或普通裸按钮检查失败 |
| `platform-profiles.json` | 各控件实际逻辑几何规格、动态表达式和源码位置；浏览器测量与源码规范分开 |
| `requirements.json` | 自动扫描全部生产 Vue / Kotlin 的逐位置用途、关联资产、控件类别、尺寸单位、命中区、状态、可访问名称责任 |
| `exports.json`、`raster-hashes.json` | 跨端品牌输出路径、实际像素层/密度、安全区、透明度、版本与内容哈希 |
| `catalog.json` | 隔离组件目录使用的数据，合并资产、用途和导出规格；不进入产品导航 |
| `acceptance/` | 实际验证环境、尺寸/密度、结果与限制；构建、模拟器和真机分开记录 |

Web 和手机网页共享 SVG，电视由同一母版生成 Compose `ImageVector`。1080p/4K 是显示环境，不能据此复制两份相同图形。`AppIcon` 的 `forward` 表示导航前进；`PlayerIcon` 的 `forward` 显式映射为 `skip-forward-10`，后退同理，数字已转路径。装饰图标不重复播报，按钮和链接提供名称；纯图标按钮必须设置 `aria-label`。正常文字、快捷键、屏幕键盘字母、媒体图片及字幕不属于业务图标。

历史图标的外部来源未在原仓库记录：每项保留迁移路径和 Git 基线，不据此推断授权信息；新绘路径单独标注。字标采用 DejaVu Sans Bold 的固定轮廓，许可证随母版保存在 `brand/DEJAVU-LICENSE.txt`。

## 修改与生成

1. 在 `assets.json` 新增语义 ID 和使用目的，创建只含 `path` 的母版。禁止 SVG 文本、变换、滤镜、外链和字体依赖；`fill-rule` 支持 `evenodd`。改款沿用语义 ID，递增修订并记录变化；删除先登记替代关系。
2. 更新 `tokens.json` 或调用点；动态取值与专门的原生按钮在 `contracts.json` 显式登记，避免图标名与行为漂移。
3. 运行生成，再查看隔离图鉴。功能图标、令牌和使用清单仅需 Python 标准库：

```sh
python scripts/build_design.py
python scripts/build_design.py --check
node scripts/smoke_design_system.mjs --demo
```

品牌位图维护使用独立环境，与产品依赖分离：

```sh
python -m venv /tmp/jzmedia-design-env
/tmp/jzmedia-design-env/bin/pip install -r design/requirements-render.txt
/tmp/jzmedia-design-env/bin/python scripts/build_design.py --render-brand
/tmp/jzmedia-design-env/bin/python scripts/build_design.py --check --render-brand
```

`--check` 是只读检查，比较所有文本生成物、实际用途清单、跨端别名及 PNG/ICO 实际尺寸；默认通过哈希检查位图内容与母版同步。`--check --render-brand` 进一步重新渲染到内存逐字节比较。ICO 显式写入 16、32、48 三个真实图层，不从 32px 位图声明不存在的 48px 层。

提交母版和生成物；禁止手工修改 `frontend/src/generated/`、Android `ui/generated/` 或导出的品牌资源。Web、帮助站、APK 的普通构建消费已提交产物，互不依赖。品牌资源统一使用 `scripts/build_design.py --render-brand` 维护。

## 验收与边界

检查桌面 1440px、手机 390/375px 和播放器 320px；普通/悬停/按下/焦点/选中/禁用/忙碌状态、减少动画、长文本及可访问名称。电视记录模拟器或设备的分辨率、实际 density、字体比例及遥控器输入；模拟器通过不能替代真机远距离辨识。

用途清单记录的是源码需求，声明的 44px 触摸目标必须由浏览器测量确认；`display_size` 中的表达式和 `component/CSS contract` 是动态组件约束，不冒充测量值。通用图标组件的参数契约列出允许的图标集合，实际调用位置在同一清单中单独记录。截图一律使用隔离 API/合成媒体，禁止为素材采集操作真实媒体库。
