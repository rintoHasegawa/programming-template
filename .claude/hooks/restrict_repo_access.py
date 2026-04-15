"""PreToolUse hook: リポジトリ外のファイルアクセスをブロックする."""

import json
import os
import sys


def get_target_path(tool_name: str, tool_input: dict) -> str | None:
    """ツールの種類に応じてチェック対象のパスを取得する."""
    if tool_name in ("Read", "Write", "Edit"):
        return tool_input.get("file_path")
    if tool_name in ("Glob", "Grep"):
        return tool_input.get("path")  # 省略時は None → cwd が使われるので許可
    return None


def is_within_directory(target: str, base: str) -> bool:
    """target が base ディレクトリ配下にあるかを判定する."""
    real_target = os.path.realpath(target)
    real_base = os.path.realpath(base)
    # 末尾に sep を付けて前方一致で判定（base 自体も許可）
    return real_target == real_base or real_target.startswith(real_base + os.sep)


def deny(reason: str) -> None:
    """ブロック用の JSON を出力して終了する."""
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    json.dump(result, sys.stdout)
    sys.exit(0)


def main() -> None:
    data = json.load(sys.stdin)
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    cwd = data.get("cwd", os.getcwd())

    target_path = get_target_path(tool_name, tool_input)

    # パスが指定されていない場合は許可（Glob/Grep の path 省略時など）
    if target_path is None:
        sys.exit(0)

    if not is_within_directory(target_path, cwd):
        deny(
            f"リポジトリ外のパスへのアクセスはブロックされました: {target_path}"
        )

    # リポジトリ内なので許可
    sys.exit(0)


if __name__ == "__main__":
    main()
