---
name: commit
model: inherit
description: "Git 規約（.claude/rules/git-conventions.md）に従い，変更内容の確認 → コミットを行う．"
argument-hint: "<コミット内容の補足（省略可）>"
---

あなたは Git 運用の担当者です．Git 規約 `.claude/rules/git-conventions.md` に従い，現在の変更をコミットしてください．
push・PR・マージの詳細手順とトラブル対応は本スキルの `reference.md`（`.claude/skills/commit/reference.md`）を参照する．
確認は不要．すべてのステップを一気に実行すること．

## ステップ 1: 状態確認

以下のコマンドで現在の状態を把握する．

- `git branch --show-current` で現在のブランチを確認
- `git status` で変更ファイルの一覧を確認
- `git diff` および `git diff --cached` で変更内容を確認

**main ブランチにいる場合は自動でブランチを作成する:**

1. 変更内容から `.claude/rules/git-conventions.md` のブランチ命名規則に従い，適切なプレフィックスと英単語 2〜4 語のブランチ名を決定する
2. `git checkout -b {ブランチ名}` で作業ブランチを作成する
3. 以降のステップを続行する

## ステップ 2: docs/ 更新漏れチェック

`.claude/commit-context.md` が存在し `docs_updated` が記録されている場合，このステップをスキップする．

ファイルが存在しない場合（`/implement` を経由していない場合），変更内容を確認し，以下に該当するにもかかわらず docs/ が更新されていない場合は警告する:

- API やデータ構造の変更
- 環境設定の変更
- 開発計画の進捗に関わる変更
- 規約やルールに関わる変更

該当がある場合，「⚠ docs/ の更新が必要かもしれません．確認してください．」と警告する．
更新自体は行わず，ユーザーの判断に委ねる．

## ステップ 3: コミットメッセージの生成

変更内容と $ARGUMENTS（指定がある場合）をもとに，`.claude/rules/git-conventions.md` の「コミットメッセージ」セクションに従ってコミットメッセージを生成する．

## ステップ 4: CLAUDE.md / PROGRESS.md 更新漏れチェック

`.claude/commit-context.md` が存在し `claude_md_updated` と `progress_md_updated` が記録されている場合，このステップをスキップする．

ファイルが存在しない場合（`/implement` を経由していない場合），変更内容に応じて以下の更新が必要か判断する:

- CLAUDE.md の「開発進捗」セクション（最新 1 行）
- `docs/PROGRESS.md`（追記型フルログ）

更新されていない場合は「⚠ CLAUDE.md の開発進捗 / docs/PROGRESS.md の更新が必要かもしれません．確認してください．」と警告する．
更新自体は行わず，ユーザーの判断に委ねる．

## ステップ 5: コミット

以下を実行する:

1. `git add` で関連ファイルをステージングする（CLAUDE.md / `docs/PROGRESS.md` の変更がある場合はそれも含める）
2. `git commit -m "{コミットメッセージ}"` でコミットする
3. `.claude/commit-context.md` が存在する場合は削除する

## ステップ 6: プッシュ・PR・マージ

$ARGUMENTS に以下のキーワードが含まれる場合のみ，該当する操作を実行する．
**いずれも含まれない場合はこのステップ全体をスキップする．**

| キーワード | 実行する操作 |
| --- | --- |
| `push` | プッシュ・PR 作成 |
| `merge` | プッシュ・PR 作成・マージ・プル（`push` を含む全操作） |

### プッシュ・PR 作成

1. `git push origin {ブランチ名}` でリモートにプッシュする
2. PR の状態を確認する:
   - **既存の PR がある場合**: PR の URL を表示して完了
   - **PR がない場合**: `reference.md` の「プルリクエスト (PR) の作成」に従い PR を作成し，URL を表示する

### マージ・プル（`merge` の場合のみ）

1. `reference.md` の「マージの実行」に従いマージする
2. `reference.md` の「ローカル環境のクリーンアップ」に従いローカルを最新化する

## 注意事項

- `.env` やクレデンシャルファイルはステージングしない
- 変更がない場合（`git status` がクリーン）は空コミットせず，その旨を伝えて終了する
