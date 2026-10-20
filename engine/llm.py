"""调模型这一层：多模型轮换 + 熔断 + 备用链 + 旁路记账 + JSON 修复。纯标准库。

比赛里每个学生每小时只有 3 次投稿机会，所以评审不能因为某一家模型打嗝就失败。
这一层只解决「稳定地拿到一个可解析的回答」，不关心评什么。

配置见 config/models.example.json。换模型只改配置，代码一行不动。
"""
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG_PATH = ROOT / "config" / "models.json"
CFG_FALLBACK = ROOT / "config" / "models.example.json"

# ⚠️ 必须带一个正经 User-Agent。urllib 默认发 "Python-urllib/3.x"，
#    有些中转站会直接 403。curl 和浏览器都通、只有 Python 不通时，先怀疑请求头。
USER_AGENT = "weyoung-ai-writing-contest/1.0"

# 测试用：设成一个函数 (role, spec, system, user) -> str，就不发真请求。
FAKE = None


class ModelError(RuntimeError):
    pass


def load_cfg():
    path = CFG_PATH if CFG_PATH.exists() else CFG_FALLBACK
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["_path"] = str(path)
    return cfg


# ── 用量记账（旁路） ──────────────────────────────────────
# 四条铁律：① 整段 try/except，坏了也不影响评分 ② 不改调用方的返回值
# ③ 只记成功的调用 ④ 失败要吼一声（空账本会被读成「没花钱」，比报错更危险）
# 记原始事实，一行一次调用；怎么汇总以后再说。用量事后无法追溯，第一天就要开。
USAGE_PATH = Path(os.environ.get("USAGE_LOG_PATH", str(ROOT / "data" / "usage.jsonl")))
USAGE_ON = os.environ.get("USAGE_LOG", "1") != "0"
_usage_lock = threading.Lock()
_usage_warned = False


def record_usage(role, spec, resp, elapsed=None):
    global _usage_warned
    if not USAGE_ON:
        return
    try:
        u = resp.get("usage") or {}
        det = u.get("completion_tokens_details") or {}
        row = {"at": round(time.time(), 3), "role": role,
               "provider": spec.get("provider"), "model": spec.get("model"),
               "prompt": u.get("prompt_tokens"), "completion": u.get("completion_tokens"),
               "total": u.get("total_tokens"), "reasoning": det.get("reasoning_tokens")}
        if elapsed is not None:
            row["sec"] = round(elapsed, 2)
        with _usage_lock:
            USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(USAGE_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 —— 故意全吞
        if not _usage_warned:
            _usage_warned = True
            print(f"[记账失败] {type(e).__name__}: {str(e)[:80]} —— 评分不受影响，但账本会缺数据")


# ── 轮换 + 熔断 ───────────────────────────────────────────
# 随机抽 ≠ 均摊：随机会让某一家连续被抽中，反而先被打爆。所以用轮转。
# 连续失败到阈值就熔断，冷却期内不再派活。
_lock = threading.Lock()
_cursor = defaultdict(int)
_fail_streak = defaultdict(int)
_cooldown_until = defaultdict(float)
FAIL_TO_TRIP = int(os.environ.get("FAIL_TO_TRIP", "3"))
COOLDOWN_SEC = int(os.environ.get("COOLDOWN_SEC", "180"))


def _key(spec):
    return f"{spec['provider']}/{spec['model']}"


def available(spec):
    return time.time() >= _cooldown_until.get(_key(spec), 0)


def mark_ok(spec):
    with _lock:
        _fail_streak[_key(spec)] = 0


def mark_fail(spec):
    k = _key(spec)
    with _lock:
        _fail_streak[k] += 1
        if _fail_streak[k] >= FAIL_TO_TRIP:
            _cooldown_until[k] = time.time() + COOLDOWN_SEC
            _fail_streak[k] = 0
            print(f"[熔断] {k} 连续失败 {FAIL_TO_TRIP} 次，冷却 {COOLDOWN_SEC}s")


def next_in_rotation(role, pool):
    """从池子里轮转取下一个不在冷却的。全在冷却返回 None。"""
    live = [p for p in pool if available(p)]
    if not live:
        return None
    with _lock:
        i = _cursor[role] % len(live)
        _cursor[role] += 1
    return live[i]


# ── 单次调用 ──────────────────────────────────────────────
def call_once(cfg, spec, role, system, user):
    """按给定的 {provider, model} 调一次 OpenAI 兼容接口，内部重试 3 次。"""
    if FAKE:
        return FAKE(role, spec, system, user)
    prov = cfg["providers"][spec["provider"]]
    key = os.environ.get(prov["key_env"], "")
    if not key:
        raise ModelError(f"环境变量 {prov['key_env']} 没设（provider={spec['provider']}）")
    payload = json.dumps({
        "model": spec["model"],
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": spec.get("temperature", 0.5),
        "max_tokens": spec.get("max_tokens", 1500),
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        prov["base"].rstrip("/") + "/chat/completions", data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": USER_AGENT})
    last = None
    for attempt in range(3):
        try:
            if attempt:
                code = getattr(last, "code", None)
                time.sleep((6 if code in (403, 429, 503) else 1.5) * attempt)
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read().decode("utf-8"))
            content = (d["choices"][0]["message"].get("content") or "").strip()
            if not content:
                # 推理模型会把 max_tokens 全花在 reasoning 上，content 就空了。当失败重试。
                raise ValueError("content 为空（推理模型的 max_tokens 可能给少了）")
            mark_ok(spec)
            record_usage(role, spec, d, time.time() - t0)
            return content
        # RemoteDisconnected / ConnectionReset 不属于 URLError，漏掉它们，
        # 一次网络抖动就会让整篇评分失败。
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError,
                OSError, KeyError, ValueError, TimeoutError) as e:
            last = e
    mark_fail(spec)
    raise ModelError(f"{role}/{_key(spec)} 调用失败：{last}")


def ask(cfg, role, system, user, rotate=True):
    """调一个角色，扛住单点故障，返回 (解析好的 JSON, 实际用的 spec)。

    顺序：① 有候选池的角色在自己池子里轮转取一个（rotate=True 时），没池子就用 roles 里配的
         ② 失败了才往下换：先换池子里其他的，再走全局备用链
    池里每一项必须是 {provider, model} 成对 —— 只换 provider 会把 A 家的模型名发给 B 家。
    """
    base = dict(cfg["roles"][role])
    pool = list(cfg.get("pools", {}).get(role) or [])
    chain = pool + list(cfg.get("fallback") or [])
    for it in chain:
        if not (isinstance(it, dict) and "provider" in it and "model" in it):
            raise ModelError(f"{role} 的候选项必须是 {{provider, model}} 成对：{it!r}")
    first = next_in_rotation(role, pool) if (rotate and pool) else None
    plan = [{**base, **first} if first else base]
    for p in chain:
        if available(p) and all(_key(p) != _key(x) for x in plan):
            plan.append({**base, **p})
    errs = []
    for spec in plan:
        try:
            return parse_json(call_once(cfg, spec, role, system, user)), spec
        except (ModelError, ValueError, OSError) as e:
            errs.append(f"{_key(spec)}: {str(e)[:80]}")
    raise ModelError(f"{role} 所有备选都失败 → " + " | ".join(errs))


# ── 解析模型吐出来的 JSON ─────────────────────────────────
def _repair(text):
    """修两种最常见的坏 JSON：字符串里没转义的半角引号；被截断缺括号。"""
    out, in_str, esc = [], False, False
    for i, ch in enumerate(text):
        if esc:
            out.append(ch); esc = False; continue
        if ch == "\\":
            out.append(ch); esc = True; continue
        if ch == '"':
            if not in_str:
                in_str = True; out.append(ch); continue
            nxt = next((c for c in text[i + 1:] if not c.isspace()), "")
            if nxt in (",", ":", "}", "]", ""):
                in_str = False; out.append(ch)
            else:
                out.append('\\"')
            continue
        out.append(ch)
    fixed = "".join(out) + ('"' if in_str else "")
    depth = fixed.count("{") - fixed.count("}")
    brk = fixed.count("[") - fixed.count("]")
    return fixed.rstrip().rstrip(",") + "]" * max(0, brk) + "}" * max(0, depth)


def parse_json(text):
    """模型爱裹 ```json、加前言、嵌中文引号、被截断 —— 四种都得扛住。"""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    cands, depth, start = [text], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            start = i if depth == 0 else start
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                cands.append(text[start:i + 1]); start = None
    if start is not None:
        cands.append(text[start:])
    for fix in (lambda s: s, _repair):
        for c in cands:
            try:
                return json.loads(fix(c))
            except ValueError:
                pass
    raise ModelError("模型没吐出可解析的 JSON：" + text[:200])
