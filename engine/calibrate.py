#!/usr/bin/env python3
"""校准闸门 —— 评分卡上线前必须过这一关。

判据：两篇「应该拉开差距」的样文，它们的平均分差必须大于同一篇自己重跑的波动。
不过关，说明评分卡分不出好坏，排名就是噪声，不许上线。

比赛里实测：同一篇重打会有 ±13 分的波动。不先量出这个数，名次就没意义。

用法：
    python3 -m engine.calibrate liangang taohua 3      # 好范文 vs 反面示范，各跑 3 次
"""
import json
import statistics as st
import sys
from concurrent.futures import ThreadPoolExecutor

from engine import llm
from engine.review import SAMPLES, load_rules, review


def main(a_id, b_id, n=3):
    cfg, rules = llm.load_cfg(), load_rules()
    essays = {e["id"]: e for e in json.loads(SAMPLES.read_text(encoding="utf-8"))["essays"]}
    runs = {}
    for sid in (a_id, b_id):
        with ThreadPoolExecutor(max_workers=n) as ex:
            totals = [r["total"] for r in ex.map(lambda _: review(cfg, rules, essays[sid]), range(n))]
        runs[sid] = {"mean": st.mean(totals), "spread": max(totals) - min(totals)}
        print(f"《{essays[sid]['title']}》 {n} 次总分 {totals}  均值 {runs[sid]['mean']:.1f}  自身波动 {runs[sid]['spread']}")
    gap = abs(runs[a_id]["mean"] - runs[b_id]["mean"])
    noise = max(r["spread"] for r in runs.values())
    ok = gap > noise
    print(f"\n两篇分差 {gap:.1f}，单篇最大波动 {noise} → {'✅ 通过' if ok else '❌ 不通过：评分卡分不出好坏，改评分卡，别上线'}")
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], args[1], int(args[2]) if len(args) > 2 else 3))
