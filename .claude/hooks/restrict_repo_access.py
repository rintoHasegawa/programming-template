"""PreToolUse hook: リポジトリ外のファイルアクセスをブロックする.

例外としてシステム一時ディレクトリ（/tmp・%TEMP% 等）は許可ゾーンとする:
- Read/Write/Edit/Glob/Grep: 一時ディレクトリ配下なら許可
  （/sync-template・/set-mode がテンプレートを mktemp -d に clone して読むため．
   また Claude Code のスクラッチパッドは %TEMP% 配下にあり，Write をブロック
   するとハーネスの一時ファイル運用が壊れる）
- Bash の破壊的コマンド: 対象が一時ディレクトリ配下なら許可
  （cp "$TEMP_DIR/..." や rm -rf "$TEMP_DIR" の後片付けは正当な操作）

一時ディレクトリは使い捨て領域であり，本フックの目的（ユーザーのファイルを
事故から守る）に照らして許可ゾーンにしても失うものがない．
"""

import json
import os
import posixpath
import re
import shlex
import sys
import tempfile

# Bash で検出する破壊的コマンド
DESTRUCTIVE_COMMANDS = {"rm", "rmdir", "mv", "cp", "chmod", "chown", "unlink"}


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


def is_temp_path(path: str) -> bool:
    """path がシステム一時ディレクトリ配下かを判定する.

    OS ネイティブの一時ディレクトリ（tempfile.gettempdir()）に加え，
    Git Bash 等の POSIX 形式 /tmp も文字列正規化で判定する
    （Windows では /tmp が実パスに解決できないため realpath に頼れない）．
    `..` は正規化してから判定するので /tmp/../etc のような脱出は温存されない．
    """
    # POSIX 形式 /tmp の判定（.. を潰してから前方一致）
    norm = posixpath.normpath(path.replace("\\", "/"))
    if norm == "/tmp" or norm.startswith("/tmp/"):
        return True
    # OS ネイティブの一時ディレクトリの判定
    return is_within_directory(path, tempfile.gettempdir())


def check_bash_command(command: str, cwd: str) -> str | None:
    """Bash コマンド内の破壊的操作がリポジトリ外を対象としていないかチェックする.

    リポジトリ外のパスに対する破壊的コマンドを検出した場合、理由文字列を返す。
    問題なければ None を返す。
    """
    # セミコロン、&&、|| やパイプで分割された各部分をチェック
    parts = re.split(r"[;&|]+", command)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            tokens = shlex.split(part)
        except ValueError:
            # クォートが閉じていない等のパースエラーは無視して続行
            continue
        if not tokens:
            continue
        # コマンド名を取得（env, sudo 等のプレフィックスをスキップ）
        cmd_name = None
        for token in tokens:
            if token in ("sudo", "env") or "=" in token:
                continue
            cmd_name = os.path.basename(token)
            break
        if cmd_name not in DESTRUCTIVE_COMMANDS:
            continue
        # 破壊的コマンドの引数からパスを抽出してチェック
        for token in tokens[1:]:
            if token.startswith("-"):
                continue
            # 絶対パスまたは .. を含むパスをチェック
            if os.path.isabs(token) or ".." in token:
                # 一時ディレクトリ配下への破壊的操作は許可
                # （テンプレート clone の cp・後片付けの rm -rf 等）
                if is_temp_path(token):
                    continue
                resolved = os.path.realpath(os.path.join(cwd, token))
                if not is_within_directory(resolved, cwd):
                    return (
                        f"リポジトリ外への破壊的操作をブロックしました: "
                        f"`{cmd_name}` が `{token}` を対象としています"
                    )
    return None


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

    # Bash ツールの破壊的コマンドチェック
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        reason = check_bash_command(command, cwd)
        if reason:
            deny(reason)
        sys.exit(0)

    # Read/Write/Edit/Glob/Grep のパスチェック
    target_path = get_target_path(tool_name, tool_input)

    # パスが指定されていない場合は許可（Glob/Grep の path 省略時など）
    if target_path is None:
        sys.exit(0)

    # 一時ディレクトリ配下は許可（Read/Write/Edit/Glob/Grep すべて）
    # （/sync-template・/set-mode が mktemp -d に clone したテンプレートを読む．
    #   Claude Code のスクラッチパッドも %TEMP% 配下で Write が必要）
    if is_temp_path(target_path):
        sys.exit(0)

    if not is_within_directory(target_path, cwd):
        deny(
            f"リポジトリ外のパスへのアクセスはブロックされました: {target_path}"
        )

    # リポジトリ内なので許可
    sys.exit(0)


if __name__ == "__main__":
    main()
