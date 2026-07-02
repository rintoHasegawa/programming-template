# プログラミングテンプレート

Claude Code と協働でプロジェクトを立ち上げ・実装するための汎用テンプレート．`/setup` で対話的に立ち上げ，`/implement` で実装を進める．

**個人開発（solo）とチーム開発（team）の 2 モード**を 1 つのテンプレートで提供する．`/setup` の冒頭でモードを選ぶだけで，チーム開発向けのルール・コマンドが有効化される．

- **solo**: 1 人で開発．進捗は `CLAUDE.md` の進捗欄＋ `docs/PROGRESS.md` で追う．チーム層ファイルは配置されない．
- **team**: 複数人が Claude Code で開発．進捗・タスクは GitHub Issues と git 履歴で追い，直列運用・条件付きセルフマージ等のチームルール（GUIDE_06）と `/task-create`・`/task-start`・`/task-handoff` コマンド，SessionStart の同期チェック（`check_sync.sh`）が有効になる．

## Quick Start

新規プロジェクトを始めるときは，以下の手順でテンプレートをカレントディレクトリに展開し，履歴を引き継がない新規リポジトリとして初期化する．

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

その後 Claude Code を起動し，`/setup <project-name>` でプロジェクト立ち上げを開始する．`/setup` の冒頭で **solo / team のモードを選択**する（選択結果は `.claude/project-mode` に記録され，以降の `/sync-template` がモードに応じてチーム層ファイルを出し分ける）．以降，テンプレートの更新を取り込むときは `/sync-template` を実行する．

> 個人で始めたプロジェクトを後からチーム開発に切り替える場合は，`.claude/project-mode` を `team` に書き換えて `/sync-template` を実行すれば，チーム層ファイル（GUIDE_06/07・`task-*`・`check_sync.sh`）が配置される．`/setup` を再実行して team を選び直してもよい．

## 主なスラッシュコマンド

- `/setup <project-name>` — GUIDE_01 に従いプロジェクト立ち上げを対話的に進行（solo/team を選択）
- `/implement <タスク>` — 実装パイプライン（コーディング → テスト → リファクタリング）
- `/commit` / `/commit push` — コミット作成（`push` でプッシュと PR 作成まで）
- `/sync-template` — テンプレートの最新変更を取り込む
- `/task-create` / `/task-start` / `/task-handoff` — **team モードのみ**．Issue ベースのタスク作成・着手・引継ぎ

## ドキュメント

`docs/01_GUIDE/` にプロジェクト運用の規約・ルールが置かれている．`/setup` を実行すれば AI がこれらを順次参照しながら立ち上げを進める．

- `GUIDE_01_プロジェクト立ち上げフロー.md` — 立ち上げの 7 フェーズ（方針決定 → 実装開始）
- `GUIDE_02_ドキュメント作成ガイド.md` — 設計ドキュメントの書き方
- `GUIDE_03_ファイル命名規則.md` — ファイル名の規則
- `GUIDE_04_Git運用ルール.md` — ブランチ・コミット・PR の運用
- `GUIDE_05_エージェント運用ルール.md` — `/implement` のエージェントチーム運用
- `GUIDE_06_チーム開発ルール.md` — **team モードのみ**．直列運用・条件付きセルフマージ・共有設定の扱い
- `GUIDE_07_Issues・Projects運用ガイド.md` — **team モードのみ**．`gh` による Issue/Project 操作手順
