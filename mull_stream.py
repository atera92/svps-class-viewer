#!/usr/bin/env python3
"""個人配信（選手本人の画面）から、配信者本人のマリガンを読み取る。

  python3 mull_stream.py VIDEO_ID --player まっつ --class ウィッチ
  python3 mull_stream.py VIDEO_ID --player まっつ --class ウィッチ --hits c.json   見つけ済みの時刻を使う

仕組み
  1. 配信全体を4秒おきに撮り、本人視点のマリガン画面（上の「CHANGE」の帯）が映る時刻を探す（並行して分担）。
  2. 見つかったマリガンごとに、少し前から等倍で再生し、0.25秒おきに撮る。
  3. 本人視点のマリガン画面は、上段 CHANGE（返すカード）・下段 KEEP（残すカード）の2段。
     カードは列を保ったまま上下に動くので、
       元の手札   ＝ 各列のカード（上下どちらにあっても同じ列）
       返したカード ＝ 確定直前に上段にあったカード
       交換後の手札 ＝ 確定後、下段に4枚そろったときのカード
  4. デッキリストが分からないので、候補はそのクラスとニュートラルの全カード（ローテーション）。
     カードの種類（フォロワー／スペル／アミュレット）で候補を絞り、別イラストの絵柄も含めて照合する。
  5. 先攻・後攻は、右下の「あなたの手番」の表示で読む。
  6. 対戦相手のクラスと勝敗は、確認用の画像（対戦開始画面・左上の戦績）から目視で入れる（自動化はしていない）。

出力
  stream/<VIDEO_ID>.json                    読み取り結果
  cache/stream_review/<VIDEO_ID>/<n>.jpg     目視確認用の画像（公開しない）
"""
import argparse, functools, json, os, sys, time
from multiprocessing import get_context

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mull_cards, mull_read as M, mull_scan as S

OUT_DIR = os.path.join(HERE, "stream")
REVIEW = os.path.join(HERE, "cache", "stream_review")
CLIP = {"x": 240, "y": 60, "width": 1600, "height": 960}
CHANGE_POS = (780, 76)                  # 「CHANGE」の帯の見本を切り出した位置
KEEP_POS = (815, 962)                   # 「KEEP」の帯の見本を切り出した位置
LABEL_BOX = (1650, 930, 1830, 1012)     # 右下「あなたの手番 先攻／後攻」の文字のあたり
MARGIN_OK = 0.12


@functools.lru_cache(maxsize=1)
def change_ref():
    return cv2.imread(os.path.join(HERE, "mull_ref", "pv_change.png"))


@functools.lru_cache(maxsize=12)
def dec(b):
    im = np.zeros((1080, 1920, 3), np.uint8)
    im[60:1020, 240:1840] = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)
    return im


def grab(pl):
    return pl.pg.screenshot(type="jpeg", quality=92, clip=CLIP)


@functools.lru_cache(maxsize=1)
def keep_ref():
    return cv2.imread(os.path.join(HERE, "mull_ref", "pv_keep.png"))


def _near(frame, t, x, y, pad=20):
    sr = frame[y - pad:y + t.shape[0] + pad, x - pad:x + t.shape[1] + pad]
    return float(cv2.matchTemplate(sr, t, cv2.TM_CCOEFF_NORMED).max())


def has_change(frame):
    """上段 CHANGE の枠が出ている（カードを選んでいる間）"""
    return _near(frame, change_ref(), *CHANGE_POS) > 0.55


def has_keep(frame):
    """下段 KEEP の枠が出ている（確定後、交換後の4枚が並んでいる間も残る）"""
    return _near(frame, keep_ref(), *KEEP_POS) > 0.55


# ---------------- 1. マリガン画面の時刻を探す ----------------
def coarse(job):
    vid, a, b = job
    pl = S.Player()
    pl.goto(vid, a)
    hits, t = [], a
    t_ref = change_ref()
    while t < b:
        pl.seek(t)
        time.sleep(0.35)
        raw = pl.pg.screenshot(type="jpeg", quality=80, clip={"x": 700, "y": 40, "width": 340, "height": 120})
        f = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if float(cv2.matchTemplate(f, t_ref, cv2.TM_CCOEFF_NORMED).max()) > 0.55:
            hits.append(round(pl.now(), 1))
        t += 4
    pl.close()
    return hits


def events_from(hits, gap=20):
    """4秒おきの当たりを、1回のマリガンごとにまとめる（最初の当たりの時刻）"""
    out = []
    for t in sorted(hits):
        if not out or t - out[-1][-1] > gap:
            out.append([t])
        else:
            out[-1].append(t)
    return [e[0] for e in out]


# ---------------- 2. マリガン1回分を撮る ----------------
def capture(pl, vid, t0):
    """戻り値 (frames, before)。frames=[(時刻, JPEG)]（マリガン画面の間）、
    before=[(時刻, JPEG)]（その前の対戦開始画面。相手クラスの確認用）"""
    pl.goto(vid, max(0, t0 - 9))
    t_q = time.time()
    while time.time() - t_q < 6 and pl.js("player.getPlaybackQuality()") != "hd1080":
        time.sleep(0.3)
    pl.seek(max(0, t0 - 9))
    frames, before, seen, wall = [], [], None, time.time()
    while time.time() - wall < 120:
        last = frames[-1][0] if frames else (before[-1][0] if before else -9)
        wait = (0.25 if frames else 1.0) - (pl.now() - last)
        if wait > 0:
            time.sleep(min(wait, 1.0))
        b, cur = grab(pl), pl.now()
        pl.unstall(cur)
        f = dec(b)
        if has_change(f) or (frames and has_keep(f)):
            seen = cur
            frames.append((cur, b))
        elif not frames:
            before.append((cur, b))
        if (seen and cur - seen > 3) or (not frames and cur > t0 + 10):
            break
    return frames, before[-8:]


# ---------------- 3. 読み取り ----------------
def gem(frame, row, i):
    """左上の緑のコストの宝石があるか（本物のカードが枠に収まっている目印）"""
    x0, y0 = M.card_origin(row, i)
    K = M.K
    h = cv2.cvtColor(frame[y0 + int(40 * K):y0 + int(140 * K), x0 + int(15 * K):x0 + int(105 * K)], cv2.COLOR_BGR2HSV)
    return float(((h[:, :, 0] >= 35) & (h[:, :, 0] <= 85) & (h[:, :, 1] >= 90) & (h[:, :, 2] >= 90)).mean())


def occupied(frame, row, i):
    return not M.is_empty(frame, row, i) and gem(frame, row, i) > 0.25


def sig(frame, row, i):
    x0, y0 = M.card_origin(row, i)
    top = frame[y0:y0 + int(60 * M.S), x0:x0 + M.W]
    bot = frame[y0 + int(250 * M.S):y0 + int(303 * M.S), x0:x0 + M.W]
    return cv2.cvtColor(np.vstack([top, bot]), cv2.COLOR_BGR2GRAY).astype(np.int16)


def best_over(items, i, cands):
    """items=[(frame, row)]。候補ごとに最高点を取り、1位と点差を返す"""
    best = {}
    for f, row in items:
        for sc, cid, _ in M.identify(f, row, i, cands):
            if cid is not None:
                best[cid] = max(best.get(cid, -9), sc)
    r = sorted(best.items(), key=lambda x: -x[1])
    if not r:
        return None, 0.0
    return r[0][0], (r[0][1] - r[1][1]) if len(r) > 1 else 1.0


def pick(xs, n):
    if len(xs) <= n:
        return xs
    idx = np.linspace(0, len(xs) - 1, n).round().astype(int)
    return [xs[k] for k in idx]


@functools.lru_cache(maxsize=1)
def side_refs():
    """本人視点の「先攻／後攻」の文字の見本（白い文字だけを取り出したもの）"""
    out = {}
    for k in ("先攻", "後攻"):
        p = os.path.join(HERE, "mull_ref", f"pv_lab_{k}.png")
        if os.path.exists(p):
            out[k] = S._bin(cv2.imread(p))
    return out


def read_side(frame):
    """右下「あなたの手番」の下の文字を読む。戻り値 (先攻 or 後攻, 確からしさ)"""
    x0, y0, x1, y1 = LABEL_BOX
    crop = S._bin(frame[y0:y1, x0:x1])
    sc = {k: float(cv2.matchTemplate(crop, t, cv2.TM_CCOEFF_NORMED).max()) for k, t in side_refs().items()}
    if len(sc) == 2:
        k = max(sc, key=sc.get)
        return k, round(abs(sc["先攻"] - sc["後攻"]), 3)
    # 見本が「後攻」しかないときは、似ているかどうかで決める
    s = sc.get("後攻", 0.0)
    return ("後攻" if s >= 0.7 else "先攻"), round(abs(s - 0.7), 3)


def analyse(frames, cands):
    n = len(frames)
    occ, sg = [], []
    for _, b in frames:
        f = dec(b)
        chg = has_change(f)          # 確定後は上段の枠が消え、その場所には盤面が映るので、上段は見ない
        occ.append({(r, i): (r == "bot" or chg) and occupied(f, r, i) for r in ("top", "bot") for i in range(4)})
        sg.append({(r, i): sig(f, r, i) for r in ("top", "bot") for i in range(4)})
    def settled(k):
        if k == 0:
            return False
        for i in range(4):
            t, b = occ[k][("top", i)], occ[k][("bot", i)]
            if t == b:                                 # 列にカードが1枚だけ収まっている
                return False
            r = "top" if t else "bot"
            if occ[k - 1][(r, i)] is False or np.abs(sg[k][(r, i)] - sg[k - 1][(r, i)]).mean() >= 10:
                return False
        return True
    st = [k for k in range(n) if settled(k)]
    if not st:
        return {"status": "unreadable", "reason": "手札が静止した場面が見つからない"}
    sel = [k for k in st if any(occ[k][("top", i)] for i in range(4))]
    if sel:
        last = sel[-1]
        ret = {i for i in range(4) if occ[last][("top", i)]}
        pre = [k for k in st if k <= last]
        post = [k for k in st if k > last and not any(occ[k][("top", i)] for i in range(4))]
    else:
        last, ret, pre, post = st[-1], set(), st, st
    orig, after, conf, flags = [], [], [], []
    for i in range(4):
        items = [(dec(frames[k][1]), "top" if occ[k][("top", i)] else "bot") for k in pick(pre, 8)]
        c, m = best_over(items, i, cands)
        orig.append(c)
        conf.append(m)
    if ret and not post:
        after = None
        flags.append("交換後の手札が映像で確認できない")
    else:
        for i in range(4):
            if i in ret:
                c, m = best_over([(dec(frames[k][1]), "bot") for k in pick(post, 10)], i, cands)
                conf.append(m)
                after.append(c)
            else:
                after.append(orig[i])
    low = [round(x, 2) for x in conf if x < MARGIN_OK]
    if low:
        flags.append(f"カードの判定があいまい（点差 {low}）")
    side, sm = read_side(dec(frames[pre[-1]][1]))
    return {"status": "ok", "t": round(frames[st[0]][0], 1), "side": side, "sideMargin": sm,
            "orig": orig, "ret": [i in ret for i in range(4)], "after": after,
            "minMargin": round(min(conf), 3), "flags": flags,
            "_keys": {"last": last, "post": post[0] if post else None}}


# ---------------- 確認用の画像 ----------------
def review_image(res, frames, before, names, path):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(S.FONT, 22)
    sheet = Image.new("RGB", (2160, 1180), (20, 20, 20))
    dr = ImageDraw.Draw(sheet)
    # 上：対戦開始画面の候補（相手クラスの確認用）と、左上の戦績
    for k, (t, b) in enumerate(before[-4:]):
        im = cv2.cvtColor(cv2.resize(dec(b)[60:1020, 240:1840], (533, 320)), cv2.COLOR_BGR2RGB)
        sheet.paste(Image.fromarray(im), (k * 540, 0))
        dr.text((k * 540 + 6, 4), f"{t:.0f}s", font=font, fill=(255, 230, 120))
    rec = cv2.cvtColor(cv2.resize(dec(frames[0][1])[60:130, 240:600], (360, 70)), cv2.COLOR_BGR2RGB)
    # 下：確定直前の2段と、交換後の下段
    keys = res["_keys"]
    y = 330
    for label, k, cards in (("確定直前（上段＝返す／下段＝残す）", keys["last"], None),
                            ("交換後", keys["post"], res["after"])):
        if k is None:
            continue
        f = dec(frames[k][1])
        crop = cv2.cvtColor(cv2.resize(f[100:1010, 240:1840], (1070, 609)), cv2.COLOR_BGR2RGB)
        x = 0 if label.startswith("確定") else 1080
        sheet.paste(Image.fromarray(crop), (x, y + 34))
        dr.text((x + 6, y), f"{res['side']} {label}  {frames[k][0]:.0f}s", font=font, fill=(255, 230, 120))
    for i, c in enumerate(res["orig"]):
        col = (255, 120, 120) if res["ret"][i] else (220, 220, 220)
        dr.text((6, 1000 + i * 26), f"元{i + 1}: {names.get(c, '?')}", font=font, fill=col)
    for i, c in enumerate(res["after"] or []):
        dr.text((1086, 1000 + i * 26), f"後{i + 1}: {names.get(c, '?')}", font=font, fill=(220, 220, 220))
    sheet.paste(Image.fromarray(rec), (1780, 1000))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sheet.save(path, quality=80)


# ---------------- 実行 ----------------
_PL = None
_CTX = {}


def _init(cls):
    M.set_layout("player")
    pool = mull_cards.class_pool(cls)
    M.register_types(pool)
    _CTX["cands"] = {c: v["name"] for c, v in pool.items()}


def work(job):
    global _PL
    vid, n, t0 = job
    try:
        if _PL is None:
            _PL = S.Player()
        frames, before = capture(_PL, vid, t0)
        if not frames:
            return n, {"status": "notfound", "t0": t0}
        res = analyse(frames, _CTX["cands"])
        res["t0"] = t0
        if res["status"] == "ok":
            review_image(res, frames, before, _CTX["cands"], os.path.join(REVIEW, vid, f"{n:02d}.jpg"))
            res.pop("_keys", None)
        return n, res
    except Exception as e:
        try:
            _PL.close()
        except Exception:
            pass
        _PL = None
        return n, {"status": "error", "t0": t0, "reason": f"{type(e).__name__}: {e}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vid")
    ap.add_argument("--player", required=True)
    ap.add_argument("--class", dest="cls", required=True)
    ap.add_argument("--deck", help="デッキの型の名前（表示用。例：魔手ウィッチ）")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--hits", nargs="*", help="見つけ済みの時刻（JSONの配列）ファイル")
    args = ap.parse_args()

    M.set_layout("player")
    if args.hits:
        hits = sum((json.load(open(p)) for p in args.hits), [])
        hits = [h[0] if isinstance(h, list) else h for h in hits]
    else:
        import re, svps_api
        h = svps_api.fetch(f"https://www.youtube.com/watch?v={args.vid}")
        length = int(re.search(r'"lengthSeconds":"(\d+)"', h).group(1))
        step = length // args.jobs + 1
        jobs = [(args.vid, a, min(length, a + step)) for a in range(0, length, step)]
        with get_context("spawn").Pool(args.jobs) as pool:
            hits = sum(pool.map(coarse, jobs), [])
    evs = events_from(hits)
    print(f"マリガン画面 {len(evs)} 回", flush=True)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{args.vid}.json")
    res = {"vid": args.vid, "player": args.player, "class": args.cls, "deck": args.deck or args.cls, "events": {}}
    with get_context("spawn").Pool(args.jobs, initializer=_init, initargs=(args.cls,)) as pool:
        for n, r in pool.imap_unordered(work, [(args.vid, n, t) for n, t in enumerate(evs, 1)]):
            res["events"][str(n)] = r
            print(f"[{n}/{len(evs)}] {r['status']} " + "; ".join(r.get("flags", [])) + (r.get("reason") or ""), flush=True)
            json.dump(res, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    finalize(res, out_path)


def finalize(res, out_path):
    """カード名の表・動画の題名と日付を付けて保存し、stream/index.json に登録する"""
    import re, svps_api
    pool_cards = mull_cards.class_pool(res["class"])
    used = {c for e in res["events"].values() for c in (e.get("orig") or []) + (e.get("after") or []) if c}
    res["cards"] = {str(c): {"name": pool_cards[c]["name"], "cost": pool_cards[c]["cost"]} for c in sorted(used)}
    res["events"] = dict(sorted(res["events"].items(), key=lambda x: int(x[0])))
    try:
        h = svps_api.fetch(f"https://www.youtube.com/watch?v={res['vid']}")
        m = re.search(r'"videoDetails":\{"videoId":"[^"]+","title":"((?:[^"\\]|\\.)*)"', h)
        res["title"] = json.loads('"' + m.group(1) + '"') if m else ""
        d = re.search(r'"(?:publishDate|uploadDate)":"(\d{4}-\d{2}-\d{2})', h)
        res["date"] = d.group(1) if d else ""
    except Exception:
        pass
    json.dump(res, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    idx_path = os.path.join(OUT_DIR, "index.json")
    idx = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else []
    idx = [x for x in idx if x != f"{res['vid']}.json"] + [f"{res['vid']}.json"]
    json.dump(idx, open(idx_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ok = sum(1 for e in res["events"].values() if e["status"] == "ok")
    print(f"読み取れたマリガン {ok}/{len(res['events'])} を {out_path} に保存しました")


if __name__ == "__main__":
    main()
