#!/usr/bin/env python3
"""内规泄露检查：内规里的词一个都不许出现在对外的东西里。

内规 = config/rules.*.json 里 internal 下的所有文字。它们只拼进评委的 system prompt。
对外的东西 = 给学生看的页面、接口返回、评语、llms.txt、参赛 skill……

用法：
    python3 -m engine.leak_check site/ public/llms.txt     # 扫文件或目录，有命中就退出码 1
"""
import json
import sys
from pathlib import Path


def secret_terms(rules):
    """把 internal 里所有字符串摊平成词表（跳过 _ 开头的说明字段）。"""
    out = []

    def walk(x):
        if isinstance(x, str):
            if len(x.strip()) >= 2:
                out.append(x.strip())
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, dict):
            for k, v in x.items():
                if not k.startswith("_"):
                    walk(v)

    walk(rules.get("internal", {}))
    return out


def find_leaks(text, rules):
    # 模型和人都爱改空格写法（「甲·乙」/「甲 · 乙」），比较前先去空格
    flat = "".join(text.split())
    return [t for t in secret_terms(rules) if "".join(t.split()) in flat]


def main(paths):
    from engine.review import load_rules
    rules, bad = load_rules(), 0
    files = [f for p in map(Path, paths) for f in ([p] if p.is_file() else p.rglob("*")) if f.is_file()]
    for f in files:
        try:
            hits = find_leaks(f.read_text(encoding="utf-8"), rules)
        except UnicodeDecodeError:
            continue
        if hits:
            bad += 1
            print(f"❌ {f}: {hits}")
    print(f"扫了 {len(files)} 个文件，{'没有泄露' if not bad else f'{bad} 个文件漏了内规'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["."]))
