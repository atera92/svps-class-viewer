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
import argparse, functools, json, os, sys, threading, time
from multiprocessing import get_context

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mull_cards, mull_read as M, mull_scan as S

OUT_DIR = os.path.join(HERE, "stream")
REVIEW = os.path.join(HERE, "cache", "stream_review")
CLIP = {"x": 150, "y": 60, "width": 1690, "height": 960}   # 対戦開始画面の左下にある自分のCRまで入る範囲
VS_POS = (820, 420)                     # 対戦開始画面の「VS」の見本を切り出した位置
SELF_CR = (150, 770, 420, 830)          # 対戦開始画面：配信者のCR
SELF_SIDE = (540, 670, 840, 810)        # 対戦開始画面：配信者側の大きな「先攻／後攻」
OPP_INFO = (1400, 680, 1840, 830)       # 対戦開始画面：相手のクラス名とCR
CHANGE_POS = (780, 76)                  # 「CHANGE」の帯の見本を切り出した位置
KEEP_POS = (815, 962)                   # 「KEEP」の帯の見本を切り出した位置
LABEL_BOX = (1650, 930, 1830, 1012)     # 右下「あなたの手番 先攻／後攻」の文字のあたり
MARGIN_OK = 0.12
BLOCK_FLAG = os.path.join(HERE, "cache", "YOUTUBE_BLOCKED")   # これがあれば、YouTubeへの自動アクセスをしない


def stop_for_block(where):
    """ロボット対策の確認画面が出た：印のファイルを作り、以後の読み取りを止める（回避はしない）"""
    os.makedirs(os.path.dirname(BLOCK_FLAG), exist_ok=True)
    open(BLOCK_FLAG, "w").write(f"{time.strftime('%Y-%m-%d %H:%M')} {where}\n")
    print(f"!!! YouTubeのロボット対策の画面が出たため中止しました（{where}）", flush=True)


@functools.lru_cache(maxsize=1)
def change_ref():
    return cv2.imread(os.path.join(HERE, "mull_ref", "pv_change.png"))


# 配信ごとのゲーム画面の縮尺と位置。配信画面上の点 ＝ s × 標準の点 ＋ (dx, dy)。
# ゲーム画面を全面に映している配信は None（そのまま読む）。calibrate() で測る
XF = None


def set_xf(xf):
    global XF
    XF = xf
    dec.cache_clear()


def to_std(f):
    """配信画面を、標準の位置・大きさ（ゲーム画面が1920x1080の全面）に直す"""
    s, dx, dy = XF["s"], XF["dx"], XF["dy"]
    A = np.float32([[1 / s, 0, -dx / s], [0, 1 / s, -dy / s]])
    return cv2.warpAffine(f, A, (1920, 1080))


@functools.lru_cache(maxsize=12)
def dec(b):
    if XF:
        return to_std(cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR))
    im = np.zeros((1080, 1920, 3), np.uint8)
    x, y = CLIP["x"], CLIP["y"]
    im[y:y + CLIP["height"], x:x + CLIP["width"]] = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)
    return im


def calibrate_safe(vid, length, tries=3):
    """calibrate を、ブラウザが止まったらやり直す形で呼ぶ"""
    state = {"t": 20, "tick": time.time()}
    for _ in range(tries):
        dog = _Dog(state)
        pl = None
        try:
            pl = S.Player()
            return calibrate(pl, vid, length, state=state)
        except Exception:
            pass
        finally:
            dog.stop()
            try:
                pl and pl.close()
            except Exception:
                pass
    return None, {"error": "ブラウザが応答せず、画面の縮尺を測れなかった（補正なしで読む）"}


def calibrate(pl, vid, length, limit=1800, state=None):
    """最初のマリガン画面を探し、「KEEP」の帯の大きさと位置から、ゲーム画面の縮尺と位置を測る"""
    ref = keep_ref()
    state = state if state is not None else {"t": 20}
    t = state["t"]
    pl.goto(vid, t)
    while t < min(length, limit):
        state["t"], state["tick"] = t, time.time()
        pl.seek(t)
        time.sleep(0.35)
        f = cv2.imdecode(np.frombuffer(pl.pg.screenshot(type="jpeg", quality=80), np.uint8), cv2.IMREAD_COLOR)
        g = cv2.resize(f, None, fx=0.5, fy=0.5)
        best = max((float(cv2.matchTemplate(g, cv2.resize(ref, None, fx=s * 0.5, fy=s * 0.5),
                                             cv2.TM_CCOEFF_NORMED).max()), s)
                   for s in np.arange(0.70, 1.06, 0.05))
        if best[0] > 0.75:
            res = []
            for s in np.arange(best[1] - 0.05, best[1] + 0.051, 0.01):
                r = cv2.matchTemplate(f, cv2.resize(ref, None, fx=s, fy=s), cv2.TM_CCOEFF_NORMED)
                _, mx, _, loc = cv2.minMaxLoc(r)
                res.append((mx, s, loc))
            mx, s, (x, y) = max(res)
            xf = {"s": round(float(s), 3), "dx": round(x - s * KEEP_POS[0], 1), "dy": round(y - s * KEEP_POS[1], 1),
                  "score": round(float(mx), 3), "at": t}
            if abs(xf["s"] - 1) < 0.015 and abs(xf["dx"]) < 8 and abs(xf["dy"]) < 8:
                return None, xf                        # ゲーム画面が全面：そのまま読める
            return xf, xf
        t += 5
    return None, {"error": "最初の30分にマリガン画面が見つからない"}


@functools.lru_cache(maxsize=1)
def vs_ref():
    return cv2.imread(os.path.join(HERE, "mull_ref", "pv_vs.png"))


def vs_score(frame):
    """対戦開始画面（中央の大きな VS）らしさ"""
    return _near(frame, vs_ref(), *VS_POS, pad=30)


def grab(pl):
    if XF:                                   # 縮小・移動して映っている配信は、全体を撮って標準の位置に直す
        return pl.pg.screenshot(type="jpeg", quality=92)
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
STEP = 5          # 何秒おきに撮って探すか。マリガン画面（CHANGE か KEEP の帯）は短くても8秒ほど映る


def coarse(job, _retry=2):
    """担当区間を STEP 秒おきに撮り、CHANGE か KEEP の帯が映っている時刻を返す。
    ブラウザが応答しなくなったら（WATCHDOG秒 進まなければ）止めて、続きからやり直す"""
    vid, a, b, xf = job
    hits = []
    for attempt in range(_retry + 1):
        state = {"t": a, "tick": time.time()}
        dog = _Dog(state)
        try:
            hits += _coarse(vid, state, b, xf)
            return hits
        except Exception:
            a = state["t"]                           # 止まったところから続ける
        finally:
            dog.stop()
    return hits


class _Dog:
    """state["tick"] が WATCHDOG 秒更新されなければ、このプロセスのブラウザを止める"""
    def __init__(self, state):
        self.state, self.alive = state, True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.alive:
            time.sleep(5)
            if self.alive and time.time() - self.state["tick"] > WATCHDOG:
                _kill_children()
                self.state["tick"] = time.time()

    def stop(self):
        self.alive = False


def _coarse(vid, state, b, xf):
    set_xf(xf)
    a = state["t"]
    pl = S.Player()
    pl.goto(vid, a)
    hits, t = [], a
    cx, cy = 760, 56                          # 撮る範囲（帯2本を縦に含む細長い範囲）の左上
    c_ref, k_ref = change_ref(), keep_ref()
    n_seen = 0
    while t < b:
        if os.path.exists(BLOCK_FLAG):
            break
        n_seen += 1
        if n_seen % 40 == 1 and pl.blocked():
            stop_for_block(f"{vid} {int(t)}秒")
            break
        pl.seek(t)
        time.sleep(0.35)
        if XF:
            full = to_std(cv2.imdecode(np.frombuffer(pl.pg.screenshot(type="jpeg", quality=75), np.uint8),
                                       cv2.IMREAD_COLOR))
        else:
            raw = pl.pg.screenshot(type="jpeg", quality=80, clip={"x": cx, "y": cy, "width": 220, "height": 960})
            f = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            full = np.zeros((1080, 1920, 3), np.uint8)
            full[cy:cy + f.shape[0], cx:cx + f.shape[1]] = f
        if has_change(full) or _near(full, k_ref, *KEEP_POS) > 0.55:
            hits.append(round(pl.now(), 1))
        t += STEP
        state["t"], state["tick"] = t, time.time()
        if (t - a) % 600 < STEP:                     # 10分ぶん進むごとに進み具合をログに出す
            print(f"  探索 {vid} {int(t)}秒 / {int(b)}秒", flush=True)
    pl.close()
    return hits


# ---------------- クラスの判定（途中でデッキを替える配信のため） ----------------
CLASSES = ["エルフ", "ロイヤル", "ウィッチ", "ドラゴン", "ナイトメア", "ビショップ", "ネメシス"]


def detect_class(items):
    """items=[(frame, row, 列)]。7クラスそれぞれの候補で照合し、合計点がいちばん高いクラスを返す。
    ニュートラルのカードはどのクラスの候補にも入っているので、クラス固有のカードで差がつく"""
    tot = {}
    for cls in CLASSES:
        cands = _CTX["pools"][cls]
        tot[cls] = sum(M.identify(f, row, i, cands)[0][0] for f, row, i in items)
    best = max(tot, key=tot.get)
    rest = sorted(tot.values())[-2]
    return best, round(tot[best] - rest, 2)


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
    if pl.blocked():
        stop_for_block(f"{vid} {int(t0)}秒")
        raise RuntimeError("YouTubeのロボット対策の画面")
    t_q = time.time()
    while time.time() - t_q < 6 and pl.js("player.getPlaybackQuality()") != "hd1080":
        time.sleep(0.3)
    pl.seek(max(0, t0 - 9))
    frames, before, seen, wall = [], [], None, time.time()
    while time.time() - wall < 120:
        last = frames[-1][0] if frames else (before[-1][0] if before else -9)
        # 対戦開始画面は2〜3秒しか映らないので、マリガン前は0.5秒おきに撮る
        wait = (0.25 if frames else 0.5) - (pl.now() - last)
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
    return frames, before[-20:]


# ---------------- 3. 読み取り ----------------
def gem(frame, row, i):
    """左上の緑のコストの宝石があるか（本物のカードが枠に収まっている目印）"""
    x0, y0 = M.card_origin(row, i)
    K = M.K
    h = cv2.cvtColor(frame[y0 + int(40 * K):y0 + int(140 * K), x0 + int(15 * K):x0 + int(105 * K)], cv2.COLOR_BGR2HSV)
    return float(((h[:, :, 0] >= 35) & (h[:, :, 0] <= 85) & (h[:, :, 1] >= 90) & (h[:, :, 2] >= 90)).mean())


def occupied(frame, row, i):
    """枠にカードがあるか。コストの宝石が配信画面の飾りで隠れていても、
    上段（返すカード）には青い交換マークが付くので、それでも判定する"""
    if M.is_empty(frame, row, i):
        return False
    return gem(frame, row, i) > 0.25 or (row == "top" and M.mark_level(frame, row, i) >= 0.3)


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
    occ, sg, chgs, bot_empty = [], [], [], []
    for _, b in frames:
        f = dec(b)
        chg = has_change(f)          # 確定後は上段の枠が消え、その場所には盤面が映るので、上段は見ない
        chgs.append(chg)
        bot_empty.append([M.is_empty(f, "bot", i) for i in range(4)])
        occ.append({(r, i): (r == "bot" or chg) and occupied(f, r, i) for r in ("top", "bot") for i in range(4)})
        sg.append({(r, i): sig(f, r, i) for r in ("top", "bot") for i in range(4)})
    # 上段の枠が配信画面の飾りで隠れている場合：選択中に下段が空なら、その列のカードは上段にある
    for k in range(n):
        for i in range(4):
            if chgs[k] and bot_empty[k][i] and not occ[k][("top", i)] and not occ[k][("bot", i)]:
                occ[k][("top", i)] = True
    # 交換を確定した瞬間 ＝ 上段（CHANGE）の枠が消えた最初のフレーム。
    # これより前が元の手札、後が交換後の手札。カードを素早く動かして確定すると、
    # 上段に置かれた場面が撮れないことがあるので、「上段にあったか」だけに頼らない
    seen_chg = False
    conf = n
    for k in range(n):
        if chgs[k]:
            seen_chg = True
        elif seen_chg:
            conf = k
            break
    def settled(k):
        """4列とも1枚ずつ収まり、ほぼ動いていない。配信のコメント欄が1列に重なって
        流れることがあるので、動いていない列が3列以上あれば静止とみなす"""
        if k == 0:
            return False
        still = 0
        for i in range(4):
            t, b = occ[k][("top", i)], occ[k][("bot", i)]
            if t == b:                                 # 列にカードが1枚だけ収まっている
                return False
            r = "top" if t else "bot"
            if occ[k - 1][(r, i)] and np.abs(sg[k][(r, i)] - sg[k - 1][(r, i)]).mean() < 10:
                still += 1
        return still >= 3
    st = [k for k in range(n) if settled(k)]
    if not st:
        return {"status": "unreadable", "reason": "手札が静止した場面が見つからない"}
    # まずクラスを判定し、対象のクラスでなければ読まない（別デッキの試合）
    k0 = st[0]
    f0 = dec(frames[k0][1])
    items = [(f0, "top" if occ[k0][("top", i)] else "bot", i) for i in range(4)]
    cls, cm = detect_class(items)
    if cls != _CTX["target"]:
        return {"status": "other_class", "class": cls, "classMargin": cm, "t": round(frames[k0][0], 1),
                "_frame": frames[k0][1]}
    pre_st = [k for k in st if k < conf]
    post = [k for k in st if k >= conf]
    if not pre_st:
        return {"status": "unreadable", "reason": "確定前の手札が静止した場面が見つからない"}
    # 返したカード ＝ 確定直前の約2秒（上段の枠が出ている最後の8フレーム）で、上段にあった回数が多い列
    tail = [k for k in range(max(0, conf - 8), conf) if chgs[k]]
    ret = {i for i in range(4) if tail and sum(occ[k][("top", i)] for k in tail) * 2 > len(tail)}
    sel = [k for k in pre_st if any(occ[k][("top", i)] for i in range(4))]
    last = sel[-1] if sel else pre_st[-1]
    pre = [k for k in pre_st if k <= last]
    orig, after, margins, flags = [], [], [], []
    for i in range(4):
        items = [(dec(frames[k][1]), "top" if occ[k][("top", i)] else "bot") for k in pick(pre, 8)]
        c, m = best_over(items, i, cands)
        orig.append(c)
        margins.append(m)
    if not post:
        after = None
        flags.append("交換後の手札が映像で確認できない")
    else:
        for i in range(4):
            # 入ってくる途中のカードは名前の部分がぶれて、同じコストのスペル同士を取り違えやすい。
            # その列のカードが止まっている場面だけで読む（なければ全部を使う）
            calm = [k for k in post if k > 0 and occ[k - 1][("bot", i)]
                    and np.abs(sg[k][("bot", i)] - sg[k - 1][("bot", i)]).mean() < 6] or post
            c, m = best_over([(dec(frames[k][1]), "bot") for k in pick(calm, 10)], i, cands)
            if i not in ret and c != orig[i] and m >= MARGIN_OK:
                ret.add(i)                          # 上段に置いた場面は撮れなかったが、カードが入れ替わっている
                flags.append(f"{i + 1}枚目は交換前後のカードの違いで「返した」と判定")
            if i in ret:
                margins.append(m)
                after.append(c)
            else:
                after.append(orig[i])
    low = [round(x, 2) for x in margins if x < MARGIN_OK]
    if low:
        flags.append(f"カードの判定があいまい（点差 {low}）")
    side, sm = read_side(dec(frames[pre[-1]][1]))
    return {"status": "ok", "t": round(frames[st[0]][0], 1), "side": side, "sideMargin": sm,
            "class": cls, "classMargin": cm,
            "orig": orig, "ret": [i in ret for i in range(4)], "after": after,
            "minMargin": round(min(margins), 3), "flags": flags,
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
    # 対戦開始画面の、配信者のCRと相手のクラス名・CR（勝敗と対戦相手の確認用）
    if before:
        vt, vb = max(before, key=lambda x: vs_score(dec(x[1])))
        vf = dec(vb)
        x0, y0, x1, y1 = SELF_CR
        a = cv2.cvtColor(cv2.resize(vf[y0:y1, x0:x1], ((x1 - x0) * 3 // 2, (y1 - y0) * 3 // 2)), cv2.COLOR_BGR2RGB)
        x0, y0, x1, y1 = OPP_INFO
        o = cv2.cvtColor(vf[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
        sheet.paste(Image.fromarray(a), (440, 1000))
        sheet.paste(Image.fromarray(o), (1080 + 440, 1020))
        dr.text((440, 972), f"対戦開始 {vt:.0f}s  自分のCR", font=font, fill=(255, 230, 120))
        dr.text((1520, 992), "相手", font=font, fill=(255, 230, 120))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sheet.save(path, quality=80)


def vs_sheet(vid):
    """読み取った各試合の対戦開始画面から、配信者のCRと相手のクラス名・CRを切り出して1枚に並べる。
    CRが次の試合で上がっていれば勝ち、下がっていれば負け（最後の試合は次がないので判定できない）"""
    from PIL import Image, ImageDraw
    res = json.load(open(os.path.join(OUT_DIR, f"{vid}.json"), encoding="utf-8"))
    set_xf(res.get("xf"))
    # 別デッキの試合も入れる（直前の試合の勝敗を、次の試合のCRで判定するため）
    evs = [(n, e) for n, e in res["events"].items() if e["status"] in ("ok", "other_class") and e.get("t0")]
    pl = S.Player()
    tiles = []
    for n, e in evs:
        t0 = e.get("t0", e["t"])
        pl.goto(vid, max(0, t0 - 11))
        best, bt = None, -1
        while pl.now() < t0 + 1:
            b = grab(pl)
            s = vs_score(dec(b))
            if s > bt:
                best, bt = b, s
            time.sleep(0.3)
        f = dec(best)
        x0, y0, x1, y1 = SELF_CR
        a = f[y0:y1, x0:x1]
        x0, y0, x1, y1 = SELF_SIDE
        sd = cv2.resize(f[y0:y1, x0:x1], ((x1 - x0) // 2, (y1 - y0) // 2))
        x0, y0, x1, y1 = OPP_INFO
        o = f[y0:y1, x0:x1]
        row = np.zeros((170, 30 + a.shape[1] + 20 + sd.shape[1] + 20 + o.shape[1], 3), np.uint8)
        row[20:20 + a.shape[0], 30:30 + a.shape[1]] = a
        x = 50 + a.shape[1]
        row[20:20 + sd.shape[0], x:x + sd.shape[1]] = sd
        x += sd.shape[1] + 20
        row[20:20 + o.shape[0], x:x + o.shape[1]] = o
        tag = "" if e["status"] == "ok" else " (other deck)"
        cv2.putText(row, f"{n} vs={bt:.2f}{tag}", (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        tiles.append(row)
    pl.close()
    w = max(t.shape[1] for t in tiles)
    sheet = np.vstack([cv2.copyMakeBorder(t, 0, 0, 0, w - t.shape[1], cv2.BORDER_CONSTANT) for t in tiles])
    out = os.path.join(REVIEW, vid, "vs_sheet.jpg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cv2.imwrite(out, sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(f"対戦開始画面の一覧を {out} に保存しました（{len(tiles)}試合）")


# ---------------- 実行 ----------------
_PL = None
_CTX = {}


def _init(cls, xf=None):
    set_xf(xf)
    M.set_layout("player")
    _CTX["pools"] = {}
    for c in CLASSES:
        pool = mull_cards.class_pool(c)
        M.register_types(pool)
        _CTX["pools"][c] = {k: v["name"] for k, v in pool.items()}
    _CTX["target"] = cls
    _CTX["cands"] = _CTX["pools"][cls]


def _kill_children():
    """このプロセスが起動したブラウザ（子孫プロセス）をすべて止める。
    ブラウザが応答しなくなると、待っている処理が永遠に返らないため、外から止めて例外にする"""
    try:                                         # Mac・Windows どちらでも動く方法
        import psutil
        for c in psutil.Process(os.getpid()).children(recursive=True):
            try:
                c.kill()
            except Exception:
                pass
        return
    except ImportError:
        pass
    import signal, subprocess                    # psutil が無い Mac 用の予備
    rows = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True).stdout.split("\n")
    kids = {}
    for r in rows:
        p = r.split()
        if len(p) == 2:
            kids.setdefault(int(p[1]), []).append(int(p[0]))
    todo, mine = [os.getpid()], []
    while todo:
        for c in kids.get(todo.pop(), []):
            mine.append(c)
            todo.append(c)
    for pid in mine:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


WATCHDOG = 300     # 1試合の読み取りがこれ（秒）を超えたら、ブラウザを止めて次へ進む


def work(job):
    global _PL
    vid, n, t0 = job
    dog = threading.Timer(WATCHDOG, _kill_children)
    dog.daemon = True
    dog.start()
    try:
        return _work(job)
    finally:
        dog.cancel()


def _work(job):
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
        elif res["status"] == "other_class":
            # 別クラスと判定した試合も、判定が正しいかを目で確かめられるよう画面を残す
            os.makedirs(os.path.join(REVIEW, vid), exist_ok=True)
            f = dec(res.pop("_frame"))
            cv2.imwrite(os.path.join(REVIEW, vid, f"{n:02d}_{res['class']}.jpg"),
                        cv2.resize(f[100:1010, 240:1840], (1070, 609)), [cv2.IMWRITE_JPEG_QUALITY, 75])
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
    ap.add_argument("--vs-only", action="store_true", help="読み取り済みの配信について、対戦開始画面の一覧だけ作る")
    args = ap.parse_args()

    M.set_layout("player")
    if os.path.exists(BLOCK_FLAG):
        sys.exit(f"YouTubeのロボット対策で止まっています（{open(BLOCK_FLAG).read().strip()}）。"
                 f"\n確認画面が出なくなったのを確かめてから {BLOCK_FLAG} を消して再開してください。")
    if args.vs_only:
        return vs_sheet(args.vid)
    import re, svps_api
    h = svps_api.fetch(f"https://www.youtube.com/watch?v={args.vid}")
    length = int(re.search(r'"lengthSeconds":"(\d+)"', h).group(1))
    # ゲーム画面の縮尺と位置を、最初のマリガン画面で測る（全面に映っていれば補正しない）
    xf, calib = calibrate_safe(args.vid, length)
    set_xf(xf)
    print(f"画面の補正: {'なし（全面）' if not xf else xf}  測定: {calib}", flush=True)
    if args.hits:
        hits = sum((json.load(open(p)) for p in args.hits), [])
        hits = [h[0] if isinstance(h, list) else h for h in hits]
    else:
        step = length // args.jobs + 1
        jobs = [(args.vid, a, min(length, a + step), xf) for a in range(0, length, step)]
        with get_context("spawn").Pool(args.jobs) as pool:
            hits = sum(pool.map(coarse, jobs), [])
    evs = events_from(hits)
    print(f"マリガン画面 {len(evs)} 回", flush=True)
    # 探した結果は保存しておく（読み取りの途中で止まっても、--hits で探し直さずに再開できる）
    os.makedirs(os.path.join(HERE, "cache", "stream_hits"), exist_ok=True)
    if not args.hits:
        json.dump(sorted(hits), open(os.path.join(HERE, "cache", "stream_hits", f"{args.vid}.json"), "w"))

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{args.vid}.json")
    res = {"vid": args.vid, "player": args.player, "class": args.cls, "deck": args.deck or args.cls,
           "xf": xf, "calib": calib, "events": {}}
    with get_context("spawn").Pool(args.jobs, initializer=_init, initargs=(args.cls, xf)) as pool:
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
