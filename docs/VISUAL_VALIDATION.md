# Tcal 历史数据可视化检查

## 目的

新数据转换为 FITS 后，将它与最近几次已经发布的数据画在同一张图中，供人工检查。

这项检查主要回答两个问题：

1. 新一次标定相对历史数据发生了什么变化。
2. 转换过程中是否出现明显异常，例如波束错位、偏振顺序错误、频率轴错误或数据截断。

图片是辅助检查，不能替代 FITS 结构、数组长度、频率轴、哈希和压缩包完整性检查。不同日期的 Tcal 本身可以发生真实变化，因此当前不设置固定的数值变化阈值，也不根据曲线差异自动判定失败。

## 历史数据选择

自动更新程序读取 `manifest.json`，选择当前标定日期之前最近 3 个日期。

例如，处理 `20260708` 时，默认比较：

- `20241029`
- `20250329`
- `20251101`
- `20260708`，即当前新数据

历史数据从对应的 GitHub Release ZIP 下载。程序检查 ZIP 的完整性及文件列表，只接受：

```text
CAL.YYYYMMDD.high.W.fits
CAL.YYYYMMDD.low.W.fits
md5sum.YYYYMMDD.txt
```

所有历史 FITS 都会再次执行形状、有限值和频率递增检查。历史数据与当前数据的频率数组必须完全相同，否则停止绘图。

## 输出图片

程序在 `build/YYYYMMDD/qa/` 中生成 5 张 PNG：

```text
tcal-spectra-high-xx.png
tcal-spectra-high-yy.png
tcal-spectra-low-xx.png
tcal-spectra-low-yy.png
tcal-relative-beams.png
```

同时生成 `comparison-summary.json`，记录参与比较的日期、绘图参数、图片大小和 SHA256。记录中的 `manual_review_required` 固定为 `true`，表示程序没有依据图片自动判断数据是否合理，维护者可以在发布后查看。

只有发现新日期时才会生成这些文件。5 张 PNG 和 JSON 摘要会自动作为独立资产上传到同一个 GitHub Release，供发布后检查；它们不会放进正式发布的 `YYYYMMDD.zip`，也不会改变该 ZIP 的下载网址。

### 19 波束频谱图

前 4 张图片分别对应 high/low 和 XX/YY。每张图片包含 M01 至 M19，共 19 个子图。

- 频率范围：1020–1480 MHz
- 为避免 65536 个点使图片过密，按 1 MHz 对数据求平均后绘图
- 历史日期使用彩色细线
- 当前新日期使用黑色粗线，并标记为 `new`

这个图保留了原 `plot_Tcal_2.ipynb` 的主要用途，但把交互式 notebook 改成可重复执行的 Python 程序。

### 波束相对值图

`tcal-relative-beams.png` 包含 high/low 和 XX/YY 四个子图。

计算方法与原 notebook 一致：

1. 取 1330–1430 MHz。
2. 每 5 MHz 计算一次各波束 Tcal 中位数。
3. 每个频率段除以同一日期、同一模式和偏振下的 M01。
4. 对各频率段的相对值计算 16%、50% 和 84% 分位数。

中心点表示 50% 分位数，误差线表示 16%–84% 范围。

## 人工检查内容

建议按以下顺序检查：

1. 四张频谱图是否都包含 19 个波束和所有比较日期。
2. 新曲线是否覆盖完整的 1020–1480 MHz，是否出现突然中断、整体平移或异常锯齿。
3. 单个波束是否出现远离其他历史日期的异常幅度或形状。
4. XX、YY 的变化是否符合预期，是否存在疑似偏振交换。
5. high 和 low 的整体关系是否合理。
6. 相对波束图中是否只有个别波束发生异常跳变。

1 MHz 平均会弱化很窄的尖峰。需要检查窄带细节时，应直接读取 FITS 或另画未经平均的数据，不能只依赖这些 PNG。

## 自动更新中的执行位置

当前顺序是：

```text
下载新数据
-> 转换并验证 FITS
-> 下载最近 3 个历史 Release
-> 生成比较图片
-> 写入 source-record.json
-> 将图片作为独立 Release 资产发布
```

默认情况下，历史数据下载、FITS 验证或绘图失败会使本次自动任务失败。仅在本地排查其他问题时，可以使用 `--history-count 0` 暂时跳过绘图。

## 手动执行

如果新 FITS 已经生成，且历史 ZIP 已在本地：

```bash
python -m tcal_pipeline.plot_tcal_comparison \
  --date 20260708 \
  --current build/20260708/release \
  --history 20241029=20241029.zip \
  --history 20250329=20250329.zip \
  --history 20251101=20251101.zip \
  --output build/20260708/qa
```

完整更新程序默认自动执行同样的检查：

```bash
python -m tcal_pipeline.update_from_fast --date 20260708 --output build
```
