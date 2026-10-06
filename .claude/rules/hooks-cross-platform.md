---
paths: [".claude/hooks/**", ".github/workflows/hooks-ci.yml"]
---

# フックのクロスプラットフォーム対応ルール (Hooks Cross-platform Rules)

`.claude/hooks/` 配下（フック本体・フックから呼ばれるスクリプト・`tests/` のテスト）を作成・修正するときは本ルールに従うこと．フックはテンプレートから派生した全プロジェクトで，開発者ごとに異なる OS 上で動く．1 つの OS でしか確かめていない修正は，他の OS で検出漏れ（安全装置の素通り）や誤ブロックを起こしうる．

## 完成条件 (Definition of Done)

- **Windows / macOS / Linux の全てでフックが動作し，`.claude/hooks/tests` が全件通ること**を完成条件とする．手元の OS だけで通った状態は未完成として扱う
- 3 OS での実行は GitHub Actions のワークフロー `.github/workflows/hooks-ci.yml`（`windows-latest` / `macos-latest` / `ubuntu-latest` のマトリクス）が行う．PR の CI が 3 OS とも緑であることを確認してからマージする
- 手元での確認はホスト OS で `python -B -m unittest discover -s .claude/hooks/tests`（`python` が無い環境では `python3`）を実行する．Windows ホストで WSL が使える場合は，リポジトリを WSL 側の一時ディレクトリ以外の場所に複製して `python3 -B -m unittest discover -s .claude/hooks/tests` を実行すると Linux の挙動も事前に確認できる
- Python 3.7 以上で動く書き方を守る（`run_python.sh` が拾う Python は環境により古い場合がある．`TestPython37Compat` が検査する）

## 実装の注意点 (Implementation Pitfalls)

- `os.path` はホスト OS によって `ntpath`（Windows）と `posixpath`（macOS / Linux）に切り替わる．区切り文字（`\` を区切りと見なすか），絶対パスの判定（`isabs`），ドライブレター，大文字小文字の区別が OS ごとに異なることを前提に書く
- 解析対象のシェルの区切りの扱いがホストの `os.path` と異なる場合は，判定の前に明示的に正規化する（例: Linux / macOS の `pwsh` は `\` も区切りとして受け付けるため，POSIX ホストでは PowerShell のパスの `\` を `/` に揃えてから `isabs`・`join`・`normpath` に渡す）
- 「`/` で始まるか」のような，ある OS でだけ成り立つ前提で分岐しない（例: cmd.exe のスイッチ `/s` と POSIX の絶対パス `/workspace/x` を区別できる判定にする）
- 絶対パスの判定を `ntpath.isabs` の結果に直接依存させない．Python 3.13 で `ntpath.isabs` が変わり，ドライブ無しの root-relative パス（`/tmp/x`・`\tmp\x`）を False と判定するようになったため，そのまま使うと cwd と結合されて `D:\tmp\x` 等に化け，Python のバージョンで判定が変わる（フックでは `is_absolute` で Windows の `/`・`\` 始まりを常に絶対パスとして扱う）
- Git Bash 形式のパス（`/c/Users/...`）・ドライブレター・UNC パス等の Windows 固有の変換は `os.name == "nt"` のときだけ行い，他 OS の挙動を変えない
- シェルスクリプト（`*.sh`）は macOS 標準の bash 3.2 と BSD 版コマンドでも動く書き方にする（GNU 拡張のオプションや bash 4 以降の構文に頼らない）

## テストの書き方 (Writing Tests)

- **OS 固有のケース**（`cmd /c`・ドライブレター・Git Bash 形式 `/c/...` 等，特定 OS にしか存在しない入力）は `@unittest.skipUnless(os.name == "nt", "理由")` 等で実行する OS を明示的に限定する．skip の理由には「なぜその OS 限定か」を書く
- **OS 非依存であるべき判定**（相対パスの解決・`..` による脱出の検出・削除／上書きの判定等）は skip せず全 OS で実行する．OS 依存の書き方で失敗したテストを skip で黙らせてはならない（本物の検出漏れを隠す）
- テスト中のパスは `os.path.join` や `tempfile` で組み立て，`\` や `C:` をハードコードしない（OS 固有ケースとして限定したテストを除く）
- リポジトリ外を表すサンドボックスはシステムの一時ディレクトリの外に作る（一時ディレクトリはフックが書き込みを許可するため，判定のテストにならない）

## 禁止事項 (Prohibitions)

- 1 つの OS でしかテストを通していない状態で，フックの修正を完了扱いにしない
- 他 OS で失敗するテストを，原因を確かめずに skip・削除しない
