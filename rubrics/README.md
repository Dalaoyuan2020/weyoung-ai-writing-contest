# 评委提示词（模板）

这里是**模板**，不是比赛实际用的评分卡。比赛用的判据属于内规，不公开。

每个文件是一个评委角色的 system prompt。写自己的评分卡时：

- 结构照着来：角色 → 只看什么 → 怎么判 → 输出 JSON 格式
- 判据换成你自己的
- 不想公开的细则写在 `config/rules.private.json` 的 `internal` 里，用 `{{internal}}` 拼进来，模板本身可以公开
- 输出一律 JSON，字段名固定，代码按字段取值

| 文件 | 角色 | 输出 |
|---|---|---|
| `quick.md` | 快评：几秒内给档位和一句话，不给分数 | `tier`、`first_look`、`biggest_problem` |
| `content.md` | 内容评委 | 它负责的维度分数 + 证据原文 |
| `craft.md` | 文字评委 | 同上 |
| `process.md` | 过程评委：只看创作过程记录，评人做了什么判断 | 同上 |
| `arbiter.md` | 仲裁：两次打分差太多时出来裁决 | 最终分数 + 理由 |
| `feedback.md` | 写评语：把分数翻译成作者能照着改的话 | `comment`、`next_step` |
