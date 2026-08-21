# テンプレート同期リファレンス (Template Sync Reference)

`/sync-template`（`SKILL.md`）の各ステップから参照される bash ヘルパーと個別処理の詳細手順．方針（どのファイルをどう扱うか）と全体フローは `SKILL.md` が定義し，本ファイルはその「実装」を持つ．

前提となるシェル変数（`SKILL.md` ステップ 2〜3 で設定）:

- `TEMP_DIR`: テンプレートを clone した一時ディレクトリ
- `NEW_SHA`: テンプレート HEAD の SHA
- `LAST_SHA`: 前回同期時のテンプレート SHA（`.claude/template-sync-sha`．初回同期では空）
- `CHANGED_ENTRIES`: `git diff --name-status` 形式の変更一覧（`status<TAB>file[<TAB>newfile]`）

## 変数・判定ヘルパー (Variables & Helpers)

ステップ 5.2 で定義する．以降のすべての処理が依存する:

```bash
MERGE_FILES=(".gitignore" "CLAUDE.md" "docs/PROGRESS.md" ".gitattributes" ".claude/settings.json" ".claude/template-overrides.md")
SKIP_FILES=("README.md")
TEAM_LAYER_FILES=(
  "docs/01_GUIDE/GUIDE_03_チーム開発ルール.md"
  ".claude/hooks/check_sync.sh"
)
OVERRIDES_FILE=".claude/template-overrides.md"

# 作業用ファイル（コピーループは pipe のサブシェルで回るため，結果はファイルに書き出す）
WORK_DIR=$(mktemp -d)
: > "$WORK_DIR/overrides.tsv"   # 台帳登録ファイル: status<TAB>file<TAB>policy（リネームは RENAMED<TAB>旧<TAB>新<TAB>policy）
: > "$WORK_DIR/diverged.tsv"    # 未登録改変ファイル: status<TAB>file<TAB>(base|nobase)

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

# 台帳に登録されている全パスを列挙する（整合チェック用）
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

## 変更一覧の差分サマリー (Diff Summary)

ステップ 4 で，マージ必須ファイル・台帳登録ファイルに変更がある場合に先出しする情報を取得する．

マージ必須ファイルの差分プレビュー（該当ファイルが変更対象かつ既存ファイルがある場合のみ出力）:

```bash
# 既存ファイル行数
wc -l "${MERGE_FILES[@]}" 2>/dev/null

# テンプレート側の実効行数（.gitignore）
grep -vE '^\s*(#|$)' "$TEMP_DIR/.gitignore" | wc -l

# 既存ファイルとテンプレート最新版の差分プレビュー
for f in "${MERGE_FILES[@]}"; do
  [ -f "$f" ] && [ -f "$TEMP_DIR/$f" ] && { echo "=== $f ==="; diff -u "$f" "$TEMP_DIR/$f" | head -50; }
done
```

台帳登録ファイルの抽出（`CHANGED_ENTRIES` の A/M/R 対象のうち `override_policy` が空でないもの）:

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

## 通常コピーループ (Copy Loop)

ステップ 5.3 で実行する．判定は「同期対象外 → solo のチーム層 → マージ必須（既存あり）→ 台帳登録（既存あり）→ 未登録改変 → `cp`」の順．`cp` しなかったものはそれぞれ後段で処理する:

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

## マージ必須ファイルの個別手順 (Merge Procedures)

ステップ 5.4 で，`$CHANGED_ENTRIES` に A/M/R* で含まれ，かつ既存ファイルが存在するマージ必須ファイルを以下の手順で処理する．

### `.gitignore`

1. 既存 `.gitignore` とテンプレート `$TEMP_DIR/.gitignore` を読む
2. テンプレート側の**実効行**（コメント `#` 行・空行を除く）を抽出する:

   ```bash
   grep -vE '^\s*(#|$)' "$TEMP_DIR/.gitignore"
   ```

3. 抽出した各行について，既存ファイルに完全一致で含まれていないものだけを収集する
4. 収集した行がある場合，既存ファイル末尾に以下のマーカー付きブロックで追記する:

   ```text

   # --- from template (sync-template) ---
   {追加行}
   ```

   - 既存ファイル末尾に同じマーカーが既にある場合は，マーカー以降に追記して重複マーカーを作らない
5. `git diff .gitignore` で結果を表示しユーザーに確認する

### `CLAUDE.md`

同期担当エージェント（= あなた）が Read と Edit ツールを使ってセクション単位でマージする:

1. 既存 `CLAUDE.md` とテンプレート版 `$TEMP_DIR/CLAUDE.md` を Read する
2. テンプレート側の変更箇所を特定する（初回同期は LAST_SHA がないためテンプレート全体を「更新候補」として扱う）:

   ```bash
   if [ -n "$LAST_SHA" ]; then
     git -C "$TEMP_DIR" show "$LAST_SHA:CLAUDE.md" 2>/dev/null > "$WORK_DIR/claude_md_old" || true
     diff -u "$WORK_DIR/claude_md_old" "$TEMP_DIR/CLAUDE.md"
   fi
   ```

3. セクションを以下の基準で分類する:
   - **共通セクション（テンプレート管理，更新対象）**: 「必須ルール」「エージェントチーム」「Git 運用」「ドキュメント」節など，テンプレートに由来する節
   - **プロジェクト固有セクション（保持対象）**: 「プロジェクト名」「開発進捗」や，プロジェクトが追加した固有規約の節
4. テンプレート側で変更があった共通セクションのみを Edit で既存ファイルに反映する．プロジェクト固有セクションは一切触らない
5. `git diff CLAUDE.md` で結果を表示しユーザーに確認する

### `docs/PROGRESS.md`

プロジェクト固有の進捗ログのため，**既存ファイルがある場合は内容を上書きしない**．テンプレート側の更新は骨組み（タイトル・案内コメント）に限定されるはずなので，差分があれば通知のみ行い手動マージを促す:

1. 既存 `docs/PROGRESS.md` とテンプレート版 `$TEMP_DIR/docs/PROGRESS.md` を Read する
2. 差分を表示する:

   ```bash
   diff -u docs/PROGRESS.md "$TEMP_DIR/docs/PROGRESS.md"
   ```

3. 差分がある場合，「⚠ テンプレート側の `docs/PROGRESS.md` 骨組みに変更があります．既存の追記内容を保持したまま骨組みを反映したい場合はファイルを直接編集してください．自動マージは行いません．」と通知する
4. 既存ファイルが無い稀なケース（テンプレートを使わずに作られた古いプロジェクト等）に限り，テンプレート版をそのまま `cp` で配置する

### `.gitattributes`

1. 既存ファイルとテンプレート版の差分を表示する:

   ```bash
   diff -u .gitattributes "$TEMP_DIR/.gitattributes"
   ```

2. ユーザーに以下のいずれかを選ばせる:
   - **上書き**: テンプレート版で既存を置換
   - **マージ**: 両者のルールを統合（具体的な統合内容は対話で決定）
   - **スキップ**: 既存ファイルを維持
3. 選択に応じて処理する

### `.claude/settings.json`

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

### `.claude/template-overrides.md`

テンプレート改変台帳はプロジェクト固有の記録のため，**既存ファイルがある場合は内容を上書きしない**（`docs/PROGRESS.md` と同じ扱い）:

1. 差分を表示する:

   ```bash
   diff -u .claude/template-overrides.md "$TEMP_DIR/.claude/template-overrides.md"
   ```

2. 差分が骨組み（説明文・記入例）のみであれば，「⚠ テンプレート側の `.claude/template-overrides.md` の説明文に変更があります．台帳の行を保持したまま反映したい場合はファイルを直接編集してください．自動マージは行いません」と通知する
3. 既存ファイルが無い場合（本機能導入前に作られたプロジェクト）に限り，テンプレート版の雛形をそのまま `cp` で配置し，「台帳を新設しました．テンプレート由来ファイルを意図的に変更している箇所があれば登録してください（`.claude/rules/template-customization.md`）」と案内する

## 削除候補の抽出 (Deletion Candidates)

ステップ 5.7 で，削除候補（D およびリネーム元）を抽出し，**ローカルに実在するファイルだけ**に絞る:

```bash
DELETIONS=$(echo "$CHANGED_ENTRIES" | awk -F'\t' '$1 == "D" {print $2} $1 ~ /^R/ {print $2}')

# ローカルに実在するファイルのみを削除候補に残す
DELETIONS=$(echo "$DELETIONS" | while IFS= read -r f; do
  [ -n "$f" ] && [ -f "$f" ] && echo "$f"
done)
```

この実在フィルタにより，テンプレート側のリネームや過去の移行でプロジェクトが持っていない旧パス，solo モードに配置されないチーム層ファイルは，削除確認に出ることなく自動的に除外される．台帳の方針による振り分け（`keep` は自動除外等）は `SKILL.md` ステップ 5.7 に従う．

## 台帳の整合チェック (Override Consistency Check)

ステップ 5.8 で，台帳に登録されているのにテンプレート最新版に存在しないパスを列挙する（リネーム・削除済み，またはパスの記入ミス）:

```bash
override_paths | while IFS= read -r p; do
  [ -z "$p" ] && continue
  case "$p" in
    */) [ -d "$TEMP_DIR/$p" ] || echo "$p" ;;
    *)  [ -f "$TEMP_DIR/$p" ] || echo "$p" ;;
  esac
done
```

## クリーンアップ (Cleanup)

ステップ 5.9 で同期済み SHA を記録し，一時ディレクトリを削除する:

```bash
echo "$NEW_SHA" > .claude/template-sync-sha
rm -rf "$TEMP_DIR" "$WORK_DIR"
```
