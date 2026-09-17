#!/usr/bin/env bash
# Python フックの起動ラッパー.
# 使い方: bash .claude/hooks/run_python.sh [--fail-closed] <script.py> [args...]
#
# 環境ごとに Python のコマンド名が異なるため (Debian/Ubuntu 系・macOS は python3 のみ,
# Windows の python.org 版は python / py のみで python3 は Microsoft Store を開くダミー),
# python3 -> python -> py の順に実際に起動できるものを探して exec する.
# exec するのでスクリプトの終了コード (PreToolUse のブロック = 2 等) はそのまま伝わる.
#
# Python が見つからない場合:
#   --fail-closed あり: exit 2 (PreToolUse ではツール実行をブロックする. アクセス制限フック用)
#   --fail-closed なし: exit 1 (非ブロックエラー. 通知フック等は素通りさせる)

fail_code=1
if [ "$1" = "--fail-closed" ]; then
  fail_code=2
  shift
fi

for py in python3 python py; do
  command -v "$py" >/dev/null 2>&1 || continue
  # ダミー (Microsoft Store のエイリアス等) を除外するため実際に起動できるか確認する
  "$py" -c '' </dev/null >/dev/null 2>&1 || continue
  exec "$py" "$@"
done

if [ "$fail_code" -eq 2 ]; then
  echo "Python（python3 / python / py）が見つからないためツール実行をブロックしました．Python 3.7 以上をインストールしてください" >&2
else
  echo "Python（python3 / python / py）が見つからないためフックをスキップしました: $1" >&2
fi
exit "$fail_code"
