#!/usr/bin/env bash
# SessionStart hook: ローカルブランチが origin と同期しているかをチェックし,
# JSON で systemMessage (UI 表示) と additionalContext (モデルに注入) を出力する.
# 読み取り専用 (git fetch のみ). 失敗してもセッション開始は阻害しない.
#
# GUIDE_06「直列運用」「main を常に動作可能」を補助する目的で,
# 古い main から作業を始めるミスをセッション開始時に可視化する.

# JSON 整形は python3 に任せる (jq 非依存. 既存 hook も python3 を利用).
emit() {
  local msg="$1"
  python3 - "$msg" <<'PY'
import json, sys
msg = sys.argv[1]
print(json.dumps({
    "systemMessage": msg,
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": msg,
    },
}, ensure_ascii=False))
PY
}

# git リポジトリ外なら静かに終了
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# origin が未設定なら静かにスキップ (個人 clone・ローカル実験用途)
git remote get-url origin >/dev/null 2>&1 || exit 0

# fetch (5 秒で打ち切り). 失敗 = オフライン等. 警告だけ出して継続.
if ! timeout 5 git fetch --quiet origin 2>/dev/null; then
  emit "[sync-check] origin への fetch に失敗 — オフラインの可能性"
  exit 0
fi

BRANCH=$(git branch --show-current)
# detached HEAD は判定できないので静かに終了
[ -z "$BRANCH" ] && exit 0

LOCAL=$(git rev-parse HEAD 2>/dev/null) || exit 0
REMOTE=$(git rev-parse "@{u}" 2>/dev/null)
if [ -z "$REMOTE" ]; then
  emit "[sync-check] $BRANCH は upstream 未設定"
  exit 0
fi
BASE=$(git merge-base HEAD "@{u}")

DIRTY=""
[ -n "$(git status --porcelain)" ] && DIRTY=" / 未コミット変更あり"

if [ "$LOCAL" = "$REMOTE" ]; then
  emit "[sync-check] ✓ $BRANCH は origin と同期${DIRTY}"
elif [ "$LOCAL" = "$BASE" ]; then
  AHEAD=$(git rev-list --count "HEAD..@{u}")
  emit "[sync-check] ⚠ $BRANCH は origin より ${AHEAD} コミット遅れ — \`git pull\` 推奨${DIRTY}"
elif [ "$REMOTE" = "$BASE" ]; then
  AHEAD=$(git rev-list --count "@{u}..HEAD")
  emit "[sync-check] ℹ $BRANCH は origin より ${AHEAD} コミット先行${DIRTY}"
else
  AHEAD=$(git rev-list --count "@{u}..HEAD")
  BEHIND=$(git rev-list --count "HEAD..@{u}")
  emit "[sync-check] ⚠ $BRANCH は origin と分岐 (先行 ${AHEAD} / 遅れ ${BEHIND}) — rebase/merge が必要${DIRTY}"
fi
