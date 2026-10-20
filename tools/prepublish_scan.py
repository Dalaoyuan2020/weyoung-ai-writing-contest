#!/usr/bin/env python3
"""发布前安检：公开仓库推上去之前跑一遍，必须零命中。

查五类东西：
  1. 常见 key 格式（sk- / ghp_ / AKIA / hf_ / xai- …）和「KEY=长串」
  2. 公网 IP、手机号、邮箱
  3. 内规泄露（engine.leak_check）
  4. 自己的禁用词表：真名、口令、服务器地址、内部路径……
     放在仓库外面（默认 ~/.prepublish_denylist.txt，一行一个），词表本身绝不进仓库
  5. 不该出现的文件：数据库、.env、真实配置

用法：python3 tools/prepublish_scan.py [--denylist 路径]
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PATTERNS = {
    "key 格式": r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
               r"|hf_[A-Za-z0-9]{20,}|xai-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{30,}|ntn_[A-Za-z0-9]{20,})",
    "KEY=长串": r"(?i)(api_?key|token|secret|password)\s*[=:]\s*['\"]?[A-Za-z0-9._-]{20,}",
    "公网 IP": r"\b(?!127\.|0\.0\.0\.0|10\.|192\.168\.)(\d{1,3}\.){3}\d{1,3}\b",
    "手机号": r"(?<!\d)1[3-9]\d{9}(?!\d)",
    "邮箱": r"[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
}
BAD_FILES = re.compile(r"(\.db|\.sqlite|\.env|models\.json|rules\.private\.json|usage\.jsonl)$")
SKIP = {"tools/prepublish_scan.py"}          # 本文件里写着这些正则，自己不扫自己


def tracked_files():
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--denylist", default=str(Path.home() / ".prepublish_denylist.txt"))
    a = ap.parse_args()
    deny = []
    p = Path(a.denylist)
    if p.exists():
        deny = [w.strip() for w in p.read_text(encoding="utf-8").splitlines()
                if w.strip() and not w.startswith("#")]
    else:
        print(f"⚠️ 没找到禁用词表 {p}，第 4 类没查")

    from engine.leak_check import find_leaks
    from engine.review import load_rules
    rules = load_rules()

    hits = 0
    files = tracked_files()
    for f in files:
        if BAD_FILES.search(f):
            print(f"❌ 不该进仓库的文件：{f}"); hits += 1
        if f in SKIP:
            continue
        try:
            text = (ROOT / f).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for name, pat in PATTERNS.items():
            for m in re.finditer(pat, text):
                print(f"❌ {f}: {name} → {m.group(0)[:6]}…"); hits += 1
        for w in deny:
            if w in text:
                print(f"❌ {f}: 禁用词表第 {deny.index(w) + 1} 个词"); hits += 1
        # 内规只该出现在评委模板里（且是示例），别处出现就是泄露
        if not f.startswith(("config/rules", "rubrics/")):
            for t in find_leaks(text, rules):
                print(f"❌ {f}: 内规词「{t}」"); hits += 1
    print(f"\n扫了 {len(files)} 个文件，禁用词 {len(deny)} 个 → "
          f"{'✅ 零命中，可以发布' if not hits else f'❌ {hits} 处命中，先处理再发布'}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
