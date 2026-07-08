# Issues・Projects 運用ガイド (Issues & Projects Operations Guide)

GitHub Issues と Projects を `gh` コマンドで操作する手順を定義する．人間も AI（Claude Code）も同じコマンドで操作する．

> ⚠ **Projects の操作は現時点では未使用**．本ガイドの「Projects の操作」セクションは将来導入時の参考として残す．現状の運用は Issue ベースで完結する（[GUIDE_06](GUIDE_06_チーム開発ルール.md)）．Issue 操作・タスクライフサイクルは引き続き本ガイドの該当セクションを参照する．

タスク管理の方針（Issue = 作業単位，ボードの列，直列運用等）は [GUIDE_06](GUIDE_06_チーム開発ルール.md) に従う．本ガイドはその操作手順を扱う．

## 基本方針 (Basic Policy)

- 操作は GitHub CLI（`gh`）に統一する．Web UI でも同じことはできるが，再現性とドキュメント化のため `gh` を基準とする．
- Issue がタスクの単位であり，`/implement` の入力でもある（GUIDE_06）．
- AI に Issue を読ませる場合は `gh issue view <Issue番号>` を使う．

### 必要なスコープ (Required Scopes)

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

## Issues の操作 (Issues)

### 参照 (View)

```bash
gh issue list                            # 一覧
gh issue list --assignee @me             # 自分の担当のみ
gh issue list --state open               # 未クローズのみ
gh issue view <Issue番号>                 # 詳細表示
gh issue view <Issue番号> --json title,body,assignees,labels   # 構造化出力（AI への受け渡し等）
```

### 作成 (Create)

```bash
gh issue create --title "<タイトル>" --body "<本文>"
```

- 本文には「やること」と「完了条件（受け入れ基準）」を必ず書く（GUIDE_06）．
- `--assignee <ユーザー名>`，`--label <ラベル>` で担当・ラベルを同時に指定できる．

### 更新・コメント・状態変更 (Edit / Comment / State)

```bash
gh issue edit <Issue番号> --add-assignee @me        # 自分をアサイン
gh issue edit <Issue番号> --add-label "<ラベル>"     # ラベル追加
gh issue comment <Issue番号> --body "<コメント>"     # コメント追加
gh issue close <Issue番号>                          # クローズ
gh issue reopen <Issue番号>                         # 再オープン
```

## Projects の操作 (Projects)

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

## タスクのライフサイクル (Task Lifecycle)

GUIDE_06 の「タスクの流れ」を `gh` コマンドで具体化したもの．

※ ステップ 1 は `/task-create <タスクの説明>` で，ステップ 2〜4 は `/task-start <Issue番号>` で半自動化できる．以下は手動でも行える操作の参照．

1. **Issue 起票**（完了条件を明記）

   ```bash
   gh issue create --title "<タイトル>" --body "<やること・完了条件>"
   ```

2. **アサインしてボードを In Progress へ**

   ```bash
   gh issue edit <Issue番号> --add-assignee @me
   gh project item-add <Project番号> --owner <owner> --url <Issue URL>   # 未追加の場合
   ```

   ボードの `Status` を `In Progress` に移す（`item-edit` または Web UI）．

3. **作業ブランチ作成**（`.claude/rules/git-conventions.md` の基本形式）

   ```bash
   git checkout main && git pull origin main
   git checkout -b feature/<概要>
   ```

   ※ 既存 Issue から作る場合は `gh issue develop <Issue番号> --name feature/<概要> --base main` を使うと，ブランチ作成と Issue↔ブランチのリンクを同時に行える（ブランチ名に依存しないリンク手段）．

4. **実装**: `gh issue view <Issue番号>` で本文を取得し，`/implement` に渡す（GUIDE_05）．

5. **PR 作成**: 本文に `Closes #<Issue番号>` を記載する（PR 作成手順は `.claude/skills/commit/reference.md`）．

6. **マージ**: マージで Issue は自動クローズされ，連動設定があればカードも `Done` へ移る（自動化設定は管理者作業．GUIDE_06「管理者の初期設定」）．

## トラブルシューティング (Troubleshooting)

- **`gh project` の書き込み系が無反応に見える**: `create` / `item-add` / `item-edit` は成功しても標準出力が空のことがある．`gh project list` / `item-list` で結果を確認する．
- **`gh project` がスコープエラーになる**: `project` スコープが未付与．`gh auth refresh -s project` を実行する．
- **コラボレーターが操作できない**: 招待を承認していない．`gh api "repos/<owner>/<repo>/collaborators" --jq '.[].login'` で承認済みメンバーを確認する．
- **`item-edit` の ID がわからない**: `gh project item-list` でアイテム ID，`gh project field-list` でフィールド ID・選択肢 ID を確認する．
