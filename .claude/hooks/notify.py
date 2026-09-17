"""Stop / Notification / PreToolUse(AskUserQuestion) hook: 作業完了・許可待ち・質問を ntfy でスマホに通知する.

送信先トピックは環境変数 NTFY_TOPIC で指定する（未設定なら何もしない）．トピック名は
知っていれば誰でも購読・送信できるパスワード相当の値のため，リポジトリには書かず
.claude/settings.local.json（gitignore 済み）の env 等で各自設定する．
自前サーバー等へ送る場合は NTFY_SERVER で送信先を変えられる（既定: https://ntfy.sh）．

settings.json では async で配線しているため，本フックの成否は Claude Code の動作に影響しない．
送信に失敗しても黙って終了する．外部サービスを経由するため，本文にコードや会話の内容は含めない．
"""

import json
import os
import subprocess
import sys
import urllib.request

DEFAULT_SERVER = "https://ntfy.sh"
SEND_TIMEOUT_SEC = 10
GIT_TIMEOUT_SEC = 5
# ntfy の優先度（3 が既定．4 は通知音・バイブが強調される）
PRIORITY_NEEDS_INPUT = 4


def build_notification(data: dict) -> dict | None:
    """イベントから ntfy の通知内容（本文・タグ・優先度）を決める．通知不要なら None を返す."""
    event = data.get("hook_event_name")
    if event == "Stop":
        return {"message": "処理が完了しました", "tags": ["white_check_mark"]}
    if event == "PreToolUse" and data.get("tool_name") == "AskUserQuestion":
        return {"message": "質問への回答を待っています", "tags": ["question"], "priority": PRIORITY_NEEDS_INPUT}
    if event == "Notification":
        if data.get("notification_type") == "permission_prompt":
            return {"message": "ツール実行の許可を待っています", "tags": ["lock"], "priority": PRIORITY_NEEDS_INPUT}
        return {"message": "入力を待っています", "tags": ["speech_balloon"], "priority": PRIORITY_NEEDS_INPUT}
    return None


def project_name(cwd: str) -> str:
    """通知に出すプロジェクト名を返す.

    ホスト OS と dev container でフォルダ名が異なっても同じ名前になるよう，
    origin リモートの URL 末尾（https://github.com/owner/repo.git・git@github.com:owner/repo.git
    のどちらでも repo）を使う．リモートが無い・git が使えない場合はフォルダ名を返す．
    """
    folder = os.path.basename(os.path.normpath(cwd))
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError):
        return folder
    url = result.stdout.strip().rstrip("/")
    if result.returncode != 0 or not url:
        return folder
    name = url.replace(":", "/").rsplit("/", 1)[-1].removesuffix(".git")
    return name or folder


def main() -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return

    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return

    notification = build_notification(data)
    if notification is None:
        return

    # 複数プロジェクトを並行して動かしても区別できるよう，タイトルにプロジェクト名を入れる
    project = project_name(data.get("cwd") or os.getcwd())
    payload = {"topic": topic, "title": f"Claude Code ({project})", **notification}

    # JSON で送る（ヘッダー送信だと日本語のタイトルが化けるため）
    server = os.environ.get("NTFY_SERVER", DEFAULT_SERVER).rstrip("/")
    request = urllib.request.Request(
        server,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SEC):
            pass
    except OSError:  # URLError・HTTPError・タイムアウトはいずれも OSError の派生
        pass


if __name__ == "__main__":
    main()
