---
name: sync-template
model: sonnet
description: "テンプレートリポジトリから最新の変更を取り込み，ルール変更に伴うコード修正を行う．"
argument-hint: ""
---

あなたはテンプレート同期の担当者です．
テンプレートリポジトリから最新の変更を取り込み，必要に応じてプロジェクトのコードを修正してください．

実行環境: bash（Git Bash または Unix シェル）が必要．`mktemp`, `rm -rf`, `cat`, シェル変数展開を使用する．

テンプレート URL: `https://github.com/rintoHasegawa/programming-template.git`

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

取り込み対象のファイル一覧を種別ごとに整理してユーザーに提示する:

「**テンプレートに以下の変更があります:**

- 追加 (A): {ファイル一覧}
- 変更 (M): {ファイル一覧}
- 削除 (D): {ファイル一覧}
- リネーム (R): {旧名 → 新名}

取り込みを開始します．」

## ステップ 5: ブランチ作成とファイル反映

1. `git checkout -b chore/sync-template` でブランチを作成する
   - 既に同名のブランチが存在する場合は削除してから作り直す

2. 追加・変更・リネーム先を反映する:

   ```bash
   echo "$CHANGED_ENTRIES" | while IFS=$'\t' read -r status file newfile; do
     [ -z "$status" ] && continue
     case "$status" in
       A|M)
         mkdir -p "$(dirname "$file")"
         cp "$TEMP_DIR/$file" "$file"
         ;;
       R*)
         # file=旧パス, newfile=新パス
         mkdir -p "$(dirname "$newfile")"
         cp "$TEMP_DIR/$newfile" "$newfile"
         ;;
     esac
   done
   ```

3. 削除候補（D およびリネーム元）を抽出する:

   ```bash
   DELETIONS=$(echo "$CHANGED_ENTRIES" | awk -F'\t' '$1 == "D" {print $2} $1 ~ /^R/ {print $2}')
   ```

4. `$DELETIONS` が空でない場合，ユーザーに確認を求める:

   「**以下のファイルはテンプレートから削除されています:**

   {削除候補一覧}

   プロジェクトからも削除してよいですか？（残したいファイルがあれば指定してください）」

   ユーザー確認後，対象ファイルを `rm` で削除する．プロジェクトが独自に残したいファイルは削除対象から除外する．

5. 同期済み SHA を記録し，一時ディレクトリを削除する:

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
- ファイルコピーによりプロジェクト固有の変更が上書きされる場合は，`git diff` で確認してユーザーに報告する
- `chore/sync-template` ブランチは他の作業ブランチと混ぜず，作成後は速やかにマージすること．複数の作業ブランチで `/sync-template` を実行すると `.claude/template-sync-sha` がコンフリクトする．コンフリクト時は新しい（HEAD 側の）SHA を採用すること．
