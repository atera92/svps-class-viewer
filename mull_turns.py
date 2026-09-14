#!/usr/bin/env python3
"""個人配信の各試合で、配信者の1〜3ターン目の手札と盤面を撮る。

  python3 mull_turns.py VIDEO_ID [VIDEO_ID ...]     読み取り済み（stream/<ID>.json）の配信について撮る

仕組み
  1. マリガンの直後から2倍速で再生し、画面中央に大きく出る「YOUR TURN」「ENEMY TURN」を見つけて、
     自分のターンの始まりと終わり（＝次の ENEMY TURN）の時刻を記録する。
  2. 自分の1〜3ターン目それぞれについて、始まりの少し後と、終わり（ENEMY TURN）の画面を撮る。
     手札は画面下にカード名つきで並ぶので、始まりにあって終わりに無いカード ＝ そのターンに使ったカード。
     盤面（自分の場のフォロワー・アミュレット）も同じ画面に映る。
  3. 1試合ぶんを1枚の確認用画像にまとめる（cache/turns_review/<ID>/<n>.jpg）。
     使ったカードは、この画像を目で読んで stream/<ID>.json の各試合の "turns" に記入する。

YouTubeのロボット対策の画面が出たら、その場で止める（mull_stream の BLOCK_FLAG）。
"""
import json, os, sys, time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mull_scan as S, mull_stream as T

REVIEW = os.path.join(HERE, "cache", "turns_review")
YOUR = cv2.cvtColor(cv2.imread(os.path.join(HERE, "mull_ref", "pv_your_turn.png")), cv2.COLOR_BGR2GRAY)
ENEMY = cv2.cvtColor(cv2.imread(os.path.join(HERE, "mull_ref", "pv_enemy_turn.png")), cv2.COLOR_BGR2GRAY)
YOUR_POS, ENEMY_POS = (555, 450), (500, 440)
CROP = (380, 480, 1540, 1080)          # 自分の盤面と手札が入る範囲（標準の1920x1080）


def full(pl):
    """画面全体を撮り、縮小して映る配信なら標準の大きさに直す"""
    f = cv2.imdecode(np.frombuffer(pl.pg.screenshot(type="jpeg", quality=90), np.uint8), cv2.IMREAD_COLOR)
    return T.to_std(f) if T.XF else f


def banner(f):
    g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
    def near(ref, pos, pad=40):
        x, y = pos
        sr = g[max(0, y - pad):y + ref.shape[0] + pad, max(0, x - pad):x + ref.shape[1] + pad]
        return float(cv2.matchTemplate(sr, ref, cv2.TM_CCOEFF_NORMED).max())
    y, e = near(YOUR, YOUR_POS), near(ENEMY, ENEMY_POS)
    # マリガン画面など別の画面でも 0.6 前後まで上がることがあるので、はっきり高く、かつ片方だけ高いときに限る
    if max(y, e) < 0.65 or abs(y - e) < 0.3:
        return None
    return "Y" if y > e else "E"


def find_turns(pl, vid, t0, limit=720):
    """マリガン後から、自分の1〜3ターン目の（始まり, 終わり）の時刻を探す"""
    pl.goto(vid, t0)
    if pl.blocked():
        T.stop_for_block(f"{vid} {int(t0)}秒（ターン）")
        raise RuntimeError("YouTubeのロボット対策の画面")
    # 「YOUR TURN」がはっきり映るのは0.6秒ほどなので、等倍で再生しながら0.1〜0.2秒おきに見る
    seq, last, wall = [], None, time.time()
    while time.time() - wall < limit + 60:
        cur = pl.now()
        if cur > t0 + limit:
            break
        pl.unstall(cur)
        b = banner(full(pl))
        if b and (not seq or seq[-1][0] != b or cur - seq[-1][1] > 20):
            if not seq or cur - seq[-1][1] > 3:
                seq.append((b, round(cur, 1)))
        own = [s for s in seq if s[0] == "Y"]
        if len(own) >= 3 and seq[-1][0] == "E" and seq[-1][1] > own[2][1]:
            break
        time.sleep(0.03)
    turns = []
    for k, (b, ts) in enumerate(seq):
        if b != "Y":
            continue
        end = next((t for bb, t in seq[k + 1:] if bb == "E"), None)
        turns.append({"k": len(turns) + 1, "start": ts, "end": end})
        if len(turns) == 3:
            break
    return turns, seq


def shoot(pl, vid, t):
    pl.seek(t)
    time.sleep(0.6)
    return full(pl)


FONT = ImageFont.truetype(S.FONT, 26)


def review(vid, n, e, shots, path):
    x0, y0, x1, y1 = CROP
    w, h = x1 - x0, y1 - y0
    sheet = Image.new("RGB", (w * 2, (h + 40) * max(1, len(shots))), (15, 15, 15))
    dr = ImageDraw.Draw(sheet)
    for r, (k, a, b, ta, tb) in enumerate(shots):
        for c, (f, tt, lab) in enumerate(((a, ta, "始まり"), (b, tb, "終わり（相手のターン開始）"))):
            if f is None:
                continue
            im = Image.fromarray(cv2.cvtColor(f[y0:y1, x0:x1], cv2.COLOR_BGR2RGB))
            sheet.paste(im, (c * w, r * (h + 40) + 40))
            dr.text((c * w + 8, r * (h + 40) + 6), f"{e.get('side','')} 自分の{k}ターン目 {lab} {tt:.0f}s", font=FONT, fill=(255, 230, 120))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sheet.save(path, quality=82)


def run(vid):
    p = os.path.join(T.OUT_DIR, f"{vid}.json")
    d = json.load(open(p, encoding="utf-8"))
    T.set_xf(d.get("xf"))
    pl = S.Player()
    try:
        for n, e in d["events"].items():
            if e.get("status") != "ok" or e.get("turns"):
                continue
            if os.path.exists(T.BLOCK_FLAG):
                print("ロボット対策で停止中のため中止", flush=True)
                break
            t0 = e["t"] + 4
            try:
                turns, seq = find_turns(pl, vid, t0)
            except Exception as ex:
                print(f"  {vid} {n}: 失敗 {type(ex).__name__}: {ex}", flush=True)
                if os.path.exists(T.BLOCK_FLAG):
                    break
                pl.close()
                pl = S.Player()
                continue
            shots = []
            for tr in turns:
                a = shoot(pl, vid, tr["start"] + 1.5)
                b = shoot(pl, vid, tr["end"] + 0.3) if tr["end"] else None
                shots.append((tr["k"], a, b, tr["start"] + 1.5, (tr["end"] or 0) + 0.3))
            review(vid, n, e, shots, os.path.join(REVIEW, vid, f"{int(n):02d}.jpg"))
            e["turnTimes"] = turns
            json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"  {vid} {n}: 自分のターン {len(turns)} 回を撮影（バナー列 {''.join(b for b, _ in seq)}）", flush=True)
    finally:
        pl.close()


if __name__ == "__main__":
    if os.path.exists(T.BLOCK_FLAG):
        sys.exit("YouTubeのロボット対策で止まっています。確認してから cache/YOUTUBE_BLOCKED を消して再開してください。")
    for v in sys.argv[1:]:
        print(f"=== {v} {time.strftime('%H:%M')}", flush=True)
        run(v)
