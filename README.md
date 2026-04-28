# プログラミングテンプレート

Claude Code と協働でプロジェクトを立ち上げ・実装するための汎用テンプレート．`/setup` で対話的に立ち上げ，`/implement` で実装を進める．

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

その後 Claude Code を起動し，`/setup <project-name>` でプロジェクト立ち上げを開始する．以降，テンプレートの更新を取り込むときは `/sync-template` を実行する．

## 主なスラッシュコマンド

- `/setup <project-name>` — GUIDE_01 に従いプロジェクト立ち上げを対話的に進行
- `/implement <タスク>` — 実装パイプライン（コーディング → テスト → リファクタリング）
- `/commit` / `/commit push` — コミット作成（`push` でプッシュと PR 作成まで）
- `/sync-template` — テンプレートの最新変更を取り込む

## ドキュメント

`docs/01_GUIDE/` にプロジェクト運用の規約・ルールが置かれている．`/setup` を実行すれば AI がこれらを順次参照しながら立ち上げを進める．

- `GUIDE_01_プロジェクト立ち上げフロー.md` — 立ち上げの 7 フェーズ（方針決定 → 実装開始）
- `GUIDE_02_ドキュメント作成ガイド.md` — 設計ドキュメントの書き方
- `GUIDE_03_ファイル命名規則.md` — ファイル名の規則
- `GUIDE_04_Git運用ルール.md` — ブランチ・コミット・PR の運用
- `GUIDE_05_エージェント運用ルール.md` — `/implement` のエージェントチーム運用
