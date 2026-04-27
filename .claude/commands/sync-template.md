---
name: sync-template
model: inherit
description: "テンプレートリポジトリから最新の変更を取り込み，ルール変更に伴うコード修正を行う．"
argument-hint: ""
---

あなたはテンプレート同期の担当者です．
テンプレートリポジトリから最新の変更を取り込み，必要に応じてプロジェクトのコードを修正してください．

実行環境: bash（Git Bash または Unix シェル）が必要．`mktemp`, `rm -rf`, `cat`, シェル変数展開を使用する．

テンプレート URL: `https://github.com/rintoHasegawa/programming-template.git`

## マージ必須ファイル (Merge-required Files)

以下のファイルは「プロジェクト固有の内容 + テンプレートの共通ルール」のハイブリッドのため，**上書きせずマージする**．コピー処理（ステップ 5.3）に入る前に対象ファイルかを判定し，該当する場合はステップ 5.4 のマージ手順に分岐させる．

| ファイル | 理由 | マージ方針 |
| --- | --- | --- |
| `.gitignore` | フレームワーク固有ルール（Flutter/Node 等）を保持する必要がある | テンプレート側の実効行で既存に含まれないもののみを追記．既存行は触らない |
| `CLAUDE.md` | プロジェクト名・開発進捗・固有規約を保持する必要がある | テンプレートで変更された共通セクション（必須ルール，エージェントチーム，ドキュメント構成等）のみを Edit で更新．プロジェクト固有セクションは触らない |
| `.gitattributes` | プロジェクトによって設定が異なる可能性がある | 差分を表示し，ユーザーに「上書き / マージ / スキップ」を問う |

マージ処理の対象は **既存ファイルが存在する場合のみ**．初回同期（`.claude/template-sync-sha` がない状態）では全ファイルが A 扱いとなるが，これらのファイルはフレームワーク初期化（`flutter create` / `npm init` 等）や `/init` で既にプロジェクトに存在するのが通常なので，そのままマージ処理に入る．既存ファイルがない稀なケースに限り通常の `cp` で配置する．

## ステップ 1: 事前確認

`git status` でワーキングツリーがクリーンか確認する．

**コミットされていない変更がある場合:**

「⚠ コミットされていない変更があります．
先に変更をコミットするか，stash してから再度 `/sync-template` を実行してください．」

→ ここで処理を中断する．

## ステップ 2: テンプレートを一時ディレクトリにクローン

以下を実行する:

```bash
TEMPLATE_URL="https://github.com/rintoHasegawa/programming-template.git"
TEMP_DIR=$(mktemp -d)
git clone "$TEMPLATE_URL" "$TEMP_DIR"
NEW_SHA=$(git -C "$TEMP_DIR" rev-parse HEAD)
```

## ステップ 3: 変更ファイルの特定

`.claude/template-sync-sha` の有無で処理を分岐する:

**ファイルが存在する場合（2 回目以降の同期）:**

```bash
LAST_SHA=$(cat .claude/template-sync-sha)
```

- `NEW_SHA == LAST_SHA` の場合:
  `rm -rf "$TEMP_DIR"` で一時ディレクトリを削除し，「テンプレートに新しい変更はありません．既に最新です．」と報告して終了する．

- `NEW_SHA != LAST_SHA` の場合:
  ```bash
  # A=追加, M=変更, D=削除, R=リネーム の種別付きで取得
  CHANGED_ENTRIES=$(git -C "$TEMP_DIR" diff --name-status "$LAST_SHA" HEAD)
  ```
  で変更されたファイルを種別付きで取得する．

**ファイルが存在しない場合（初回同期）:**

テンプレートの全ファイルを「追加（A）」として対象に含める:

```bash
CHANGED_ENTRIES=$(cd "$TEMP_DIR" && find . -type f -not -path "./.git/*" | sed 's|^\./||' | awk -v OFS='\t' '{print "A", $0}')
```

## ステップ 4: 変更一覧をユーザーに提示

取り込み対象のファイル一覧を種別ごとに整理してユーザーに提示する．マージ必須ファイル（`.gitignore`, `CLAUDE.md`, `.gitattributes`）に変更がある場合は，**ユーザーが取り込み前に影響範囲を把握できるよう差分サマリーを先出しする**:

「**テンプレートに以下の変更があります:**

- 追加 (A): {ファイル一覧}
- 変更 (M): {ファイル一覧}
- 削除 (D): {ファイル一覧}
- リネーム (R): {旧名 → 新名}

**⚠ マージ必須ファイル（上書きせず差分マージします）:**

- `.gitignore`（既存 {N} 行 / テンプレート {M} 行．既存の固有ルールを保持し，テンプレート側で追加されている {K} 行を追記）
- `CLAUDE.md`（既存にプロジェクト固有セクションが {L} 行．テンプレート更新セクションのみマージ）
- `.gitattributes`（差分 {D} 行．処理方針をユーザーに確認）

取り込みを開始します．」

差分サマリーは以下で取得する（該当ファイルがマージ対象かつ既存ファイルがある場合のみ出力）:

```bash
# 既存ファイル行数
wc -l .gitignore CLAUDE.md .gitattributes 2>/dev/null

# テンプレート側の実効行数（.gitignore）
grep -vE '^\s*(#|$)' "$TEMP_DIR/.gitignore" | wc -l

# 既存ファイルとテンプレート最新版の差分プレビュー
diff -u .gitignore "$TEMP_DIR/.gitignore" | head -30
diff -u CLAUDE.md "$TEMP_DIR/CLAUDE.md" | head -50
diff -u .gitattributes "$TEMP_DIR/.gitattributes" | head -30
```

## ステップ 5: ブランチ作成とファイル反映

### 5.1 ブランチ作成

`git checkout -b chore/sync-template` でブランチを作成する．既に同名のブランチが存在する場合は削除してから作り直す．

### 5.2 マージ必須ファイル判定ヘルパー

以降の処理で利用する判定関数を定義する:

```bash
MERGE_FILES=(".gitignore" "CLAUDE.md" ".gitattributes")

is_merge_file() {
  local t="$1"
  for f in "${MERGE_FILES[@]}"; do
    [ "$f" = "$t" ] && return 0
  done
  return 1
}
```

### 5.3 通常コピー対象の反映（マージ必須ファイル以外）

マージ必須ファイルは後段（5.4）で個別処理するため，このループではスキップする．既存ファイルがない場合は通常どおり `cp` で配置する:

```bash
echo "$CHANGED_ENTRIES" | while IFS=$'\t' read -r status file newfile; do
  [ -z "$status" ] && continue
  case "$status" in
    A|M)
      # マージ必須ファイルで既存ファイルがある場合は 5.4 で処理
      if is_merge_file "$file" && [ -f "$file" ]; then
        continue
      fi
      mkdir -p "$(dirname "$file")"
      cp "$TEMP_DIR/$file" "$file"
      ;;
    R*)
      # file=旧パス, newfile=新パス
      if is_merge_file "$newfile" && [ -f "$newfile" ]; then
        continue
      fi
      mkdir -p "$(dirname "$newfile")"
      cp "$TEMP_DIR/$newfile" "$newfile"
      ;;
  esac
done
```

### 5.4 マージ必須ファイルの個別処理

`$CHANGED_ENTRIES` に A/M/R* で含まれ，かつ既存ファイルが存在するマージ必須ファイルについて，以下の手順で処理する．

**`.gitignore` のマージ手順**

1. 既存 `.gitignore` とテンプレート `$TEMP_DIR/.gitignore` を読む
2. テンプレート側の**実効行**（コメント `#` 行・空行を除く）を抽出する:
   ```bash
   grep -vE '^\s*(#|$)' "$TEMP_DIR/.gitignore"
   ```
3. 抽出した各行について，既存ファイルに完全一致で含まれていないものだけを収集する
4. 収集した行がある場合，既存ファイル末尾に以下のマーカー付きブロックで追記する:
   ```
   
   # --- from template (sync-template) ---
   {追加行}
   ```
   - 既存ファイル末尾に同じマーカーが既にある場合は，マーカー以降に追記して重複マーカーを作らない
5. `git diff .gitignore` で結果を表示しユーザーに確認する

**`CLAUDE.md` のマージ手順**

同期担当エージェント（= あなた）が Read と Edit ツールを使ってセクション単位でマージする:

1. 既存 `CLAUDE.md` とテンプレート版 `$TEMP_DIR/CLAUDE.md` を Read する
2. テンプレート側の変更箇所を特定する（初回同期は LAST_SHA がないためテンプレート全体を「更新候補」として扱う）:
   ```bash
   if [ -n "$LAST_SHA" ]; then
     git -C "$TEMP_DIR" show "$LAST_SHA:CLAUDE.md" 2>/dev/null > /tmp/claude_md_old || true
     diff -u /tmp/claude_md_old "$TEMP_DIR/CLAUDE.md"
   fi
   ```
3. セクションを以下の基準で分類する:
   - **共通セクション（テンプレート管理，更新対象）**: 「必須ルール」「エージェントチーム」「Git 運用」「ドキュメント」節など，テンプレートに由来する節
   - **プロジェクト固有セクション（保持対象）**: 「プロジェクト名」「開発進捗」や，プロジェクトが追加した固有規約の節
4. テンプレート側で変更があった共通セクションのみを Edit で既存ファイルに反映する．プロジェクト固有セクションは一切触らない
5. `git diff CLAUDE.md` で結果を表示しユーザーに確認する

**`.gitattributes` のマージ手順**

1. 既存ファイルとテンプレート版の差分を表示する:
   ```bash
   diff -u .gitattributes "$TEMP_DIR/.gitattributes"
   ```
2. ユーザーに以下のいずれかを選ばせる:
   - **上書き**: テンプレート版で既存を置換
   - **マージ**: 両者のルールを統合（具体的な統合内容は対話で決定）
   - **スキップ**: 既存ファイルを維持
3. 選択に応じて処理する

### 5.5 削除候補の確認

削除候補（D およびリネーム元）を抽出する:

```bash
DELETIONS=$(echo "$CHANGED_ENTRIES" | awk -F'\t' '$1 == "D" {print $2} $1 ~ /^R/ {print $2}')
```

`$DELETIONS` が空でない場合，ユーザーに確認を求める:

「**以下のファイルはテンプレートから削除されています:**

{削除候補一覧}

プロジェクトからも削除してよいですか？（残したいファイルがあれば指定してください）」

ユーザー確認後，対象ファイルを `rm` で削除する．プロジェクトが独自に残したいファイルは削除対象から除外する．

### 5.6 同期済み SHA の記録とクリーンアップ

```bash
echo "$NEW_SHA" > .claude/template-sync-sha
rm -rf "$TEMP_DIR"
```

## ステップ 6: 変更内容の分析

コピーされた変更を以下のカテゴリに分類する:

- **ルール変更**: コーディング規約，Git 運用ルール，テスト方針等の変更
- **ドキュメント更新**: 手順書やガイドの改善
- **設定変更**: CLAUDE.md や .claude/ 配下の変更
- **その他**: 上記に該当しない変更

## ステップ 7: ルール変更に伴うコード修正

ステップ 6 で「ルール変更」に該当するものがある場合:

1. 変更されたルールの内容を要約してユーザーに報告する
2. そのルール変更がプロジェクトの既存コードに影響するか分析する
3. 影響がある場合，修正が必要な箇所と修正内容を提示する
4. ユーザーの確認を得てから修正を実行する

**影響がない場合:**

「ルール変更に伴うコード修正は不要です．」

## ステップ 8: 結果報告

すべての作業が完了したら，結果を報告する:

「**テンプレート同期が完了しました．**

- ブランチ: `{ブランチ名}`
- 取り込んだ変更: {変更の要約}
- コード修正: {あり（内容）/なし}

`/commit push` でプッシュと PR 作成ができます．」

## 注意事項

- テンプレートリポジトリへの push は行わない
- コード修正はユーザーの確認なしに実行しない
- マージ必須ファイル（`.gitignore`, `CLAUDE.md`, `.gitattributes`）は必ずステップ 5.4 の手順でマージする．盲目的な `cp` で上書きしない（フレームワーク固有の除外ルールやプロジェクト固有セクションが失われる）
- 通常コピー対象でもプロジェクト固有の変更が上書きされうる場合は，`git diff` で確認してユーザーに報告する
- テンプレートが管理するのは `.claude/` 配下のうち `agents/`，`commands/`，`hooks/`，`settings.json`，`template-sync-sha` のみ．`.claude/plans/` や `.claude/commit-context.md` 等のプロジェクト固有ファイルはテンプレートに含まれないため同期対象外
- `chore/sync-template` ブランチは他の作業ブランチと混ぜず，作成後は速やかにマージすること．複数の作業ブランチで `/sync-template` を実行すると `.claude/template-sync-sha` がコンフリクトする．コンフリクト時は新しい（HEAD 側の）SHA を採用すること．
