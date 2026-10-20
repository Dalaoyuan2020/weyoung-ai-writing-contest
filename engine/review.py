#!/usr/bin/env python3
"""评审流水线（参考实现，纯标准库）。

    快评（几秒，只给档位）
      → 详评：每位评委各打两次（轮换到不同模型）→ 分差大就仲裁
      → 代码兜底（分数夹到满分范围内、自创的档位丢掉）
      → 写评语
      → public_view()：唯一对外出口，白名单字段

用法：
    python3 -m engine.review --selftest                # 不调 API，用假回答跑通全流程
    python3 -m engine.review --sample liangang         # 用真模型评一篇范文（要先配 config/models.json）
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engine import llm

ROOT = Path(__file__).resolve().parent.parent
RUBRICS = ROOT / "rubrics"
RULES_PRIVATE = ROOT / "config" / "rules.private.json"
RULES_EXAMPLE = ROOT / "config" / "rules.example.json"
SAMPLES = ROOT / "samples" / "essays.json"


def load_rules():
    path = RULES_PRIVATE if RULES_PRIVATE.exists() else RULES_EXAMPLE
    return json.loads(path.read_text(encoding="utf-8"))


def rubric(name, rules, dims=None):
    """读评分卡模板，把档位、维度、内规拼进去。内规只进 system prompt。"""
    text = (RUBRICS / f"{name}.md").read_text(encoding="utf-8")
    tiers = "、".join(t for _, t in rules["tiers"][::-1])
    dim_txt = "；".join(f"{k}（{rules['dims'][k]['name']}，满分 {rules['dims'][k]['full']}）"
                       for k in (dims or []))
    internal = json.dumps(rules.get("internal", {}), ensure_ascii=False)
    return (text.replace("{{tiers}}", tiers).replace("{{dims}}", dim_txt)
                .replace("{{internal}}", internal))


def essay_text(sub, with_process=True):
    s = f"题目：{sub['title']}\n\n正文：\n{sub['body']}"
    if with_process:
        s += f"\n\n创作过程记录：\n{sub.get('process_log', '')}"
    return s


def tier_of(total, rules):
    for cut, name in rules["tiers"]:
        if total >= cut:
            return name
    return rules["tiers"][-1][1]


# ── 第一段：快评 ──────────────────────────────────────────
def quick(cfg, rules, sub):
    d, _ = llm.ask(cfg, "quick", rubric("quick", rules), essay_text(sub, with_process=False))
    names = [t for _, t in rules["tiers"]]
    if d.get("tier") not in names:          # 模型自创档位就当没给
        d["tier"] = None
    return d


# ── 第二段：详评 ──────────────────────────────────────────
def clamp(v, full):
    """能算的硬约束用代码兜，别指望模型记住。模型给出 25/20、"十二分" 都见过。"""
    try:
        return max(0, min(full, round(float(v))))
    except (TypeError, ValueError):
        return 0


def judge_twice(cfg, rules, sub, judge, dims):
    """同一位评委打两次（轮换会派给不同模型），分差超过阈值的维度交给仲裁。"""
    system = rubric(judge, rules, dims)
    user = essay_text(sub)
    with ThreadPoolExecutor(max_workers=2) as ex:
        a, b = [f.result()[0] for f in [ex.submit(llm.ask, cfg, judge, system, user) for _ in range(2)]]
    out, notes = {}, []
    for k in dims:
        full = rules["dims"][k]["full"]
        sa = clamp((a.get("scores") or {}).get(k), full)
        sb = clamp((b.get("scores") or {}).get(k), full)
        if abs(sa - sb) > rules.get("arbitrate_gap", 4):
            ctx = json.dumps({"维度": rules["dims"][k]["name"], "满分": full,
                              "评委甲": {"分": sa, "证据": (a.get("evidence") or {}).get(k)},
                              "评委乙": {"分": sb, "证据": (b.get("evidence") or {}).get(k)}},
                             ensure_ascii=False)
            d, _ = llm.ask(cfg, "arbiter", rubric("arbiter", rules), user + "\n\n" + ctx, rotate=False)
            score = clamp(d.get("score"), full)
        else:
            score = round((sa + sb) / 2)
        out[k] = score
    notes += [x for x in (a.get("note"), b.get("note")) if x]
    return out, notes


def review(cfg, rules, sub, on_quick=None):
    q = quick(cfg, rules, sub)
    if on_quick:
        on_quick(q)              # 学生等详评的那几十秒里，先有东西看
    by_judge = {}
    for k, d in rules["dims"].items():
        by_judge.setdefault(d["judge"], []).append(k)
    dims, notes = {}, []
    with ThreadPoolExecutor(max_workers=len(by_judge)) as ex:
        futs = [ex.submit(judge_twice, cfg, rules, sub, j, ks) for j, ks in by_judge.items()]
        for f in futs:
            s, n = f.result()
            dims.update(s); notes += n
    total = sum(dims.values())
    fb, _ = llm.ask(cfg, "feedback", rubric("feedback", rules),
                    essay_text(sub) + "\n\n评委备注：\n" + "\n".join(notes))
    return {"quick": q, "dims": dims, "total": total, "tier": tier_of(total, rules),
            "comment": fb.get("comment", ""), "next_step": fb.get("next_step", ""),
            "_notes": notes}        # 下划线开头 = 内部字段，public_view 不会带出去


def public_view(result, rules):
    """唯一对外出口：只挑白名单字段。新加的内部字段默认不会漏出去。"""
    q = result.get("quick") or {}
    return {"total": result["total"], "tier": result["tier"],
            "dims": {rules["dims"][k]["name"]: v for k, v in result["dims"].items()},
            "quick": {k: q.get(k) for k in ("tier", "first_look", "biggest_problem")},
            "comment": result["comment"], "next_step": result["next_step"]}


# ── 自检：不调 API ────────────────────────────────────────
def _fake(role, spec, system, user):
    if role == "quick":
        return '```json\n{"tier": "有骨", "first_look": "有个真事撑着。", "biggest_problem": "结尾在升华"}\n```'
    if role == "arbiter":
        return '{"score": 10, "why": "证据更具体"}'
    if role == "feedback":
        return '前言 {"comment": "这篇立在一件具体的事上。", "next_step": "删掉最后一段'
    full = {"content": ["real", "anti_cliche"], "craft": ["voice", "structure"], "process": ["process"]}[role]
    hi = spec["provider"] == "provider_a"            # 让两次打分拉开，触发仲裁
    return json.dumps({"scores": {k: (14 if hi else 8) for k in full},
                       "evidence": {k: "第 14 页" for k in full}, "note": f"{role} 备注"},
                      ensure_ascii=False)


def selftest():
    from engine import leak_check
    llm.FAKE, llm.USAGE_ON = _fake, False
    cfg, rules = llm.load_cfg(), load_rules()
    sub = json.loads(SAMPLES.read_text(encoding="utf-8"))["essays"][0]
    fails = 0

    def check(name, ok):
        nonlocal fails
        fails += not ok
        print(("  ✅ " if ok else "  ❌ ") + name)

    r = review(cfg, rules, sub)
    pv = public_view(r, rules)
    check("快评只给档位不给分", "total" not in r["quick"] and r["quick"]["tier"] == "有骨")
    check("五个维度都有分", len(r["dims"]) == len(rules["dims"]))
    check("分数夹在满分以内", all(0 <= v <= rules["dims"][k]["full"] for k, v in r["dims"].items()))
    check("分差大的维度走了仲裁", r["dims"]["real"] == 10)
    check("被截断的 JSON 修回来了", r["comment"] and r["next_step"])
    check("对外视图没有内部字段", not any(k.startswith("_") for k in pv))
    check("对外视图没漏内规", not leak_check.find_leaks(json.dumps(pv, ensure_ascii=False), rules))
    check("评委 prompt 里确实拼进了内规",
          bool(leak_check.find_leaks(rubric("content", rules, ["real"]), rules)))
    used = [llm.ask(cfg, "quick", "s", "u")[1]["provider"] for _ in range(2)]
    check("没候选池的角色固定用配置里的模型，不轮转", used == [cfg["roles"]["quick"]["provider"]] * 2)
    spec = {"provider": "provider_x", "model": "m"}
    for _ in range(llm.FAIL_TO_TRIP):
        llm.mark_fail(spec)
    check("连续失败后熔断，不再派活", not llm.available(spec))
    pool = [{"provider": "p1", "model": "m"}, {"provider": "p2", "model": "m"}]
    picks = [llm.next_in_rotation("t", pool)["provider"] for _ in range(4)]
    check("轮转均摊而不是随机", picks == ["p1", "p2", "p1", "p2"])
    print(f"\n{'全部通过' if not fails else f'{fails} 项不通过'}")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--sample", help="samples/essays.json 里的 id")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.sample:
        cfg, rules = llm.load_cfg(), load_rules()
        sub = next(e for e in json.loads(SAMPLES.read_text(encoding="utf-8"))["essays"] if e["id"] == a.sample)
        r = review(cfg, rules, sub, on_quick=lambda q: print("快评：", json.dumps(q, ensure_ascii=False)))
        print(json.dumps(public_view(r, rules), ensure_ascii=False, indent=2))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
