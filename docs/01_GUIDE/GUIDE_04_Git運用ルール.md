# Git 運用ルール (Git Operation Rules)

## 基本方針 (Basic Policy)

チーム開発におけるコードの整合性を保ち，手戻りを防ぐために以下の運用フローを徹底する．

- **メインブランチ (main)**:
  - 本番環境または常に動作可能な状態を維持するブランチ．
  - 直接のコミット（直プッシュ）は禁止する．必ずプルリクエスト（PR）経由でマージする．
- **作業ブランチ (Feature Branch)**:
  - 機能追加やバグ修正ごとに `main` から派生させて作成する．
  - 作業完了後，`main` へマージし，原則として削除する．
- **`.gitignore`**:
  - リポジトリ作成時に必ず整備する．
  - ビルド成果物，依存パッケージ（`node_modules/` 等），環境変数ファイル（`.env` 等）は必ず除外する．
  - `git add .` を安全に使うための前提条件であるため，メンバー追加時にも確認する．

## ブランチ命名規則 (Branch Naming)

### プレフィックス (Prefix)

作業の種類を一目で判別するために，以下のプレフィックスを使用する．

| プレフィックス | 用途 |
| --- | --- |
| `feature/` | 新機能の実装，仕様変更 |
| `fix/` | バグ修正 |
| `refactor/` | コードの整理（挙動は変えない） |
| `docs/` | ドキュメントの追記・修正 |
| `chore/` | ビルド設定やツール導入など，雑多な作業 |

### 書式 (Format)

- 書式: `[プレフィックス][概要]`
- 区切り: スラッシュ `/` を使用する．概要内の単語区切りはハイフン `-` を使用する．
- 概要: 作業内容を端的に表す英単語 2〜4 語程度（小文字）とする．
- 例:
  - `feature/player-jump`
  - `fix/collision-bug`
  - `docs/guide-update`

## 開発フロー (Development Workflow)

作業を開始してからマージされるまでの手順．

### ローカルの最新化

作業開始前は必ず `main` ブランチに切り替え，リモートの最新状態を取り込む．

```bash
git checkout main
git pull origin main
```

### 作業ブランチの作成

最新の `main` から新しいブランチを作成して移動する．

```bash
git checkout -b feature/new-function
```

### 実装とコミット

- 作業単位（意味のあるまとまり）ごとにコミットする．
- 巨大なコミットは避け，レビューしやすい粒度を心がける．
- AI によるコミットメッセージの生成機能を利用してもよい．ただし，生成されたメッセージは必ず「コミットメッセージ」セクションの書式ルールに合わせて修正すること．

```bash
git add .
git commit -m "[add] 新機能を実装"
```

### リモートへのプッシュ

作業ブランチをリモートリポジトリへ送信する．

```bash
git push origin feature/new-function
```

### プルリクエスト (PR) の作成

GitHub CLI (`gh`) を使用して PR を作成する．

```bash
gh pr create --title "[add] 新機能を実装" --body "概要"
```

- `--title` はコミットメッセージと同じ書式（`[タグ] 内容`）とする．
- 必要に応じて `--reviewer` でレビュワーを指定する．

### レビューと修正の対応

- レビュワーのコメントを確認し，修正が必要な場合はローカルで修正・コミット・プッシュを繰り返す．
- 全ての指摘に対応し，レビュワーから Approve をもらう．

### マージの実行

- マージ方式は「Create a merge commit」を使用する（作業ブランチの全コミット履歴が `main` に残る）．
  - 「Squash and merge」「Rebase and merge」は使用しない．

```bash
gh pr merge --merge --delete-branch
```

### ローカル環境のクリーンアップ

マージ完了後はローカル環境も最新状態に戻し，古いブランチを削除する．

```bash
git checkout main
git pull origin main
git branch -d feature/new-function
```

## コミットメッセージ (Commit Messages)

### 書式

- 書式: `[タグ] 内容`
- 言語: 日本語
- 句読点: 末尾に句点は不要

### タグ一覧

| タグ | 用途 |
| --- | --- |
| `[add]` | ファイルや機能の追加 |
| `[update]` | 機能やデータの更新・修正 |
| `[fix]` | バグ修正 |
| `[remove]` | 削除 |
| `[clean]` | 整理，リファクタリング |

## トラブルシューティング (Troubleshooting)

### コンフリクトが発生した場合

他メンバーの変更と競合した場合の対処手順．

**main の取り込み**

作業ブランチに `main` の最新内容をリベースして競合箇所を洗い出す．`merge` ではなく `rebase` を使うことで，履歴が線形に保たれレビューしやすくなる．

```bash
git checkout main
git pull origin main
git checkout feature/new-function
git rebase main
```

**競合の解消**

エディタ上で `<<<<<<<`，`=======`，`>>>>>>>` で囲まれた箇所を手動で修正する．修正後，以下のコマンドでリベースを続行する．

```bash
git add [修正したファイル]
git rebase --continue
```

全ての競合が解消されたらプッシュする．リベース後は履歴が書き換わるため `-f` オプションが必要になる．

```bash
git push -f origin feature/new-function
```

### 間違えて main にコミットしてしまった場合

**まだプッシュしていない場合**

コミットを取り消し，変更内容を保持したまま新しいブランチへ移動する．

```bash
git reset --soft HEAD^
git checkout -b feature/new-function
git commit -m "[add] ..."
```

**すでにプッシュしてしまった場合**

`main` への force push はチーム全員の履歴を壊す危険があるため，自分では対処しない．直ちにチームメンバーに報告し，全員で対応方針を決める．
