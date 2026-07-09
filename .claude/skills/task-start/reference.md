# Issues・Projects gh 操作リファレンス (gh Operations Reference)

`/task-create`・`/task-start`・`/task-handoff` の Issue・ボード操作で参照する詳細手順．
運用方針（Issue = 作業単位，直列運用，ボードの列等）は [GUIDE_03_チーム開発ルール](../../../docs/01_GUIDE/GUIDE_03_チーム開発ルール.md) に従う．

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

## Projects の操作 (Projects)

> ⚠ **Projects（ボード）は既定では未使用**（GUIDE_03）．以下は将来導入時の参考として残す．

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
