#!/usr/bin/env python3
"""公式APIの試合結果 + YouTubeのチャプターを突き合わせて data.json を作る。

・公式API（svps_api.py）が「どのバトルで誰が何クラスを使い、どちらが勝ったか」を持っている
・YouTubeの概要欄チャプターが「ROUND1開始 / BATTLE1 / BATTLE2 ...」の時刻を持っている

この2つを突き合わせると、全バトルの開始・終了時刻とクラスが確定する。
チャプターがまだ付いていない配信は、時刻だけ未設定にして登録する
（あとで再実行すると自動で正式な時刻に置き換わる）。

  python3 build_data.py
"""
import json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import svps_api

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data.json")


def main():
    try:
        days = svps_api.load()
    except Exception as e:
        sys.exit(f"APIの取得に失敗しました（{type(e).__name__}: {e}）。"
                 f"\n既存の {OUT} はそのままにしています。")

    arcs = json.load(open(os.path.join(HERE, "archives.json"), encoding="utf-8"))
    vid_of = {a["label"]: a["videoId"] for a in arcs}

    segs, warn = [], []
    for d in days:
        vid = d["videoId"] or vid_of.get(d["label"])
        if not vid or not d["rounds"]:
            continue

        try:
            desc = svps_api.description(vid)
        except Exception as e:
            warn.append(f"{d['label']}: YouTubeの概要欄を取得できませんでした（{type(e).__name__}）")
            desc = ""
        ch = svps_api.rounds_from_chapters(svps_api.chapters(desc))
        cards = svps_api.round_cards(desc)
        has_ch = bool(ch)

        if not has_ch:
            warn.append(f"{d['label']}: 動画にチャプターが無いため、開始時刻は未設定で登録しました"
                        f"（公式がチャプターを付けたら再実行すると自動で埋まります）")
        elif len(ch) != len(d["rounds"]):
            warn.append(f"{d['label']}: 動画のROUND数({len(ch)})と試合数({len(d['rounds'])})が不一致")

        for i, rd in enumerate(d["rounds"]):
            rno = i + 1
            battles = []
            if has_ch:
                # 概要欄のチーム名で該当ラウンドを特定する（並び順に頼らない）
                want = {rd["teamA"], rd["teamB"]}
                hit = next((c for c in ch if cards.get(c[0]) and set(cards[c[0]]) == want), None)
                if hit is None and i < len(ch):
                    hit = ch[i]
                    if cards:
                        warn.append(f"{d['label']} {rd['teamA']} vs {rd['teamB']}: "
                                    f"概要欄のチーム名と一致せず、順番で対応させました")
                if hit:
                    rno, battles = hit[0], list(hit[1])

            if has_ch and battles and len(battles) != len(rd["battles"]):
                warn.append(f"{d['label']} ROUND{rno} {rd['teamA']} vs {rd['teamB']}: "
                            f"チャプター{len(battles)}件 / 実際{len(rd['battles'])}バトル "
                            f"→ 少ない方に合わせます")
            if not battles:
                battles = [(b["no"], None, None) for b in rd["battles"]]

            for (bno, st, en), b in zip(battles, rd["battles"]):
                segs.append({
                    "id": f"{vid}-R{rno}B{bno}",
                    "vid": vid, "start": st, "end": en, "pending": st is None,
                    "c1": b["c1"], "c2": b["c2"],
                    "p1": b["p1"], "p2": b["p2"],
                    "t1": rd["teamA"], "t2": rd["teamB"],
                    "battle": str(bno), "win": b["win"], "memo": f"R{rno}",
                    "deck1": b["deck1"], "deck2": b["deck2"],
                    "src": "auto",
                })

    if not segs:
        sys.exit(f"1件も生成できませんでした。既存の {OUT} はそのままにしています。")

    prev = 0
    if os.path.exists(OUT):
        try:
            prev = len(json.load(open(OUT, encoding="utf-8"))["segments"])
        except Exception:
            pass
    if prev and len(segs) < prev:
        sys.exit(f"生成できたのが {len(segs)} 件で、既存の {prev} 件より少ないため中止しました。"
                 f"\n意図した減少であれば {OUT} を手で消してから再実行してください。")

    segs.sort(key=lambda s: (s["vid"], -1 if s["start"] is None else s["start"]))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"segments": segs}, f, ensure_ascii=False, indent=1)

    pend = sum(1 for s in segs if s["pending"])
    print(f"{len(segs)} バトルを {OUT} に書き出しました"
          + (f"（うち {pend} 件は開始時刻が未設定）" if pend else "")
          + (f" / 前回 {prev} 件" if prev else "") + "\n")

    c = Counter()
    for s in segs:
        c[s["c1"]] += 1
        c[s["c2"]] += 1
    print("クラス別の登場数:")
    for k, v in c.most_common():
        print(f"  {k:<6} {v}")

    if warn:
        print("\n[要確認]")
        for w in warn:
            print("  -", w)


if __name__ == "__main__":
    main()
