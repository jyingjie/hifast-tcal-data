# Tcal 标定日期识别说明

本文记录 FAST 噪声二极管标定数据的日期来源、识别顺序和异常处理规则。发布页、PDF 文件名和 PDF 正文可能分别包含月份、报告发布日期和实际测试日期，不能混用。

## 1. 日期用于什么

HiFAST Tcal 数据使用完整的 `YYYYMMDD` 日期作为版本标识，例如：

```text
20260708
```

该日期用于：

- 输出目录名
- FITS 文件名
- ZIP 文件名
- GitHub Release 标签
- `manifest.json` 中的可用日期
- R2 对象路径

对应文件示例：

```text
CAL.20260708.high.W.fits
CAL.20260708.low.W.fits
md5sum.20260708.txt
20260708.zip
```

这里需要的是实际标定测试日期，不是网页发布日期或 PDF 报告发布日期。

## 2. 官网不同位置提供的日期

FAST 发布页的文章标题通常只包含月份：

```text
Noise Diode Calibration Report-202607
```

high/low 原始数据文件通常也只包含月份：

```text
high_202607.tar.gz
low_202607.tar.gz
```

因此，不能从文章标题或 high/low 文件名直接得到 `YYYYMMDD`。

PDF 文件名有时包含完整日期：

```text
noise_test_20260708_en.pdf
```

但也可能只包含月份：

```text
Noise Diode Calibration Report-202503.pdf
```

所以 PDF 文件名是优先来源，但不能假定它永远包含日。

## 3. PDF 中存在多种日期

一份报告中可能同时出现：

- PDF 报告发布日期
- 实际测试日期
- 正文中引用的其他历史测试日期

例如 `202607` 报告前部包含：

```text
July 13, 2026
```

这是报告发布日期。

正文随后写明：

```text
We performed a noise temperature test ... July 08, 2026 ...
```

这是实际测试日期，对应：

```text
20260708
```

因此，不能简单提取 PDF 中出现的第一个日期。

## 4. 当前日期识别顺序

### 第一步：解析 PDF 文件名

从 PDF URL 或文件名中查找合法的 8 位日期：

```text
20\d{6}
```

提取后使用日历规则验证，例如：

- `20260708`：有效
- `20260230`：无效
- `202607`：不是完整日期

如果文件名包含合法的 `YYYYMMDD`，先将其作为候选日期。

### 第二步：读取 PDF 正文

使用 `pypdf` 提取 PDF 前两页文字。测试日期通常位于报告开头的测试说明中，没有必要解析报告中的全部图片和公式。

正文解析支持：

```text
July 08, 2026
July 8th, 2026
2026-07-08
2026/07/08
```

程序不会选择任意日期，而是检查日期附近是否包含测试或测量说明，例如：

```text
test
measurement
testing
calibration
observation
performed
conducted
carried out
measured
between
during
```

只有与测试描述关联且结果唯一的日期才可以作为实际标定日期。

### 第三步：文件名与正文交叉检查

如果 PDF 文件名包含完整日期：

```text
文件名日期 == PDF 正文测试日期
```

两者必须一致，否则停止处理。

如果 PDF 文件名只有月份：

- 使用 PDF 正文中唯一且明确的测试日期。
- 正文日期的年月应与文章标题及 PDF 文件名中的月份一致。
- 如果不能唯一确定，停止处理。

## 5. 禁止使用 manifest.json 推断新日期

不能使用下面的方式获得新数据日期：

```text
根据文章月份，在 manifest.json 中查找同月日期
```

原因是 `manifest.json` 只包含已经处理和发布的数据。新报告的日期在处理完成前不会出现在清单中，所以清单不能作为新日期的来源。

`manifest.json` 只用于判断：

- 某个已经确定的日期是否发布过
- 是否出现新的日期

它不能用于补全未知的日。

## 6. 已验证示例

### 202607

网页文章：

```text
Noise Diode Calibration Report-202607
```

PDF 文件名：

```text
noise_test_20260708_en.pdf
```

PDF 正文：

```text
We performed a noise temperature test ... July 08, 2026 ...
```

识别结果：

```text
20260708
```

日期来源记录为：

```text
date_source = report_url
pdf_test_date = 20260708
```

### 202503

网页文章：

```text
Noise Diode Calibration Report-202503
```

PDF 文件名：

```text
Noise Diode Calibration Report-202503.pdf
```

文件名没有提供日。程序读取 PDF 正文后得到：

```text
20250329
```

日期来源记录为：

```text
date_source = pdf_text
```

这证明 PDF 正文解析可以用于文件名只有月份的情况。

## 7. 必须停止处理的情况

遇到以下任一情况时，自动任务必须失败：

- PDF 下载失败
- 下载结果不是 PDF
- PDF 没有可提取文字
- 正文没有测试日期
- 正文存在多个同样可能的测试日期
- 日期不是有效日历日期
- PDF 文件名日期与正文测试日期不一致
- 正文日期的月份与文章标题明显不一致

程序不能根据月份、发布时间或相邻版本猜测日期。

## 8. 扫描 PDF 和复杂文档

当前官方报告是文字型 PDF，`pypdf` 可以直接读取。对于纯扫描 PDF，`pypdf` 可能无法获得正文。

以后可以按以下顺序增加备用处理：

1. 使用 OCR 或文档解析服务提取前一至两页文字。
2. 使用与当前相同的日期规则检查提取结果。
3. 如果规则仍无法唯一确定，可以把 PDF 前部文字或页面图像交给大语言模型识别。
4. 大语言模型必须返回日期和支持该日期的原文片段。
5. 文件名、规则解析和大语言模型结果不一致时，要求人工确认。

大语言模型只能作为备用方式，不能覆盖明确的文件名日期或正文测试日期。

## 9. 来源记录

每次构建的 `source-record.json` 应至少记录：

```json
{
  "article_id": "...",
  "article_title": "Noise Diode Calibration Report-202607",
  "article_created_at": "2026-07-13 17:11:15",
  "article_updated_at": "2026-07-13 17:11:23",
  "calibration_date": "20260708",
  "date_source": "report_url",
  "pdf_test_date": "20260708",
  "report_url": "...",
  "downloads": {
    "report": {
      "url": "...",
      "sha256": "..."
    }
  }
}
```

这些信息用于以后检查官网文章、PDF 或原始数据是否发生变化。

## 10. 相关实现

- `tcal_pipeline/fast_source.py`：解析发布页、PDF 链接和文件名日期
- `tcal_pipeline/pdf_date.py`：读取 PDF 正文并识别实际测试日期
- `tcal_pipeline/update_from_fast.py`：下载 PDF，并检查文件名日期与正文日期
- `tests/test_fast_source.py`：文件名日期和月份测试
- `tests/test_pdf_date.py`：报告发布日期与测试日期区分测试
