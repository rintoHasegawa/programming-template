---
name: set-mode
model: inherit
description: "開発モード（solo / team）を切り替える．チーム層ファイル・settings.json・CLAUDE.md・.claude/project-mode を一括で整合させる．"
argument-hint: "<solo | team>"
---

あなたは開発モード切替の担当者です．
プロジェクトの開発モードを **solo（個人開発）↔ team（チーム開発）** で切り替え，モードに紐づくファイル一式を過不足なく整合させてください．

`/sync-template` は「テンプレートの版を追従する」道具であり，モード遷移（ファイルの追加・削除，CLAUDE.md/settings.json の書き換え）は行いません．本コマンドがその遷移を担います．

実行環境: bash（Git Bash または Unix シェル）が必要．`mktemp`, `rm -rf`, `cp`, シェル変数展開を使用する．

テンプレート URL: `https://github.com/rintoHasegawa/programming-template.git`

## チーム層ファイル (Team-layer Files)

モードに応じて配置／削除する対象．`/sync-template` の「モード依存ファイル」と同一のリストに保つこと．

```
docs/01_GUIDE/GUIDE_03_チーム開発ルール.md
.claude/skills/task-create/SKILL.md
.claude/skills/task-start/SKILL.md
.claude/skills/task-start/reference.md
.claude/skills/task-handoff/SKILL.md
.claude/hooks/check_sync.sh
```

## ステップ 1: 事前確認 (Pre-check)

1. `git status` でワーキングツリーがクリーンか確認する．未コミットの変更がある場合は「⚠ コミットされていない変更があります．先にコミットまたは stash してから再実行してください．」と伝えて中断する．
2. `$ARGUMENTS` を確認する．`solo` または `team` のいずれかであること．未指定・不正な値なら「切替先を `solo` または `team` で指定してください（例: `/set-mode team`）」と伝えて終了する．
3. 現在のモードを取得する（`.claude/project-mode` が無ければ `solo` 扱い）:
   ```bash
   if [ -f .claude/project-mode ]; then CURRENT=$(tr -d '[:space:]' < .claude/project-mode); else CURRENT="solo"; fi
   TARGET="$ARGUMENTS"   # solo | team
   ```
4. `CURRENT == TARGET` の場合は「既に `$TARGET` モードです．変更はありません．」と伝えて終了する．

## ステップ 2: ブランチ作成 (Branch)

モード切替は共有設定（`CLAUDE.md` のルール部・`.claude/`）の変更を含むため，GUIDE_03「共有設定の扱い」に従い専用ブランチで行う．

```bash
git checkout -b chore/set-mode-$TARGET
```

既に同名ブランチがあれば削除して作り直す．**本コマンドはコミットしない**（CLAUDE.md のルールに従い，取り込みは後で `/commit` で行う）．

## ステップ 3-A: solo → team（TARGET が team のとき）

### 3-A.1 テンプレートから team 層ファイルを取得・配置

`/sync-template` の版差分では過去に追加済みの team 層ファイルを配置できないため，本コマンドはテンプレートを直接 clone して確実に配置する:

```bash
TEMPLATE_URL="https://github.com/rintoHasegawa/programming-template.git"
TEMP_DIR=$(mktemp -d)
git clone --depth 1 "$TEMPLATE_URL" "$TEMP_DIR"

# team 層ファイルをコピー（既存があっても最新版で上書き）
for f in \
  "docs/01_GUIDE/GUIDE_03_チーム開発ルール.md" \
  ".claude/skills/task-create/SKILL.md" \
  ".claude/skills/task-start/SKILL.md" \
  ".claude/skills/task-start/reference.md" \
  ".claude/skills/task-handoff/SKILL.md" \
  ".claude/hooks/check_sync.sh" ; do
  mkdir -p "$(dirname "$f")"
  cp "$TEMP_DIR/$f" "$f"
done
rm -rf "$TEMP_DIR"
```

### 3-A.2 settings.json に SessionStart(check_sync) を配線

`.claude/settings.json` を Read し，既存の `PreToolUse`（`restrict_repo_access.py`）を保持したまま，`SessionStart` フックを追加する（既に配線済みなら何もしない）:

```json
"SessionStart": [
  {
    "hooks": [
      { "type": "command", "command": "bash .claude/hooks/check_sync.sh", "timeout": 10 }
    ]
  }
]
```

Edit 後に `git diff .claude/settings.json` で結果を確認する．

### 3-A.3 CLAUDE.md を team 化

`CLAUDE.md` を Read し，`/setup` の Phase 7（team 化）および GUIDE_03 に合わせて以下を反映する（既に反映済みの項目は触らない）:

- **「開発進捗」節**を，進捗欄（最新 1 行）ではなく **Issues ポインタ**に置き換える（例:「進捗・タスクは GitHub Issues と git 履歴で追う（GUIDE_03）．現在のタスクは `gh issue list` で確認する．`CLAUDE.md` には進捗を書かない．」）．
- **「必須ルール（コード実装時）」に「チーム開発（GUIDE_03 準拠）」小節を追加**する（直列運用・Issue ベースのタスク管理・`/task-create`／`/task-start`／`/task-handoff` の案内・条件付きセルフマージ・共有設定変更は専用 PR＋他メンバー 1 名 Approve 必須）．
- **「Git 運用」小節**に，セッション開始時の `[sync-check]` 警告を必ず認識する旨の 1 行を追加する．
- **「ドキュメント」→「01_GUIDE」一覧**に `GUIDE_03` の行を追加する．

Edit 後に `git diff CLAUDE.md` で結果を確認する．

### 3-A.4 その他

- `.gitignore` に `.claude/settings.local.json` が無ければ追記する（個人設定用．GUIDE_03）．
- `echo team > .claude/project-mode` でモードを記録する．

## ステップ 3-B: team → solo（TARGET が solo のとき）

clone は不要（ローカルの削除・書き換えのみ）．**破壊的操作を含むため，実行前に対象ファイルの一覧と CLAUDE.md 差分の要約をユーザーに提示し，同意を得てから実行する**．

### 3-B.1 team 層ファイルを削除

```bash
rm -f \
  "docs/01_GUIDE/GUIDE_03_チーム開発ルール.md" \
  ".claude/skills/task-create/SKILL.md" \
  ".claude/skills/task-start/SKILL.md" \
  ".claude/skills/task-start/reference.md" \
  ".claude/skills/task-handoff/SKILL.md" \
  ".claude/hooks/check_sync.sh"

# 中身が無くなったスキルディレクトリを取り除く
rmdir ".claude/skills/task-create" ".claude/skills/task-start" ".claude/skills/task-handoff" 2>/dev/null || true
```

### 3-B.2 settings.json から SessionStart(check_sync) を除去

`.claude/settings.json` を Read し，`check_sync.sh` を呼ぶ `SessionStart` フックのみを除去する．`PreToolUse`（`restrict_repo_access.py`）は保持する．他に個別追加された `SessionStart` フックがあれば残す（`check_sync.sh` の配線だけを外す）．Edit 後に `git diff .claude/settings.json` で確認する．

### 3-B.3 CLAUDE.md を solo 化

`CLAUDE.md` を Read し，team 化で加えた箇所を元の solo 既定へ戻す:

- **「開発進捗」節**を solo 既定の骨組みに戻す．team 化で Issues ポインタになっているため，進捗欄（最新 1 行）＋案内コメントの形へ置き換える:
  ```markdown
  ## 開発進捗

  最新: <直近の状況を 1 行．不明なら「（git 履歴 / 旧 Issue を参照）」>
  ※ 本欄は**最新ステップ 1 行のみ上書き更新**．詳細な進捗履歴は docs/PROGRESS.md に追記する．運用ルールは .claude/rules/progress-log.md 参照．
  ```
  「最新」行に入れる現状はユーザーに確認する（team 期間の進捗は Issues / git 履歴にあるため，ここへ移し替える必要はない）．
- **「チーム開発（GUIDE_03 準拠）」小節を削除**する．
- **「Git 運用」小節**の `[sync-check]` 警告の行を削除する．
- **「ドキュメント」→「01_GUIDE」一覧**から `GUIDE_03` の行を削除する（GUIDE_04 以降のプロジェクト固有ドキュメントがあれば残す）．

Edit 後に `git diff CLAUDE.md` で確認する．

### 3-B.4 その他

- `echo solo > .claude/project-mode` でモードを記録する．
- `docs/PROGRESS.md` が無い場合は，solo 運用のため骨組みを用意するか確認する（テンプレート由来のファイルなので通常は存在する）．

## ステップ 4: 結果報告 (Report)

以下を報告する:

「**開発モードを `{CURRENT}` → `{TARGET}` に切り替えました．**

- ブランチ: `chore/set-mode-{TARGET}`
- team 層ファイル: {配置した / 削除した} 一覧
- `.claude/settings.json`: {SessionStart(check_sync) を追加 / 除去 / 変更なし}
- `CLAUDE.md`: {team 化 / solo 化} を反映
- `.claude/project-mode`: `{TARGET}`

内容を確認のうえ `/commit push` で取り込んでください．
{team に切り替えた場合: 「チーム運用の管理者初期設定（GitHub repo・CI 等）は GUIDE_03 を参照してください．共有設定の変更のため，他メンバー 1 名の Approve を得てからマージしてください（GUIDE_03）．」}」

## 注意事項 (Notes)

- 本コマンドは**コミットしない**．変更は `chore/set-mode-*` ブランチに未コミットで乗るので，`/commit` で取り込む．
- team ↔ solo の切替は共有設定の変更にあたる（GUIDE_03「共有設定の扱い」）．team プロジェクトでは専用 PR＋他メンバー 1 名 Approve を経てマージする．
- team 層ファイルのリストは `/sync-template` の「モード依存ファイル」と一致させること．どちらかを増減したら両方を更新する．
- solo→team で取得する team 層ファイルはテンプレート HEAD 版．版の細かな追従は以後の `/sync-template` に任せる（`template-sync-sha` は本コマンドでは変更しない）．
