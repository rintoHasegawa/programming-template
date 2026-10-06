# プログラミングテンプレート

Claude Code と協働でプロジェクトを立ち上げ・実装するための汎用テンプレート．`/setup` で対話的に立ち上げ，`/implement` で実装を進める．

**個人開発（solo）とチーム開発（team）の 2 モード**を 1 つのテンプレートで提供する．`/setup` の冒頭でモードを選ぶだけで，チーム開発向けのルール・コマンドが有効化される．

- **solo**: 1 人で開発．進捗は `CLAUDE.md` の進捗欄＋ `docs/PROGRESS.md` で追う．チーム層ファイル（GUIDE_03・`check_sync.sh`）は配置されない．
- **team**: 複数人が Claude Code で開発．進捗・タスクは GitHub Issues と git 履歴で追い，直列運用・条件付きセルフマージ等のチームルール（GUIDE_03）と SessionStart の同期チェック（`check_sync.sh`）が有効になる．

※ Issue ベースのタスク管理コマンド（`/task-create`・`/task-start`・`/task-handoff`）は**両モード共通**で使用できる．

## Quick Start

新規プロジェクトを始めるときは，以下の手順でテンプレートをカレントディレクトリに展開し，履歴を引き継がない新規リポジトリとして初期化する（bash 前提．Windows の PowerShell で実行する場合は後述の読み替えに従う）．

```bash
# 1. プロジェクトディレクトリを作成して移動
mkdir <project-name>
cd <project-name>

# 2. カレントディレクトリにテンプレートをクローン（末尾の . に注意）
git clone https://github.com/rintoHasegawa/programming-template.git .

# 3. 現在のテンプレート SHA を記録（以降の /sync-template で差分同期するため）
git rev-parse HEAD > .claude/template-sync-sha

# 4. テンプレートの git 履歴を削除し，新しいリポジトリとして初期化
rm -rf .git
git init -b main

# 5. テンプレート紹介用 README をプロジェクトから削除
rm README.md
```

### Windows (PowerShell) の場合

PowerShell では手順 4 の `rm -rf .git` が失敗する（`rm` は `Remove-Item` のエイリアスであり `-rf` を解釈しないため，「パラメーター名 'rf' と一致するパラメーターが見つかりません」となる）．Git Bash で上記をそのまま実行するか，以下に読み替える．

```powershell
mkdir <project-name>
cd <project-name>
git clone https://github.com/rintoHasegawa/programming-template.git .
git rev-parse HEAD | Set-Content -Encoding utf8 .claude\template-sync-sha
Remove-Item -Recurse -Force .git
git init -b main
Remove-Item README.md
```

- `Remove-Item -Recurse -Force` が bash の `rm -rf` に相当する．`.git` は隠し属性のため `-Force` が必須
- 手順 3 をリダイレクト `>` で書くと，Windows PowerShell 5.1 では UTF-16LE で出力され `/sync-template` が `.claude/template-sync-sha` を読めなくなる．`Set-Content -Encoding utf8` を使う（PowerShell 7 では `>` でも UTF-8 なので問題ない）
- ※ `.git` の削除は取り消せない．テンプレートのクローン直後（手順 4）以外で実行しないこと

その後 Claude Code を起動し，`/setup <project-name>` でプロジェクト立ち上げを開始する．`/setup` の冒頭で **solo / team のモードを選択**する（選択結果は `.claude/project-mode` に記録され，以降の `/sync-template` がモードに応じてチーム層ファイルを出し分ける）．以降，テンプレートの更新を取り込むときは `/sync-template` を実行する．

> 途中でモードを切り替える場合は **`/set-mode <solo|team>`** を実行する．team 層ファイル（GUIDE_03・`check_sync.sh`）の配置／削除，`settings.json` の hook 配線，`CLAUDE.md` の team 化／solo 化，`.claude/project-mode` の更新を一括で整合させる（`.claude/project-mode` を手で書き換えるだけでは切り替わらない）．

## 主なスラッシュコマンド

- `/setup <project-name>` — GUIDE_01 に従いプロジェクト立ち上げを対話的に進行（solo/team を選択．環境構築は手順書を書いた後に実際に構築まで行う．成果物はファイルに書き出してからレビューを受け，コミットは人間が `/commit` で行う．ブランチは `chore/project-setup`．GitHub リポジトリの Dependabot alerts / security updates を `gh api` で有効化し，`.github/dependabot.yml` も生成）
- `/implement <タスク>` — 実装パイプライン（コーディング →（任意の検証）→ テスト → リファクタリング）
- `/verify [観点]` — ローカル環境（およびプロファイルが許可した検証用環境）で動作とエッジケースを Claude に確認させ，人間が確認すべき範囲を絞る（検証プロファイル `.claude/verify-profile.md` を用意したプロジェクトのみ．雛形は `.claude/skills/verify/profile-template.md`．人間の動作確認は無くならない）
- `/commit` / `/commit push` / `/commit merge` — コミット作成（`push` でプッシュと PR 作成まで，`merge` でマージ・プルまで）
- `/auto-refactor` / `/auto-audit` — 無人運転ループ（リファクタ／ドキュメント整理，バグ／脆弱性の巡回監査）．専用ブランチへ自律コミットし，push・PR・マージはしない
- `/deps-update` — Dependabot の依存更新 PR と alert を処理（メジャー更新でなく CI／ローカル検証が緑の PR は `main` に自動マージ，メジャー更新・CI 赤・修正版なし alert は影響分析付きで報告．`/deps-update report` で報告のみ）
- `/sync-template` — テンプレートの最新変更を取り込む（プロジェクトで意図的に変えたファイルは `.claude/template-overrides.md` の台帳に登録しておくと，上書きせず方針に従ってマージ／保持される）
- `/set-mode <solo\|team>` — 開発モードを切り替える（team 層ファイル・settings.json・CLAUDE.md・project-mode を一括整合）
- `/task-create` / `/task-start` / `/task-handoff` — Issue ベースのタスク作成・着手・引継ぎ（solo / team 両モード共通）

## リポジトリ外アクセスの制限

同梱のフック（`.claude/hooks/restrict_repo_access.py`）が，Claude がリポジトリ外のファイルを取り返しのつかない形で壊さないようにする．

- リポジトリ外の**削除**（`rm`・`Remove-Item`・`find -delete` 等．移動元を含む）と**既存ファイルの上書き**（`>` リダイレクト・`cp` の書き込み先・`sed -i`・`Set-Content`・Write・Edit 等）だけを拒否する（一時ディレクトリは許可）
- 新規作成・ダウンロード・`mkdir`・追記（`>>` 等）は止めない．環境構築を自動で進められるようにするためで，それ以外の判断は権限モード（auto mode の分類器等）に任せる
- 読み取り（Read・Glob・Grep・`cat` 等）はリポジトリ外も許可する．**読んだ内容は Claude の API に送信される**ため，読ませたくない場所は `.claude/repo-access.json` の `deny_read` に書いて禁止する（書式は GUIDE_01「環境構築」）
- 拒否された操作が必要なときは，Claude が提示したコマンドの内容を確認し，プロンプトで `! <コマンド>` として自分で実行する．**`!` で実行したコマンドにはこのフックはかからない**
- コマンド文字列の解析なので完全には防げない（変数展開・スクリプト経由等）．確実性が必要なら OS のフォルダ権限・`permissions.deny`・コマンドごとの承認・バックアップを併用する（GUIDE_01「環境構築」）

## ドキュメント

`docs/01_GUIDE/` にプロジェクト運用のガイドが置かれている．`/setup` を実行すれば AI がこれらを順次参照しながら立ち上げを進める．

- `GUIDE_01_プロジェクト立ち上げフロー.md` — 立ち上げの 7 フェーズ（方針決定 → 実装開始）
- `GUIDE_02_エージェント運用ルール.md` — `/implement` のエージェントチーム運用
- `GUIDE_03_チーム開発ルール.md` — **team モードのみ**．直列運用・条件付きセルフマージ・共有設定の扱い

Git 規約（ブランチ命名・コミット書式）・ドキュメントの書式・ファイル命名・進捗記録・テンプレート改変記録・フックのクロスプラットフォーム対応のルールは `.claude/rules/`（`git-conventions` / `markdown-style` / `docs-naming` / `progress-log` / `template-customization` / `hooks-cross-platform`）にあり，Claude へ自動ロードされる（git-conventions は常時，他は該当ファイル編集時）．push・PR・マージの詳細手順は `.claude/skills/commit/reference.md`，Issues・Projects の `gh` 操作リファレンスは `.claude/skills/task-start/reference.md`，テンプレート同期の bash ヘルパー・個別マージ手順は `.claude/skills/sync-template/reference.md` にある．
