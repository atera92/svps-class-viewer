# Windows デスクトップPCへの移行手順

移行データ（Macのデスクトップの「SVPS_Windows移行」フォルダ）を、USBメモリかGoogleドライブ等でWindowsに移してから、上から順にやる。
**分からないところは、WindowsのClaude Codeにこのファイルを渡して「この手順で移行して」と頼めば、2〜6は代わりにやれる。**

## 1. Windowsに入れておくもの（本人がやる）

| もの | 入手先 | 注意 |
|---|---|---|
| Python 3.10以上 | https://www.python.org/downloads/ | インストール画面で **「Add python.exe to PATH」にチェック** |
| Git | https://git-scm.com/download/win | 既定のままでよい |
| Google Chrome | https://www.google.com/chrome/ | 動画の読み取りとPDF作成に使う |
| Claude Code（デスクトップアプリ） | https://claude.ai/download | Macと同じアカウントでログイン |

## 2. フォルダを置く

1. `svps-class-viewer.zip` を、例えば `C:\Users\<ユーザー名>\yamato\tools\` に展開する
   → `C:\Users\<ユーザー名>\yamato\tools\shadowberse\` ができる（中に index.html などがあれば正しい）
2. `yamato_workspace.zip` を `C:\Users\<ユーザー名>\yamato\` に展開する（CLAUDE.md・HANDOFF.md・work・life）

zipの中身はGitHubの最新版と同じコードに、GitHubに入れていないもの（`cache/` のカード画像・デッキ情報・確認用画像、作成途中の `mull_pro.json`）を足したもの。
展開したフォルダはそのまま git の作業フォルダとして使える（`.git` も入っている）。

## 3. セットアップ

`shadowberse\セットアップ.bat` をダブルクリック（Pythonの部品とブラウザを入れる。数分）。

## 4. 動くか確かめる

- `起動.bat` → ブラウザでビューアが開けばよい。スマホ用のアドレスも表示される
- 公開サイトはPCを移しても変わらない：https://atera92.github.io/svps-class-viewer/

## 5. GitHubへの公開（本人が一度だけ）

`公開.bat` を初めて使うとき、Gitがブラウザでログインを求める。GitHubアカウント **atera92** でログインする。
（パスワードの入力はClaudeには頼めない。ログインだけ本人がやる）

## 6. Claude Code に記憶と引き継ぎを渡す

1. WindowsのClaude Codeで `C:\Users\<ユーザー名>\yamato` を開く
2. `claude_memory` フォルダの中身を、Claude Codeの記憶フォルダにコピーする。
   場所はWindowsだと `C:\Users\<ユーザー名>\.claude\projects\<yamatoフォルダのパスを-でつないだ名前>\memory\`
   （分からなければClaudeに「claude_memory を記憶フォルダに入れて」と頼む）
3. `claude_global_CLAUDE.md` を `C:\Users\<ユーザー名>\.claude\CLAUDE.md` として置く
   （中のパスは `/Users/zaitsuyamato/yamato/...` のままなので、Claudeに「Windowsのパスに直して」と頼む）
4. 最初の会話で「`yamato\tools\shadowberse\HANDOFF_SVPS.md` を読んで続きをやって」と伝える

## 7. Macでやめておくこと

- Macの `起動.command` のサーバは、移行後は使わない（止める：ターミナルで `pkill -f "http.server 8899"`）
- 同じ作業を両方のPCで同時に進めない（GitHubで食い違う）。移行後はWindowsで `git pull` してから作業する
