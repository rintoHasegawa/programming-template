"""Stop / Notification / PreToolUse(AskUserQuestion) hook: 作業完了・許可待ち・質問を ntfy でスマホに通知する.

送信先トピックは環境変数 NTFY_TOPIC で指定する（未設定なら何もしない）．トピック名は
知っていれば誰でも購読・送信できるパスワード相当の値のため，リポジトリには書かず
.claude/settings.local.json（gitignore 済み）の env 等で各自設定する．
自前サーバー等へ送る場合は NTFY_SERVER で送信先を変えられる（既定: https://ntfy.sh）．

Stop はターンが終わるたびに呼ばれるため，サブエージェントに作業を依頼して待っているだけの
ターン終了（依頼した直後にメインの手が空いて一旦ターンが終わる）でも発火する．そのままでは
「処理が完了しました」が実際の完了より前に届くので，hook 入力の background_tasks を見て
依頼中のエージェントが残っているターンでは通知しない（PENDING_TASK_TYPES 参照）．

settings.json では async で配線しているため，本フックの成否は Claude Code の動作に影響しない．
送信に失敗しても黙って終了する．外部サービスを経由するため，本文にコードや会話の内容は含めない．
"""

from __future__ import annotations

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
# Stop 時点で「まだ作業が続く」と見なす背景タスクの種類（hook 入力 background_tasks[].type）．
# subagent・workflow は完了するとセッションが再開して作業が続くため，これらが残っているターン終了は
# 完了ではない．shell・monitor は開発サーバーのように意図して動かし続けるものが該当しうる
# （含めると通知が永久に止まる）ため対象にしない．
PENDING_TASK_TYPES = ("subagent", "workflow")
# エージェント名は設定由来の短い識別子だが，通知本文が壊れないよう長さを制限する
AGENT_NAME_MAX = 40


def has_pending_agent(data: dict) -> bool:
    """依頼した作業がまだ動いている（サブエージェント・ワークフローが在席している）かを返す.

    background_tasks を持たない古い Claude Code では常に False となり，従来どおり毎ターン通知する．
    """
    tasks = data.get("background_tasks")
    if not isinstance(tasks, list):
        return False
    for task in tasks:
        if isinstance(task, dict) and task.get("type") in PENDING_TASK_TYPES:
            return True
    return False


def agent_suffix(data: dict) -> str:
    """サブエージェント起因のイベントなら「（エージェント: 名前）」を返す（そうでなければ空文字）."""
    name = data.get("agent_type")
    if not isinstance(name, str) or not name.strip():
        return ""
    return "（エージェント: {}）".format(" ".join(name.split())[:AGENT_NAME_MAX])


def build_notification(data: dict) -> dict | None:
    """イベントから ntfy の通知内容（本文・タグ・優先度）を決める．通知不要なら None を返す."""
    event = data.get("hook_event_name")
    if event == "Stop":
        # 依頼したエージェントが動いている間のターン終了は完了ではない．
        # 依頼が終わるとセッションが再開するので，本当に終わった最後のターン終了で通知される
        if has_pending_agent(data):
            return None
        return {"message": "処理が完了しました", "tags": ["white_check_mark"]}
    if event == "PreToolUse" and data.get("tool_name") == "AskUserQuestion":
        message = "質問への回答を待っています" + agent_suffix(data)
        return {"message": message, "tags": ["question"], "priority": PRIORITY_NEEDS_INPUT}
    if event == "Notification":
        if data.get("notification_type") == "permission_prompt":
            message = "ツール実行の許可を待っています" + agent_suffix(data)
            return {"message": message, "tags": ["lock"], "priority": PRIORITY_NEEDS_INPUT}
        return {"message": "入力を待っています" + agent_suffix(data), "tags": ["speech_balloon"], "priority": PRIORITY_NEEDS_INPUT}
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
    name = url.replace(":", "/").rsplit("/", 1)[-1]
    if name.endswith(".git"):  # str.removesuffix は 3.9 以降のため使わない
        name = name[: -len(".git")]
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
