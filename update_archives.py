#!/usr/bin/env python3
"""公式APIからアーカイブ一覧（archives.json）を作る。

新しい節が配信されたら再実行するだけでよい:  python3 update_archives.py

取得に失敗したり0件だった場合は、既存の archives.json を上書きせずに終了する。
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import svps_api

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "archives.json")

# 公式サイトに配信リンクが載らない回の手動補完（節名 -> videoId）
MANUAL = {
    "第4節・前半": "x6PeWkSyyV4",
    "第4節・後半": "t5f1hh-W48E",
}


def main():
    try:
        days = svps_api.load()
    except Exception as e:
        sys.exit(f"APIの取得に失敗しました（{type(e).__name__}: {e}）。"
                 f"\n既存の {OUT} はそのままにしています。")

    archives = []
    for d in days:
        vid = d["videoId"] or MANUAL.get(d["label"])
        if not vid or not d["rounds"]:
            continue
        archives.append({
            "videoId": vid,
            "label": d["label"],
            "date": d["date"],
            "cards": [{"round": f"ROUND {i}", "teamA": r["teamA"], "teamB": r["teamB"],
                       "scoreA": str(r["scoreA"]), "scoreB": str(r["scoreB"])}
                      for i, r in enumerate(d["rounds"], 1)],
        })

    if not archives:
        sys.exit(f"1件も取得できませんでした。サイト側の仕様変更が疑われます。"
                 f"\n既存の {OUT} はそのままにしています。")

    prev = 0
    if os.path.exists(OUT):
        try:
            prev = len(json.load(open(OUT, encoding="utf-8")))
        except Exception:
            pass
    if prev and len(archives) < prev:
        sys.exit(f"取得できたのが {len(archives)} 件で、既存の {prev} 件より少ないため中止しました。"
                 f"\n意図した減少であれば {OUT} を手で消してから再実行してください。")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(archives, f, ensure_ascii=False, indent=1)

    print(f"{len(archives)} 件を {OUT} に書き出しました" + (f"（前回 {prev} 件）" if prev else ""))
    for a in archives:
        print(" ", a["label"], a["date"], a["videoId"],
              " / ".join(f'{c["teamA"]} {c["scoreA"]}-{c["scoreB"]} {c["teamB"]}' for c in a["cards"]))


if __name__ == "__main__":
    main()
