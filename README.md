# 未央黑客松 · AI 征文大赛：评审系统架构

一场让大学生**练习怎么驱动 AI** 的征文比赛，以及它背后的 AI 自动评审系统。

作品都是学生用 AI 写的。比的不是 AI 写得多好，是人在中间做了多少判断。
每篇投稿交正文 + 创作过程记录，十几秒拿到快评，几十秒拿到五维分数和评语，改完再投。

> 征文不是一个赛道，它是一种教学形式的设计。

线上网站（含最终战报）：**https://weyoung.winnielyu.com**

## 结果

| | |
|---|---|
| **92** 人参加 | **405** 篇作品，全部评完 |
| **72%** | 投过两次以上的人里，改完分数涨了（平均 +7.9，最多 +37） |
| **82%** | 改完反而比上一稿分低的 117 次里，97 次又投了一稿 |
| **约 32 小时** | 从零搭好上线 |

完整汇总见 [`stats/summary.json`](stats/summary.json)（只有统计数字，不含任何作品和个人信息）。

## 这个仓库里有什么

这里讲的是**架构怎么搭**，附一个能跑的参考实现。比赛实际用的评分细则（内规）不公开。

| 位置 | 内容 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | **先读这个。** 全流程图 + 七个关键设计 |
| [`docs/lessons.md`](docs/lessons.md) | 复盘：数据说了什么、踩过的五类坑、哪些决定是对的 |
| [`engine/llm.py`](engine/llm.py) | 调模型：多模型轮换 + 熔断 + 备用链 + 用量旁路记账 + 坏 JSON 修复 |
| [`engine/review.py`](engine/review.py) | 评审流水线：快评 → 多评委各打两次 → 分差大就仲裁 → 写评语 → 白名单出口 |
| [`engine/calibrate.py`](engine/calibrate.py) | 校准闸门：评分卡分不出好坏就不许上线 |
| [`engine/leak_check.py`](engine/leak_check.py) | 内规泄露检查 |
| [`rubrics/`](rubrics/) | 评委提示词**模板**（判据留空，换成你自己的） |
| [`config/`](config/) | 模型配置和评分规则的**示例** |
| [`samples/essays.json`](samples/essays.json) | 3 篇主办方写的范文（含 1 篇反面示范），不是选手作品 |
| [`tools/prepublish_scan.py`](tools/prepublish_scan.py) | 发布前安检：key、IP、手机号、邮箱、内规、禁用词，零命中才发布 |

## 跑起来

只用 Python 标准库，不用装任何依赖（Python 3.9+）。

```bash
# 1. 不调任何 API，用假回答把全流程跑一遍
python3 -m engine.review --selftest

# 2. 接真模型：复制配置，填 OpenAI 兼容接口的地址，key 放环境变量
cp config/models.example.json config/models.json
export PROVIDER_A_API_KEY=...
python3 -m engine.review --sample liangang

# 3. 上线前过校准闸门：好范文 vs 反面示范，各跑 3 次
python3 -m engine.calibrate liangang taohua 3
```

想办自己的比赛：把 `rubrics/` 里的判据写成你自己的，不想公开的细则放进
`config/rules.private.json`（已在 `.gitignore` 里），再跑校准闸门。

## 许可

- 代码（`engine/`、`tools/`）：[MIT](LICENSE)
- 文档、范文和统计（`docs/`、`rubrics/`、`samples/`、`stats/`、本 README）：
  [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/deed.zh-hans)，可以转载和改编，注明出处，不可商用
