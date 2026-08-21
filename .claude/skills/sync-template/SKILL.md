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
| `docs/PROGRESS.md` | プロジェクト固有の進捗ログを保持する必要がある | 既存ファイルがある場合は内容を上書きしない．テンプレート側の骨組み（タイトル・案内コメント）に差分があれば通知のみ行い手動マージを促す |
| `.gitattributes` | プロジェクトによって設定が異なる可能性がある | 差分を表示し，ユーザーに「上書き / マージ / スキップ」を問う |
| `.claude/settings.json` | team モードで SessionStart(check_sync) 配線を追加している等，プロジェクト固有の hook 設定を保持する必要がある | 既存の hooks を保持しつつ，テンプレート側で追加・変更された hook のみ統合．差分を表示しユーザーに確認 |
| `.claude/template-overrides.md` | プロジェクト固有の「テンプレート改変台帳」．登録内容はプロジェクト固有 | 既存ファイルがある場合は内容を上書きしない．テンプレート側の骨組み（説明文・記入例）に差分があれば通知のみ行い手動マージを促す（`docs/PROGRESS.md` と同じ扱い） |

マージ処理の対象は **既存ファイルが存在する場合のみ**．初回同期（`.claude/template-sync-sha` がない状態）では全ファイルが A 扱いとなるが，これらのファイルはフレームワーク初期化（`flutter create` / `npm init` 等）や `/init` で既にプロジェクトに存在するのが通常なので，そのままマージ処理に入る．既存ファイルがない稀なケースに限り通常の `cp` で配置する．

## プロジェクト固有改変ファイル (Project Overrides)

マージ必須ファイル以外のテンプレート管理ファイル（`.claude/skills/*`，`.claude/rules/*`，`.claude/agents/*`，`.claude/hooks/*`，`docs/01_GUIDE/*` 等）も，プロジェクトの都合で**意図的に改変**されていることがある．これらを A/M のたびに `cp` で盲目的に上書きすると改変が失われるため，以下の 2 段構えで保護する．

### 台帳に登録された改変（明示的保護）

プロジェクトは意図的な改変を `.claude/template-overrides.md`（テンプレート改変台帳）に記録する（運用ルール: `.claude/rules/template-customization.md`）．台帳の「台帳」節の表から `| \`パス\` | 方針 | 理由 | 記録日 |` 形式の行を読み，登録ファイルは **`cp` せず方針に従って処理する**（ステップ 5.5）:

| 方針 | 処理 |
| --- | --- |
| `keep` | 触らない．テンプレート側に変更があれば，その差分（前回同期版 → 最新）を表示して通知のみ行う |
| `merge` | base = 前回同期版（`$LAST_SHA`），ours = プロジェクト版，theirs = テンプレート最新版の **3-way マージ**で，テンプレート側の差分だけを取り込みプロジェクトの改変を保持する．競合があれば同期担当エージェント（= あなた）が両者の意図を保って解決し，ユーザーに確認する |
| `ask` | プロジェクトの改変内容とテンプレート側の差分を表示し，ユーザーに「上書き / マージ / スキップ」を問う |

- パスが末尾 `/` で終わる行はディレクトリ指定であり，配下の全ファイルに同じ方針を適用する
- 台帳に載っているのにテンプレート側に存在しないパス（リネーム・削除済み，または記入ミス）は同期の最後に警告する
- 初回同期（`$LAST_SHA` 無し）では base が取れないため，`merge` は `ask` として扱う

### 台帳に無い改変の検出（安全網）

台帳に登録されていなくても，A/M 対象ファイルが**ローカルに存在し，かつ前回同期版（`$LAST_SHA` 時点のテンプレート版）と内容が異なる**場合，プロジェクト側で改変されている．これは上書きせずに「未登録改変」として集め（ステップ 5.3），まとめてユーザーに確認する（ステップ 5.6）．確認では「意図的な改変 → 台帳に登録してマージ／保持」「意図しない差分（古い手修正等） → 上書き」を選べるようにし，選択に応じて台帳へ行を追加する．

前回同期版に存在しない（テンプレートで新規追加された）パスにローカルファイルが既にある場合も同様に未登録改変として扱う（プロジェクトが独自に作ったファイルをテンプレート版で潰さない）．

## 同期対象外ファイル (Skip-on-sync Files)

以下のファイルはテンプレート紹介専用であり，テンプレートから作られた各プロジェクトには反映しない．コピー・上書き・削除のいずれも行わない．

| ファイル | 理由 | 方針 |
| --- | --- | --- |
| `README.md` | テンプレートの README は GitHub の repo ページ向けのテンプレート紹介用．各プロジェクトは独自の README を持つべき | プロジェクト側にコピー・上書きしない．テンプレート側の追加・変更・削除も無視する |

判定はステップ 5.3 のループ内でマージ必須ファイル判定より先に行う．

## モード依存ファイル (Mode-gated / Team-layer Files)

テンプレートは個人開発（solo）とチーム開発（team）の両モードを 1 つのリポジトリで提供する（GUIDE_03）．以下の**チーム層ファイル**は team モードのプロジェクトにのみ配置し，solo モードのプロジェクトには同期しない．

| ファイル | レイヤ |
| --- | --- |
| `docs/01_GUIDE/GUIDE_03_チーム開発ルール.md` | team |
| `.claude/hooks/check_sync.sh` | team |

※ `task-create`・`task-start`（+ `reference.md`）・`task-handoff` の各 skill は**共通層**（solo でも Issue ベースのタスク管理に使う）であり，モードに関わらず通常どおり同期する．

判定はプロジェクトの `.claude/project-mode`（`solo` または `team`．`/setup` が作成）で行う:

- **`team`**: チーム層ファイルを通常どおり同期（A/M/D すべて反映）．
- **`solo`**: チーム層ファイルを同期対象外ファイルと同様に**完全スキップ**（コピー・上書き・削除いずれもしない）．solo プロジェクトは `/setup` 時にこれらを削除済みのため，再配置しない．
- **`.claude/project-mode` が存在しない**（本機能導入前に作られた既存プロジェクト）: 安全側に倒して **`solo` 扱い**とし，チーム層を配置しない．同期の最後に「チーム開発なら `.claude/project-mode` に `team` と記入し再同期してください」と案内する．

なお `.claude/project-mode` 自体はテンプレートに含まれない（`/setup` が各プロジェクトで生成する）ため，同期で触れることはない．

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

取り込み対象のファイル一覧を種別ごとに整理してユーザーに提示する．マージ必須ファイル（`.gitignore`, `CLAUDE.md`, `docs/PROGRESS.md`, `.gitattributes`, `.claude/settings.json`）に変更がある場合は，**ユーザーが取り込み前に影響範囲を把握できるよう差分サマリーを先出しする**:

「**テンプレートに以下の変更があります:**

- 追加 (A): {ファイル一覧}
- 変更 (M): {ファイル一覧}
- 削除 (D): {ファイル一覧}
- リネーム (R): {旧名 → 新名}

**⚠ マージ必須ファイル（上書きせず差分マージします）:**

- `.gitignore`（既存 {N} 行 / テンプレート {M} 行．既存の固有ルールを保持し，テンプレート側で追加されている {K} 行を追記）
- `CLAUDE.md`（既存にプロジェクト固有セクションが {L} 行．テンプレート更新セクションのみマージ）
- `docs/PROGRESS.md`（プロジェクト固有の進捗ログ．既存があれば内容を保持し，差分があれば通知のみ）
- `.gitattributes`（差分 {D} 行．処理方針をユーザーに確認）
- `.claude/settings.json`（既存の hooks を保持し，テンプレート側で追加・変更された hook のみ統合）

**📒 台帳登録済みのプロジェクト固有改変ファイルに変更があります（方針に従って処理します）:**

- `{パス}`（方針: {keep/merge/ask}．理由: {台帳の理由}）

取り込みを開始します．」

台帳登録ファイルの抽出は，ステップ 5.2 のヘルパー定義後に次で行う（`CHANGED_ENTRIES` の A/M/R 対象のうち `override_policy` が空でないもの）．ステップ 4 の提示時点でヘルパーが未定義なら，5.2 の定義をここで先に実行してよい:

```bash
echo "$CHANGED_ENTRIES" | while IFS=$'\t' read -r status file newfile; do
  case "$status" in
    A|M) t="$file" ;;
    R*)  t="$newfile" ;;
    *)   continue ;;
  esac
  p=$(override_policy "$t")
  [ -n "$p" ] && echo "$t ($p)"
done
```

差分サマリーは以下で取得する（該当ファイルがマージ対象かつ既存ファイルがある場合のみ出力）:

```bash
# 既存ファイル行数
wc -l .gitignore CLAUDE.md docs/PROGRESS.md .gitattributes .claude/settings.json 2>/dev/null

# テンプレート側の実効行数（.gitignore）
grep -vE '^\s*(#|$)' "$TEMP_DIR/.gitignore" | wc -l

# 既存ファイルとテンプレート最新版の差分プレビュー
diff -u .gitignore "$TEMP_DIR/.gitignore" | head -30
diff -u CLAUDE.md "$TEMP_DIR/CLAUDE.md" | head -50
diff -u docs/PROGRESS.md "$TEMP_DIR/docs/PROGRESS.md" | head -30
diff -u .gitattributes "$TEMP_DIR/.gitattributes" | head -30
diff -u .claude/settings.json "$TEMP_DIR/.claude/settings.json" | head -30
```

## ステップ 5: ブランチ作成とファイル反映

### 5.1 ブランチ作成

`git checkout -b chore/sync-template` でブランチを作成する．既に同名のブランチが存在する場合は削除してから作り直す．

### 5.2 マージ必須ファイル判定ヘルパー

以降の処理で利用する判定関数を定義する:

```bash
MERGE_FILES=(".gitignore" "CLAUDE.md" "docs/PROGRESS.md" ".gitattributes" ".claude/settings.json" ".claude/template-overrides.md")
SKIP_FILES=("README.md")
OVERRIDES_FILE=".claude/template-overrides.md"

# 作業用ファイル（5.3 のループは pipe のサブシェルで回るため，結果はファイルに書き出す）
WORK_DIR=$(mktemp -d)
: > "$WORK_DIR/overrides.tsv"   # 台帳登録ファイル: status<TAB>file<TAB>policy
: > "$WORK_DIR/diverged.tsv"    # 未登録改変ファイル: status<TAB>file<TAB>(base|nobase)
TEAM_LAYER_FILES=(
  "docs/01_GUIDE/GUIDE_03_チーム開発ルール.md"
  ".claude/hooks/check_sync.sh"
)

# プロジェクトの開発モードを取得（未設定なら安全側で solo 扱い）
if [ -f .claude/project-mode ]; then
  PROJECT_MODE=$(tr -d '[:space:]' < .claude/project-mode)
else
  PROJECT_MODE="solo"
fi

is_merge_file() {
  local t="$1"
  for f in "${MERGE_FILES[@]}"; do
    [ "$f" = "$t" ] && return 0
  done
  return 1
}

is_skip_file() {
  local t="$1"
  for f in "${SKIP_FILES[@]}"; do
    [ "$f" = "$t" ] && return 0
  done
  return 1
}

is_team_layer_file() {
  local t="$1"
  for f in "${TEAM_LAYER_FILES[@]}"; do
    [ "$f" = "$t" ] && return 0
  done
  return 1
}

# 台帳（.claude/template-overrides.md）から対象パスの方針（keep/merge/ask）を返す．未登録なら空
# - 「台帳」節以外の表やコードブロック内の行は読まない
# - 末尾 / の行はディレクトリ指定（前方一致）
override_policy() {
  local t="$1"
  [ -f "$OVERRIDES_FILE" ] || return 0
  awk -F'|' -v target="$t" '
    /^```/ { infence = !infence; next }
    infence { next }
    /^## / { insec = ($0 ~ /^## 台帳/) ; next }
    !insec { next }
    /^\|/ {
      p=$2; gsub(/^[ \t]+|[ \t]+$/, "", p); gsub(/`/, "", p)
      pol=$3; gsub(/^[ \t]+|[ \t]+$/, "", pol)
      if (pol != "keep" && pol != "merge" && pol != "ask") next
      if (p == target) { print pol; exit }
      if (substr(p, length(p)) == "/" && index(target, p) == 1) { print pol; exit }
    }' "$OVERRIDES_FILE"
}

# 台帳に登録されている全パスを列挙する（最後の整合チェック用）
override_paths() {
  [ -f "$OVERRIDES_FILE" ] || return 0
  awk -F'|' '
    /^```/ { infence = !infence; next }
    infence { next }
    /^## / { insec = ($0 ~ /^## 台帳/) ; next }
    !insec { next }
    /^\|/ {
      p=$2; gsub(/^[ \t]+|[ \t]+$/, "", p); gsub(/`/, "", p)
      pol=$3; gsub(/^[ \t]+|[ \t]+$/, "", pol)
      if (pol == "keep" || pol == "merge" || pol == "ask") print p
    }' "$OVERRIDES_FILE"
}

# ローカルの既存ファイルが前回同期版（$LAST_SHA 時点のテンプレート版）から改変されているか
# 戻り値 0: 改変あり（stdout に base|nobase），1: 改変なし／判定不能
is_diverged() {
  local t="$1"
  [ -n "$LAST_SHA" ] && [ -f "$t" ] || return 1
  if git -C "$TEMP_DIR" cat-file -e "$LAST_SHA:$t" 2>/dev/null; then
    # 改行コード差（CRLF/LF）は改変とみなさない
    if git -C "$TEMP_DIR" show "$LAST_SHA:$t" | diff -q --strip-trailing-cr - "$t" >/dev/null 2>&1; then
      return 1
    fi
    echo base; return 0
  fi
  # 前回同期版に存在しない（テンプレート新規追加）のにローカルに既にある → 独自ファイル
  echo nobase; return 0
}
```

`PROJECT_MODE` が `solo` の場合，チーム層ファイル（`is_team_layer_file` が真）は同期対象外ファイルと同様に完全スキップする（ステップ 5.3 のループでは `is_skip_file` 判定の直後に `[ "$PROJECT_MODE" = "solo" ] && is_team_layer_file "$file"` を追加で判定して `continue` する）．`team` の場合は通常のコピー／マージ判定に進む．

### 5.3 通常コピー対象の反映（マージ必須ファイル以外）

マージ必須ファイルは後段（5.4）で個別処理するため，このループではスキップする．既存ファイルがない場合は通常どおり `cp` で配置する:

```bash
echo "$CHANGED_ENTRIES" | while IFS=$'\t' read -r status file newfile; do
  [ -z "$status" ] && continue
  case "$status" in
    A|M)
      # 同期対象外ファイルは完全にスキップ（コピーも上書きもしない）
      if is_skip_file "$file"; then
        continue
      fi
      # solo モードではチーム層ファイルを完全スキップ（再配置しない）
      if [ "$PROJECT_MODE" = "solo" ] && is_team_layer_file "$file"; then
        continue
      fi
      # マージ必須ファイルで既存ファイルがある場合は 5.4 で処理
      if is_merge_file "$file" && [ -f "$file" ]; then
        continue
      fi
      # 台帳登録済みのプロジェクト固有改変ファイル（既存あり）は 5.5 で方針に従って処理
      policy=$(override_policy "$file")
      if [ -n "$policy" ] && [ -f "$file" ]; then
        printf '%s\t%s\t%s\n' "$status" "$file" "$policy" >> "$WORK_DIR/overrides.tsv"
        continue
      fi
      # 台帳に無いが前回同期版から改変されているファイルは上書きせず 5.6 で確認
      if kind=$(is_diverged "$file"); then
        printf '%s\t%s\t%s\n' "$status" "$file" "$kind" >> "$WORK_DIR/diverged.tsv"
        continue
      fi
      mkdir -p "$(dirname "$file")"
      cp "$TEMP_DIR/$file" "$file"
      ;;
    R*)
      # file=旧パス, newfile=新パス
      if is_skip_file "$newfile"; then
        continue
      fi
      if [ "$PROJECT_MODE" = "solo" ] && is_team_layer_file "$newfile"; then
        continue
      fi
      if is_merge_file "$newfile" && [ -f "$newfile" ]; then
        continue
      fi
      policy=$(override_policy "$newfile")
      if [ -n "$policy" ] && [ -f "$newfile" ]; then
        printf '%s\t%s\t%s\n' "$status" "$newfile" "$policy" >> "$WORK_DIR/overrides.tsv"
        continue
      fi
      # 旧パスが台帳登録されている場合は，新パスに改変を引き継ぐべきか 5.5 で確認する
      oldpolicy=$(override_policy "$file")
      if [ -n "$oldpolicy" ] && [ -f "$file" ]; then
        printf 'RENAMED\t%s\t%s\t%s\n' "$file" "$newfile" "$oldpolicy" >> "$WORK_DIR/overrides.tsv"
        continue
      fi
      if kind=$(is_diverged "$newfile"); then
        printf '%s\t%s\t%s\n' "$status" "$newfile" "$kind" >> "$WORK_DIR/diverged.tsv"
        continue
      fi
      mkdir -p "$(dirname "$newfile")"
      cp "$TEMP_DIR/$newfile" "$newfile"
      ;;
  esac
done
```

ループ終了後，`$WORK_DIR/overrides.tsv` と `$WORK_DIR/diverged.tsv` にそれぞれ「台帳登録ファイル」「未登録改変ファイル」が溜まっている．5.4 のあと，5.5・5.6 で順に処理する．

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

**`docs/PROGRESS.md` のマージ手順**

`docs/PROGRESS.md` はプロジェクト固有の進捗ログのため，**既存ファイルがある場合は内容を上書きしない**．テンプレート側の更新は骨組み（タイトル・案内コメント）に限定されるはずなので，差分があれば通知のみ行い手動マージを促す:

1. 既存 `docs/PROGRESS.md` とテンプレート版 `$TEMP_DIR/docs/PROGRESS.md` を Read する
2. 差分を表示する:
   ```bash
   diff -u docs/PROGRESS.md "$TEMP_DIR/docs/PROGRESS.md"
   ```
3. 差分がある場合，「⚠ テンプレート側の `docs/PROGRESS.md` 骨組みに変更があります．既存の追記内容を保持したまま骨組みを反映したい場合はファイルを直接編集してください．自動マージは行いません．」と通知する
4. 既存ファイルが無い稀なケース（テンプレートを使わずに作られた古いプロジェクト等）に限り，テンプレート版をそのまま `cp` で配置する

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

**`.claude/settings.json` のマージ手順**

`settings.json` は hooks 設定を持つ．team モードのプロジェクトは PreToolUse（`restrict_repo_access.py`）に加えて SessionStart（`check_sync.sh`）を配線しているため，テンプレート版で盲目的に上書きするとプロジェクト固有の配線が失われる．同期担当エージェント（= あなた）が Read と Edit で JSON をマージする:

1. 既存 `.claude/settings.json` とテンプレート版 `$TEMP_DIR/.claude/settings.json` を Read する
2. 差分を表示する:
   ```bash
   diff -u .claude/settings.json "$TEMP_DIR/.claude/settings.json"
   ```
3. **既存の hooks を保持したまま**，テンプレート側で追加・変更された hook（イベント・matcher・command）のみを統合する:
   - 既存に無いイベント／hook はテンプレート版から追加する
   - プロジェクト固有の hook（team の SessionStart `check_sync.sh` 等）は残す
   - 同一 hook の command 変更（例: `restrict_repo_access.py` の起動方法変更）はテンプレート版に合わせる
   - solo モードで SessionStart(check_sync) が無い場合は，team 専用の配線を勝手に追加しない
4. `git diff .claude/settings.json` で結果を表示しユーザーに確認する

**`.claude/template-overrides.md` のマージ手順**

テンプレート改変台帳はプロジェクト固有の記録のため，**既存ファイルがある場合は内容を上書きしない**（`docs/PROGRESS.md` と同じ扱い）:

1. 差分を表示する:
   ```bash
   diff -u .claude/template-overrides.md "$TEMP_DIR/.claude/template-overrides.md"
   ```
2. 差分が骨組み（説明文・記入例）のみであれば，「⚠ テンプレート側の `.claude/template-overrides.md` の説明文に変更があります．台帳の行を保持したまま反映したい場合はファイルを直接編集してください．自動マージは行いません」と通知する
3. 既存ファイルが無い場合（本機能導入前に作られたプロジェクト）に限り，テンプレート版の雛形をそのまま `cp` で配置し，「台帳を新設しました．テンプレート由来ファイルを意図的に変更している箇所があれば登録してください（`.claude/rules/template-customization.md`）」と案内する

### 5.5 台帳登録ファイルの処理

`$WORK_DIR/overrides.tsv` の各行（`status<TAB>file<TAB>policy`，またはリネーム時の `RENAMED<TAB>旧パス<TAB>新パス<TAB>policy`）を方針ごとに処理する．共通で使う差分は次のとおり:

```bash
# テンプレート側の差分（前回同期版 → 最新）: 何が変わったのかを示す
show_template_delta() {  # $1=file
  if [ -n "$LAST_SHA" ] && git -C "$TEMP_DIR" cat-file -e "$LAST_SHA:$1" 2>/dev/null; then
    git -C "$TEMP_DIR" diff "$LAST_SHA" HEAD -- "$1"
  else
    echo "(前回同期版なし: テンプレート最新版を全文表示)"; cat "$TEMP_DIR/$1"
  fi
}

# プロジェクト側の改変（前回同期版 → プロジェクト現行）: 何を守るべきかを示す
show_project_delta() {  # $1=file
  if [ -n "$LAST_SHA" ] && git -C "$TEMP_DIR" cat-file -e "$LAST_SHA:$1" 2>/dev/null; then
    git -C "$TEMP_DIR" show "$LAST_SHA:$1" | diff -u --strip-trailing-cr --label "template@$LAST_SHA" --label "project" - "$1"
  else
    diff -u --strip-trailing-cr --label "template@HEAD" --label "project" "$TEMP_DIR/$1" "$1"
  fi
}

# 3-way マージ: base=前回同期版, ours=プロジェクト, theirs=テンプレート最新
# 戻り値 0: 競合なく $1 を更新, 1: 競合あり（$WORK_DIR/merged/$1 に競合マーカー付き結果）, 2: base 無しで実行不可
three_way_merge() {  # $1=file
  local f="$1" base ours theirs out
  [ -n "$LAST_SHA" ] && git -C "$TEMP_DIR" cat-file -e "$LAST_SHA:$f" 2>/dev/null || return 2
  mkdir -p "$WORK_DIR/3way/$(dirname "$f")" "$WORK_DIR/merged/$(dirname "$f")"
  base="$WORK_DIR/3way/$f.base"; ours="$WORK_DIR/3way/$f.ours"; theirs="$WORK_DIR/3way/$f.theirs"
  out="$WORK_DIR/merged/$f"
  # 改行コードを LF に揃えてからマージする（CRLF 混在で全行競合するのを防ぐ）
  git -C "$TEMP_DIR" show "$LAST_SHA:$f" | sed 's/\r$//' > "$base"
  sed 's/\r$//' "$f" > "$ours"
  sed 's/\r$//' "$TEMP_DIR/$f" > "$theirs"
  if git merge-file -p --diff3 -L project -L "template@$LAST_SHA" -L template@HEAD "$ours" "$base" "$theirs" > "$out"; then
    cp "$out" "$f"; return 0
  fi
  return 1
}
```

**`keep` の処理**

触らない．テンプレート側に変更があった事実だけを伝える:

「📒 `{file}` は台帳で `keep` のため取り込みません（理由: {台帳の理由}）．参考までにテンプレート側の変更は以下です:」

→ `show_template_delta "$file"` の出力を要約して添える．取り込みたくなった場合は台帳の行を削除または `merge` に変更して再同期するよう案内する．

**`merge` の処理**

1. `three_way_merge "$file"` を実行する
2. 戻り値 0（競合なし）: 「📒 `{file}` をマージしました（プロジェクトの改変を保持しつつテンプレートの差分を取り込み）」と伝え，`git diff -- "$file"` を表示してユーザーに確認する
3. 戻り値 1（競合あり）: `$WORK_DIR/merged/$file` の競合箇所と `show_project_delta` / `show_template_delta` を Read し，同期担当エージェント（= あなた）が**プロジェクトの改変意図（台帳の理由）とテンプレート側の変更意図の両方を保つ**ように解決した内容で `$file` を Edit / Write する．競合マーカーは残さない．解決方針を説明し，`git diff -- "$file"` を表示してユーザーに確認する．両立不能と判断した場合はユーザーに「プロジェクト版優先 / テンプレート版優先」を問う
4. 戻り値 2（初回同期等で base 無し）: `ask` の処理に切り替える

**`ask` の処理**

1. `show_project_delta "$file"` と `show_template_delta "$file"` を表示し，台帳の理由を添えてユーザーに問う:
   - **上書き**: テンプレート版で置換（改変を破棄．台帳の行を削除するか確認する）
   - **マージ**: `merge` の処理を行う（以後もマージでよければ台帳の方針を `merge` に更新するか確認する）
   - **スキップ**: 触らない
2. 選択に応じて処理する

**`RENAMED`（旧パスが台帳登録されているリネーム）の処理**

旧パス `{old}` にプロジェクトの改変があり，テンプレートでは `{new}` にリネームされている．ユーザーに状況を示し，次のいずれかを選ばせる:

- プロジェクト版（旧パス）を新パスへ `git mv` し，その上で台帳の方針（`keep` / `merge` / `ask`）に従って処理する（既定の推奨）．台帳のパスも新パスに書き換える
- テンプレート版を新パスに `cp` し，旧パスは 5.7 の削除確認に回す（改変を破棄．台帳の行を削除する）

### 5.6 未登録改変ファイルの確認

`$WORK_DIR/diverged.tsv` が空でない場合，ユーザーにまとめて提示する:

「**⚠ 台帳に未登録ですが，プロジェクト側で改変されているテンプレート由来ファイルがあります（上書きしていません）:**

- `{file}`（前回同期版との差分 {N} 行）／ `{file}`（前回同期版に無し: プロジェクト独自ファイル）

各ファイルについて処理を選んでください:

1. **意図的な改変 → マージ**（プロジェクトの改変を保持しつつテンプレートの差分を取り込む．台帳に `merge` で登録）
2. **意図的な改変 → 保持**（テンプレート側の変更を取り込まない．台帳に `keep` で登録）
3. **意図しない差分 → 上書き**（テンプレート版で置換）

判断材料としてプロジェクト側の改変内容を表示します．」

→ 各ファイルについて `show_project_delta "$file"` を表示する（長い場合は要約し，全文は求めに応じて出す）．

ユーザーの選択に応じて処理する:

- **マージ**: 5.5 の `merge` の処理を行い，台帳の「台帳」表に `| \`{file}\` | merge | {ユーザーから聞いた理由} | {今日の日付} |` を追記する
- **保持**: 触らない．台帳に `keep` で同様に追記する
- **上書き**: `cp "$TEMP_DIR/$file" "$file"` で置換する
- `nobase`（テンプレート新規追加パスにプロジェクト独自ファイルが既にある）の場合は base が無いためマージできない．「保持（`keep` 登録）」「上書き」「プロジェクト版を別名に退避してテンプレート版を配置」から選ばせる

理由を聞かずに勝手に台帳へ登録しない（理由は次回同期の判断根拠になる）．ユーザーが理由を省略した場合は「（理由未記入．次回同期時に確認）」と記録する．

### 5.7 削除候補の確認

削除候補（D およびリネーム元）を抽出し，**ローカルに実在するファイルだけ**に絞る:

```bash
DELETIONS=$(echo "$CHANGED_ENTRIES" | awk -F'\t' '$1 == "D" {print $2} $1 ~ /^R/ {print $2}')

# ローカルに実在するファイルのみを削除候補に残す
DELETIONS=$(echo "$DELETIONS" | while IFS= read -r f; do
  [ -n "$f" ] && [ -f "$f" ] && echo "$f"
done)
```

この実在フィルタにより，テンプレート側のリネームや過去の移行でプロジェクトが持っていない旧パス，solo モードに配置されないチーム層ファイルは，削除確認に出ることなく自動的に除外される．

さらに台帳の方針で振り分ける:

- 台帳で `keep` のファイルは削除候補から**自動的に除外**し，「📒 `{file}` はテンプレートから削除されましたが台帳で `keep` のため残します」と通知する
- 台帳で `merge` / `ask` のファイル，および 5.5 の `RENAMED` で「テンプレート版を採用」を選んだ旧パスは削除候補に残し，一覧に「（台帳: {方針}．理由: {理由}）」を添えて確認する．削除する場合は台帳の行も削除する

`$DELETIONS` が空でない場合，ユーザーに確認を求める:

「**以下のファイルはテンプレートから削除されています:**

{削除候補一覧}

プロジェクトからも削除してよいですか？（残したいファイルがあれば指定してください）」

ユーザー確認後，対象ファイルを `rm` で削除する．プロジェクトが独自に残したいファイルは削除対象から除外する（今後も残すなら台帳に `keep` で登録するよう勧める）．

### 5.8 台帳の整合チェック

台帳に登録されているのにテンプレート最新版に存在しないパスを警告する（リネーム・削除済み，またはパスの記入ミス）:

```bash
override_paths | while IFS= read -r p; do
  [ -z "$p" ] && continue
  case "$p" in
    */) [ -d "$TEMP_DIR/$p" ] || echo "$p" ;;
    *)  [ -f "$TEMP_DIR/$p" ] || echo "$p" ;;
  esac
done
```

該当があれば「⚠ 台帳の以下のパスはテンプレートに存在しません．リネーム／削除に追従してパスを直すか，プロジェクト独自ファイルなら台帳から行を削除してください: {一覧}」と通知する（自動では書き換えない）．

### 5.9 同期済み SHA の記録とクリーンアップ

```bash
echo "$NEW_SHA" > .claude/template-sync-sha
rm -rf "$TEMP_DIR" "$WORK_DIR"
```

## ステップ 6: 変更内容の分析

コピーされた変更を以下のカテゴリに分類する:

- **ルール変更**: コーディング規約，Git 運用ルール，テスト方針等の変更（`.claude/rules/` 配下の変更を含む）
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
- 開発モード: `{PROJECT_MODE}`（チーム層ファイルは {team: 同期対象 / solo: スキップ}）
- 取り込んだ変更: {変更の要約}
- プロジェクト固有改変: 台帳登録 {N} 件を処理（keep {a} / merge {b} / ask {c}），未登録改変 {M} 件を確認（台帳に {K} 件追加）
- コード修正: {あり（内容）/なし}

`/commit push` でプッシュと PR 作成ができます．」

`.claude/project-mode` が存在せず `solo` 扱いにした場合は，末尾に次を添える:

「※ `.claude/project-mode` が未設定のため solo として同期しました．チーム開発にする場合は `/set-mode team` を実行してください（`.claude/project-mode` を手で書き換えるだけでは team 層は配置されません）．」

## 注意事項

- 本コマンドは一時ディレクトリ（`mktemp -d`）に clone したテンプレートを Read / `cp` / `rm -rf` する．`restrict_repo_access.py` フックはシステム一時ディレクトリを許可ゾーンとして例外扱いしており，本コマンドはそれに依存している（フックの例外を外すと本コマンドが動かなくなる）
- テンプレートリポジトリへの push は行わない
- コード修正はユーザーの確認なしに実行しない
- マージ必須ファイル（`.gitignore`, `CLAUDE.md`, `docs/PROGRESS.md`, `.gitattributes`, `.claude/settings.json`, `.claude/template-overrides.md`）は必ずステップ 5.4 の手順でマージする．盲目的な `cp` で上書きしない（フレームワーク固有の除外ルールやプロジェクト固有セクションが失われる）
- テンプレート改変台帳（`.claude/template-overrides.md`）に登録されたファイルは `cp` せず，方針（`keep` / `merge` / `ask`）に従ってステップ 5.5 で処理する．台帳に無くても前回同期版と内容が異なるローカルファイルは上書きせず，ステップ 5.6 でユーザーに確認する（意図的な改変なら台帳に登録する）．`/sync-template` は**プロジェクトの改変を黙って消さない**ことを最優先にし，判断に迷う場合は上書きせずユーザーに問う
- 台帳への登録は必ず理由を添える（ユーザーから聞く）．理由の無い登録は次回同期時の判断材料にならない．台帳の書式（パス列はバッククォート囲み，方針列は `keep` / `merge` / `ask`）を崩さない
- 同期対象外ファイル（`README.md`）はテンプレート紹介用のためプロジェクトには反映しない．テンプレート側で追加・変更・削除があってもプロジェクトの該当ファイルは触らない
- チーム層ファイル（`GUIDE_03`／`check_sync.sh`）は `.claude/project-mode` が `team` のプロジェクトにのみ同期する．`task-*` skill は共通層のためモードに関わらず同期する．`solo`（または未設定）のプロジェクトには配置・更新・削除いずれもしない．`/sync-template` は「版の追従」のみを行い，**モードの切り替えはしない**．solo↔team の切替は `/set-mode <solo|team>` を使う（team 層ファイルの配置／削除・`settings.json` 配線・`CLAUDE.md` の team 化／solo 化・`project-mode` 更新を一括で行う）．`.claude/project-mode` を手で書き換えるだけでは切り替わらない
- `.claude/settings.json` はマージ必須ファイル．team の SessionStart(check_sync) 配線を保持したままテンプレートの hook 変更を統合する．盲目的な `cp` で上書きしない
- 通常コピー対象でもプロジェクト固有の変更が上書きされうる場合は，`git diff` で確認してユーザーに報告する
- テンプレートが管理するのは `.claude/` 配下のうち `agents/`，`skills/`，`rules/`，`hooks/`，`settings.json`，`template-sync-sha`，`template-overrides.md`（雛形のみ．登録内容はプロジェクト固有）のみ．`.claude/plans/` や `.claude/commit-context.md` 等のプロジェクト固有ファイルはテンプレートに含まれないため同期対象外
- `chore/sync-template` ブランチは他の作業ブランチと混ぜず，作成後は速やかにマージすること．複数の作業ブランチで `/sync-template` を実行すると `.claude/template-sync-sha` がコンフリクトする．コンフリクト時は新しい（HEAD 側の）SHA を採用すること．
