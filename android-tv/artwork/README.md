# 电视品牌产物

设计母版位于仓库根目录 `design/brand/`，本目录不保留独立SVG副本。导航字标、启动图标和320×180横幅由 `scripts/build_design.py --render-brand` 同源生成，导出路径/尺寸/密度详见 `design/exports.json`。普通APK构建直接消费 `app/src/main/res/` 中已提交产物。
