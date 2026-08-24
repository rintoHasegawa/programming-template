# Issues・Projects gh 操作リファレンス (gh Operations Reference)

`/task-create`・`/task-start`・`/task-handoff` の Issue・ボード操作で参照する詳細手順（solo / team 両モード共通）．
team モードでの運用方針（Issue = 作業単位，直列運用，ボードの列等）は [GUIDE_03_チーム開発ルール](../../../docs/01_GUIDE/GUIDE_03_チーム開発ルール.md) に従う．solo モードでは GUIDE_03 は配置されないため，本ファイルと各 skill の記述を基準とする．

- 操作は GitHub CLI（`gh`）に統一する．Web UI でも同じことはできるが，再現性とドキュメント化のため `gh` を基準とする．
- AI に Issue を読ませる場合は `gh issue view <Issue番号>` を使う（構造化出力は `--json title,body,assignees,labels`）．

## 必要なスコープ (Required Scopes)

`gh` のトークンスコープが操作範囲を決める．

| 操作 | 必要スコープ |
| --- | --- |
| Issues の読み書き | `repo` |
| Projects の読み書き | `repo` ＋ `project` |

スコープの確認と追加:

```bash
gh auth status                  # 現在のスコープを確認
gh auth refresh -s project      # Projects 用スコープを追加（対話的な再認証が走る）
```

## ブランチと Issue のリンク (Branch-Issue Link)

既存 Issue から作業ブランチを作る場合は `gh issue develop` を使うと，ブランチ作成と Issue↔ブランチのリンクを同時に行える（ブランチ名に依存しないリンク手段）:

```bash
gh issue develop <Issue番号> --name feature/<概要> --base main
```

## /task-create 実行手順 (task-create Execution Flow)

`/task-create` の司令塔（メインループ）から，確定済みの Issue タイトル・本文を受け取った ops-runner が実行する．

1. **Project の特定**: `gh repo view --json owner --jq .owner.login` で所有者を取得し，`gh project list --owner <owner>` で Project を特定する．複数ある場合は停止して確認事項として報告する．見つからない場合はボード追加を飛ばして進める（Project ボードは既定では未使用のため，無くても問題ない）
2. **Issue 作成**: 渡されたタイトル・本文を**そのまま**使い，`gh issue create --title "<タイトル>" --body "<本文>"` で作成する（本文を書き換えない）．担当者はアサインしない
3. **ボードに追加**: 「Projects の操作」に従い `item-add` → Status を `Todo` に設定 → `item-list` で結果を確認する（Project が無い場合は飛ばす）
4. **報告**: Issue 番号・タイトル・URL・ボード状態を返す

## /task-start 実行手順 (task-start Execution Flow)

`/task-start` の司令塔から Issue 番号を受け取った ops-runner が実行する．ブランチ命名は `.claude/rules/git-conventions.md` に従う．

1. **前提確認**: `git status` を確認し，未コミットの変更があれば停止して報告する．`git branch --show-current` を確認し，`main` 以外にいる場合は `git log main..HEAD --oneline` で未マージコミットを確認し，残っていれば停止して報告する（直列運用の原則）
2. **直列運用チェック**: 所有者を取得し Project を特定する（複数なら停止して報告．無ければボード操作を飛ばす）．進行中の作業を数える（Project があれば `item-list` で Status が `In Progress` のもの，無ければ `gh issue list --assignee @me --state open`）．対象 Issue 以外に 1 件以上あれば，該当タスクの一覧を添えて停止して報告する（禁止ではなく原則）
3. **対象 Issue の確認・アサイン**: `gh issue view <Issue番号>` で内容を確認する（存在しない・クローズ済み・他人がアサイン済みの場合は停止して報告する）．`gh issue edit <Issue番号> --add-assignee @me` で自分をアサインする
4. **ボードを In Progress へ**: 「Projects の操作」に従い，未追加なら `item-add` → Status を `In Progress` に変更 → `item-list` で結果を確認する（Project が無い場合は飛ばす）
5. **作業ブランチ作成**: `git checkout main && git pull origin main` で `main` を最新化し，規約どおりのブランチ名（プレフィックス＋英単語 2〜4 語．Issue 番号は含めない）で `git checkout -b <ブランチ名>` する
6. **報告**: Issue 番号・タイトル・URL・ボード状態・作成したブランチ名を返す

※ ステップ 1〜3 の停止条件に複数該当する場合は，まとめて 1 回の報告で返す（司令塔との往復を減らす）．司令塔から「ユーザー承認済み」と明示された項目では停止しない．

## /task-handoff 実行手順 (task-handoff Execution Flow)

`/task-handoff` の司令塔から委譲される 2 つのフェーズ．

### 収集フェーズ (Collect)

1. 所有者を取得し Project を特定する（無くても問題ない）．着手中の Issue を抽出する（Project があれば `item-list` で Status が `In Progress` のもの，無ければ `gh issue list --assignee @me --state open`）
2. 0 件ならその旨を報告して終了する．複数件なら一覧（番号・タイトル）を確認事項として返す
3. 対象 Issue について以下を収集して返す:
   - `gh issue view <Issue番号> --json number,title,body,comments`（本文と過去コメントの要点．過去の進捗メモと重複させないための材料）
   - `git branch --show-current`／`git rev-parse --short HEAD`／`git log main..HEAD --oneline`／`git status --porcelain`

### 投稿フェーズ (Post)

1. 司令塔から渡された本文を**そのまま** `gh issue comment <Issue番号> --body "<本文>"` で投稿する（書き換えない）
2. コメントの URL を返す

## Projects の操作 (Projects)

> ⚠ **Projects（ボード）は既定では未使用**．以下は将来導入時の参考として残す．

※ `gh project` 系は `project` スコープが必要（「必要なスコープ」参照）．`<owner>` はリポジトリ所有者（個人またはオーガニゼーション）．

### 参照 (View)

```bash
gh project list --owner <owner>                      # プロジェクト一覧（Project 番号を確認）
gh project view <Project番号> --owner <owner>         # プロジェクトの概要
gh project item-list <Project番号> --owner <owner>    # ボード上のアイテム一覧
gh project field-list <Project番号> --owner <owner>   # フィールド（Status 等）と選択肢の ID を確認
```

### Issue をボードに追加 (Add)

```bash
gh project item-add <Project番号> --owner <owner> --url <Issue の URL>
```

### カードの状態（列）を移す (Move)

`Status` フィールドの選択肢（`Todo` / `In Progress` 等）を変更する．フィールド ID と選択肢 ID は `field-list` で確認する．

```bash
gh project item-edit \
  --id <アイテム ID> \
  --project-id <プロジェクト ID> \
  --field-id <Status フィールド ID> \
  --single-select-option-id <移動先の選択肢 ID>
```

※ ID の確認が煩雑なため，日常のカード移動は Web UI のボードで行い，一覧取得や自動化に `gh` を使う，という使い分けでもよい．

## トラブルシューティング (Troubleshooting)

- **`gh project` の書き込み系が無反応に見える**: `create` / `item-add` / `item-edit` は成功しても標準出力が空のことがある．`gh project list` / `item-list` で結果を確認する．
- **`gh project` がスコープエラーになる**: `project` スコープが未付与．`gh auth refresh -s project` を実行する．
- **コラボレーターが操作できない**: 招待を承認していない．`gh api "repos/<owner>/<repo>/collaborators" --jq '.[].login'` で承認済みメンバーを確認する．
- **`item-edit` の ID がわからない**: `gh project item-list` でアイテム ID，`gh project field-list` でフィールド ID・選択肢 ID を確認する．
