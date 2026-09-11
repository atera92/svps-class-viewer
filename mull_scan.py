#!/usr/bin/env python3
"""プレミアシリーズの配信から、各バトルの両プレイヤーのマリガンを読み取る。

  python3 mull_scan.py              まだ読んでいないバトルだけ読む
  python3 mull_scan.py --redo       全部読み直す
  python3 mull_scan.py --only ID    指定したバトルだけ（data.json の id。複数可）
  python3 mull_scan.py --jobs 3     ブラウザを3つ並べて同時に読む（既定 3）

仕組み
  1. ヘッドレスのChromeでYouTubeの埋め込みプレイヤーを開き、バトル開始時刻から再生して画面を撮る。
     動画ファイルは保存しない（画面を撮って、その場で読むだけ）。
  2. 「KEEP」の帯が映っている間をマリガン画面とみなす。
  3. 配信のマリガン画面は、上段が GUEST、下段が OWNER の手札4枚。
     交換を確定すると、返したカードの枠がいったん空になり、同じ位置に新しいカードが入る。
     → 空になった枠 ＝ 返したカード。空になる前の4枚 ＝ 元の手札。埋まった後の4枚 ＝ 交換後の手札。
  4. カード名は mull_read.py で、そのプレイヤーのデッキ（公式のデッキリスト）の中から当てる。
  5. 右側の「先攻／後攻」の表示を見本と照合して、どちらが先攻かを決める。

出力
  mull_pro.json                 読み取り結果（サイトが読む）
  cache/mull_review/<id>.jpg    目視確認用の画像（公開しない）
"""
import argparse, functools, http.server, json, os, sys, threading, time
from multiprocessing import get_context

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mull_cards, mull_read as M

OUT = os.path.join(HERE, "mull_pro.json")
REVIEW = os.path.join(HERE, "cache", "mull_review")
REF = os.path.join(HERE, "mull_ref")

KEEP_POS = {"top": (960, 138), "bot": (960, 905)}       # 「KEEP」の帯の見本を切り出した位置
LAB_POS = {"top": (1712, 232), "bot": (1712, 897)}      # 「先攻／後攻」の文字の位置
MARGIN_OK = 0.12                                         # 1位と2位の点差がこれ未満なら要確認


# ---------------- 見本との照合 ----------------
def _ref(name):
    return cv2.imread(os.path.join(REF, name + ".png"))


REFS = {}


def refs():
    if not REFS:
        REFS["keep_top"], REFS["keep_bot"] = _ref("keep_top"), _ref("keep_bot")
        # 見本の上段は「後攻」、下段は「先攻」
        REFS["後攻"], REFS["先攻"] = _bin(_ref("lab_top")), _bin(_ref("lab_bot"))
    return REFS


def _bin(im):
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    return np.where(g > 170, 255, 0).astype(np.uint8)


def _ncc(frame, tp, x, y, pad=12):
    h, w = tp.shape[:2]
    sr = frame[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
    return float(cv2.matchTemplate(sr, tp, cv2.TM_CCOEFF_NORMED).max())


def is_mulligan(frame):
    r = refs()
    return max(_ncc(frame, r["keep_top"], *KEEP_POS["top"]),
               _ncc(frame, r["keep_bot"], *KEEP_POS["bot"])) > 0.6


def read_side(frame, row):
    """その段の「先攻／後攻」を読む。戻り値 (先攻 or 後攻, 点差)"""
    r = refs()
    x, y = LAB_POS[row]
    crop = _bin(frame[y - 10:y + 56, x - 10:x + 98])
    s1 = float(cv2.matchTemplate(crop, r["先攻"], cv2.TM_CCOEFF_NORMED).max())
    s2 = float(cv2.matchTemplate(crop, r["後攻"], cv2.TM_CCOEFF_NORMED).max())
    return ("先攻" if s1 > s2 else "後攻"), abs(s1 - s2)


# ---------------- 撮影 ----------------
def _serve():
    """mull_player.html を配るための一時サーバー（空いているポートで立てる）"""
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    h = functools.partial(Quiet, directory=HERE)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


class Player:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self.port = _serve()
        self.pw = sync_playwright().start()
        self.br = self.pw.chromium.launch(
            channel="chrome", headless=True,
            args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"])
        self.pg = self.br.new_page(viewport={"width": 1920, "height": 1080})
        self.vid = None

    def js(self, s):
        return self.pg.evaluate(s)

    def goto(self, vid, t):
        if vid != self.vid:
            self.pg.goto(f"http://127.0.0.1:{self.port}/mull_player.html?v={vid}&t={int(t)}")
            self.pg.wait_for_function("window.ready===true", timeout=60000)
            self.vid = vid
        self.seek(t)

    def seek(self, t):
        self.js(f"player.seekTo({t},true);player.playVideo()")
        t0 = time.time()
        while time.time() - t0 < 30:
            st, cur = self.js("[player.getPlayerState(),player.getCurrentTime()]")
            if st == 1 and abs(cur - t) < 3:
                return
            time.sleep(0.2)

    def rate(self, r):
        self.js(f"player.setPlaybackRate({r})")

    def now(self):
        return float(self.js("player.getCurrentTime()"))

    def grab(self):
        """必要な範囲（手札・KEEPの帯・先攻後攻の表示）だけ JPEG で撮る。画像に戻すのは dec()"""
        return self.pg.screenshot(type="jpeg", quality=92, clip={"x": 440, "y": 120, "width": 1400, "height": 840})

    def unstall(self, cur):
        """再生が止まったまま進まないときは再生し直す"""
        if getattr(self, "_last", None) != cur:
            self._last, self._since = cur, time.time()
        elif time.time() - self._since > 4:
            self.js(f"player.seekTo({cur + 1},true);player.playVideo()")
            self._since = time.time()

    def close(self):
        self.br.close()
        self.pw.stop()


@functools.lru_cache(maxsize=12)
def dec(b):
    """grab() の JPEG を、配信画面（1920x1080）と同じ座標の画像に戻す。
    フレームは JPEG のまま持ち、使うときだけ戻す（メモリを数GB使わないため）"""
    im = np.zeros((1080, 1920, 3), np.uint8)
    im[120:960, 440:1840] = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)
    return im


def capture(pl, seg):
    """マリガン画面の間のフレームを集める。戻り値 [(時刻, JPEG), ...]"""
    start = seg["start"]
    limit = min(start + 240, seg["end"] or start + 240)
    pl.goto(seg["vid"], start + 5)
    # hd1080 に上がるまで少し待つ（最初の数秒は画質が低い）
    t0 = time.time()
    while time.time() - t0 < 8 and pl.js("player.getPlaybackQuality()") != "hd1080":
        time.sleep(0.3)
    pl.rate(2)
    found, wall = None, time.time()
    while time.time() - wall < 240:                 # 再生が止まっても抜けられるよう実時間でも区切る
        cur = pl.now()
        if cur > limit:
            break
        pl.unstall(cur)
        if is_mulligan(dec(pl.grab())):
            found = cur
            break
        time.sleep(0.3)                              # 2倍速なので約0.6秒おきに確認すれば足りる
    if found is None:
        return []
    pl.rate(1)
    pl.seek(max(start, found - 4))
    # 0.25秒おきに保存し、KEEPの帯が3秒続けて見えなくなったら終わり
    # （カードの移動で帯が一瞬隠れることがあるので、1回見えないだけでは終わらせない）
    frames, seen, wall = [], None, time.time()
    while time.time() - wall < 240:
        if frames:                                   # 撮りすぎるとCPUを食うので、0.25秒おきに撮る
            wait = 0.25 - (pl.now() - frames[-1][0])
            if wait > 0:
                time.sleep(wait)
        b, cur = pl.grab(), pl.now()
        pl.unstall(cur)
        if is_mulligan(dec(b)):
            seen = cur
            if not frames or cur - frames[-1][0] >= 0.25:
                frames.append((cur, b))
        if (seen and cur - seen > 3) or cur > found + 150 or (not frames and cur > found + 10):
            break
    return frames


# ---------------- 読み取り ----------------
def best_over(frames, row, i, cands):
    """複数フレームで照合し、候補ごとの最高点で順位を付ける"""
    best = {}
    for _, b in frames:
        for sc, cid, _ in M.identify(dec(b), row, i, cands):
            best[cid] = max(best.get(cid, -9), sc)
    ranked = sorted(best.items(), key=lambda x: -x[1])
    (c1, s1), (c2, s2) = ranked[0], (ranked[1] if len(ranked) > 1 else (None, s1 - 1))
    return c1, s1 - s2, best


def pick(frames, n=8):
    if len(frames) <= n:
        return frames
    idx = np.linspace(0, len(frames) - 1, n).round().astype(int)
    return [frames[i] for i in idx]


def _sig(f, row, i):
    """カードの上部（コスト・名前）と下部（攻撃力・体力）。交換マークが重なる中央は使わない"""
    x0, y0 = M.card_origin(row, i)
    top = f[y0:y0 + 60, x0:x0 + M.W]
    bot = f[y0 + 250:y0 + 303, x0:x0 + M.W]
    return cv2.cvtColor(np.vstack([top, bot]), cv2.COLOR_BGR2GRAY).astype(np.int16)


def analyse_row(frames, row):
    """その段について、交換前の区間・空いた枠・交換後の区間を切り分ける。

    配られる途中のカードは動いているので「静止した4枚」だけを手札として扱う。
    静止 ＝ 前のフレームとカードの上部・下部がほぼ同じ。
    """
    n = len(frames)
    empt, sig = [], []
    for _, b in frames:
        f = dec(b)
        empt.append({i for i in range(4) if M.is_empty(f, row, i)})
        sig.append([_sig(f, row, i) for i in range(4)])
    still = [k > 0 and not empt[k] and
             all(np.abs(sig[k][i] - sig[k - 1][i]).mean() < 10 for i in range(4)) for k in range(n)]
    p = next((k for k in range(n - 1) if still[k] and still[k + 1]), None)
    if p is None:
        return None
    e0 = next((k for k in range(p, n) if empt[k]), None)
    if e0 is None:
        pre = [frames[k] for k in range(p, n) if still[k]]
        return {"pre": pre, "ret": set(), "post": pre, "t": frames[p][0], "swapped": False}
    # 返したカードが抜ける・新しいカードが入る動きは1枚ずつずれることがあるので、
    # 交換後の4枚が静止するまでの間に一度でも空になった枠をすべて数える
    qs = next((k for k in range(e0, n - 1) if still[k] and still[k + 1]), None)
    ret = set().union(*empt[e0:qs if qs is not None else n])
    # 後から確定した側は、交換後の4枚が1秒ほどしか映らないので、静止を待たずに使う
    # （入ってくる途中のフレームは点が低くなるだけで、複数フレームの最高点で選べば影響しない）
    q = next((k for k in range(e0, n) if not empt[k]), None)
    pre = [frames[k] for k in range(p, e0) if still[k]]
    post = [frames[k] for k in range(q, n) if not empt[k]] if q is not None else []
    return {"pre": pre, "ret": ret, "post": post, "t": frames[e0 - 1][0], "swapped": True}


def marked(pre, row):
    """確定直前のフレームで交換マークが付いている枠。
    絵柄が青いカードを誤って拾わないよう、手札が配られた直後との差も見る"""
    last = dec(pre[-1][1])
    out = set()
    for i in range(4):
        lv0 = min(M.mark_level(dec(b), row, i) for _, b in pick(pre, 5))
        lv = M.mark_level(last, row, i)
        if (lv >= 0.38 and lv - lv0 >= 0.2) or lv >= 0.55:
            out.add(i)
    return out


def analyse(seg, frames):
    d1 = mull_cards.deck(mull_cards.deck_hash(seg["deck1"]))
    d2 = mull_cards.deck(mull_cards.deck_hash(seg["deck2"]))
    for d in (d1, d2):
        for cid, c in d.items():
            mull_cards.images(cid, c)
        M.register_types(d)
    names = {**{c: v["name"] for c, v in d2.items()}, **{c: v["name"] for c, v in d1.items()}}
    rows = {}
    for row in ("top", "bot"):
        a = analyse_row(frames, row)
        if not a or not a["pre"]:
            return {"status": "unreadable", "reason": f"{row}段の手札が読めない"}
        pre = pick(a["pre"])
        # どちらのプレイヤーの手札かを、デッキとの合い方で決める
        a["fit"] = {k: sum(M.identify(dec(pre[-1][1]), row, i, d)[0][0] for i in range(4))
                    for k, d in (("1", d1), ("2", d2))}
        rows[row] = a

    # 上段・下段をプレイヤーに割り当てる（合計点が高くなる組み合わせ）
    straight = rows["top"]["fit"]["1"] + rows["bot"]["fit"]["2"]
    cross = rows["top"]["fit"]["2"] + rows["bot"]["fit"]["1"]
    owner = {"top": "1", "bot": "2"} if straight >= cross else {"top": "2", "bot": "1"}
    flags = []
    if abs(straight - cross) < 0.5:
        flags.append("上段・下段とプレイヤーの対応があいまい（デッキが似ている）")

    last_pre = dec(rows["top"]["pre"][-1][1])
    side_top, m_top = read_side(last_pre, "top")
    side_bot, m_bot = read_side(last_pre, "bot")
    if side_top == side_bot:
        flags.append("先攻／後攻の表示を読み違えた可能性")
        side_bot = "後攻" if side_top == "先攻" else "先攻"

    out = []
    for row in ("top", "bot"):
        a, k = rows[row], owner[row]
        deck = d1 if k == "1" else d2
        cands = {c: deck[c]["name"] for c in deck}
        pre, post = pick(a["pre"]), pick(a["post"], 12)
        orig, conf = [], []
        for i in range(4):
            c, m, _ = best_over(pre, row, i, cands)
            orig.append(c)
            conf.append(m)
        # 返したカード ＝ 枠が空になった ∪ 確定直前に交換マークが付いていた ∪ 交換の前後でカードが変わった
        mk = marked(a["pre"], row)
        ret = set(a["ret"]) | mk
        after, how = None, []
        if a["swapped"] and post:
            after = []
            for i in range(4):
                c, m, sc = best_over(post, row, i, cands)
                changed = c != orig[i] and sc[c] - sc.get(orig[i], -9) > 0.25
                if changed and i not in ret:
                    ret.add(i)
                    how.append(f"枠{i + 1}はカードの変化で判定")
                if i in ret:
                    conf.append(m)
                after.append(c if i in ret else orig[i])
        elif not a["swapped"] and not mk:
            after = list(orig)
        if mk != set(a["ret"]) and a["swapped"]:
            how.append(f"空になった枠{sorted(x + 1 for x in a['ret'])}とマーク{sorted(x + 1 for x in mk)}が不一致")
        a["ret"] = ret
        low = [round(x, 2) for x in conf if x < MARGIN_OK]
        out.append({
            "row": row, "who": k,
            "player": seg["p" + k], "team": seg["t" + k], "class": seg["c" + k],
            "side": side_top if row == "top" else side_bot,
            "orig": orig, "ret": [i in a["ret"] for i in range(4)], "after": after,
            "t": round(a["t"], 1), "minMargin": round(min(conf), 3),
            "flags": (["交換後の手札が映像で確認できない"] if after is None else [])
                     + ([f"カードの判定があいまい（点差 {low}）"] if low else []) + how,
        })
    return {"status": "ok", "vid": seg["vid"], "t": round(min(r["t"] for r in out), 1),
            "sideMargin": round(min(m_top, m_bot), 3), "flags": flags, "rows": out,
            "names": {str(c): n for c, n in names.items()}}


# ---------------- 確認用の画像 ----------------
FONT = "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc"


def review_image(seg, res, frames, path):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(FONT, 22)
    names = res["names"]
    tiles = []
    for r in res["rows"]:
        a = analyse_row(frames, r["row"])
        y0 = M.YS[r["row"]] - 165
        for label, src, cards in (("元の手札", a["pre"][-1][1], r["orig"]),
                                  ("交換後", (a["post"] or a["pre"])[-1][1], r["after"] or [None] * 4)):
            crop = cv2.cvtColor(dec(src)[y0:y0 + 330, 480:1560], cv2.COLOR_BGR2RGB)
            im = Image.new("RGB", (1080, 400), (20, 20, 20))
            im.paste(Image.fromarray(crop), (0, 36))
            dr = ImageDraw.Draw(im)
            dr.text((8, 4), f"{r['side']} {r['player']}（{r['class']}） {label}", font=font, fill=(255, 230, 120))
            for i, c in enumerate(cards):
                col = (255, 120, 120) if (label == "元の手札" and r["ret"][i]) else (220, 220, 220)
                dr.text((i * 265 + 20, 368), (names.get(str(c), "?") if c else "?")[:11], font=font, fill=col)
            tiles.append(im)
    sheet = Image.new("RGB", (2160, 800))
    for k, t in enumerate(tiles):
        sheet.paste(t, ((k % 2) * 1080, (k // 2) * 400))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sheet.convert("RGB").save(path, quality=80)


# ---------------- 実行 ----------------
_PL = None


def work(seg):
    global _PL
    try:
        if _PL is None:
            _PL = Player()
        frames = capture(_PL, seg)
        if not frames:
            return seg["id"], {"status": "notfound", "vid": seg["vid"]}
        res = analyse(seg, frames)
        if res["status"] == "ok":
            review_image(seg, res, frames, os.path.join(REVIEW, seg["id"] + ".jpg"))
        return seg["id"], res
    except Exception as e:
        try:
            _PL.close()
        except Exception:
            pass
        _PL = None
        return seg["id"], {"status": "error", "vid": seg["vid"], "reason": f"{type(e).__name__}: {e}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--finalize", action="store_true", help="読み取りはせず、保存形式だけ整える")
    args = ap.parse_args()

    segs = json.load(open(os.path.join(HERE, "data.json"), encoding="utf-8"))["segments"]
    res = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {"battles": {}}
    if args.finalize:
        return finalize(res, segs)
    todo = [s for s in segs if s.get("start") is not None and s.get("deck1") and s.get("deck2")]
    if args.only:
        todo = [s for s in todo if s["id"] in args.only]
    elif not args.redo:
        todo = [s for s in todo if res["battles"].get(s["id"], {}).get("status") != "ok"]
    print(f"{len(todo)} バトルを読みます（同時 {args.jobs}）", flush=True)

    ctx = get_context("spawn")
    n = 0
    with ctx.Pool(args.jobs) as pool:
        for sid, r in pool.imap_unordered(work, todo):
            n += 1
            r.pop("names", None)
            res["battles"][sid] = r
            res["updatedAt"] = time.strftime("%Y-%m-%d %H:%M")
            json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            fl = r.get("flags", []) + [f for x in r.get("rows", []) for f in x["flags"]]
            print(f"[{n}/{len(todo)}] {sid}: {r['status']}" + (f"  要確認: {'; '.join(fl)}" if fl else "")
                  + (f"  {r.get('reason')}" if r.get("reason") else ""), flush=True)
    finalize(res, segs)


def finalize(res, segs):
    """サイトが使うカード名・コストの表を付けて保存する（バトルごとの names は重複なので外す）"""
    cards = {}
    for s in segs:
        for k in ("deck1", "deck2"):
            if s.get(k):
                for cid, c in mull_cards.deck(mull_cards.deck_hash(s[k])).items():
                    cards[str(cid)] = {"name": c["name"], "cost": c["cost"]}
    used = {str(c) for b in res["battles"].values() for r in b.get("rows", [])
            for c in r["orig"] + (r["after"] or []) if c}
    res["cards"] = {k: v for k, v in sorted(cards.items()) if k in used}
    for b in res["battles"].values():
        b.pop("names", None)
    ok = sum(1 for b in res["battles"].values() if b.get("status") == "ok")
    res["summary"] = {"battles": len(res["battles"]), "ok": ok}
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"読み取れたバトル {ok}/{len(res['battles'])} を {OUT} に保存しました")


if __name__ == "__main__":
    main()
