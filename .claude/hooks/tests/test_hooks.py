"""`.claude/hooks/` のフック起動まわりのテスト（Issue #35: Python フックの起動ラッパー）.

実行（リポジトリルートで）:
    python -B -m unittest discover -s .claude/hooks/tests -v

外部依存なし（標準ライブラリの unittest のみ）．bash が必要（Windows では Git for Windows の bash）．
notify.py は NTFY_TOPIC 未設定，またはローカルの一時 HTTP サーバー宛てでのみ実行し，ntfy.sh には送信しない．
"""

from __future__ import annotations

import ast
import contextlib
import http.server
import io
import json
import ntpath
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

HOOKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(HOOKS_DIR))
WRAPPER = os.path.join(HOOKS_DIR, "run_python.sh")
SETTINGS = os.path.join(REPO_ROOT, ".claude", "settings.json")
RESTRICT = os.path.join(HOOKS_DIR, "restrict_repo_access.py")
NOTIFY = os.path.join(HOOKS_DIR, "notify.py")
CHECK_SYNC = os.path.join(HOOKS_DIR, "check_sync.sh")

TIMEOUT = 60


def find_bash() -> str:
    """テストに使う bash を返す（Windows では WSL の System32\\bash.exe を避ける）."""
    env_bash = os.environ.get("HOOK_TEST_BASH")
    if env_bash:
        return env_bash
    found = shutil.which("bash")
    if os.name == "nt":
        if found is None or "system32" in found.lower():
            for candidate in (
                r"C:\Program Files\Git\usr\bin\bash.exe",
                r"C:\Program Files\Git\bin\bash.exe",
            ):
                if os.path.exists(candidate):
                    return candidate
    if found is None:
        raise unittest.SkipTest("bash が見つからない")
    return found


BASH = find_bash()
REAL_PYTHON = sys.executable.replace("\\", "/")


def bash_bin_dirs() -> list[str]:
    """git・timeout・dirname 等の基本コマンドがあるディレクトリ（Python は含まない）."""
    dirs = [os.path.dirname(BASH)]
    for cmd in ("git", "timeout", "dirname"):
        found = shutil.which(cmd)
        if found and os.path.dirname(found) not in dirs:
            dirs.append(os.path.dirname(found))
    if os.name != "nt":
        for d in ("/usr/bin", "/bin"):
            if d not in dirs:
                dirs.append(d)
    return dirs


class FakeBin:
    """PATH の先頭に置く偽コマンド用の一時ディレクトリ."""

    def __init__(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="fakebin_")

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, name: str, body: str) -> None:
        path = os.path.join(self.dir, name)
        with open(path, "w", newline="\n") as f:
            f.write("#!/bin/sh\n" + body)
        os.chmod(path, 0o755)

    def dummy(self, name: str) -> None:
        """存在するが起動できない（常に exit 1）ダミー（Microsoft Store エイリアス相当）."""
        self._write(name, 'echo "DUMMY_CALLED:' + name + '" >&2\nexit 1\n')

    def marker(self, name: str) -> None:
        """`-c ''` は成功し，本実行時は自分の名前と引数を出力する偽 Python."""
        self._write(
            name,
            'if [ "$1" = "-c" ]; then exit 0; fi\n'
            'echo "SELECTED:' + name + '"\nexit 0\n',
        )

    def real(self, name: str) -> None:
        """テスト実行中の本物の Python に委譲する."""
        self._write(name, 'exec "' + REAL_PYTHON + '" "$@"\n')


def make_env(path_dirs: list[str], extra: dict | None = None, drop: tuple = ()) -> dict:
    env = dict(os.environ)
    for key in drop:
        env.pop(key, None)
    env["PATH"] = os.pathsep.join(path_dirs)
    if extra:
        env.update(extra)
    return env


def run_wrapper(args: list[str], env: dict, stdin: bytes = b"", cwd: str | None = None):
    return subprocess.run(
        [BASH, WRAPPER] + args,
        input=stdin,
        capture_output=True,
        env=env,
        cwd=cwd or REPO_ROOT,
        timeout=TIMEOUT,
    )


class TempDirMixin:
    def setUp(self) -> None:  # noqa: D401
        self.fake = FakeBin()
        self.work = tempfile.mkdtemp(prefix="hooktest_")

    def tearDown(self) -> None:
        self.fake.cleanup()
        shutil.rmtree(self.work, ignore_errors=True)

    def write_script(self, name: str, source: str) -> str:
        path = os.path.join(self.work, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(source)
        return path


# ---------------------------------------------------------------------------
# run_python.sh: Python コマンドの選択順
# ---------------------------------------------------------------------------
class TestWrapperSelection(TempDirMixin, unittest.TestCase):
    def selected(self, *wrapper_args: str) -> subprocess.CompletedProcess:
        return run_wrapper(list(wrapper_args) or ["script.py"], make_env([self.fake.dir]))

    def assertSelected(self, result, name: str) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode().strip(), "SELECTED:" + name)

    def test_prefers_python3_when_all_available(self):
        for n in ("python3", "python", "py"):
            self.fake.marker(n)
        self.assertSelected(self.selected(), "python3")

    def test_falls_back_to_python_when_no_python3(self):
        self.fake.marker("python")
        self.fake.marker("py")
        self.assertSelected(self.selected(), "python")

    def test_falls_back_to_py_when_only_py(self):
        self.fake.marker("py")
        self.assertSelected(self.selected(), "py")

    def test_skips_dummy_python3(self):
        self.fake.dummy("python3")
        self.fake.marker("python")
        self.fake.marker("py")
        self.assertSelected(self.selected(), "python")

    def test_skips_dummy_python3_and_python(self):
        self.fake.dummy("python3")
        self.fake.dummy("python")
        self.fake.marker("py")
        self.assertSelected(self.selected(), "py")

    def test_fail_closed_flag_does_not_change_selection(self):
        self.fake.dummy("python3")
        self.fake.marker("python")
        self.assertSelected(self.selected("--fail-closed", "script.py"), "python")

    def test_dummy_is_not_used_for_actual_run(self):
        """ダミーは起動確認で弾かれ，本実行には使われない."""
        self.fake.dummy("python3")
        self.fake.marker("python")
        result = self.selected()
        self.assertSelected(result, "python")
        # ダミーが呼ばれるのは起動確認（-c ''）のみで，その stderr は捨てられている
        self.assertNotIn(b"DUMMY_CALLED", result.stderr)


# ---------------------------------------------------------------------------
# run_python.sh: Python 未検出時
# ---------------------------------------------------------------------------
class TestWrapperNotFound(TempDirMixin, unittest.TestCase):
    def test_no_python_exit_1_without_flag(self):
        result = run_wrapper(["script.py"], make_env([self.fake.dir]))
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.strip(), "stderr に理由が出力されること")

    def test_no_python_exit_2_with_fail_closed(self):
        result = run_wrapper(["--fail-closed", "script.py"], make_env([self.fake.dir]))
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.strip(), "stderr に理由が出力されること")

    def test_only_dummies_exit_1_without_flag(self):
        for n in ("python3", "python", "py"):
            self.fake.dummy(n)
        result = run_wrapper(["script.py"], make_env([self.fake.dir]))
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.strip())

    def test_only_dummies_exit_2_with_fail_closed(self):
        for n in ("python3", "python", "py"):
            self.fake.dummy(n)
        result = run_wrapper(["--fail-closed", "script.py"], make_env([self.fake.dir]))
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.strip())

    def test_not_found_with_no_script_args(self):
        """引数なしでも異常終了（set -u 的なクラッシュ）せず規定の終了コードを返す."""
        result = run_wrapper([], make_env([self.fake.dir]))
        self.assertEqual(result.returncode, 1)
        result = run_wrapper(["--fail-closed"], make_env([self.fake.dir]))
        self.assertEqual(result.returncode, 2)


# ---------------------------------------------------------------------------
# run_python.sh: 終了コード・stdin・引数の伝搬（本物の Python に委譲）
# ---------------------------------------------------------------------------
class TestWrapperPassthrough(TempDirMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.fake.dummy("python3")  # Windows の Store エイリアス相当
        self.fake.real("python")
        self.env = make_env([self.fake.dir])

    def test_exit_codes_propagate(self):
        script = self.write_script("exit.py", "import sys\nsys.exit(int(sys.argv[1]))\n")
        for code in (0, 1, 2, 3, 42):
            for flag in ([], ["--fail-closed"]):
                with self.subTest(code=code, flag=flag):
                    result = run_wrapper(flag + [script, str(code)], self.env)
                    self.assertEqual(result.returncode, code, result.stderr)

    def test_args_passed_verbatim(self):
        script = self.write_script(
            "argv.py", "import json, sys\nprint(json.dumps(sys.argv[1:]))\n"
        )
        args = ["a b", "", "--fail-closed", "x=y", "日本語", "--flag"]
        for flag in ([], ["--fail-closed"]):
            with self.subTest(flag=flag):
                result = run_wrapper(
                    flag + [script] + args,
                    dict(self.env, PYTHONIOENCODING="utf-8"),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout.decode("utf-8")), args)

    def test_fail_closed_flag_not_passed_to_script(self):
        script = self.write_script(
            "argv.py", "import json, sys\nprint(json.dumps(sys.argv[1:]))\n"
        )
        result = run_wrapper(["--fail-closed", script, "one"], self.env)
        self.assertEqual(json.loads(result.stdout.decode()), ["one"])

    def test_stdin_passed_through(self):
        script = self.write_script(
            "stdin.py",
            "import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n",
        )
        payload = json.dumps({"tool_name": "Read", "x": "日本語"}, ensure_ascii=False).encode("utf-8")
        payload = payload * 200  # ある程度の大きさでも欠けないこと
        for flag in ([], ["--fail-closed"]):
            with self.subTest(flag=flag):
                result = run_wrapper(flag + [script], self.env, stdin=payload)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, payload)

    def test_stdin_not_consumed_by_probe(self):
        """起動確認（-c ''）が stdin を読み捨てないこと（ダミー・本物ともに）."""
        self.fake.real("python3")  # python3 も本物にして probe → 本実行の経路を通す
        script = self.write_script(
            "stdin.py", "import sys\nsys.stdout.write(sys.stdin.read())\n"
        )
        result = run_wrapper([script], self.env, stdin=b"hello-stdin")
        self.assertEqual(result.stdout, b"hello-stdin")

    def test_stdin_script_dash(self):
        """check_sync.sh と同じ `-` 形式（ヒアドキュメントのスクリプト）が動く."""
        result = run_wrapper(
            ["-", "arg1"],
            self.env,
            stdin=b"import sys\nprint('ok:' + sys.argv[1])\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode().strip(), "ok:arg1")


# ---------------------------------------------------------------------------
# settings.json の配線
# ---------------------------------------------------------------------------
class TestSettingsJson(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with open(SETTINGS, encoding="utf-8") as f:
            cls.raw = f.read()
        cls.settings = json.loads(cls.raw)
        cls.commands = []
        for event, groups in cls.settings.get("hooks", {}).items():
            for group in groups:
                for hook in group.get("hooks", []):
                    if hook.get("type") == "command":
                        cls.commands.append((event, group.get("matcher"), hook))

    def test_restrict_repo_access_uses_wrapper_with_fail_closed(self):
        cmds = [h["command"] for _, _, h in self.commands if "restrict_repo_access.py" in h["command"]]
        self.assertEqual(
            cmds,
            ["bash .claude/hooks/run_python.sh --fail-closed .claude/hooks/restrict_repo_access.py"],
        )

    def test_restrict_repo_access_is_pretooluse_for_file_tools(self):
        entries = [(e, m) for e, m, h in self.commands if "restrict_repo_access.py" in h["command"]]
        self.assertEqual(len(entries), 1)
        event, matcher = entries[0]
        self.assertEqual(event, "PreToolUse")
        for tool in ("Read", "Write", "Edit", "Glob", "Grep", "Bash"):
            self.assertIn(tool, matcher.split("|"))

    def test_notify_three_hooks_use_wrapper_without_fail_closed(self):
        cmds = [h["command"] for _, _, h in self.commands if "notify.py" in h["command"]]
        self.assertEqual(len(cmds), 3)
        for cmd in cmds:
            self.assertEqual(cmd, "bash .claude/hooks/run_python.sh .claude/hooks/notify.py")
            self.assertNotIn("--fail-closed", cmd)

    def test_notify_events(self):
        events = sorted(e for e, _, h in self.commands if "notify.py" in h["command"])
        self.assertEqual(events, ["Notification", "PreToolUse", "Stop"])

    def test_no_direct_python_invocation_left(self):
        self.assertIsNone(
            re.search(r"\b(python3?|py)\s+\.claude/hooks/", self.raw),
            "python .claude/hooks/ の直接呼び出しが残っている",
        )
        for _, _, hook in self.commands:
            if ".py" in hook["command"]:
                self.assertTrue(
                    hook["command"].startswith("bash .claude/hooks/run_python.sh "),
                    hook["command"],
                )

    def test_referenced_scripts_exist(self):
        for _, _, hook in self.commands:
            for token in hook["command"].split():
                if token.startswith(".claude/hooks/"):
                    self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, token)), token)


# ---------------------------------------------------------------------------
# restrict_repo_access.py をラッパー経由で（settings.json と同じコマンドで）起動
# ---------------------------------------------------------------------------
class TestRestrictViaWrapper(TempDirMixin, unittest.TestCase):
    OUTSIDE = os.path.join(os.path.dirname(REPO_ROOT), "outside_of_repo_dummy.txt")
    INSIDE = os.path.join(REPO_ROOT, "CLAUDE.md")

    def run_hook(self, data: dict, env: dict) -> subprocess.CompletedProcess:
        # settings.json の command と同じ相対パス・cwd で起動する
        return subprocess.run(
            [BASH, ".claude/hooks/run_python.sh", "--fail-closed", ".claude/hooks/restrict_repo_access.py"],
            input=json.dumps(data).encode("utf-8"),
            capture_output=True,
            env=env,
            cwd=REPO_ROOT,
            timeout=TIMEOUT,
        )

    @staticmethod
    def is_blocked(result) -> bool:
        if result.returncode == 2:
            return True
        if result.returncode != 0:
            return False
        out = result.stdout.decode("utf-8").strip()
        if not out:
            return False
        decision = json.loads(out).get("hookSpecificOutput", {}).get("permissionDecision")
        return decision == "deny"

    def envs(self):
        # 1) 実環境の PATH（このマシンでは python3 が Store エイリアスのダミー）
        yield "real PATH", dict(os.environ)
        # 2) ダミー python3 + 本物 python のみ
        self.fake.dummy("python3")
        self.fake.real("python")
        yield "dummy python3 + python", make_env([self.fake.dir])

    def test_blocks_edit_outside_repo(self):
        # Issue #49: 読み取りと新規作成はリポジトリ外も許可し，既存ファイルの書き換え（Edit）はブロックする
        data = {"tool_name": "Edit", "tool_input": {"file_path": self.OUTSIDE, "old_string": "a", "new_string": "b"}, "cwd": REPO_ROOT}
        for label, env in self.envs():
            with self.subTest(env=label):
                result = self.run_hook(data, env)
                self.assertTrue(self.is_blocked(result), (result.returncode, result.stdout, result.stderr))

    def test_blocks_destructive_bash_outside_repo(self):
        outside_dir = os.path.dirname(REPO_ROOT).replace("\\", "/")
        data = {"tool_name": "Bash", "tool_input": {"command": f'rm -rf "{outside_dir}/foo"'}, "cwd": REPO_ROOT}
        for label, env in self.envs():
            with self.subTest(env=label):
                result = self.run_hook(data, env)
                self.assertTrue(self.is_blocked(result), (result.returncode, result.stdout, result.stderr))

    def test_allows_inside_repo(self):
        cases = [
            {"tool_name": "Read", "tool_input": {"file_path": self.INSIDE}, "cwd": REPO_ROOT},
            {"tool_name": "Edit", "tool_input": {"file_path": os.path.join(REPO_ROOT, "docs", "x.md")}, "cwd": REPO_ROOT},
            {"tool_name": "Grep", "tool_input": {"pattern": "x"}, "cwd": REPO_ROOT},
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf ./build"}, "cwd": REPO_ROOT},
        ]
        for label, env in self.envs():
            for data in cases:
                with self.subTest(env=label, tool=data["tool_name"]):
                    result = self.run_hook(data, env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(self.is_blocked(result), result.stdout)

    def test_blocks_everything_when_python_missing(self):
        """Python が無いと fail-closed で exit 2（リポジトリ内でもブロック）."""
        data = {"tool_name": "Read", "tool_input": {"file_path": self.INSIDE}, "cwd": REPO_ROOT}
        self.fake.dummy("python3")
        result = self.run_hook(data, make_env([self.fake.dir]))
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.strip())


# ---------------------------------------------------------------------------
# notify.py をラッパー経由で起動（ntfy.sh には送らない）
# ---------------------------------------------------------------------------
class _Capture(http.server.BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        _Capture.received.append(self.rfile.read(length))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # 出力を抑制
        pass


class TestNotifyViaWrapper(TempDirMixin, unittest.TestCase):
    def run_notify(self, data: dict, env: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [BASH, ".claude/hooks/run_python.sh", ".claude/hooks/notify.py"],
            input=json.dumps(data).encode("utf-8"),
            capture_output=True,
            env=env,
            cwd=REPO_ROOT,
            timeout=TIMEOUT,
        )

    def test_no_topic_does_nothing(self):
        env = dict(os.environ)
        env.pop("NTFY_TOPIC", None)
        env["NTFY_SERVER"] = "http://127.0.0.1:9"  # 万一送信しても外部に出ない
        result = self.run_notify({"hook_event_name": "Stop", "cwd": REPO_ROOT}, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_python_missing_is_non_blocking(self):
        self.fake.dummy("python3")
        env = make_env([self.fake.dir])
        env.pop("NTFY_TOPIC", None)
        result = self.run_notify({"hook_event_name": "Stop"}, env)
        self.assertEqual(result.returncode, 1)
        self.assertNotEqual(result.returncode, 2)

    def test_sends_to_local_server_with_repo_name(self):
        """ローカル HTTP サーバー宛てに送信し，Python 3.7 互換化後も内容が正しいこと."""
        _Capture.received = []
        server = http.server.HTTPServer(("127.0.0.1", 0), _Capture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            repo = os.path.join(self.work, "folder-name")
            os.makedirs(repo)
            subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", repo, "remote", "add", "origin", "git@github.com:owner/my-repo.git"],
                check=True,
                capture_output=True,
            )
            self.fake.dummy("python3")
            self.fake.real("python")
            env = make_env(
                [self.fake.dir] + bash_bin_dirs(),
                extra={
                    "NTFY_TOPIC": "test-topic",
                    "NTFY_SERVER": f"http://127.0.0.1:{server.server_address[1]}",
                },
            )
            result = self.run_notify({"hook_event_name": "Stop", "cwd": repo}, env)
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(len(_Capture.received), 1)
        payload = json.loads(_Capture.received[0].decode("utf-8"))
        self.assertEqual(payload["topic"], "test-topic")
        self.assertEqual(payload["title"], "Claude Code (my-repo)")


# ---------------------------------------------------------------------------
# notify.project_name（removesuffix 置換後の挙動）
# ---------------------------------------------------------------------------
class TestNotifyProjectName(TempDirMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, HOOKS_DIR)
        try:
            import notify  # noqa: WPS433
        finally:
            sys.path.pop(0)
        cls.notify = notify

    def repo_with_origin(self, url: str | None) -> str:
        repo = os.path.join(self.work, "local-folder")
        os.makedirs(repo, exist_ok=True)
        subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
        if url is not None:
            subprocess.run(["git", "-C", repo, "remote", "add", "origin", url], check=True, capture_output=True)
        return repo

    def test_cases(self):
        cases = [
            ("https://github.com/owner/repo.git", "repo"),
            ("https://github.com/owner/repo", "repo"),
            ("https://github.com/owner/repo/", "repo"),
            ("git@github.com:owner/repo.git", "repo"),
            ("git@github.com:owner/repo", "repo"),
            ("https://github.com/owner/my.git.repo.git", "my.git.repo"),
            ("https://github.com/owner/gitrepo", "gitrepo"),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                shutil.rmtree(os.path.join(self.work, "local-folder"), ignore_errors=True)
                repo = self.repo_with_origin(url)
                self.assertEqual(self.notify.project_name(repo), expected)

    def test_no_origin_returns_folder(self):
        repo = self.repo_with_origin(None)
        self.assertEqual(self.notify.project_name(repo), "local-folder")

    def test_only_dotgit_falls_back_to_folder(self):
        repo = self.repo_with_origin("https://example.com/.git")
        self.assertEqual(self.notify.project_name(repo), "local-folder")


# ---------------------------------------------------------------------------
# notify.build_notification（どのイベントで通知するか）
# ---------------------------------------------------------------------------
def _subagent_task(agent_type: str = "coder") -> dict:
    return {"id": "t1", "type": "subagent", "status": "running", "description": "...", "agent_type": agent_type}


class TestNotifyBuildNotification(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, HOOKS_DIR)
        try:
            import notify  # noqa: WPS433
        finally:
            sys.path.pop(0)
        cls.notify = notify

    def build(self, data: dict):
        return self.notify.build_notification(data)

    # Stop: 依頼したエージェントが動いている間は通知しない
    def test_stop_without_background_tasks_notifies(self):
        self.assertIsNotNone(self.build({"hook_event_name": "Stop"}))

    def test_stop_with_empty_background_tasks_notifies(self):
        self.assertIsNotNone(self.build({"hook_event_name": "Stop", "background_tasks": []}))

    def test_stop_with_running_subagent_is_silent(self):
        self.assertIsNone(self.build({"hook_event_name": "Stop", "background_tasks": [_subagent_task()]}))

    def test_stop_with_running_workflow_is_silent(self):
        task = {"id": "w1", "type": "workflow", "status": "running", "description": "...", "name": "wf"}
        self.assertIsNone(self.build({"hook_event_name": "Stop", "background_tasks": [task]}))

    def test_stop_with_shell_task_still_notifies(self):
        """開発サーバー等の常駐 shell / monitor で通知が止まらないこと."""
        for kind in ("shell", "monitor"):
            with self.subTest(kind=kind):
                task = {"id": "s1", "type": kind, "status": "running", "description": "..."}
                self.assertIsNotNone(self.build({"hook_event_name": "Stop", "background_tasks": [task]}))

    def test_stop_with_mixed_tasks_is_silent(self):
        tasks = [{"id": "s1", "type": "shell", "status": "running", "description": "..."}, _subagent_task()]
        self.assertIsNone(self.build({"hook_event_name": "Stop", "background_tasks": tasks}))

    def test_stop_with_malformed_background_tasks_notifies(self):
        """想定外の形（古いバージョン・壊れた入力）でも通知を落とさない."""
        for tasks in ("not-a-list", {"type": "subagent"}, [None, "x", 1], [{}]):
            with self.subTest(tasks=tasks):
                self.assertIsNotNone(self.build({"hook_event_name": "Stop", "background_tasks": tasks}))

    # 待ち状態の通知はエージェント名を添える
    def test_permission_prompt_from_subagent_names_agent(self):
        data = {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                "agent_id": "a1", "agent_type": "coder"}
        self.assertIn("coder", self.build(data)["message"])

    def test_permission_prompt_from_main_has_no_agent_label(self):
        data = {"hook_event_name": "Notification", "notification_type": "permission_prompt"}
        self.assertNotIn("エージェント", self.build(data)["message"])

    def test_question_from_subagent_names_agent(self):
        data = {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "agent_type": "tester"}
        self.assertIn("tester", self.build(data)["message"])

    def test_agent_name_is_trimmed_and_capped(self):
        data = {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                "agent_type": "  a" + chr(10) + "b  " + "x" * 200}
        message = self.build(data)["message"]
        self.assertNotIn(chr(10), message)
        self.assertIn("a b", message)
        self.assertLess(len(message), 80)

    def test_blank_agent_type_is_ignored(self):
        for value in ("", "   ", None, 123):
            with self.subTest(value=value):
                data = {"hook_event_name": "Notification", "notification_type": "idle_prompt", "agent_type": value}
                self.assertNotIn("エージェント", self.build(data)["message"])

    def test_unknown_event_is_silent(self):
        self.assertIsNone(self.build({"hook_event_name": "SubagentStop"}))
        self.assertIsNone(self.build({"hook_event_name": "PreToolUse", "tool_name": "Bash"}))


# ---------------------------------------------------------------------------
# Python 3.7 互換（静的チェック）
# ---------------------------------------------------------------------------
PY39_ONLY_ATTRS = {"removesuffix", "removeprefix", "is_relative_to", "with_stem", "readlink", "randbytes", "lcm", "cache"}
PY38_PLUS_MODULES = {"zoneinfo", "graphlib", "tomllib", "importlib.metadata"}
GENERIC_BUILTINS = {"list", "dict", "tuple", "set", "frozenset", "type"}


class TestPython37Compat(unittest.TestCase):
    def check_file(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            source = f.read()
        # 3.8+ 専用構文（walrus・位置専用引数等）を検出する
        tree = ast.parse(source, filename=path, feature_version=(3, 7))

        # from __future__ import annotations（docstring の直後）
        body = tree.body
        idx = 1 if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) else 0
        first = body[idx]
        self.assertIsInstance(first, ast.ImportFrom, "先頭に from __future__ import annotations が必要")
        self.assertEqual(first.module, "__future__")
        self.assertIn("annotations", [a.name for a in first.names])

        # アノテーション以外（実行時に評価される箇所）での list[...] / X | None を検出
        annotation_nodes = set()
        for node in ast.walk(tree):
            anns = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                anns.append(node.returns)
                a = node.args
                for arg in a.args + a.kwonlyargs + getattr(a, "posonlyargs", []) + [a.vararg, a.kwarg]:
                    if arg is not None:
                        anns.append(arg.annotation)
            elif isinstance(node, ast.AnnAssign):
                anns.append(node.annotation)
            for ann in anns:
                if ann is not None:
                    annotation_nodes.update(id(n) for n in ast.walk(ann))

        problems = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in PY39_ONLY_ATTRS:
                problems.append(f"{node.lineno}: .{node.attr}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [node.module] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
                for name in names:
                    if name in PY38_PLUS_MODULES:
                        problems.append(f"{node.lineno}: import {name}")
            if id(node) in annotation_nodes:
                continue
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in GENERIC_BUILTINS:
                problems.append(f"{node.lineno}: 実行時の {node.value.id}[...]")
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                if any(isinstance(s, ast.Constant) and s.value is None for s in (node.left, node.right)):
                    problems.append(f"{node.lineno}: 実行時の X | None")
            if hasattr(ast, "Match") and isinstance(node, ast.Match):
                problems.append(f"{node.lineno}: match 文")
        self.assertEqual(problems, [], f"{os.path.basename(path)} に 3.7 非互換の記述")

    def test_notify_py37_compatible(self):
        self.check_file(NOTIFY)

    def test_restrict_repo_access_py37_compatible(self):
        self.check_file(RESTRICT)


# ---------------------------------------------------------------------------
# check_sync.sh: python3 決め打ちではなくラッパー経由
# ---------------------------------------------------------------------------
# check_sync.sh は team 層のファイルで，solo モードのプロジェクトには配置されない．
# 一方このテスト自体は共通層として全プロジェクトに同期されるため，存在ガードで skip する．
@unittest.skipUnless(os.path.exists(CHECK_SYNC), "check_sync.sh は team 層のため solo には無い")
class TestCheckSync(TempDirMixin, unittest.TestCase):
    def test_no_hardcoded_python3(self):
        with open(CHECK_SYNC, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if not l.lstrip().startswith("#")]
        code = "\n".join(lines)
        self.assertIsNone(re.search(r"(^|[\s;|&(])(python3?|py)\s", code), "python コマンドの直接呼び出しが残っている")
        self.assertIn("run_python.sh", code)

    def test_emits_json_with_dummy_python3(self):
        """ダミー python3 しか無い環境（Windows 相当）でも JSON を出力する."""
        # origin が存在しないパスなので fetch が失敗し emit が呼ばれる
        repo = os.path.join(self.work, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", repo, "remote", "add", "origin", os.path.join(self.work, "no-such-remote")],
            check=True,
            capture_output=True,
        )
        self.fake.dummy("python3")
        self.fake.real("python")
        env = make_env([self.fake.dir] + bash_bin_dirs(), drop=("PYTHONIOENCODING",))
        result = subprocess.run(
            [BASH, CHECK_SYNC],
            capture_output=True,
            env=env,
            cwd=repo,
            timeout=TIMEOUT,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout.decode("utf-8"))
        self.assertIn("[sync-check]", out["systemMessage"])
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"], out["systemMessage"])

    def test_python_missing_does_not_block_session(self):
        repo = os.path.join(self.work, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", repo, "remote", "add", "origin", os.path.join(self.work, "no-such-remote")],
            check=True,
            capture_output=True,
        )
        self.fake.dummy("python3")
        env = make_env([self.fake.dir] + bash_bin_dirs())
        # bash_bin_dirs に python が無い前提（あればこのテストは意味をなさないのでスキップ）
        for d in bash_bin_dirs():
            for n in ("python3", "python", "py"):
                if os.path.exists(os.path.join(d, n)) or os.path.exists(os.path.join(d, n + ".exe")):
                    self.skipTest(f"{d} に {n} がある")
        result = subprocess.run([BASH, CHECK_SYNC], capture_output=True, env=env, cwd=repo, timeout=TIMEOUT)
        self.assertEqual(result.returncode, 0, result.stderr)


# ---------------------------------------------------------------------------
# restrict_repo_access.py: 判定ロジック（Issue #49 の仕様）
# ---------------------------------------------------------------------------
# 「リポジトリ外かつ一時ディレクトリ外の既存ファイル」が必要なため，このテストのディレクトリ配下に
# 使い捨てのサンドボックスを作り，その中に「仮のリポジトリ」「リポジトリ外」「仮のホーム」を
# 置いて判定関数に repo_root を直接渡す（ユーザーの実ファイルは作成・変更・削除しない）．
# テストは判定関数を呼ぶだけで，実際の削除・上書きは行わない．
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_restrict():
    sys.path.insert(0, HOOKS_DIR)
    try:
        import restrict_repo_access  # noqa: WPS433
    finally:
        sys.path.pop(0)
    return restrict_repo_access


def _fwd(path: str) -> str:
    """Bash のコマンド文字列に埋め込む形（区切りを / に）."""
    return path.replace("\\", "/")


def _gitbash(path: str) -> str:
    """Windows の C:\\x を Git Bash 形式 /c/x にする."""
    p = _fwd(path)
    if re.match(r"^[A-Za-z]:/", p):
        return "/" + p[0].lower() + p[2:]
    return p


class RestrictSandboxMixin:
    """sandbox/
         repo/            仮のリポジトリ（repo_root）．sub/・file.txt・.claude/
         outside/         リポジトリ外（一時ディレクトリ外）
           existing.txt   既存ファイル
           dir/file.txt   既存ディレクトリ内の既存ファイル
           other/data/    削除対象のディレクトリ
           archive_dest/  既存の展開先ディレクトリ
         home/            仮のホーム（~・$HOME）．.bashrc・.ssh/id_rsa・source/other/
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.rra = _load_restrict()

    def setUp(self) -> None:
        self.base = tempfile.mkdtemp(prefix="_restrict_sandbox_", dir=TESTS_DIR)
        if self.rra.is_temp_path(self.base):
            shutil.rmtree(self.base, ignore_errors=True)
            self.skipTest("リポジトリが一時ディレクトリ配下にあり，一時ディレクトリ外のリポジトリ外パスを用意できない")
        self.repo = os.path.join(self.base, "repo")
        self.sub = os.path.join(self.repo, "sub")
        self.out = os.path.join(self.base, "outside")
        self.home = os.path.join(self.base, "home")
        for d in (self.sub, os.path.join(self.repo, ".claude"), os.path.join(self.out, "dir"),
                  os.path.join(self.out, "other", "data"), os.path.join(self.out, "archive_dest"),
                  os.path.join(self.home, ".ssh"), os.path.join(self.home, "source", "other")):
            os.makedirs(d)
        for f in (os.path.join(self.repo, "file.txt"), os.path.join(self.out, "existing.txt"),
                  os.path.join(self.out, "dir", "file.txt"), os.path.join(self.home, ".bashrc"),
                  os.path.join(self.home, ".ssh", "id_rsa"), os.path.join(self.out, "other", "data", "a.txt")):
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("x")
        self.existing = os.path.join(self.out, "existing.txt")
        self.missing = os.path.join(self.out, "new_file.txt")
        self.deny = []
        self.transcript_path = None  # フック入力の transcript_path に相当（既定は無し）

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    # --- 判定ヘルパー ---
    def bash(self, command: str, cwd: str | None = None):
        return self.rra.check_bash_command(command, cwd or self.repo, repo_root=self.repo, deny_read=self.deny,
                                           transcript_path=self.transcript_path)

    def ps(self, command: str, cwd: str | None = None):
        return self.rra.check_powershell_command(command, cwd or self.repo, repo_root=self.repo, deny_read=self.deny,
                                                 transcript_path=self.transcript_path)

    def tool(self, name: str, tool_input: dict, cwd: str | None = None):
        policy = self.rra.Policy(self.repo, self.deny, self.transcript_path)
        return self.rra.check_file_tool(name, tool_input, cwd or self.repo, policy)

    def fake_home(self):
        return mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home})

    def run_main(self, data: dict, env: dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", RESTRICT], input=json.dumps(data).encode("utf-8"),
                              capture_output=True, env=env, cwd=self.repo, timeout=TIMEOUT)

    @staticmethod
    def decision(result):
        out = result.stdout.decode("utf-8").strip()
        if not out:
            return None
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"]

    def assertDenied(self, reason, label=""):
        self.assertIsNotNone(reason, f"拒否されるべき: {label}")

    def assertAllowed(self, reason, label=""):
        self.assertIsNone(reason, f"許可されるべき: {label} / 理由: {reason}")


class TestRestrictIssueExamples(RestrictSandboxMixin, unittest.TestCase):
    """Issue #49 の取りこぼし例がすべて拒否されること."""

    def test_powershell_remove_item_recurse(self):
        target = os.path.join(self.out, "other")
        self.assertDenied(self.ps(f"Remove-Item {target} -Recurse"))
        self.assertDenied(self.ps(f"Remove-Item {target} -Recurse -Force"))

    def test_cd_outside_then_rm_relative(self):
        self.assertDenied(self.bash(f'cd "{_fwd(os.path.join(self.out, "other"))}" && rm -rf data'))

    def test_rm_rf_home_subdir(self):
        with self.fake_home():
            self.assertDenied(self.bash("rm -rf ~/source/other"))
        # 実際のホームでも（削除は存在に関係なく拒否）
        self.assertDenied(self.bash("rm -rf ~/source/__no_such_dir_for_test__"))

    def test_overwrite_existing_file_variants(self):
        ex = _fwd(self.existing)
        cases = {
            "sed -i": f"sed -i 's/a/b/' \"{ex}\"",
            ">": f"echo x > \"{ex}\"",
            "tee": f"echo x | tee \"{ex}\"",
            "truncate": f"truncate -s 0 \"{ex}\"",
        }
        for label, cmd in cases.items():
            with self.subTest(label):
                self.assertDenied(self.bash(cmd), label)

    def test_python_inline_rmtree(self):
        target = _fwd(os.path.join(self.out, "other"))
        self.assertDenied(self.bash(f"python -c \"import shutil; shutil.rmtree('{target}')\""))


class TestRestrictBaseAndZones(RestrictSandboxMixin, unittest.TestCase):
    """基準（リポジトリのルート・cwd 起点の相対パス）と許可ゾーン."""

    def test_relative_from_subfolder_inside_repo_is_allowed(self):
        self.assertAllowed(self.bash("rm ../file.txt", cwd=self.sub))
        self.assertAllowed(self.ps("Remove-Item ..\\file.txt", cwd=self.sub))

    def test_relative_from_subfolder_escaping_repo_is_denied(self):
        self.assertDenied(self.bash("rm ../../outside/existing.txt", cwd=self.sub))
        self.assertDenied(self.ps("Remove-Item ..\\..\\outside\\existing.txt", cwd=self.sub))

    def test_inside_repo_delete_and_overwrite_allowed(self):
        f = _fwd(os.path.join(self.repo, "file.txt"))
        for cmd in (f'rm -rf "{f}"', f'echo x > "{f}"', f'sed -i s/a/b/ "{f}"', "rm -rf build"):
            with self.subTest(cmd):
                self.assertAllowed(self.bash(cmd))
        self.assertAllowed(self.tool("Edit", {"file_path": os.path.join(self.repo, "file.txt")}))
        self.assertAllowed(self.tool("Write", {"file_path": os.path.join(self.repo, "file.txt")}))

    def test_temp_dir_allowed(self):
        tmp = tempfile.mkdtemp(prefix="hooktest_zone_")
        try:
            existing = os.path.join(tmp, "e.txt")
            with open(existing, "w") as fh:
                fh.write("x")
            for cmd in (f'rm -rf "{_fwd(tmp)}"', f'echo x > "{_fwd(existing)}"', "rm -rf /tmp/some-dir"):
                with self.subTest(cmd):
                    self.assertAllowed(self.bash(cmd))
            self.assertAllowed(self.ps(f"Remove-Item {tmp} -Recurse -Force"))
            self.assertAllowed(self.tool("Edit", {"file_path": existing}))
            self.assertAllowed(self.tool("Write", {"file_path": existing}))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_special_paths_allowed(self):
        for cmd in ("echo x > /dev/null", "ls 2> /dev/null", "cp file.txt /dev/null", "echo x &> /dev/null"):
            with self.subTest(cmd):
                self.assertAllowed(self.bash(cmd))
        for cmd in ("Get-Item x > $null", "echo x | Out-File NUL", "echo x | Out-File $null", "echo x > NUL"):
            with self.subTest(cmd):
                self.assertAllowed(self.ps(cmd))

    def test_main_uses_claude_project_dir_and_cwd(self):
        """CLAUDE_PROJECT_DIR を基準に，相対パスは入力の cwd 起点で解決する（main 経由）."""
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.repo)
        allow = self.run_main({"tool_name": "Bash", "tool_input": {"command": "rm ../file.txt"}, "cwd": self.sub}, env)
        self.assertEqual(allow.returncode, 0, allow.stderr)
        self.assertIsNone(self.decision(allow))
        deny = self.run_main({"tool_name": "Bash", "tool_input": {"command": "rm ../../outside/existing.txt"}, "cwd": self.sub}, env)
        self.assertEqual(self.decision(deny), "deny")

    def test_main_falls_back_to_cwd_without_project_dir(self):
        env = dict(os.environ)
        env.pop("CLAUDE_PROJECT_DIR", None)
        allow = self.run_main({"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}, "cwd": self.repo}, env)
        self.assertIsNone(self.decision(allow))
        deny = self.run_main({"tool_name": "Bash", "tool_input": {"command": "rm -rf ../outside/other"}, "cwd": self.repo}, env)
        self.assertEqual(self.decision(deny), "deny")

    def test_main_powershell_and_notebook_tools(self):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.repo)
        cases = [
            {"tool_name": "PowerShell", "tool_input": {"command": f"Remove-Item {self.out} -Recurse"}},
            {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": os.path.join(self.out, "n.ipynb"), "new_source": "x"}},
        ]
        for data in cases:
            with self.subTest(data["tool_name"]):
                data["cwd"] = self.repo
                self.assertEqual(self.decision(self.run_main(data, env)), "deny")


class TestRestrictFileTools(RestrictSandboxMixin, unittest.TestCase):
    def test_read_glob_grep_outside_allowed(self):
        self.assertAllowed(self.tool("Read", {"file_path": self.existing}))
        self.assertAllowed(self.tool("Glob", {"pattern": "**/*", "path": self.out}))
        self.assertAllowed(self.tool("Glob", {"pattern": _fwd(self.out) + "/**/*.txt"}))
        self.assertAllowed(self.tool("Grep", {"pattern": "x", "path": self.out}))

    def test_write_outside_new_allowed_existing_denied(self):
        self.assertAllowed(self.tool("Write", {"file_path": self.missing, "content": "x"}))
        self.assertDenied(self.tool("Write", {"file_path": self.existing, "content": "x"}))

    def test_edit_and_notebook_edit_outside_always_denied(self):
        for path in (self.existing, self.missing):
            with self.subTest(path=path):
                self.assertDenied(self.tool("Edit", {"file_path": path, "old_string": "a", "new_string": "b"}))
                self.assertDenied(self.tool("NotebookEdit", {"notebook_path": path + ".ipynb", "new_source": "x"}))

    def test_relative_tool_path_resolved_from_cwd(self):
        self.assertDenied(self.tool("Write", {"file_path": "../../outside/existing.txt"}, cwd=self.sub))
        self.assertAllowed(self.tool("Edit", {"file_path": "../file.txt"}, cwd=self.sub))

    def test_settings_matcher_covers_all_target_tools(self):
        with open(SETTINGS, encoding="utf-8") as f:
            settings = json.load(f)
        matchers = [g.get("matcher", "") for g in settings["hooks"]["PreToolUse"]
                    if any("restrict_repo_access.py" in h.get("command", "") for h in g.get("hooks", []))]
        self.assertEqual(len(matchers), 1)
        for tool in ("Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep", "Bash", "PowerShell"):
            self.assertIn(tool, matchers[0].split("|"))


class TestRestrictDenyRead(RestrictSandboxMixin, unittest.TestCase):
    def write_config(self, text: str) -> None:
        with open(os.path.join(self.repo, ".claude", "repo-access.json"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_load_absolute_home_and_relative_entries(self):
        abs_entry = _fwd(os.path.join(self.out, "dir"))
        self.write_config(json.dumps({"deny_read": [abs_entry, "~/.ssh", "secrets"]}))
        with self.fake_home():
            entries = self.rra.load_deny_read(self.repo)
        normed = [os.path.normcase(os.path.normpath(e)) for e in entries]
        for expected in (os.path.join(self.out, "dir"), os.path.join(self.home, ".ssh"), os.path.join(self.repo, "secrets")):
            self.assertIn(os.path.normcase(os.path.normpath(expected)), normed)

    @unittest.skipUnless(os.name == "nt", "Git Bash 形式のパスは Windows のみ")
    def test_load_gitbash_form_entry(self):
        self.write_config(json.dumps({"deny_read": [_gitbash(os.path.join(self.out, "dir"))]}))
        entries = self.rra.load_deny_read(self.repo)
        self.assertEqual([os.path.normcase(e) for e in entries], [os.path.normcase(os.path.join(self.out, "dir"))])

    def test_missing_config_is_empty(self):
        self.assertEqual(self.rra.load_deny_read(self.repo), [])

    def test_broken_config_is_empty_with_warning(self):
        for text in ("{not json", json.dumps({"deny_read": "~/.ssh"}), json.dumps({"deny_read": [1, 2]}), json.dumps([1])):
            with self.subTest(text=text):
                self.write_config(text)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(self.rra.load_deny_read(self.repo), [])
                self.assertTrue(err.getvalue().strip(), "stderr に警告が出ること")

    def test_broken_config_via_main_does_not_block(self):
        self.write_config("{broken")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.repo)
        result = self.run_main({"tool_name": "Read", "tool_input": {"file_path": self.existing}, "cwd": self.repo}, env)
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(self.decision(result))
        self.assertTrue(result.stderr.strip())

    def test_config_via_main_denies_read(self):
        self.write_config(json.dumps({"deny_read": [_fwd(os.path.join(self.out, "dir"))]}))
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.repo)
        data = {"tool_name": "Read", "tool_input": {"file_path": os.path.join(self.out, "dir", "file.txt")}, "cwd": self.repo}
        self.assertEqual(self.decision(self.run_main(data, env)), "deny")

    def test_denied_path_read_and_write_rejected(self):
        denied_dir = os.path.join(self.out, "dir")
        self.deny = [denied_dir]
        f = os.path.join(denied_dir, "file.txt")
        self.assertDenied(self.tool("Read", {"file_path": f}))
        self.assertDenied(self.tool("Write", {"file_path": os.path.join(denied_dir, "new.txt")}), "新規作成でも拒否")
        self.assertDenied(self.bash(f'cat "{_fwd(f)}"'))
        self.assertDenied(self.bash(f'echo x >> "{_fwd(os.path.join(denied_dir, "new.txt"))}"'))
        self.assertDenied(self.ps(f"Get-Content {f}"))
        # 禁止されていない兄弟は読める
        self.assertAllowed(self.tool("Read", {"file_path": self.existing}))
        self.assertAllowed(self.bash(f'cat "{_fwd(self.existing)}"'))

    def test_denied_path_inside_repo(self):
        self.deny = [os.path.join(self.repo, "secrets")]
        self.assertDenied(self.tool("Read", {"file_path": os.path.join(self.repo, "secrets", "k.txt")}))
        self.assertDenied(self.bash("cat secrets/k.txt"))

    def test_search_root_containing_denied_path_rejected(self):
        self.deny = [os.path.join(self.out, "dir")]
        self.assertDenied(self.tool("Grep", {"pattern": "x", "path": self.out}))
        self.assertDenied(self.tool("Glob", {"pattern": "**/*", "path": self.out}))
        self.assertDenied(self.tool("Glob", {"pattern": _fwd(self.out) + "/**/*.txt"}))
        self.assertDenied(self.bash(f'grep -r x "{_fwd(self.out)}"'))
        self.assertDenied(self.bash(f'find "{_fwd(self.out)}" -name "*.txt"'))
        self.assertDenied(self.ps(f"Get-ChildItem {self.out} -Recurse"))
        # 起点が禁止パスを含まない場所なら許可
        self.assertAllowed(self.tool("Grep", {"pattern": "x", "path": os.path.join(self.out, "other")}))

    def test_home_entry_denies_tilde_access(self):
        with self.fake_home():
            self.deny = [os.path.join(self.home, ".ssh")]
            self.assertDenied(self.bash("cat ~/.ssh/id_rsa"))
            self.assertDenied(self.bash("cat $HOME/.ssh/id_rsa"))
            self.assertDenied(self.tool("Read", {"file_path": "~/.ssh/id_rsa"}))


class TestRestrictCreateAllowed(RestrictSandboxMixin, unittest.TestCase):
    """新規作成・ダウンロード・追記・権限変更はリポジトリ外でも許可."""

    def test_bash_create_append_chmod(self):
        ex, new = _fwd(self.existing), _fwd(self.missing)
        newdir = _fwd(os.path.join(self.out, "newdir", "a"))
        cases = [
            f'mkdir -p "{newdir}"',
            f'touch "{new}"',
            f'touch "{ex}"',
            f'echo x >> "{ex}"',
            f'echo x | tee -a "{ex}"',
            f'curl -o "{new}" https://example.com/x',
            f'curl -fsSLo "{new}" https://example.com/x',
            f'wget -O "{new}" https://example.com/x',
            f'chmod 600 "{ex}"',
            f'echo x > "{new}"',
            f'cp file.txt "{new}"',
            f'cp -n file.txt "{ex}"',
            f'cat "{ex}"',
        ]
        for cmd in cases:
            with self.subTest(cmd):
                self.assertAllowed(self.bash(cmd), cmd)

    def test_powershell_create_append_acl(self):
        ex, new = self.existing, self.missing
        cases = [
            f"New-Item -ItemType Directory {os.path.join(self.out, 'newdir')}",
            f"New-Item -ItemType Directory -Force {os.path.join(self.out, 'dir')}",
            f"New-Item {new}",
            f"Add-Content {ex} 'x'",
            f"Add-Content -Path {ex} -Value 'x'",
            f"'x' | Out-File {ex} -Append",
            f"'x' | Out-File -FilePath {ex} -Append",
            f"Invoke-WebRequest https://example.com/x -OutFile {new}",
            f"Set-Acl {ex} $acl",
            f"Get-Content {ex}",
            f"'x' > {new}",
            f"'x' >> {ex}",
            f"Set-Content {new} 'x'",
            f"Copy-Item file.txt {new}",
        ]
        for cmd in cases:
            with self.subTest(cmd):
                self.assertAllowed(self.ps(cmd), cmd)


class TestRestrictDelete(RestrictSandboxMixin, unittest.TestCase):
    """削除は（存在に関係なく）拒否."""

    def test_bash_delete_commands(self):
        ex = _fwd(self.existing)
        d = _fwd(os.path.join(self.out, "other"))
        cases = [
            f'rm "{ex}"', f'rm -f "{_fwd(self.missing)}"', f'rmdir "{d}"', f'unlink "{ex}"',
            f'truncate -s 0 "{ex}"', f'find "{d}" -delete', f'find "{d}" -name "*.txt" -exec rm {{}} \\;',
            f'mv "{ex}" ./moved.txt', f'sudo rm -rf "{d}"',
        ]
        for cmd in cases:
            with self.subTest(cmd):
                self.assertDenied(self.bash(cmd), cmd)

    def test_powershell_delete_commands(self):
        ex = self.existing
        d = os.path.join(self.out, "other")
        cases = [
            f"Remove-Item {ex}", f"Remove-Item -Path {d} -Recurse -Force", f"Remove-Item -LiteralPath {ex}",
            f"del {ex}", f"rd {d} -Recurse", f"ri {ex}", f"rm {ex}", f"erase {ex}", f"rmdir {d}",
            f"Clear-Content {ex}", f"Move-Item {ex} .\\moved.txt", f"Move-Item -Path {ex} -Destination .\\m.txt",
            f"Rename-Item {ex} renamed.txt", f"[IO.File]::Delete('{ex}')", f"[System.IO.File]::Delete(\"{ex}\")",
        ]
        for cmd in cases:
            with self.subTest(cmd):
                self.assertDenied(self.ps(cmd), cmd)

    def test_cmd_exe_delete_commands_with_host_absolute_path(self):
        """cmd /c の引数のホスト OS の絶対パスをスイッチと取り違えないこと（Issue #51 原因 1）.

        スイッチ（/s・/q）と絶対パス（POSIX の /workspace/x・Windows の C:\\x）の区別は
        OS に依存しない判定なので全 OS で実行する．
        """
        ex = self.existing
        d = os.path.join(self.out, "other")
        cases = [
            f"cmd /c del {ex}", f"cmd /c rd /s /q {d}", f"cmd /c del /f /q {ex}", f"cmd /c rmdir /s {d}",
            f"cmd /c del {_fwd(ex)}", f"cmd /c rd /s /q {_fwd(d)}", f'cmd /c rd /s /q "{d}"',
            f"cmd /c erase {ex}", f"cmd /c ren {ex} renamed.txt", f"cmd /c move {ex} moved.txt",
            f"cmd /c robocopy src {d} /mir", f"cmd /c robocopy src {d} /purge",
            f"cmd /c echo x > {ex}",
        ]
        for cmd in cases:
            with self.subTest(cmd):
                self.assertDenied(self.ps(cmd), cmd)

    def test_cmd_exe_relative_backslash_path_escaping_repo(self):
        """cmd /c の相対パス（`\\` 区切り）の `..` による脱出を全 OS で検出すること."""
        rel = os.path.join("..", "..", "outside")
        self.assertDenied(self.ps("cmd /c del ..\\..\\outside\\existing.txt", cwd=self.sub))
        self.assertDenied(self.ps("cmd /c rd /s /q ..\\..\\outside\\other", cwd=self.sub))
        self.assertDenied(self.ps(f"cmd /c del {_fwd(rel)}/existing.txt", cwd=self.sub))
        self.assertAllowed(self.ps("cmd /c del ..\\file.txt", cwd=self.sub))
        self.assertAllowed(self.ps("cmd /c rd /s /q build"))

    def test_cmd_exe_switches_only_or_inside_repo_allowed(self):
        """スイッチだけ・リポジトリ内の対象は許可（スイッチをパスとして誤検出しない）."""
        for cmd in ("cmd /c dir /s /b", "cmd /c del /q file.txt", "cmd /c rd /s /q sub",
                    "cmd /c xcopy file.txt sub /y /-y", "cmd /c robocopy sub build /mir /log:out.txt",
                    "cmd /c dir /a:h", "cmd /c del /?"):
            with self.subTest(cmd):
                self.assertAllowed(self.ps(cmd), cmd)

    @unittest.skipUnless(os.name == "nt", "ドライブレター・Git Bash 形式（/c/...）のパスは Windows にしか存在しないため")
    def test_cmd_exe_windows_path_forms(self):
        ex = self.existing
        d = os.path.join(self.out, "other")
        cases = [
            f"cmd /c del {_gitbash(ex)}", f"cmd /c rd /s /q {_gitbash(d)}",
            f"cmd /c del {ex.replace(os.sep, '/')}", f"cmd /c robocopy src {d} /mir /log:{os.path.join(self.repo, 'l.txt')}",
            f"cmd /c del {ex[0].lower()}{ex[1:]}",
        ]
        for cmd in cases:
            with self.subTest(cmd):
                self.assertDenied(self.ps(cmd), cmd)
        # /log:C:\x はスイッチなのでリポジトリ外を指していても削除対象にならない
        self.assertAllowed(self.ps(f"cmd /c robocopy sub build /log:{ex}"))

    def test_inline_delete_calls(self):
        d = _fwd(os.path.join(self.out, "other"))
        ex = _fwd(self.existing)
        cases = [
            f"python -c \"import shutil; shutil.rmtree('{d}')\"",
            f"python3 -c \"import os; os.remove('{ex}')\"",
            f"py -c \"import os; os.unlink('{ex}')\"",
            f"node -e \"require('fs').rmSync('{d}', {{recursive: true}})\"",
        ]
        for cmd in cases:
            with self.subTest(cmd):
                self.assertDenied(self.bash(cmd), cmd)
        self.assertDenied(self.ps(f"python -c \"import shutil; shutil.rmtree('{d}')\""))

    def test_inline_without_delete_or_inside_repo_allowed(self):
        ex = _fwd(self.existing)
        self.assertAllowed(self.bash(f"python -c \"print(open('{ex}').read())\""))
        self.assertAllowed(self.bash("python -c \"import shutil; shutil.rmtree('build')\""))


class TestRestrictOverwrite(RestrictSandboxMixin, unittest.TestCase):
    """既存なら拒否，存在しなければ許可."""

    BASH_TEMPLATES = [
        "echo x > \"{t}\"",
        "echo x >| \"{t}\"",
        "cp file.txt \"{t}\"",
        "mv file.txt \"{t}\"",
        "sed -i 's/a/b/' \"{t}\"",
        "sed -i.bak -e 's/a/b/' \"{t}\"",
        "echo x | tee \"{t}\"",
        "dd if=file.txt of=\"{t}\"",
        "curl -o \"{t}\" https://example.com/x",
        "curl --output \"{t}\" https://example.com/x",
        "wget -O \"{t}\" https://example.com/x",
        "python -c \"open('{t}', 'w').write('x')\"",
    ]
    PS_TEMPLATES = [
        "'x' > {t}",
        "Copy-Item file.txt {t}",
        "Copy-Item -Path file.txt -Destination {t}",
        "Move-Item file.txt {t}",
        "Set-Content {t} 'x'",
        "Set-Content -Path {t} -Value 'x'",
        "'x' | Out-File {t}",
        "'x' | Out-File -FilePath {t}",
        "New-Item {t} -Force",
        "Invoke-WebRequest https://example.com/x -OutFile {t}",
        "iwr https://example.com/x -OutFile {t}",
    ]

    def test_bash_overwrite(self):
        for tpl in self.BASH_TEMPLATES:
            with self.subTest(tpl, state="existing"):
                self.assertDenied(self.bash(tpl.format(t=_fwd(self.existing))), tpl)
            with self.subTest(tpl, state="missing"):
                self.assertAllowed(self.bash(tpl.format(t=_fwd(self.missing))), tpl)

    def test_powershell_overwrite(self):
        for tpl in self.PS_TEMPLATES:
            with self.subTest(tpl, state="existing"):
                self.assertDenied(self.ps(tpl.format(t=self.existing)), tpl)
            with self.subTest(tpl, state="missing"):
                self.assertAllowed(self.ps(tpl.format(t=self.missing)), tpl)

    def test_copy_into_existing_directory_uses_basename(self):
        d = os.path.join(self.out, "dir")  # dir/file.txt が既存
        with open(os.path.join(self.repo, "fresh.txt"), "w") as fh:
            fh.write("x")
        self.assertDenied(self.bash(f'cp file.txt "{_fwd(d)}"'))
        self.assertDenied(self.bash(f'cp file.txt "{_fwd(d)}/"'))
        self.assertAllowed(self.bash(f'cp fresh.txt "{_fwd(d)}"'))
        self.assertDenied(self.ps(f"Copy-Item file.txt {d}"))
        self.assertDenied(self.ps(f"Copy-Item -Path file.txt -Destination {d}"))
        self.assertAllowed(self.ps(f"Copy-Item fresh.txt {d}"))

    def test_cp_no_clobber_allowed(self):
        for opt in ("-n", "--no-clobber"):
            with self.subTest(opt):
                self.assertAllowed(self.bash(f'cp {opt} file.txt "{_fwd(self.existing)}"'))

    def test_tee_append_and_out_file_append_allowed(self):
        self.assertAllowed(self.bash(f'echo x | tee -a "{_fwd(self.existing)}"'))
        self.assertAllowed(self.ps(f"'x' | Out-File {self.existing} -Append"))

    def test_expand_archive_force(self):
        dest = os.path.join(self.out, "archive_dest")
        self.assertDenied(self.ps(f"Expand-Archive a.zip -DestinationPath {dest} -Force"))
        self.assertDenied(self.ps(f"Expand-Archive -Path a.zip -DestinationPath {dest} -Force"))
        self.assertAllowed(self.ps(f"Expand-Archive a.zip -DestinationPath {dest}"))
        self.assertAllowed(self.ps(f"Expand-Archive a.zip -DestinationPath {os.path.join(self.out, 'new_dest')} -Force"))

    def test_new_item_without_force_allowed(self):
        self.assertAllowed(self.ps(f"New-Item {self.existing}"))


class TestRestrictLocationTracking(RestrictSandboxMixin, unittest.TestCase):
    def test_bash_cd_variants(self):
        out = _fwd(self.out)
        repo = _fwd(self.repo)
        self.assertDenied(self.bash(f'cd "{out}"; echo x > existing.txt'))
        self.assertDenied(self.bash(f'cd "{out}" && rm -rf other'))
        self.assertAllowed(self.bash(f'cd "{out}" && cat existing.txt'))
        self.assertAllowed(self.bash(f'cd "{out}" && echo x > brand_new.txt'))
        self.assertAllowed(self.bash(f'cd "{out}" && cd "{repo}" && rm -rf build'))
        self.assertDenied(self.bash(f'pushd "{out}" && rm existing.txt'))
        self.assertAllowed(self.bash(f'pushd "{out}" && popd && rm -rf build'))
        self.assertDenied(self.bash("cd .. && rm -rf outside", cwd=self.repo))

    def test_command_substitution_cd_does_not_leak(self):
        out = _fwd(self.out)
        self.assertAllowed(self.bash(f'x=$(cd "{out}" && pwd); rm -rf build'))
        self.assertAllowed(self.bash(f'echo "$(cd "{out}" && pwd)" && rm -rf build'))
        self.assertAllowed(self.bash(f'(cd "{out}" && ls); rm -rf build'))
        # 内側の削除は検出する
        self.assertDenied(self.bash(f'x=$(cd "{out}" && rm -rf other)'))

    def test_powershell_location_variants(self):
        out = self.out
        for verb in ("Set-Location", "cd", "sl", "chdir"):
            with self.subTest(verb):
                self.assertDenied(self.ps(f"{verb} {out}; Remove-Item other -Recurse"))
        self.assertDenied(self.ps(f"Set-Location -Path {out}; Remove-Item existing.txt"))
        self.assertDenied(self.ps(f"Push-Location {out}; Remove-Item existing.txt"))
        self.assertAllowed(self.ps(f"Push-Location {out}; Pop-Location; Remove-Item build -Recurse"))
        self.assertAllowed(self.ps(f"Set-Location {out}; Get-Content existing.txt"))
        self.assertDenied(self.ps(f"Set-Location {out}; Set-Content existing.txt 'x'"))

    def test_home_expansion(self):
        with self.fake_home():
            for cmd in ("rm -rf ~/source/other", "rm -rf $HOME/source/other", "rm -rf ${HOME}/source/other",
                        "echo x > ~/.bashrc", "cd ~ && rm -rf source", "cd && rm .bashrc", "cd $HOME/source && rm -rf other"):
                with self.subTest(cmd):
                    self.assertDenied(self.bash(cmd), cmd)
            for cmd in ("echo x >> ~/.bashrc", "mkdir -p ~/newdir", "echo x > ~/new_file.txt", "cat ~/.bashrc"):
                with self.subTest(cmd):
                    self.assertAllowed(self.bash(cmd), cmd)
            for cmd in ("Remove-Item ~/source/other -Recurse", "Remove-Item $HOME\\source\\other -Recurse",
                        "Remove-Item $env:USERPROFILE\\source\\other -Recurse", "Set-Content ~/.bashrc 'x'"):
                with self.subTest(cmd):
                    self.assertDenied(self.ps(cmd), cmd)
            self.assertAllowed(self.ps("Add-Content ~/.bashrc 'x'"))

    def test_nested_shells(self):
        out = _fwd(self.out)
        self.assertDenied(self.bash(f"bash -c 'rm -rf \"{out}/other\"'"))
        self.assertDenied(self.bash(f"sh -c 'cd \"{out}\" && rm -rf other'"))
        self.assertDenied(self.bash(f"pwsh -Command \"Remove-Item '{self.out}' -Recurse\""))
        self.assertDenied(self.bash(f"powershell -Command \"Remove-Item '{self.out}' -Recurse\""))
        self.assertDenied(self.ps(f"bash -c 'rm -rf \"{out}/other\"'"))
        self.assertDenied(self.ps(f"pwsh -Command \"Remove-Item {self.out} -Recurse\""))
        self.assertAllowed(self.bash("bash -c 'rm -rf build'"))

    @unittest.skipUnless(os.name == "nt", "Git Bash 形式（/c/...）は Windows のみ")
    def test_gitbash_drive_form(self):
        out = _gitbash(self.out)
        self.assertDenied(self.bash(f'rm -rf "{out}/other"'))
        self.assertDenied(self.bash(f'cd "{out}" && rm -rf other'))
        self.assertDenied(self.bash(f'echo x > "{out}/existing.txt"'))
        self.assertAllowed(self.bash(f'echo x > "{out}/brand_new.txt"'))
        self.assertAllowed(self.bash(f'rm -rf "{_gitbash(self.repo)}/build"'))
        self.assertDenied(self.bash(f'rm -rf "/cygdrive{out}/other"'))


class TestRestrictPowerShellBackslash(RestrictSandboxMixin, unittest.TestCase):
    """PowerShell の `\\` 区切りの相対パスをホスト OS に関係なく解決すること（Issue #51 原因 2）.

    pwsh は Linux / macOS でも `\\` を区切りとして受け付けるため，`..\\..` による脱出の検出は
    OS 非依存であるべき判定として全 OS で実行する．
    """

    def test_remove_item_escaping_repo_denied(self):
        cases = [
            "Remove-Item ..\\..\\outside\\existing.txt",
            "Remove-Item ..\\..\\outside\\other -Recurse -Force",
            "Remove-Item -Path ..\\..\\outside\\other -Recurse",
            "Remove-Item -LiteralPath ..\\..\\outside\\existing.txt",
            "Remove-Item ..\\../outside/existing.txt",
            "Remove-Item .\\..\\..\\outside\\existing.txt",
            "Clear-Content ..\\..\\outside\\existing.txt",
            "Move-Item ..\\..\\outside\\existing.txt .\\moved.txt",
            "Remove-Item ..\\..\\outside\\__no_such_file__.txt",
        ]
        for cmd in cases:
            with self.subTest(cmd):
                self.assertDenied(self.ps(cmd, cwd=self.sub), cmd)

    def test_overwrite_escaping_repo_by_existence(self):
        self.assertDenied(self.ps("Set-Content ..\\..\\outside\\existing.txt 'x'", cwd=self.sub))
        self.assertDenied(self.ps("'x' > ..\\..\\outside\\existing.txt", cwd=self.sub))
        self.assertDenied(self.ps("Copy-Item ..\\file.txt ..\\..\\outside\\existing.txt", cwd=self.sub))
        self.assertAllowed(self.ps("Set-Content ..\\..\\outside\\brand_new.txt 'x'", cwd=self.sub))
        self.assertAllowed(self.ps("Add-Content ..\\..\\outside\\existing.txt 'x'", cwd=self.sub))

    def test_inside_repo_backslash_relative_allowed(self):
        for cmd in ("Remove-Item ..\\file.txt", "Remove-Item ..\\sub\\x -Recurse", "Remove-Item .\\build -Recurse",
                    "Set-Content ..\\file.txt 'x'", "Remove-Item ..\\..\\repo\\file.txt"):
            with self.subTest(cmd):
                self.assertAllowed(self.ps(cmd, cwd=self.sub), cmd)

    def test_location_change_with_backslash_relative(self):
        self.assertDenied(self.ps("Set-Location ..\\..\\outside; Remove-Item existing.txt", cwd=self.sub))
        self.assertDenied(self.ps("cd ..\\..\\outside\\other; Remove-Item data -Recurse", cwd=self.sub))
        self.assertAllowed(self.ps("Set-Location ..\\..\\outside; Get-Content existing.txt", cwd=self.sub))

    def test_main_powershell_backslash_relative(self):
        """main 経由（フック入力の cwd 起点）でも拒否されること."""
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.repo)
        deny = self.run_main({"tool_name": "PowerShell", "cwd": self.sub,
                              "tool_input": {"command": "Remove-Item ..\\..\\outside\\other -Recurse"}}, env)
        self.assertEqual(deny.returncode, 0, deny.stderr)
        self.assertEqual(self.decision(deny), "deny")
        allow = self.run_main({"tool_name": "PowerShell", "cwd": self.sub,
                               "tool_input": {"command": "Remove-Item ..\\file.txt"}}, env)
        self.assertIsNone(self.decision(allow))


@contextlib.contextmanager
def _simulate_posix_host(module):
    """module から見える os を POSIX ホスト相当（os.name == "posix"・os.path == posixpath）にする.

    差し替えるのは module の名前空間の `os` だけで，本物の os モジュール・他のテストには影響しない．
    with を抜けると（例外時も）mock.patch.object が元に戻す．
    """
    fake = types.SimpleNamespace(**{k: getattr(os, k) for k in dir(os) if not k.startswith("__")})
    fake.name = "posix"
    fake.path = posixpath
    fake.sep = "/"
    fake.altsep = None
    with mock.patch.object(module, "os", fake):
        yield


class TestRestrictPosixHostSimulation(unittest.TestCase):
    """ホスト OS に関係なく，POSIX ホストでの解決結果を確かめる（Windows 上でも POSIX の挙動を検証する）.

    実 OS 上のサンドボックスを使うテスト（TestRestrictPowerShellBackslash 等）を補完する．
    パスは POSIX 形式の文字列で与える（このクラスでは os.path を posixpath に差し替えているため）．
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.rra = _load_restrict()

    def test_simulation_is_restored(self):
        real_os = self.rra.os
        with _simulate_posix_host(self.rra):
            self.assertEqual(self.rra.os.name, "posix")
            self.assertIs(self.rra.os.path, posixpath)
        self.assertIs(self.rra.os, real_os)
        self.assertIs(self.rra.os, os)
        with self.assertRaises(RuntimeError):
            with _simulate_posix_host(self.rra):
                raise RuntimeError("x")
        self.assertIs(self.rra.os, os, "例外時も復元されること")

    def test_powershell_backslash_relative_resolved(self):
        cases = {
            "..\\..\\outside\\existing.txt": "/work/outside/existing.txt",
            "..\\..\\outside\\other": "/work/outside/other",
            "..\\../outside/x": "/work/outside/x",
            ".\\a\\..\\b": "/work/repo/sub/b",
            "..\\file.txt": "/work/repo/file.txt",
            "\\work\\outside\\x": "/work/outside/x",
        }
        with _simulate_posix_host(self.rra):
            for token, expected in cases.items():
                with self.subTest(token):
                    self.assertEqual(self.rra.resolve_path(token, "/work/repo/sub", "powershell"), expected)

    def test_bash_dialect_unchanged(self):
        """bash の `\\` はエスケープ・ファイル名の一部であり，区切りへの正規化は PowerShell に限る."""
        with _simulate_posix_host(self.rra):
            self.assertEqual(self.rra.resolve_path("../x", "/work/repo/sub", "bash"), "/work/repo/x")
            self.assertEqual(self.rra.resolve_path("a\\b", "/work/repo", "bash"), "/work/repo/a\\b")

    def test_no_windows_drive_conversion_on_posix(self):
        """Git Bash 形式（/c/...）の変換は Windows のときだけ行う."""
        with _simulate_posix_host(self.rra):
            self.assertEqual(self.rra.convert_posix_drive("/c/Users/x"), "/c/Users/x")
            self.assertEqual(self.rra.resolve_path("/c/Users/x", "/work/repo", "bash"), "/c/Users/x")
            self.assertEqual(self.rra.resolve_path("/c/Users/x", "/work/repo", "powershell"), "/c/Users/x")

    def test_powershell_remove_item_escaping_repo_denied(self):
        """受け入れ条件: POSIX ホストでも `Remove-Item ..\\..\\outside\\x` が拒否される."""
        with _simulate_posix_host(self.rra):
            for cmd in ("Remove-Item ..\\..\\outside\\x", "Remove-Item ..\\..\\outside\\x -Recurse -Force",
                        "cmd /c del ..\\..\\outside\\x", "cmd /c rd /s /q /work/outside/x"):
                with self.subTest(cmd):
                    reason = self.rra.check_powershell_command(cmd, "/work/repo/sub", repo_root="/work/repo", deny_read=[])
                    self.assertIsNotNone(reason, f"拒否されるべき: {cmd}")
            for cmd in ("Remove-Item ..\\x", "cmd /c rd /s /q ..\\build"):
                with self.subTest(cmd):
                    reason = self.rra.check_powershell_command(cmd, "/work/repo/sub", repo_root="/work/repo", deny_read=[])
                    self.assertIsNone(reason, f"許可されるべき: {cmd} / 理由: {reason}")


def _isabs_py313(path) -> bool:
    """Python 3.13 以降の ntpath.isabs 相当（UNC・デバイスパスか，ドライブとルートを持つときだけ True）.

    3.12 以前は `/tmp/x`・`\\tmp\\x` のようなドライブ無しの root-relative パスも True だった．
    """
    s = os.fspath(path)
    if isinstance(s, bytes):
        s = s.decode("utf-8", "surrogateescape")
    s = s[:3].replace("/", "\\")
    return (s.startswith("\\") and s.startswith("\\", 1)) or s.startswith(":\\", 1)


@contextlib.contextmanager
def _simulate_py313_ntpath_isabs():
    """ntpath.isabs を Python 3.13 相当に差し替える（with を抜けると例外時も mock.patch が元に戻す）.

    Windows ではフックの os.path は ntpath そのものなので，フックの os.path.isabs にも効く．
    実行中の Python が 3.13 以降なら実質的に同じ挙動のまま（差し替えても結果は変わらない）．
    """
    with mock.patch.object(ntpath, "isabs", _isabs_py313):
        yield


class TestIsAbsoluteAllHosts(unittest.TestCase):
    """is_absolute: ホスト OS に依存しない判定と，POSIX ホストの挙動が変わらないこと（全 OS で実行）."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rra = _load_restrict()

    def test_relative_and_native_absolute(self):
        for path in ("x", "a/b", "../x", ".", ""):
            with self.subTest(path=path):
                self.assertFalse(self.rra.is_absolute(path), path)
        native_abs = os.path.abspath("x")
        self.assertTrue(self.rra.is_absolute(native_abs), native_abs)

    def test_posix_host_slash_absolute_backslash_relative(self):
        """POSIX ホストでは `/tmp/x` は絶対，`\\tmp\\x` は相対のまま（ファイル名の一部）."""
        with _simulate_posix_host(self.rra):
            self.assertTrue(self.rra.is_absolute("/tmp/x"))
            self.assertTrue(self.rra.is_absolute("/"))
            self.assertFalse(self.rra.is_absolute("\\tmp\\x"))
            self.assertFalse(self.rra.is_absolute("\\"))
            self.assertFalse(self.rra.is_absolute("C:\\x"))

    def test_posix_host_resolve_backslash_root_stays_relative(self):
        with _simulate_posix_host(self.rra):
            self.assertEqual(self.rra.resolve_path("\\tmp\\x", "/work/repo", "bash"), "/work/repo/\\tmp\\x")
            self.assertEqual(self.rra.resolve_path("/tmp/some-dir", "/work/repo", "bash"), "/tmp/some-dir")
            self.assertTrue(self.rra.is_temp_path(self.rra.resolve_path("/tmp/some-dir", "/work/repo", "bash")))
            self.assertIsNone(self.rra.check_bash_command("rm -rf /tmp/some-dir", "/work/repo",
                                                          repo_root="/work/repo", deny_read=[]))

    def test_posix_host_unaffected_by_ntpath_patch(self):
        """ntpath の差し替えは POSIX ホスト相当（posixpath）の判定に影響しない."""
        with _simulate_posix_host(self.rra), _simulate_py313_ntpath_isabs():
            self.assertTrue(self.rra.is_absolute("/tmp/x"))
            self.assertFalse(self.rra.is_absolute("\\tmp\\x"))
            self.assertEqual(self.rra.resolve_path("/tmp/some-dir", "/work/repo", "bash"), "/tmp/some-dir")


class TestPy313SimulationRestored(unittest.TestCase):
    def test_patch_effective_and_restored(self):
        original = ntpath.isabs
        with _simulate_py313_ntpath_isabs():
            self.assertFalse(ntpath.isabs("/tmp/x"))
            self.assertFalse(ntpath.isabs("\\tmp\\x"))
            self.assertTrue(ntpath.isabs("C:\\x"))
            self.assertTrue(ntpath.isabs("C:/x"))
            self.assertTrue(ntpath.isabs("\\\\server\\share\\x"))
            self.assertFalse(ntpath.isabs("C:x"))
        self.assertIs(ntpath.isabs, original)
        with self.assertRaises(RuntimeError):
            with _simulate_py313_ntpath_isabs():
                raise RuntimeError("x")
        self.assertIs(ntpath.isabs, original, "例外時も復元されること")


class _WindowsRootRelativeCases:
    """Windows でドライブ無しの root-relative パスを絶対パスとして扱うこと（Python のバージョンによらず）.

    サブクラスの simulate() が ntpath.isabs の挙動（実行中の Python のまま / 3.13 相当）を切り替える．
    """

    def simulate(self):
        raise NotImplementedError

    def test_is_absolute_root_relative(self):
        with self.simulate():
            for path in ("/tmp/x", "\\tmp\\x", "/", "\\", "C:\\x", "C:/x", "\\\\server\\share\\x"):
                with self.subTest(path=path):
                    self.assertTrue(self.rra.is_absolute(path), path)
            for path in ("x", "..\\x", "C:x", ""):
                with self.subTest(path=path):
                    self.assertFalse(self.rra.is_absolute(path), path)

    def test_resolve_root_relative_not_joined_with_cwd(self):
        with self.simulate():
            for token, dialect in (("/tmp/some-dir", "bash"), ("/tmp/some-dir", "powershell"),
                                   ("\\tmp\\some-dir", "powershell"), ("\\tmp\\some-dir", "bash")):
                with self.subTest(token=token, dialect=dialect):
                    resolved = self.rra.resolve_path(token, self.repo, dialect)
                    self.assertEqual(resolved, "\\tmp\\some-dir")
                    self.assertTrue(self.rra.is_temp_path(resolved), resolved)
            # cwd が無くても（cwd 不明でも）絶対パスとして解決できる
            self.assertEqual(self.rra.resolve_path("/tmp/some-dir", None, "bash"), "\\tmp\\some-dir")

    def test_relative_still_joined_with_cwd(self):
        with self.simulate():
            self.assertEqual(os.path.normcase(self.rra.resolve_path("../file.txt", self.sub, "bash")),
                             os.path.normcase(os.path.join(self.repo, "file.txt")))
            self.assertEqual(os.path.normcase(self.rra.resolve_path("..\\file.txt", self.sub, "powershell")),
                             os.path.normcase(os.path.join(self.repo, "file.txt")))

    def test_rm_rf_tmp_allowed(self):
        with self.simulate():
            for cmd in ("rm -rf /tmp/some-dir", "rm -rf /tmp/some-dir/sub", "echo x > /tmp/some-file"):
                with self.subTest(cmd):
                    self.assertAllowed(self.bash(cmd), cmd)
            for cmd in ("Remove-Item /tmp/some-dir -Recurse -Force", "Remove-Item \\tmp\\some-dir -Recurse"):
                with self.subTest(cmd):
                    self.assertAllowed(self.ps(cmd), cmd)

    def test_tmp_escape_still_denied(self):
        """/tmp/.. による脱出は温存しない（絶対パス扱いにしても一時ディレクトリ判定を緩めない）."""
        with self.simulate():
            self.assertDenied(self.bash("rm -rf /tmp/../some-dir"))
            self.assertDenied(self.bash(f'rm -rf "{_fwd(os.path.join(self.out, "other"))}"'))

    def test_deny_read_root_relative_entry_not_joined_with_repo_root(self):
        cfg = os.path.join(self.repo, ".claude", "repo-access.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"deny_read": ["/foo", "\\bar\\baz", "secrets"]}))
        with self.simulate():
            entries = self.rra.load_deny_read(self.repo)
        normed = [os.path.normcase(e) for e in entries]
        self.assertIn(os.path.normcase("\\foo"), normed)
        self.assertIn(os.path.normcase("\\bar\\baz"), normed)
        self.assertIn(os.path.normcase(os.path.join(self.repo, "secrets")), normed)
        self.assertEqual(len(entries), 3)


@unittest.skipUnless(os.name == "nt", "ntpath の root-relative パス（ドライブ無しの /tmp・\\tmp）は Windows ホストのみの概念")
class TestWindowsRootRelativeCurrentPython(_WindowsRootRelativeCases, RestrictSandboxMixin, unittest.TestCase):
    """実行中の Python の ntpath.isabs のまま検証する."""

    def simulate(self):
        return contextlib.nullcontext()


@unittest.skipUnless(os.name == "nt", "ntpath.isabs の 3.13 での変更は Windows（ntpath）でのみフックに影響する")
class TestWindowsRootRelativePy313(_WindowsRootRelativeCases, RestrictSandboxMixin, unittest.TestCase):
    """ntpath.isabs を Python 3.13 相当に差し替えて検証する（3.12 以前のホストでも 3.13+ の挙動を再現する）."""

    def simulate(self):
        return _simulate_py313_ntpath_isabs()

    def test_temp_dir_allowed_existing_case(self):
        """CI（windows-latest / Python 3.14）で失敗した test_temp_dir_allowed の /tmp ケースの再現."""
        with self.simulate():
            self.assertFalse(os.path.isabs("/tmp/some-dir"), "差し替えが効いていること")
            self.assertAllowed(self.bash("rm -rf /tmp/some-dir"))


class TestCmdSwitchDetection(unittest.TestCase):
    """cmd.exe のスイッチとパスの区別（Issue #51 原因 1）．文字列だけの判定なので全 OS で実行する."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rra = _load_restrict()

    def test_switches(self):
        for text in ("/s", "/q", "/S", "/Q", "/f", "/y", "/-y", "/+y", "/?", "/mir", "/MIR", "/purge", "/mov",
                     "/move", "/e", "/a:h", "/A:-H", "/log:C:\\x\\log.txt", "/log:out.txt", "/r:3", "/w:10",
                     "/xd", "/mt:8", "/b", "/c", "/k", "/x1"):
            with self.subTest(text):
                self.assertTrue(self.rra.is_cmd_switch(text), text)

    def test_paths(self):
        for text in ("/workspace/x", "/workspace/repo/outside/existing.txt", "/c/Users", "/c/", "/C/Users/x",
                     "/tmp/x", "/home/user/.bashrc", "/cygdrive/c/x", "/s/x", "/a\\b", "//server/share",
                     "C:\\x", "C:/x", "..\\x", "x", "", "/", "/1", "/-", "/:x", "/ s", "/.ssh"):
            with self.subTest(text):
                self.assertFalse(self.rra.is_cmd_switch(text), text)


class TestRestrictReasons(RestrictSandboxMixin, unittest.TestCase):
    def assertWriteGuidance(self, reason):
        self.assertIsNotNone(reason)
        self.assertIn("迂回", reason)
        self.assertIn("! <コマンド>", reason)

    def test_delete_and_overwrite_reasons_have_guidance(self):
        self.assertWriteGuidance(self.bash(f'rm -rf "{_fwd(self.out)}"'))
        self.assertWriteGuidance(self.bash(f'echo x > "{_fwd(self.existing)}"'))
        self.assertWriteGuidance(self.ps(f"Remove-Item {self.out} -Recurse"))
        self.assertWriteGuidance(self.bash(f"python -c \"import shutil; shutil.rmtree('{_fwd(self.out)}')\""))
        self.assertWriteGuidance(self.tool("Write", {"file_path": self.existing}))
        self.assertWriteGuidance(self.tool("Edit", {"file_path": self.existing}))

    def test_deny_read_reason_has_no_bang_request(self):
        self.deny = [os.path.join(self.out, "dir")]
        reasons = [
            self.tool("Read", {"file_path": os.path.join(self.out, "dir", "file.txt")}),
            self.bash(f'cat "{_fwd(os.path.join(self.out, "dir", "file.txt"))}"'),
            self.tool("Grep", {"pattern": "x", "path": self.out}),
        ]
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertIsNotNone(reason)
                self.assertIn("迂回", reason)
                self.assertNotIn("! <コマンド>", reason)
                self.assertNotIn("`!", reason)


# ---------------------------------------------------------------------------
# 今のプロジェクトのメモのフォルダの許可（Issue #58）
# ---------------------------------------------------------------------------
class MemorySandboxMixin(RestrictSandboxMixin):
    """home/.claude/
         settings.json
         projects/
           memory/note.md             projects 直下の memory（別の場所）
           proj/                      今のプロジェクト
             session.jsonl            transcript_path
             memory/note.md・sub/deep.md
             memory2/note.md 等       名前の似たフォルダ
             nested/memory/note.md    別の場所の memory
           other/                     他のプロジェクト
             session.jsonl・memory/note.md
    """

    SIMILAR_NAMES = ("memory2", "memory-old", "memory.bak", "memor", "xmemory")

    def setUp(self) -> None:
        super().setUp()
        self.dot_claude = os.path.join(self.home, ".claude")
        self.projects = os.path.join(self.dot_claude, "projects")
        self.proj = os.path.join(self.projects, "proj")
        self.other = os.path.join(self.projects, "other")
        self.memory = os.path.join(self.proj, "memory")
        self.note = os.path.join(self.memory, "note.md")
        self.deep_dir = os.path.join(self.memory, "sub")
        self.deep = os.path.join(self.deep_dir, "deep.md")
        self.new_note = os.path.join(self.memory, "new_note.md")
        self.session = os.path.join(self.proj, "session.jsonl")
        self.settings = os.path.join(self.dot_claude, "settings.json")
        self.other_session = os.path.join(self.other, "session.jsonl")
        self.other_note = os.path.join(self.other, "memory", "note.md")
        self.elsewhere = [
            os.path.join(self.out, "memory", "note.md"),
            os.path.join(self.dot_claude, "memory", "note.md"),
            os.path.join(self.projects, "memory", "note.md"),
            os.path.join(self.proj, "nested", "memory", "note.md"),
            os.path.join(self.memory, "..", "nested", "memory", "note.md"),
        ]
        self.similar = [os.path.join(self.proj, name, "note.md") for name in self.SIMILAR_NAMES]
        for f in [self.note, self.deep, self.session, self.settings, self.other_session, self.other_note,
                  *self.elsewhere, *self.similar]:
            os.makedirs(os.path.dirname(f), exist_ok=True)
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("x")
        self.transcript_path = self.session

    def link_dir(self, target: str, link: str) -> None:
        """ディレクトリへのリンクを作る（Windows で権限が無ければジャンクションを使う）."""
        try:
            os.symlink(target, link, target_is_directory=True)
            return
        except OSError:
            if os.name != "nt":
                raise
        result = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True)
        if result.returncode != 0:
            self.skipTest("Windows でシンボリックリンクの権限が無く，ジャンクションも作成できない")

    def write_ops(self, path: str) -> dict:
        """既存ファイルを壊す操作（Edit・Write・rm・上書き）を Bash / PowerShell を含めて判定する."""
        f = _fwd(path)
        return {
            "Edit": self.tool("Edit", {"file_path": path, "old_string": "x", "new_string": "y"}),
            "Write": self.tool("Write", {"file_path": path, "content": "y"}),
            "MultiEdit": self.tool("MultiEdit", {"file_path": path, "edits": []}),
            "bash rm": self.bash(f'rm "{f}"'),
            "bash rm -rf": self.bash(f'rm -rf "{f}"'),
            "bash redirect": self.bash(f'echo y > "{f}"'),
            "bash tee": self.bash(f'echo y | tee "{f}"'),
            "bash sed -i": self.bash(f"sed -i s/x/y/ \"{f}\""),
            "bash truncate": self.bash(f'truncate -s 0 "{f}"'),
            "bash cp": self.bash(f'cp file.txt "{f}"'),
            "bash mv": self.bash(f'mv "{f}" "{f}.moved"'),
            "ps Remove-Item": self.ps(f'Remove-Item "{path}"'),
            "ps Remove-Item -Force": self.ps(f'Remove-Item "{path}" -Recurse -Force'),
            "ps redirect": self.ps(f'echo y > "{path}"'),
            "ps Set-Content": self.ps(f'Set-Content "{path}" y'),
            "ps Out-File": self.ps(f'echo y | Out-File "{path}"'),
            "ps Copy-Item": self.ps(f'Copy-Item file.txt "{path}"'),
            "ps Move-Item": self.ps(f'Move-Item "{path}" "{path}.moved"'),
        }

    def assertAllWriteOpsAllowed(self, path: str) -> None:
        for label, reason in self.write_ops(path).items():
            with self.subTest(op=label, path=path):
                self.assertAllowed(reason, label)

    def assertAllWriteOpsDenied(self, path: str) -> None:
        for label, reason in self.write_ops(path).items():
            with self.subTest(op=label, path=path):
                self.assertDenied(reason, label)


class TestRestrictMemoryAllowed(MemorySandboxMixin, unittest.TestCase):
    """memory の配下は Edit・Write・rm・上書きのすべてが許可される（文字列から確定できないパスを除く）."""

    def test_all_write_ops_on_existing_file(self):
        self.assertAllWriteOpsAllowed(self.note)

    def test_all_write_ops_in_subfolder(self):
        self.assertAllWriteOpsAllowed(self.deep)

    def test_all_write_ops_on_missing_file(self):
        self.assertAllWriteOpsAllowed(self.new_note)

    def test_notebook_edit(self):
        self.assertAllowed(self.tool("NotebookEdit", {"notebook_path": os.path.join(self.memory, "n.ipynb"), "new_source": "x"}))

    def test_memory_folder_not_created_yet(self):
        """memory がまだ無くても，配下への操作は許可される（判定は文字列の位置で決まる）."""
        shutil.rmtree(self.memory)
        self.assertAllWriteOpsAllowed(self.note)

    def test_delete_subfolder(self):
        d = _fwd(self.deep_dir)
        self.assertAllowed(self.bash(f'rm -rf "{d}"'))
        self.assertAllowed(self.bash(f'rmdir "{d}"'))
        self.assertAllowed(self.ps(f'Remove-Item "{self.deep_dir}" -Recurse -Force'))

    def test_relative_paths_from_cwd(self):
        self.assertAllowed(self.bash("rm note.md", cwd=self.memory))
        self.assertAllowed(self.bash("rm -rf sub", cwd=self.memory))
        self.assertAllowed(self.bash("echo y > note.md", cwd=self.memory))
        self.assertDenied(self.bash("rm ../note.md", cwd=self.deep_dir))
        self.assertAllowed(self.bash("rm memory/note.md", cwd=self.proj))
        self.assertAllowed(self.ps("Remove-Item note.md", cwd=self.memory))
        self.assertAllowed(self.ps("Set-Content note.md y", cwd=self.memory))
        self.assertDenied(self.ps("Remove-Item ..\\note.md", cwd=self.deep_dir))
        self.assertAllowed(self.tool("Edit", {"file_path": "note.md"}, cwd=self.memory))
        self.assertDenied(self.tool("Write", {"file_path": "../note.md"}, cwd=self.deep_dir))

    def test_cd_into_memory_then_delete(self):
        self.assertAllowed(self.bash(f'cd "{_fwd(self.memory)}" && rm note.md'))
        self.assertAllowed(self.ps(f'Set-Location "{self.memory}"; Remove-Item note.md'))

    def test_dot_dot_staying_inside_memory_is_denied(self):
        """`..` を含むパスは，正規化すると配下に留まる場合も許可しない（途中がリンクだと外に出るため）."""
        path = os.path.join(self.deep_dir, "..", "note.md")
        self.assertDenied(self.tool("Edit", {"file_path": path}))
        self.assertDenied(self.bash(f'rm "{_fwd(path)}"'))
        self.assertDenied(self.ps(f'Remove-Item "{path}"'))

    def test_home_shorthand(self):
        with self.fake_home():
            self.assertAllowed(self.bash("rm ~/.claude/projects/proj/memory/note.md"))
            self.assertAllowed(self.bash("echo y > $HOME/.claude/projects/proj/memory/note.md"))
            self.assertAllowed(self.ps("Remove-Item $HOME/.claude/projects/proj/memory/note.md"))
            self.assertAllowed(self.tool("Edit", {"file_path": "~/.claude/projects/proj/memory/note.md"}))

    def test_inline_python(self):
        n = _fwd(self.note)
        self.assertAllowed(self.bash(f"python -c \"import os; os.remove('{n}')\""))
        self.assertAllowed(self.bash(f"python -c \"open('{n}', 'w').write('y')\""))

    def test_glob_under_memory(self):
        """memory の中身だけを指すワイルドカードは配下への操作なので許可される."""
        self.assertAllowed(self.bash(f'rm -rf "{_fwd(self.memory)}"/*'))
        self.assertAllowed(self.ps(f'Remove-Item "{os.path.join(self.memory, "*")}"'))

    def test_brace_expansion_under_memory_is_denied(self):
        """波括弧を含むパスは，展開後がすべて配下に留まる場合も許可しない."""
        m = _fwd(self.memory)
        self.assertDenied(self.bash(f"rm {m}/{{note.md,sub/deep.md}}"))
        self.assertDenied(self.bash(f"rm -rf {m}/{{note.md,sub}}"))

    def test_transcript_with_forward_slashes(self):
        self.transcript_path = _fwd(self.session)
        self.assertAllWriteOpsAllowed(self.note)

    def test_transcript_with_dot_dot_is_normalized(self):
        self.transcript_path = os.path.join(self.proj, "memory", "..", "session.jsonl")
        self.assertAllowed(self.tool("Edit", {"file_path": self.note}))

    def test_transcript_file_need_not_exist(self):
        os.remove(self.session)
        self.assertAllowed(self.tool("Edit", {"file_path": self.note}))

    def test_transcript_parent_reached_through_link(self):
        """親フォルダまでのリンクは同じ場所として扱う（macOS の /tmp 等）."""
        linked = os.path.join(self.projects, "linked")
        self.link_dir(self.proj, linked)
        self.transcript_path = os.path.join(linked, "session.jsonl")
        self.assertAllowed(self.tool("Edit", {"file_path": self.note}))
        self.assertAllowed(self.tool("Edit", {"file_path": os.path.join(linked, "memory", "note.md")}))
        self.transcript_path = self.session
        self.assertAllowed(self.tool("Edit", {"file_path": os.path.join(linked, "memory", "note.md")}))

    def test_other_zones_still_work(self):
        self.assertAllowed(self.bash(f'rm "{_fwd(os.path.join(self.repo, "file.txt"))}"'))
        self.assertDenied(self.bash(f'rm "{_fwd(self.existing)}"'))
        self.assertAllowed(self.tool("Write", {"file_path": self.missing}))

    def test_is_inside_memory(self):
        policy = self.rra.Policy(self.repo, [], self.session)
        self.assertTrue(policy.is_inside_memory(self.note))
        self.assertTrue(policy.is_inside_memory(self.deep))
        self.assertTrue(policy.is_inside_memory(self.new_note))
        self.assertFalse(policy.is_inside_memory(self.other_note))
        self.assertFalse(policy.is_inside_memory(self.session))


class TestRestrictMemoryInvalidTranscript(MemorySandboxMixin, unittest.TestCase):
    """transcript_path が使えないときは許可を追加しない（例外も出さない）."""

    def invalid_values(self):
        return {
            "None": None,
            "empty": "",
            "int": 123,
            "zero": 0,
            "bool": True,
            "list": [self.session],
            "dict": {"path": self.session},
            "relative file": "session.jsonl",
            "relative dir": os.path.join("proj", "session.jsonl"),
            "dot relative": os.path.join(".", "session.jsonl"),
            "dot dot relative": os.path.join("..", "proj", "session.jsonl"),
            "NUL at end": self.session + "\0",
            "NUL in folder": os.path.join(self.proj + "\0", "session.jsonl"),
        }

    def test_policy_does_not_allow_memory(self):
        for label, value in self.invalid_values().items():
            with self.subTest(label):
                policy = self.rra.Policy(self.repo, [], value)
                self.assertFalse(policy.is_inside_memory(self.note))
                self.assertFalse(policy.is_write_allowed(self.note))

    def test_all_write_ops_denied(self):
        for label, value in self.invalid_values().items():
            self.transcript_path = value
            with self.subTest(label):
                self.assertAllWriteOpsDenied(self.note)

    def test_relative_transcript_is_not_resolved_from_cwd(self):
        """cwd から解決すれば今のプロジェクトになる相対パスでも許可しない."""
        self.transcript_path = "session.jsonl"
        self.assertDenied(self.bash("rm memory/note.md", cwd=self.proj))
        self.assertDenied(self.ps("Remove-Item memory/note.md", cwd=self.proj))
        self.assertDenied(self.tool("Edit", {"file_path": "memory/note.md"}, cwd=self.proj))

    def test_omitted_argument_means_no_permission(self):
        """transcript_path を渡さない既存の呼び方では許可されない."""
        n = _fwd(self.note)
        self.assertDenied(self.rra.check_bash_command(f'rm "{n}"', self.repo, repo_root=self.repo))
        self.assertDenied(self.rra.check_powershell_command(f'Remove-Item "{self.note}"', self.repo, repo_root=self.repo))
        self.assertDenied(self.rra.check_file_tool("Edit", {"file_path": self.note}, self.repo, self.rra.Policy(self.repo, [])))


class TestRestrictMemoryDeniedPaths(MemorySandboxMixin, unittest.TestCase):
    """memory の配下でないパスは transcript_path があっても拒否される."""

    def test_other_project_memory(self):
        self.assertAllWriteOpsDenied(self.other_note)

    def test_other_project_transcript(self):
        self.assertAllWriteOpsDenied(self.other_session)

    def test_own_transcript(self):
        self.assertAllWriteOpsDenied(self.session)

    def test_settings_json(self):
        self.assertAllWriteOpsDenied(self.settings)

    def test_memory_folder_elsewhere(self):
        for path in self.elsewhere:
            self.assertAllWriteOpsDenied(path)

    def test_similar_folder_names(self):
        for path in self.similar:
            self.assertAllWriteOpsDenied(path)

    def test_dot_dot_escaping_memory(self):
        for path in (os.path.join(self.memory, "..", "session.jsonl"),
                     os.path.join(self.deep_dir, "..", "..", "session.jsonl"),
                     os.path.join(self.memory, "..", "..", "other", "memory", "note.md"),
                     os.path.join(self.memory, "..", "memory2", "note.md"),
                     os.path.join(self.memory, "..", "..", "..", "settings.json")):
            self.assertAllWriteOpsDenied(path)

    def test_dot_dot_escaping_relative(self):
        self.assertDenied(self.bash("rm ../session.jsonl", cwd=self.memory))
        self.assertDenied(self.bash("echo y > ../session.jsonl", cwd=self.memory))
        self.assertDenied(self.bash("rm ../../session.jsonl", cwd=self.deep_dir))
        self.assertDenied(self.ps("Remove-Item ..\\session.jsonl", cwd=self.memory))
        self.assertDenied(self.ps("Set-Content ..\\session.jsonl y", cwd=self.memory))
        self.assertDenied(self.ps("Remove-Item ..\\..\\session.jsonl", cwd=self.deep_dir))
        self.assertDenied(self.tool("Edit", {"file_path": "../session.jsonl"}, cwd=self.memory))
        self.assertDenied(self.tool("Write", {"file_path": "../session.jsonl"}, cwd=self.memory))

    def test_brace_expansion_escaping_memory(self):
        """Bash は memory/{a,../x} を memory/a と memory/../x に展開する（`..` で外に出る）."""
        m = _fwd(self.memory)
        for cmd in (f"rm -rf {m}/{{a,../../other/memory}}", f"rm -rf {m}/{{../session.jsonl,note.md}}",
                    f"rm -rf {m}/{{a,..}}"):
            with self.subTest(cmd):
                self.assertDenied(self.bash(cmd), cmd)

    def test_transcript_of_other_project_does_not_allow_this_memory(self):
        self.transcript_path = self.other_session
        self.assertAllWriteOpsDenied(self.note)

    def test_memory_is_directly_under_transcript_parent_only(self):
        """transcript_path の親の親の memory や，親フォルダの下の別の階層の memory は対象外."""
        self.transcript_path = os.path.join(self.proj, "nested", "session.jsonl")
        self.assertAllWriteOpsDenied(self.note)

    def test_home_shorthand(self):
        with self.fake_home():
            self.assertDenied(self.bash("rm -rf ~/.claude/projects/other/memory"))
            self.assertDenied(self.bash("echo y > ~/.claude/settings.json"))
            self.assertDenied(self.ps("Remove-Item $HOME/.claude/projects/other/memory/note.md"))
            self.assertDenied(self.tool("Edit", {"file_path": "~/.claude/projects/other/memory/note.md"}))

    def test_move_between_memory_and_outside(self):
        """移動の両端を見る（移動元が外の削除，移動先が外の既存ファイルの上書き）."""
        n, ex = _fwd(self.note), _fwd(self.existing)
        self.assertDenied(self.bash(f'mv "{ex}" "{n}"'))
        self.assertDenied(self.bash(f'mv "{n}" "{ex}"'))
        self.assertDenied(self.ps(f'Move-Item "{self.existing}" "{self.note}"'))
        self.assertDenied(self.ps(f'Move-Item "{self.note}" "{self.existing}"'))

    def test_inline_python(self):
        self.assertDenied(self.bash(f"python -c \"import os; os.remove('{_fwd(self.other_note)}')\""))
        self.assertDenied(self.bash(f"python -c \"open('{_fwd(self.session)}', 'w').write('y')\""))


class TestRestrictMemoryFolderItself(MemorySandboxMixin, unittest.TestCase):
    """memory フォルダ自体の削除・移動は拒否される（memory/・memory/. も）."""

    def forms(self):
        return (
            self.memory,
            self.memory + os.sep,
            os.path.join(self.memory, "."),
            os.path.join(self.memory, "sub", ".."),
            os.path.join(self.proj, ".", "memory"),
            os.path.join(self.proj, "nested", "..", "memory"),
        )

    def test_is_inside_memory_is_false_for_folder_itself(self):
        policy = self.rra.Policy(self.repo, [], self.session)
        for path in self.forms():
            with self.subTest(path):
                self.assertFalse(policy.is_inside_memory(self.rra.resolve_path(path, self.repo)))
                self.assertFalse(policy.is_inside_memory(path))

    def test_bash_delete_and_move(self):
        for path in self.forms():
            p = _fwd(path)
            for cmd in (f'rm -rf "{p}"', f'rm -r "{p}"', f'rmdir "{p}"', f'mv "{p}" "{_fwd(self.missing)}"',
                        f'mv "{p}" "{_fwd(os.path.join(self.proj, "renamed"))}"',
                        f"python -c \"import shutil; shutil.rmtree('{p}')\""):
                with self.subTest(cmd):
                    self.assertDenied(self.bash(cmd), cmd)

    def test_powershell_delete_and_move(self):
        for path in self.forms():
            for cmd in (f'Remove-Item "{path}"', f'Remove-Item "{path}" -Recurse -Force', f'rd "{path}"',
                        f'Move-Item "{path}" "{self.missing}"', f'Rename-Item "{path}" renamed'):
                with self.subTest(cmd):
                    self.assertDenied(self.ps(cmd), cmd)

    def test_file_tools(self):
        for path in self.forms():
            with self.subTest(path):
                self.assertDenied(self.tool("Edit", {"file_path": path}))
                self.assertDenied(self.tool("Write", {"file_path": path}))

    def test_overwrite_folder_path(self):
        for path in self.forms():
            with self.subTest(path):
                self.assertDenied(self.bash(f'echo y > "{_fwd(path)}"'))
                self.assertDenied(self.ps(f'Set-Content "{path}" y'))

    def test_relative_from_parent(self):
        for cmd in ("rm -rf memory", "rm -rf memory/", "rm -rf memory/.", "rm -rf ./memory", "mv memory renamed",
                    "rm -rf nested/../memory"):
            with self.subTest(cmd):
                self.assertDenied(self.bash(cmd, cwd=self.proj), cmd)
        for cmd in ("Remove-Item memory -Recurse", "Remove-Item memory\\ -Recurse", "Remove-Item memory\\. -Recurse",
                    "Remove-Item .\\memory -Recurse", "Move-Item memory renamed", "Rename-Item memory renamed"):
            with self.subTest(cmd):
                self.assertDenied(self.ps(cmd, cwd=self.proj), cmd)

    def test_relative_from_inside(self):
        for cmd in ("rm -rf .", "rm -rf ./", "rm -rf ../memory", "mv ../memory ../renamed"):
            with self.subTest(cmd):
                self.assertDenied(self.bash(cmd, cwd=self.memory), cmd)
        self.assertDenied(self.bash("rm -rf ..", cwd=self.deep_dir))
        for cmd in ("Remove-Item . -Recurse", "Remove-Item ..\\memory -Recurse"):
            with self.subTest(cmd):
                self.assertDenied(self.ps(cmd, cwd=self.memory), cmd)
        self.assertDenied(self.ps("Remove-Item .. -Recurse", cwd=self.deep_dir))

    def test_cd_then_delete(self):
        self.assertDenied(self.bash(f'cd "{_fwd(self.proj)}" && rm -rf memory'))
        self.assertDenied(self.bash(f'cd "{_fwd(self.memory)}" && rm -rf .'))
        self.assertDenied(self.bash(f'cd "{_fwd(self.memory)}" && cd .. && rm -rf memory'))
        self.assertDenied(self.ps(f'Set-Location "{self.proj}"; Remove-Item memory -Recurse'))
        self.assertDenied(self.ps(f'Set-Location "{self.memory}"; Remove-Item . -Recurse'))

    def test_parent_folder_delete(self):
        self.assertDenied(self.bash(f'rm -rf "{_fwd(self.proj)}"'))
        self.assertDenied(self.ps(f'Remove-Item "{self.proj}" -Recurse'))

    def test_glob_resolving_to_folder_itself(self):
        self.assertDenied(self.bash(f'rm -rf "{_fwd(self.memory)}"/*/..'))


class TestRestrictMemoryWildcards(MemorySandboxMixin, unittest.TestCase):
    """memory の配下だと確定できないワイルドカードは拒否される."""

    def test_delete_from_absolute(self):
        projects = _fwd(self.projects)
        for tail in ("*/memory", "*/memory/note.md", "*/memory/*", "pro*/memory/note.md", "pro?/memory/note.md",
                     "proj/mem*", "proj/mem*/note.md", "proj/memory*", "proj/memory?/note.md", "proj/*", "*"):
            with self.subTest(tail):
                self.assertDenied(self.bash(f"rm -rf {projects}/{tail}"), tail)
                self.assertDenied(self.ps(f"Remove-Item {projects}/{tail} -Recurse"), tail)

    def test_delete_from_relative(self):
        self.assertDenied(self.bash("rm -rf */memory", cwd=self.projects))
        self.assertDenied(self.bash("rm -rf ../*/memory", cwd=self.proj))
        self.assertDenied(self.bash("rm -rf ../../*/memory", cwd=self.memory))
        self.assertDenied(self.bash("rm -rf mem*", cwd=self.proj))
        self.assertDenied(self.ps("Remove-Item */memory -Recurse", cwd=self.projects))
        self.assertDenied(self.ps("Remove-Item mem* -Recurse", cwd=self.proj))

    def test_dot_dot_after_memory_then_wildcard(self):
        m = _fwd(self.memory)
        self.assertDenied(self.bash(f"rm -rf {m}/../../*/memory"))
        self.assertDenied(self.ps(f"Remove-Item {m}/../../*/memory -Recurse"))

    def test_edit_with_wildcard_path(self):
        path = os.path.join(self.projects, "*", "memory", "note.md")
        self.assertDenied(self.tool("Edit", {"file_path": path}))


class TestRestrictMemoryLinks(MemorySandboxMixin, unittest.TestCase):
    """シンボリックリンク（Windows で権限が無ければジャンクション）で外に出るパスは拒否される."""

    def replace_memory_with_link(self, target: str) -> None:
        shutil.rmtree(self.memory)
        self.link_dir(target, self.memory)

    def test_memory_links_outside(self):
        target = os.path.join(self.out, "dir")
        self.replace_memory_with_link(target)
        self.assertAllWriteOpsDenied(os.path.join(self.memory, "file.txt"))

    def test_memory_links_outside_missing_file(self):
        self.replace_memory_with_link(os.path.join(self.out, "dir"))
        path = os.path.join(self.memory, "no_such_file.txt")
        self.assertDenied(self.tool("Edit", {"file_path": path}))
        self.assertDenied(self.bash(f'rm "{_fwd(path)}"'))
        self.assertDenied(self.ps(f'Remove-Item "{path}"'))

    def test_memory_links_to_other_project_memory(self):
        self.replace_memory_with_link(os.path.join(self.other, "memory"))
        self.assertAllWriteOpsDenied(self.note)

    def test_memory_links_to_similar_name(self):
        self.replace_memory_with_link(os.path.join(self.proj, "memory2"))
        self.assertAllWriteOpsDenied(self.note)

    def test_memory_link_dangling(self):
        target = os.path.join(self.out, "will_be_removed")
        os.makedirs(target)
        self.replace_memory_with_link(target)
        os.rmdir(target)
        path = os.path.join(self.memory, "x.md")
        self.assertDenied(self.tool("Edit", {"file_path": path}))
        self.assertDenied(self.bash(f'rm "{_fwd(path)}"'))
        self.assertDenied(self.ps(f'Remove-Item "{path}"'))

    def test_memory_link_itself_is_denied(self):
        self.replace_memory_with_link(os.path.join(self.out, "dir"))
        self.assertDenied(self.bash(f'rm -rf "{_fwd(self.memory)}"'))
        self.assertDenied(self.ps(f'Remove-Item "{self.memory}" -Recurse'))

    def test_is_inside_memory_false_when_memory_is_link(self):
        self.replace_memory_with_link(os.path.join(self.out, "dir"))
        policy = self.rra.Policy(self.repo, [], self.session)
        self.assertFalse(policy.is_inside_memory(os.path.join(self.memory, "file.txt")))
        self.assertFalse(policy.is_inside_memory(os.path.join(self.out, "dir", "file.txt")))

    def test_link_inside_memory_to_outside(self):
        link = os.path.join(self.memory, "escape")
        self.link_dir(os.path.join(self.out, "dir"), link)
        self.assertAllWriteOpsDenied(os.path.join(link, "file.txt"))

    def test_link_inside_memory_to_transcript_folder(self):
        link = os.path.join(self.memory, "up")
        self.link_dir(self.proj, link)
        self.assertAllWriteOpsDenied(os.path.join(link, "session.jsonl"))

    def test_link_inside_memory_then_dot_dot(self):
        """memory/escape/../x は OS が escape の先の親（memory の外）として解決する（上書き系を含む全操作）."""
        link = os.path.join(self.memory, "escape")
        self.link_dir(os.path.join(self.out, "dir"), link)
        self.assertAllWriteOpsDenied(os.path.join(link, "..", "existing.txt"))

    def test_wildcard_matching_link_inside_memory(self):
        """memory/*/file.txt はシェルが展開すると memory/escape/file.txt（memory の外の実体）になる."""
        link = os.path.join(self.memory, "escape")
        self.link_dir(os.path.join(self.out, "dir"), link)
        path = _fwd(os.path.join(self.memory, "*", "file.txt"))
        self.assertDenied(self.bash(f"rm {path}"))
        self.assertDenied(self.ps(f"Remove-Item {path}"))

    def test_cwd_inside_link_then_relative(self):
        link = os.path.join(self.memory, "escape")
        self.link_dir(os.path.join(self.out, "dir"), link)
        self.assertDenied(self.bash("rm file.txt", cwd=link))
        self.assertDenied(self.ps("Remove-Item file.txt", cwd=link))
        self.assertDenied(self.tool("Edit", {"file_path": "file.txt"}, cwd=link))

    def test_file_symlink_inside_memory_to_outside(self):
        link = os.path.join(self.memory, "link.txt")
        try:
            os.symlink(self.existing, link)
        except OSError:
            if os.name != "nt":
                raise
            self.skipTest("Windows でシンボリックリンクの作成権限が無い（ファイルにはジャンクションを使えない）")
        self.assertDenied(self.tool("Edit", {"file_path": link}))
        self.assertDenied(self.tool("Write", {"file_path": link}))
        self.assertDenied(self.bash(f'echo y > "{_fwd(link)}"'))
        self.assertDenied(self.ps(f'Set-Content "{link}" y'))


class TestRestrictMemoryLocationChange(MemorySandboxMixin, unittest.TestCase):
    """cd 等の移動先を文字列から確定できないコマンドには memory の許可を与えない.

    cd -P や PowerShell の Set-Location はリンクの先へ移動するので，その後の `..` は memory の外を
    指しうる．判定上の cwd は memory の配下に見えるので，移動先の文字列で判断する必要がある.
    """

    def setUp(self) -> None:
        super().setUp()
        self.m = _fwd(self.memory)
        self.link_dir(os.path.join(self.out, "dir"), os.path.join(self.memory, "escape"))

    def test_bash_cd_physical_through_link_is_denied(self):
        m = self.m
        for cmd in (f'cd -P "{m}/escape/.." && rm x',
                    f'cd -P "{m}/escape" && cd .. && rm x',
                    f'cd -P "{m}/escape" && cd ../ && rm x',
                    f'(cd -P "{m}/escape/.." && rm x)'):
            with self.subTest(cmd):
                self.assertDenied(self.bash(cmd), cmd)

    def test_pushd_is_denied(self):
        m = self.m
        for cmd in (f'pushd "{m}/escape/.." && rm x', f'pushd "{m}/escape" && cd .. && rm x'):
            with self.subTest(cmd):
                self.assertDenied(self.bash(cmd), cmd)

    def test_set_location_is_denied(self):
        m = self.m
        for cmd in (f'Set-Location "{m}/escape/.."; Remove-Item x',
                    f'Set-Location "{m}/escape"; Set-Location ..; Remove-Item x',
                    f'Set-Location -Path "{m}/escape/.."; Remove-Item x',
                    f'Set-Location -LiteralPath "{m}/escape/.."; Remove-Item x',
                    f'sl "{m}/escape/.."; Remove-Item x',
                    f'Push-Location "{m}/escape/.."; Remove-Item x'):
            with self.subTest(cmd):
                self.assertDenied(self.ps(cmd), cmd)

    def test_inside_bash_c_is_denied(self):
        m = self.m
        for cmd in (f"bash -c 'cd -P \"{m}/escape/..\" && rm x'",
                    f"bash -c 'cd -P \"{m}/escape\" && cd .. && rm x'"):
            with self.subTest(cmd):
                self.assertDenied(self.bash(cmd), cmd)

    def test_wildcard_destination_is_denied(self):
        """移動先のワイルドカードは最後の要素でも確定できない（リンクに一致しうる）."""
        m = self.m
        for cmd in (f'cd "{m}"/* && rm -rf .', f'cd "{m}"/* && rm x', f'cd "{m}"/esc* && rm x'):
            with self.subTest(cmd):
                self.assertDenied(self.bash(cmd), cmd)
        for cmd in (f'Set-Location "{m}/*"; Remove-Item x', f'Set-Location "{m}/esc*"; Remove-Item x'):
            with self.subTest(cmd):
                self.assertDenied(self.ps(cmd), cmd)

    def test_brace_destination_is_denied(self):
        self.assertDenied(self.bash(f'cd "{self.m}"/{{sub,escape}} && rm x'))

    def test_plain_cd_is_allowed(self):
        m = self.m
        for cmd in (f'cd "{m}" && rm note.md', f'cd "{m}/" && rm note.md', f'cd "{m}/sub" && rm deep.md',
                    f'cd "{m}" && rm -rf sub', f'pushd "{m}/sub" && rm deep.md',
                    f"bash -c 'cd \"{m}\" && rm note.md'"):
            with self.subTest(cmd):
                self.assertAllowed(self.bash(cmd), cmd)
        for cmd in (f'Set-Location "{m}"; Remove-Item note.md', f'Set-Location "{m}/sub"; Remove-Item deep.md',
                    f'Set-Location -Path "{m}"; Remove-Item note.md'):
            with self.subTest(cmd):
                self.assertAllowed(self.ps(cmd), cmd)


class TestRestrictMemoryTailWildcard(MemorySandboxMixin, unittest.TestCase):
    """最後の要素だけのワイルドカードは許可し，途中の階層のワイルドカードは許可しない."""

    def test_tail_wildcard_is_allowed(self):
        m = _fwd(self.memory)
        for tail in ("*", "*.md", "sub/*", "sub/*.md"):
            with self.subTest(tail):
                self.assertAllowed(self.bash(f'rm "{m}"/{tail}'), tail)
                self.assertAllowed(self.ps(f'Remove-Item "{m}/{tail}"'), tail)

    def test_trailing_slash_makes_wildcard_intermediate(self):
        """`*/` はディレクトリ（リンクの先を含む）に一致するので，途中の階層と同じに扱う."""
        m = _fwd(self.memory)
        self.assertDenied(self.bash(f'rm -rf "{m}"/*/'))
        self.assertDenied(self.ps(f'Remove-Item "{m}/*/" -Recurse'))

    def test_intermediate_wildcard_is_denied(self):
        m = _fwd(self.memory)
        for tail in ("*/deep.md", "*/*", "s*/deep.md", "su?/deep.md", "[s]ub/deep.md"):
            with self.subTest(tail):
                self.assertDenied(self.bash(f'rm "{m}"/{tail}'), tail)
                self.assertDenied(self.ps(f'Remove-Item "{m}/{tail}"'), tail)


class TestRestrictMemoryCopyDest(MemorySandboxMixin, unittest.TestCase):
    """コピー元の名前に由来する波括弧（copy_dest_entries が解決済みのパスにだけ付ける）の扱い."""

    def setUp(self) -> None:
        super().setUp()
        self.src = os.path.join(self.out, "x")
        os.makedirs(self.src)
        for name in ("a", "b"):
            with open(os.path.join(self.src, name), "w", encoding="utf-8") as fh:
                fh.write("x")

    def m_dir(self) -> str:
        return _fwd(self.memory) + "/"

    def verdicts(self, command: str) -> tuple:
        """transcript_path があるとき・無いときの判定（拒否なら True）を返す."""
        with_transcript = self.bash(command) is not None
        self.transcript_path = None
        without_transcript = self.bash(command) is not None
        self.transcript_path = self.session
        return with_transcript, without_transcript

    def test_entry_keeps_brace_and_gets_no_permission(self):
        """コマンド中の書き込み先の文字列に波括弧が無くても，解決済みのパスで許可を与えない."""
        raw = self.m_dir()
        entries = self.rra.copy_dest_entries("cp", raw, [f"{_fwd(self.src)}/{{a,b}}"], self.repo, "bash")
        self.assertEqual(len(entries), 1)
        _, entry_raw, resolved, _ = entries[0]
        self.assertEqual(entry_raw, raw)
        self.assertEqual(os.path.basename(resolved), "{a,b}")
        self.assertFalse(self.rra.Policy(self.repo, [], self.session).is_write_allowed(resolved, entry_raw))

    def test_brace_source_when_literal_name_exists_is_denied(self):
        with open(os.path.join(self.memory, "{a,b}"), "w", encoding="utf-8") as fh:
            fh.write("x")
        command = f'cp "{_fwd(self.src)}"/{{a,b}} "{self.m_dir()}"'
        self.assertEqual(self.verdicts(command), (True, True))

    def test_brace_source_does_not_depend_on_transcript(self):
        """memory の許可に頼らないので，transcript_path の有無で判定が変わらない."""
        for dest in (self.m_dir(), _fwd(self.memory)):
            command = f'cp "{_fwd(self.src)}"/{{a,b}} "{dest}"'
            with self.subTest(command):
                with_transcript, without_transcript = self.verdicts(command)
                self.assertEqual(with_transcript, without_transcript, command)

    def test_plain_source_uses_memory_permission(self):
        """比較用: 波括弧が無ければ memory の既存ファイルへのコピーは transcript_path で許可される."""
        with open(os.path.join(self.memory, "a"), "w", encoding="utf-8") as fh:
            fh.write("x")
        command = f'cp "{_fwd(self.src)}/a" "{self.m_dir()}"'
        self.assertEqual(self.verdicts(command), (False, True))


class TestRestrictMemoryKeepsOtherZones(MemorySandboxMixin, unittest.TestCase):
    """リポジトリ内・一時ディレクトリの判定は，曖昧なパスでも transcript_path の有無で変わらない."""

    def setUp(self) -> None:
        super().setUp()
        self.r = _fwd(self.repo)
        self.t = _fwd(tempfile.gettempdir())

    def verdicts(self, check, *args, **kwargs) -> tuple:
        """transcript_path があるとき・無いときの判定（拒否なら True）を返す."""
        with_transcript = check(*args, **kwargs) is not None
        self.transcript_path = None
        without_transcript = check(*args, **kwargs) is not None
        self.transcript_path = self.session
        return with_transcript, without_transcript

    def bash_commands(self, base: str) -> list:
        return [f'rm "{base}"/{{file.txt,sub}}', f'rm "{base}/sub/../file.txt"', f'rm "{base}"/*/x',
                f'rm -rf "{base}"/*/', f'cd "{base}/sub/.." && rm file.txt', f'cd "{base}"/* && rm x',
                f'cd "{base}"/{{sub,other}} && rm x']

    def ps_commands(self, base: str) -> list:
        return [f'Remove-Item "{base}/sub/../file.txt"', f'Remove-Item "{base}/*/x"',
                f'Set-Location "{base}/sub/.."; Remove-Item file.txt', f'Set-Location "{base}/*"; Remove-Item x']

    def test_repo_is_allowed(self):
        for cmd in self.bash_commands(self.r):
            with self.subTest(cmd):
                self.assertEqual(self.verdicts(self.bash, cmd), (False, False), cmd)
        for cmd in self.ps_commands(self.r):
            with self.subTest(cmd):
                self.assertEqual(self.verdicts(self.ps, cmd), (False, False), cmd)

    def test_temp_is_allowed(self):
        for cmd in self.bash_commands(self.t):
            with self.subTest(cmd):
                self.assertEqual(self.verdicts(self.bash, cmd), (False, False), cmd)
        for cmd in self.ps_commands(self.t):
            with self.subTest(cmd):
                self.assertEqual(self.verdicts(self.ps, cmd), (False, False), cmd)

    def test_file_tools(self):
        for path in (os.path.join(self.repo, "sub", "..", "file.txt"), os.path.join(self.repo, "{a,b}", "x"),
                     os.path.join(self.repo, "*", "x"), os.path.join(tempfile.gettempdir(), "a", "..", "x"),
                     os.path.join(tempfile.gettempdir(), "{a,b}", "x"), os.path.join(tempfile.gettempdir(), "*", "x")):
            for name in ("Edit", "Write"):
                with self.subTest(name=name, path=path):
                    self.assertEqual(self.verdicts(self.tool, name, {"file_path": path}), (False, False))

    def test_relative_cd_up_inside_repo(self):
        """移動先が曖昧でも外れるのは memory の許可だけで，リポジトリ内の許可は残る."""
        self.assertEqual(self.verdicts(self.bash, "cd .. && rm file.txt", cwd=self.sub), (False, False))
        self.assertEqual(self.verdicts(self.ps, "Set-Location ..; Remove-Item file.txt", cwd=self.sub), (False, False))

    def test_outside_is_denied_either_way(self):
        out = _fwd(self.out)
        for cmd in (f'rm "{self.r}/../outside/existing.txt"', f'cd "{self.r}/.." && rm outside/existing.txt',
                    f'rm "{out}"/{{existing.txt,dir}}', f'rm "{out}"/*/file.txt'):
            with self.subTest(cmd):
                self.assertEqual(self.verdicts(self.bash, cmd), (True, True), cmd)

    def test_brace_leaving_repo_does_not_depend_on_transcript(self):
        """リポジトリから波括弧で出るパスも，transcript_path の有無で判定が変わらない."""
        command = f'rm "{self.r}"/{{file.txt,../outside/existing.txt}}'
        with_transcript, without_transcript = self.verdicts(self.bash, command)
        self.assertEqual(with_transcript, without_transcript)


class TestRestrictMemoryDenyRead(MemorySandboxMixin, unittest.TestCase):
    """deny_read は memory の許可より優先する."""

    def assertDenyRead(self, reason, label=""):
        self.assertIsNotNone(reason, label)
        self.assertNotIn("! <コマンド>", reason, label)

    def test_entry_inside_memory(self):
        secret = os.path.join(self.memory, "sub")
        self.deny = [secret]
        for label, reason in self.write_ops(self.deep).items():
            with self.subTest(label):
                self.assertDenyRead(reason, label)
        self.assertDenyRead(self.tool("Read", {"file_path": self.deep}))
        self.assertDenyRead(self.bash(f'cat "{_fwd(self.deep)}"'))
        self.assertDenyRead(self.ps(f'Get-Content "{self.deep}"'))
        self.assertAllWriteOpsAllowed(self.note)

    def test_memory_folder_is_denied_entirely(self):
        self.deny = [self.memory]
        for label, reason in self.write_ops(self.note).items():
            with self.subTest(label):
                self.assertDenyRead(reason, label)

    def test_entry_above_memory(self):
        self.deny = [self.projects]
        self.assertDenyRead(self.tool("Edit", {"file_path": self.note}))
        self.assertDenyRead(self.bash(f'rm "{_fwd(self.note)}"'))
        self.assertDenyRead(self.ps(f'Remove-Item "{self.note}"'))


class TestRestrictMemoryViaMain(MemorySandboxMixin, unittest.TestCase):
    """main に標準入力で JSON を渡す経路で transcript_path が実際に使われること."""

    def env(self) -> dict:
        return dict(os.environ, CLAUDE_PROJECT_DIR=self.repo)

    def hook_inputs(self, path: str) -> dict:
        f = _fwd(path)
        return {
            "Edit": ("Edit", {"file_path": path, "old_string": "x", "new_string": "y"}),
            "Write": ("Write", {"file_path": path, "content": "y"}),
            "Bash rm": ("Bash", {"command": f'rm "{f}"'}),
            "Bash redirect": ("Bash", {"command": f'echo y > "{f}"'}),
            "PowerShell Remove-Item": ("PowerShell", {"command": f'Remove-Item "{path}"'}),
            "PowerShell Set-Content": ("PowerShell", {"command": f'Set-Content "{path}" y'}),
        }

    def run_tool(self, tool_name: str, tool_input: dict, **extra):
        data = dict({"tool_name": tool_name, "tool_input": tool_input, "cwd": self.repo}, **extra)
        return self.run_main(data, self.env())

    def assertHookAllows(self, result, label=""):
        self.assertEqual(result.returncode, 0, (label, result.stderr))
        self.assertIsNone(self.decision(result), (label, result.stdout))

    def assertHookDenies(self, result, label=""):
        self.assertEqual(result.returncode, 0, (label, result.stderr))
        self.assertEqual(self.decision(result), "deny", (label, result.stdout))

    def test_allowed_with_transcript_path(self):
        for label, (name, tool_input) in self.hook_inputs(self.note).items():
            with self.subTest(label):
                self.assertHookAllows(self.run_tool(name, tool_input, transcript_path=self.session), label)

    def test_denied_without_transcript_path(self):
        for label, (name, tool_input) in self.hook_inputs(self.note).items():
            with self.subTest(label):
                self.assertHookDenies(self.run_tool(name, tool_input), label)

    def test_denied_with_unusable_transcript_path(self):
        name, tool_input = self.hook_inputs(self.note)["Edit"]
        for value in (None, "", 123, True, [self.session], {"a": 1}, "session.jsonl", self.session + "\0"):
            with self.subTest(repr(value)):
                self.assertHookDenies(self.run_tool(name, tool_input, transcript_path=value))

    def test_denied_for_other_project(self):
        for label, (name, tool_input) in self.hook_inputs(self.other_note).items():
            with self.subTest(label):
                self.assertHookDenies(self.run_tool(name, tool_input, transcript_path=self.session), label)

    def test_denied_for_memory_folder_itself(self):
        cases = [("Bash", {"command": f'rm -rf "{_fwd(self.memory)}"'}),
                 ("Bash", {"command": f'rm -rf "{_fwd(self.memory)}/"'}),
                 ("Bash", {"command": f'rm -rf "{_fwd(self.memory)}/."'}),
                 ("PowerShell", {"command": f'Remove-Item "{self.memory}" -Recurse'})]
        for name, tool_input in cases:
            with self.subTest(tool_input["command"]):
                self.assertHookDenies(self.run_tool(name, tool_input, transcript_path=self.session))

    def test_deny_read_config_wins(self):
        with open(os.path.join(self.repo, ".claude", "repo-access.json"), "w", encoding="utf-8") as fh:
            json.dump({"deny_read": [self.memory]}, fh)
        name, tool_input = self.hook_inputs(self.note)["Edit"]
        self.assertHookDenies(self.run_tool(name, tool_input, transcript_path=self.session))

    def test_via_wrapper_like_settings_json(self):
        """settings.json と同じコマンド（run_python.sh 経由）でも transcript_path が届く."""
        data = {"tool_name": "Edit", "tool_input": {"file_path": self.note, "old_string": "x", "new_string": "y"},
                "cwd": self.repo}
        command = [BASH, ".claude/hooks/run_python.sh", "--fail-closed", ".claude/hooks/restrict_repo_access.py"]

        def run(payload):
            return subprocess.run(command, input=json.dumps(payload).encode("utf-8"), capture_output=True,
                                  env=self.env(), cwd=REPO_ROOT, timeout=TIMEOUT)

        self.assertHookAllows(run(dict(data, transcript_path=self.session)))
        self.assertHookDenies(run(data))


@unittest.skipUnless(os.name == "nt", "ドライブレター・Git Bash 形式・大文字小文字の同一視は Windows 固有のため")
class TestRestrictMemoryWindows(MemorySandboxMixin, unittest.TestCase):
    def test_git_bash_form_paths(self):
        self.assertAllowed(self.bash(f'rm "{_gitbash(self.note)}"'))
        self.assertAllowed(self.bash(f'echo y > "{_gitbash(self.note)}"'))
        self.assertDenied(self.bash(f'rm -rf "{_gitbash(self.memory)}"'))
        self.assertDenied(self.bash(f'rm "{_gitbash(self.other_note)}"'))

    def test_case_insensitive(self):
        upper = os.path.join(self.proj, "MEMORY", "NOTE.md")
        self.assertAllowed(self.tool("Edit", {"file_path": upper}))
        self.assertAllowed(self.ps(f'Remove-Item "{upper}"'))
        self.assertDenied(self.ps(f'Remove-Item "{os.path.join(self.proj, "MEMORY")}" -Recurse'))
        self.transcript_path = self.session.upper()
        self.assertAllowed(self.tool("Edit", {"file_path": self.note}))

    def test_drive_relative_transcript_is_not_absolute(self):
        self.transcript_path = self.session[:2] + os.path.relpath(self.session, self.session[:3])
        self.assertDenied(self.tool("Edit", {"file_path": self.note}))

    def test_junction_memory_explicitly(self):
        """シンボリックリンクの権限の有無によらず，ジャンクションでも許可しない."""
        target = os.path.join(self.out, "dir")
        shutil.rmtree(self.memory)
        result = subprocess.run(["cmd", "/c", "mklink", "/J", self.memory, target], capture_output=True)
        if result.returncode != 0:
            self.skipTest("ジャンクションを作成できない環境")
        self.assertAllWriteOpsDenied(os.path.join(self.memory, "file.txt"))


# ---------------------------------------------------------------------------
# Issue #59: 波括弧・ワイルドカード・cd -・リンクの後の `..` の検出漏れ
# ---------------------------------------------------------------------------
class TestExpandBraces(unittest.TestCase):
    """expand_braces が実際の Bash の波括弧展開と同じ結果を返すこと（期待値は bash 5 で確認したもの）."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rra = _load_restrict()

    def test_same_as_bash(self):
        cases = {
            "plain": ["plain"],
            "a{b,c}d": ["abd", "acd"],
            "{a,{b,c}}": ["a", "b", "c"],
            "{{a,b}}": ["{a}", "{b}"],
            "a{b,c}d{e,f}": ["abde", "abdf", "acde", "acdf"],
            "x/{a,../y/z}": ["x/a", "x/../y/z"],
            "{1..3}": ["1", "2", "3"],
            "{3..1}": ["3", "2", "1"],
            "{01..03}": ["01", "02", "03"],
            "{a..c}": ["a", "b", "c"],
            "{c..a}": ["c", "b", "a"],
            "{1..5..2}": ["1", "3", "5"],
            "{-1..1}": ["-1", "0", "1"],
            "{1..2}{a,b}": ["1a", "1b", "2a", "2b"],
            "{a,b}}": ["a}", "b}"],
            "{{a,b}": ["{a", "{b"],
            "x{a,b}y}z": ["xay}z", "xby}z"],
            "{a,b{c,d}}e": ["ae", "bce", "bde"],
        }
        for word, expected in cases.items():
            with self.subTest(word):
                self.assertEqual(self.rra.expand_braces(word), expected)

    def test_literal_when_bash_does_not_expand(self):
        for word in ("{a}", "{}", "{a,b", "a,b}", "{1..a}", "{a..1}", "${VAR}", "${a,b}", "{../outside/x}"):
            with self.subTest(word):
                self.assertEqual(self.rra.expand_braces(word), [word])

    def test_variable_followed_by_brace(self):
        self.assertEqual(self.rra.expand_braces("${x}{a,b}"), ["${x}a", "${x}b"])

    def test_expansion_limit_boundary(self):
        limit = self.rra.MAX_BRACE_EXPANSIONS
        self.assertEqual(limit, 256)
        self.assertEqual(len(self.rra.expand_braces("{1..256}")), 256)
        self.assertIsNone(self.rra.expand_braces("{1..257}"))
        self.assertEqual(len(self.rra.expand_braces("{a,b}{1..128}")), 256)
        self.assertIsNone(self.rra.expand_braces("{a,b}{1..129}"))
        self.assertEqual(len(self.rra.expand_braces("{%s}" % ",".join(str(n) for n in range(256)))), 256)
        self.assertIsNone(self.rra.expand_braces("{%s}" % ",".join(str(n) for n in range(257))))

    def test_huge_or_deep_words_end_quickly(self):
        """巨大な順序式・多重の波括弧でフックが止まらない（タイムアウトすると検査が効かない）."""
        for word in ("{1..1000000000}", "{a,b}" * 40, "{,}" * 40, "{a," * 1000 + "}" * 1000, "x" * 5000 + "{a,b}"):
            with self.subTest(word[:20]):
                self.assertIsNone(self.rra.expand_braces(word))


class Issue59SandboxMixin(MemorySandboxMixin):
    """リポジトリ内に src/ 等を足す（誤拒否のテスト用）."""

    def setUp(self) -> None:
        super().setUp()
        self.o = _fwd(self.out)
        self.r = _fwd(self.repo)
        self.m = _fwd(self.memory)
        self.p = _fwd(self.projects)

    def touch(self, *parts: str) -> str:
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x")
        return path

    def make_repo_escape(self) -> str:
        """repo/escape → outside/dir のリンクを作る."""
        link = os.path.join(self.repo, "escape")
        self.link_dir(os.path.join(self.out, "dir"), link)
        return link

    def make_memory_escape(self) -> str:
        link = os.path.join(self.memory, "escape")
        self.link_dir(os.path.join(self.out, "dir"), link)
        return link

    def assertBashDenied(self, commands, cwd: str | None = None) -> None:
        for cmd in commands:
            with self.subTest(cmd):
                self.assertDenied(self.bash(cmd, cwd=cwd), cmd)

    def assertBashAllowed(self, commands, cwd: str | None = None) -> None:
        for cmd in commands:
            with self.subTest(cmd):
                self.assertAllowed(self.bash(cmd, cwd=cwd), cmd)

    def assertPsDenied(self, commands, cwd: str | None = None) -> None:
        for cmd in commands:
            with self.subTest(cmd):
                self.assertDenied(self.ps(cmd, cwd=cwd), cmd)

    def assertPsAllowed(self, commands, cwd: str | None = None) -> None:
        for cmd in commands:
            with self.subTest(cmd):
                self.assertAllowed(self.ps(cmd, cwd=cwd), cmd)


class TestRestrictBraceDelete(Issue59SandboxMixin, unittest.TestCase):
    """波括弧で外に出る削除は拒否される."""

    def test_brace_escape_from_repo(self):
        r = self.r
        self.assertBashDenied([
            f"rm {r}/{{a,../outside/existing.txt}}",
            f"rm -f {r}/{{a,../outside/existing.txt}}",
            f'rm "{r}"/{{a,../outside/existing.txt}}',
            f"rm {r}/{{a,../outside}}/existing.txt",
            f"rm {r}/{{a,..}}/outside/existing.txt",
            f"rm -rf {r}/{{a,../outside/dir}}",
            f"rm {r}/{{../outside,x}}/*",
            f"rmdir {r}/{{a,../outside/dir}}",
            f"mv {r}/{{a,../outside/existing.txt}} {r}/sub/",
        ])

    def test_relative_brace_escape(self):
        self.assertBashDenied(["rm {a,../outside/existing.txt}", "rm -rf sub/{a,../../outside/dir}"])

    def test_brace_escape_via_prefix_and_nested_shell(self):
        r = self.r
        self.assertBashDenied([
            f"sudo rm {r}/{{a,../outside/existing.txt}}",
            f"env X=1 rm {r}/{{a,../outside/existing.txt}}",
            f"bash -c 'rm {r}/{{a,../outside/existing.txt}}'",
            f"true && rm {r}/{{a,../outside/existing.txt}}",
        ])

    def test_brace_directly_outside(self):
        self.assertBashDenied([f"rm {self.o}/{{existing.txt,dir/file.txt}}", f"rm {self.o}/{{dir,other}}/*"])

    def test_single_alternative_is_not_expanded(self):
        """`{x}` はコンマが無いので Bash は展開しない（文字どおりの，リポジトリ内の名前）."""
        self.assertBashAllowed([f"rm {self.r}/{{../outside/existing.txt}}"])


class TestRestrictBraceWithinRepoAllowed(Issue59SandboxMixin, unittest.TestCase):
    """リポジトリ内で閉じる波括弧・ワイルドカードは拒否しない（実体の有無によらない）."""

    COMMANDS = ("rm src/{a,b}.txt", "rm src/*/x.txt", "cp a.txt {b,c}/", "mv src/*.txt dst/", "rm src/{1..3}.txt",
                "rm -rf src/{a,b}", "sed -i s/x/y/ src/{a,b}.txt", "echo y | tee src/{a,b}.txt", "echo y > src/a.txt")

    def test_without_files(self):
        self.assertBashAllowed(self.COMMANDS)

    def test_with_files(self):
        for name in ("a.txt", "b.txt", "1.txt", "2.txt", "3.txt", os.path.join("d1", "x.txt"), os.path.join("d2", "x.txt")):
            self.touch(self.repo, "src", name)
        self.touch(self.repo, "a.txt")
        for d in ("b", "c", "dst"):
            os.makedirs(os.path.join(self.repo, d))
        self.assertBashAllowed(self.COMMANDS)

    def test_absolute_paths(self):
        self.touch(self.repo, "src", "d1", "x.txt")
        self.assertBashAllowed([f"rm {self.r}/src/{{a,b}}.txt", f"rm {self.r}/src/*/x.txt", f"rm {self.r}/src/{{1..3}}.txt"])

    def test_dot_dot_staying_inside_repo(self):
        self.assertBashAllowed([f"rm {self.r}/sub/{{a,../file.txt}}", "rm sub/{a,../file.txt}"])


class TestRestrictBraceNotPathArgument(Issue59SandboxMixin, unittest.TestCase):
    """パス引数でない波括弧で誤拒否しない."""

    def test_echo(self):
        self.assertBashAllowed(["echo '{a,b}'", "echo {a,b}", 'echo "{a,../../../outside/existing.txt}"'])

    def test_json_redirected_into_repo(self):
        self.assertBashAllowed([
            """echo '{"a": 1, "b": 2}' > out.json""",
            """echo '{"a":{"b":[1,2]},"c":"../../x"}' > out.json""",
            """printf '{"a":1,"b":2}' | tee out.json""",
        ])

    def test_variable_reference(self):
        self.assertBashAllowed(["echo ${HOME}", "echo ${a,b}"])


class TestRestrictBraceLimit(Issue59SandboxMixin, unittest.TestCase):
    """波括弧の上限超え（1 語 256 展開超）が削除・上書きの対象なら拒否する."""

    def test_over_limit_targets_are_denied(self):
        for base in (self.r + "/f", "f", self.o + "/f"):
            with self.subTest(base):
                self.assertBashDenied([
                    f"rm {base}{{1..257}}",
                    f"rm {base}{{1..1000000000}}",
                    f"echo y > {base}{{1..257}}",
                    f"echo y | tee {base}{{1..257}}",
                    f"sed -i s/x/y/ {base}{{1..257}}",
                    f"cp file.txt {base}{{1..257}}",
                    f"mv {base}{{1..257}} sub/",
                ])

    def test_over_limit_copy_source_is_denied(self):
        self.assertBashDenied([f"cp x{{1..257}} {self.o}/dir/", f"cp {self.r}/x{{1..257}} {self.o}/dir/"])

    def test_over_limit_in_other_form_is_denied(self):
        self.assertBashDenied(["dd if=/dev/zero of=f{1..257}", "rm f{a,b}{1..129}"])

    def test_at_limit_is_judged_normally(self):
        self.assertBashAllowed(["rm f{1..256}", "echo y | tee f{1..256}", "rm f{a,b}{1..128}"])

    def test_over_limit_not_a_target_is_allowed(self):
        self.assertBashAllowed([
            "echo {1..257}",
            "echo {1..257} > out.txt",
            "seq 1 3 | grep x{1..257}",
            "ls f{1..257}",
            "echo " + "x" * 5000 + "{a,b} > out.txt",
        ])

    def test_powershell_does_not_expand(self):
        self.assertPsAllowed([f"Remove-Item {self.r}/f{{1..257}}", "Set-Content f{1..257} y"])

    def test_via_main_is_denied(self):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.repo)
        data = {"tool_name": "Bash", "tool_input": {"command": "rm f{1..257}"}, "cwd": self.repo}
        self.assertEqual(self.decision(self.run_main(data, env)), "deny")
        data["tool_input"] = {"command": "rm f{1..256}"}
        self.assertIsNone(self.decision(self.run_main(data, env)))


class TestRestrictWildcardOverwrite(Issue59SandboxMixin, unittest.TestCase):
    """途中の階層のワイルドカードが既存ファイルに一致する上書きは拒否する."""

    def forms_bash(self, path: str) -> list:
        return [f"echo y > {path}", f"echo y | tee {path}", f"sed -i s/x/y/ {path}"]

    def forms_ps(self, path: str) -> list:
        return [f"Set-Content {path} y", f"echo y > {path}", f"echo y | Out-File {path}"]

    def test_projects_wildcard(self):
        path = f"{self.p}/*/memory/note.md"
        self.assertBashDenied(self.forms_bash(path))
        self.assertPsDenied(self.forms_ps(path))

    def test_projects_wildcard_without_transcript(self):
        self.transcript_path = None
        path = f"{self.p}/*/memory/note.md"
        self.assertBashDenied(self.forms_bash(path))
        self.assertPsDenied(self.forms_ps(path))

    def test_outside_wildcards(self):
        for tail in ("*/file.txt", "d*/file.txt", "di?/file.txt", "[d]ir/file.txt", "*.txt", "exist*"):
            path = f"{self.o}/{tail}"
            self.assertBashDenied(self.forms_bash(path))
            self.assertPsDenied(self.forms_ps(path))

    def test_relative_wildcard(self):
        self.assertBashDenied(["echo y > ../outside/*/file.txt"])
        self.assertBashDenied(["echo y > */file.txt"], cwd=self.out)
        self.assertPsDenied(["Set-Content */file.txt y"], cwd=self.out)

    def test_hidden_name_is_matched_on_the_safe_side(self):
        self.touch(self.out, ".hidden", "only.txt")
        self.assertBashDenied([f"echo y > {self.o}/*/only.txt"])
        self.assertPsDenied([f"Set-Content {self.o}/*/only.txt y"])


class TestRestrictOutsideCreateAllowed(Issue59SandboxMixin, unittest.TestCase):
    """リポジトリ外への新規作成は拒否しない（一致の無いワイルドカードを含む）."""

    def test_new_file(self):
        self.assertBashAllowed([f"echo y > {self.o}/new.txt", f"echo y | tee {self.o}/new.txt"])
        self.assertPsAllowed([f"Set-Content {self.o}/new.txt y", f"echo y > {self.o}/new.txt"])

    def test_wildcard_without_match(self):
        path = f"{self.o}/*/none/new.txt"
        self.assertBashAllowed([f"echo y > {path}", f"echo y | tee {path}"])
        self.assertPsAllowed([f"Set-Content {path} y", f"echo y > {path}"])
        self.assertBashAllowed([f"echo y > {self.o}/none*.txt"])

    def test_brace_to_new_files(self):
        self.assertBashAllowed([f"echo y | tee {self.o}/{{new1,new2}}.txt"])


class TestRestrictBraceOverwriteFromMemory(Issue59SandboxMixin, unittest.TestCase):
    """波括弧でメモのフォルダの外に出る上書きは拒否する."""

    def test_brace_escape_to_transcript(self):
        tail = "{note.md,../session.jsonl}"
        self.touch(self.out, "x", "a")
        self.assertBashDenied([
            f"sed -i s/x/y/ {self.m}/{tail}",
            f"echo y | tee {self.m}/{tail}",
            f"cp {self.o}/x/a {self.m}/{{a,../session.jsonl}}",
            f"rm {self.m}/{tail}",
            f"echo y > {self.m}/{tail}",
        ])

    def test_brace_with_transcript_outside_repo(self):
        self.transcript_path = None
        self.assertBashDenied([f"sed -i s/x/y/ {self.m}/{{note.md,../session.jsonl}}"])


class TestRestrictMemoryBraceStaysDenied(Issue59SandboxMixin, unittest.TestCase):
    """メモのフォルダの中で閉じていても，波括弧・`..` は従来どおり許可しない（緩めていない）."""

    def setUp(self) -> None:
        super().setUp()
        self.touch(self.deep_dir, "x")

    def test_brace_inside_memory(self):
        m = self.m
        self.assertBashDenied([
            f"rm {m}/{{note.md,sub/x}}",
            f"echo y > {m}/{{note.md,sub/x}}",
            f"sed -i s/x/y/ {m}/{{note.md,sub/x}}",
        ])

    def test_dot_dot_inside_memory(self):
        self.assertBashDenied([f"rm {self.m}/sub/../note.md"])
        self.assertPsDenied([f"Remove-Item {self.m}/sub/../note.md"])


class TestRestrictMemoryTailStaysAllowed(Issue59SandboxMixin, unittest.TestCase):
    """既存のメモのフォルダの許可が，リンクがある状態でも保たれること."""

    def setUp(self) -> None:
        super().setUp()
        self.make_memory_escape()

    def test_tail_wildcard_with_link_present(self):
        m = self.m
        self.assertBashAllowed([f"rm {m}/*", f"rm {m}/*.md", f"rm {m}/sub/*", f"rm -rf {m}/*"])
        self.assertPsAllowed([f"Remove-Item {m}/*", f"Remove-Item {m}/*.md", f"Remove-Item {m}/sub/*"])

    def test_plain_overwrite_with_link_present(self):
        self.assertBashAllowed([f"echo y > {self.m}/note.md", f"echo y | tee {self.m}/note.md"])
        self.assertPsAllowed([f"Set-Content {self.m}/note.md y"])

    def test_content_destroying_commands_follow_the_link(self):
        """truncate・shred・Clear-Content はリンク先の中身を壊すので，一致したリンクのリンク先も判定する."""
        m = self.m
        self.assertBashDenied([f"truncate -s 0 {m}/*", f"shred {m}/*", f"truncate -s 0 {m}/esc*"])
        self.assertPsDenied([f"Clear-Content {m}/*", f"Clear-Content {m}/esc*"])


class TestRestrictCopySourceBrace(Issue59SandboxMixin, unittest.TestCase):
    """コピー元の波括弧・ワイルドカードは，展開後の名前でコピー先の既存ファイルを判定する."""

    def setUp(self) -> None:
        super().setUp()
        for root in (self.repo, self.out):
            self.touch(root, "src", "x", "a")
            self.touch(root, "src", "x", "b")
        self.dest = os.path.join(self.out, "dest")
        os.makedirs(self.dest)
        self.d = _fwd(self.dest)

    def test_existing_in_dest_is_denied(self):
        self.touch(self.dest, "a")
        for src in (f"{self.r}/src", f"{self.o}/src", "src"):
            for dest in (f"{self.d}/", self.d):
                self.assertBashDenied([f"cp {src}/x/{{a,b}} {dest}", f"cp {src}/x/* {dest}", f"mv {src}/x/{{a,b}} {dest}"])
        self.assertPsDenied([f"Copy-Item {self.r}/src/x/* {self.d}/"])

    def test_second_alternative_existing_is_denied(self):
        self.touch(self.dest, "b")
        self.assertBashDenied([f"cp {self.r}/src/x/{{a,b}} {self.d}/"])

    def test_nothing_existing_in_dest_is_allowed(self):
        self.touch(self.dest, "c")
        self.assertBashAllowed([f"cp {self.r}/src/x/{{a,b}} {self.d}/", f"cp {self.r}/src/x/* {self.d}/"])
        self.assertPsAllowed([f"Copy-Item {self.r}/src/x/* {self.d}/"])


class TestRestrictLinkThenDotDotInRepo(Issue59SandboxMixin, unittest.TestCase):
    """リポジトリ内の途中のリンクの後の `..` はリンク先の親へ上がる."""

    def test_all_write_ops(self):
        link = self.make_repo_escape()
        self.assertAllWriteOpsDenied(os.path.join(link, "..", "existing.txt"))

    def test_new_file_through_link_is_allowed(self):
        link = self.make_repo_escape()
        path = _fwd(os.path.join(link, "..", "brand_new.txt"))
        self.assertBashAllowed([f"echo y > {path}", f"echo y | tee {path}"])
        self.assertPsAllowed([f"Set-Content {path} y"])
        self.assertAllowed(self.tool("Write", {"file_path": os.path.join(link, "..", "brand_new.txt"), "content": "y"}))

    def test_dot_dot_without_link_stays_in_repo(self):
        path = os.path.join(self.repo, "sub", "..", "file.txt")
        self.assertAllWriteOpsAllowed(path)

    def test_relative_path(self):
        self.make_repo_escape()
        self.assertBashDenied(["rm escape/../existing.txt", "echo y > escape/../existing.txt"])
        self.assertPsDenied(["Set-Content escape/../existing.txt y"])
        self.assertDenied(self.tool("Write", {"file_path": os.path.join("escape", "..", "existing.txt"), "content": "y"}))


class TestRestrictWildcardMatchingLinkInRepo(Issue59SandboxMixin, unittest.TestCase):
    """リポジトリ内のワイルドカードが外へのリンクに一致する場合."""

    def setUp(self) -> None:
        super().setUp()
        self.make_repo_escape()

    def test_intermediate_wildcard_is_denied(self):
        path = f"{self.r}/*/file.txt"
        self.assertBashDenied([f"rm {path}", f"echo y > {path}", f"echo y | tee {path}", f"sed -i s/x/y/ {path}",
                               "rm */file.txt"])
        self.assertPsDenied([f"Remove-Item {path}", f"Set-Content {path} y", f"echo y > {path}"])

    def test_intermediate_wildcard_new_file_is_allowed(self):
        path = f"{self.r}/*/brand_new.txt"
        self.assertBashAllowed([f"echo y > {path}"])
        self.assertPsAllowed([f"Set-Content {path} y"])

    def test_brace_to_outside_directory(self):
        self.assertBashDenied([f"rm -rf {self.r}/{{a,../outside/dir}}"])

    def test_tail_wildcard_only_removes_the_link(self):
        """rm 等は一致したリンク自体を消すだけで，リンク先には触らない."""
        r = self.r
        self.assertBashAllowed([f"rm {r}/*", f"rm -rf {r}/*", f"rm -rf {r}/esc*", "rm -rf *", f"mv {r}/esc* {r}/sub/"])
        self.assertPsAllowed([f"Remove-Item {r}/*", f"Remove-Item {r}/* -Recurse", f"Remove-Item {r}/esc* -Recurse"])

    def test_tail_wildcard_with_slash_reaches_link_target(self):
        """`*/` は末尾の / でリンクをたどるので，rm -rf はリンク先の中身を消す（Git Bash・Linux の rm で確認）."""
        r = self.r
        self.assertBashDenied([f"rm -rf {r}/*/", f"rm -rf {r}/esc*/", "rm -rf */"])

    def test_tail_wildcard_with_content_destroying_commands_is_denied(self):
        r = self.r
        self.assertBashDenied([f"truncate -s 0 {r}/*", f"shred {r}/*", f"truncate -s 0 {r}/esc*"])
        self.assertPsDenied([f"Clear-Content {r}/*", f"Clear-Content {r}/esc*"])

    def test_tail_wildcard_file_link_with_content_destroying_commands(self):
        link = os.path.join(self.repo, "link.txt")
        try:
            os.symlink(self.existing, link)
        except OSError:
            if os.name != "nt":
                raise
            self.skipTest("Windows でシンボリックリンクの作成権限が無い（ファイルにはジャンクションを使えない）")
        r = self.r
        self.assertBashDenied([f"truncate -s 0 {r}/*.txt", f"shred {r}/link*"])
        self.assertPsDenied([f"Clear-Content {r}/link*"])
        self.assertBashAllowed([f"rm {r}/link*"])

    def test_copy_into_link_matched_by_wildcard(self):
        """cp・mv のコピー先のワイルドカードがリンクに一致すると，リンク先のディレクトリへ書き込む."""
        r = self.r
        self.assertBashDenied([f"cp {r}/x/file.txt {r}/esc*", f"mv {r}/x/file.txt {r}/esc*"])
        self.assertBashAllowed([f"cp {r}/x/brand_new.txt {r}/esc*"])


class TestRestrictPowerShellBraceIsLiteral(Issue59SandboxMixin, unittest.TestCase):
    """PowerShell は波括弧を展開しないので文字どおりの名前として扱う."""

    def test_literal_brace_stays_inside_repo(self):
        self.assertPsAllowed([
            f"Remove-Item '{self.r}/{{a,../outside/existing.txt}}'",
            f"Remove-Item {self.r}/{{a,../outside/existing.txt}}",
            f"Set-Content '{self.r}/{{a,../outside/existing.txt}}' y",
        ])

    def test_bash_c_inside_powershell_expands(self):
        self.assertPsDenied([f"bash -c 'rm {self.r}/{{a,../outside/existing.txt}}'"])


class TestRestrictCdMinus(Issue59SandboxMixin, unittest.TestCase):
    """cd - / Set-Location - は同じコマンド内で追跡できる場合だけ解決する."""

    def setUp(self) -> None:
        super().setUp()
        self.x = self.touch(self.out, "x")

    def test_returns_to_outside_is_denied(self):
        for d in (f"{self.o}/dir", f"{self.r}/sub", f"{self.m}/sub", f"{self.r}"):
            self.assertBashDenied([f"cd {d} && cd - && rm x", f"cd {d}; cd -; rm -f x"], cwd=self.out)
            self.assertPsDenied([f"Set-Location {d}; Set-Location -; Remove-Item x", f"sl {d}; sl -; Remove-Item x"],
                                cwd=self.out)

    def test_inside_nested_shell_is_denied(self):
        self.assertBashDenied([f"bash -c 'cd {self.r}/sub && cd - && rm x'"], cwd=self.out)

    def test_returns_to_repo_is_allowed(self):
        for d in (f"{self.m}/sub", f"{self.o}/dir", f"{self.r}/sub"):
            self.assertBashAllowed([f"cd {d} && cd - && rm x"])
            self.assertPsAllowed([f"Set-Location {d}; Set-Location -; Remove-Item x"])

    def test_cd_minus_twice_toggles(self):
        self.assertBashDenied([f"cd {self.r}/sub && cd {self.o} && cd - && cd - && rm x"])
        self.assertBashAllowed([f"cd {self.o} && cd - && rm x", f"cd {self.o} && cd - && cd - && cd - && rm x"])

    def test_set_location_plus_goes_forward(self):
        self.assertPsDenied([f"Set-Location {self.o}; Set-Location -; Set-Location +; Remove-Item x"])
        self.assertPsAllowed([f"Set-Location {self.o}; Set-Location -; Remove-Item x"])

    def test_subshell_restores_previous_directory(self):
        self.assertBashDenied([f"cd {self.r}/sub && (cd {self.o}/dir) && cd - && rm x"], cwd=self.out)
        self.assertBashDenied([f"cd {self.o} && (cd - && rm x) && rm x"])

    def test_untrackable_stays_unresolved(self):
        """コマンドより前の移動先は分からないので，従来どおり未解決（判定しない）."""
        for cwd in (self.repo, self.out):
            self.assertBashAllowed(["cd - && rm x", "cd -; rm x"], cwd=cwd)
            self.assertPsAllowed(["Set-Location -; Remove-Item x", "Set-Location +; Remove-Item x"], cwd=cwd)
        self.assertBashAllowed([f"(cd {self.r}/sub) && cd - && rm x"], cwd=self.out)

    def test_absolute_target_after_unresolved_is_still_denied(self):
        self.assertBashDenied([f"cd - && rm {self.o}/x"])
        self.assertPsDenied([f"Set-Location -; Remove-Item {self.o}/x"])


class TestRestrictWildcardMatchLimit(Issue59SandboxMixin, unittest.TestCase):
    """一致が上限（1 パス 1000 件）を超えると，基点がリポジトリ内・一時ディレクトリなら許可，それ以外は拒否."""

    def make_dirs(self, parent: str, start: int, stop: int) -> None:
        for n in range(start, stop):
            os.makedirs(os.path.join(parent, "d%d" % n))

    def test_outside_boundary(self):
        many = os.path.join(self.out, "many")
        self.make_dirs(many, 0, 1000)
        cmd = f"echo y > {_fwd(many)}/*/nope.txt"
        ps_cmd = f"Set-Content {_fwd(many)}/*/nope.txt y"
        self.assertAllowed(self.bash(cmd), "ちょうど上限なら全部調べられる")
        self.assertAllowed(self.ps(ps_cmd), "ちょうど上限なら全部調べられる")
        self.make_dirs(many, 1000, 1001)
        self.assertDenied(self.bash(cmd), "上限超え")
        self.assertDenied(self.ps(ps_cmd), "上限超え")

    def test_inside_repo_over_limit_is_allowed(self):
        many = os.path.join(self.repo, "many")
        self.make_dirs(many, 0, 1001)
        self.assertAllowed(self.bash(f"echo y > {_fwd(many)}/*/nope.txt"))
        self.assertAllowed(self.bash(f"rm {_fwd(many)}/*"))
        self.assertAllowed(self.ps(f"Remove-Item {_fwd(many)}/*"))

    def test_with_small_limit(self):
        temp = tempfile.mkdtemp(prefix="_restrict_limit_")
        self.addCleanup(shutil.rmtree, temp, True)
        for parent in (os.path.join(self.repo, "many"), os.path.join(self.out, "many"), os.path.join(temp, "many")):
            self.make_dirs(parent, 0, 6)
        with mock.patch.object(self.rra, "MAX_WILDCARD_MATCHES", 5):
            self.assertAllowed(self.bash(f"echo y > {self.r}/many/*/nope.txt"))
            self.assertAllowed(self.bash(f"echo y > {_fwd(temp)}/many/*/nope.txt"))
            self.assertDenied(self.bash(f"echo y > {self.o}/many/*/nope.txt"))
            self.assertAllowed(self.ps(f"Set-Content {self.r}/many/*/nope.txt y"))
            self.assertAllowed(self.ps(f"Set-Content {_fwd(temp)}/many/*/nope.txt y"))
            self.assertDenied(self.ps(f"Set-Content {self.o}/many/*/nope.txt y"))

    def test_memory_base_over_limit_is_denied(self):
        """基点がメモのフォルダでも，リポジトリ内・一時ディレクトリではないので拒否する."""
        self.make_dirs(self.memory, 0, 6)
        with mock.patch.object(self.rra, "MAX_WILDCARD_MATCHES", 5):
            self.assertDenied(self.bash(f"rm {self.m}/*"))

    def test_new_file_append_is_not_undecidable(self):
        many = os.path.join(self.out, "many")
        self.make_dirs(many, 0, 6)
        with mock.patch.object(self.rra, "MAX_WILDCARD_MATCHES", 5):
            self.assertAllowed(self.bash(f"echo y >> {_fwd(many)}/*/nope.txt"))

    def test_scan_budget_is_reset_per_command(self):
        many = os.path.join(self.out, "many")
        self.make_dirs(many, 0, 3)
        cmd = f"echo y > {_fwd(many)}/*/nope.txt"
        with mock.patch.object(self.rra, "MAX_WILDCARD_SCANS", 5):
            for _ in range(4):
                self.assertAllowed(self.bash(cmd))
        with mock.patch.object(self.rra, "MAX_WILDCARD_SCANS", 1):
            self.assertDenied(self.bash(cmd))


class TestRestrictExpansionInTemp(Issue59SandboxMixin, unittest.TestCase):
    """一時ディレクトリ配下の波括弧・ワイルドカードも誤拒否しない."""

    def setUp(self) -> None:
        super().setUp()
        self.temp = tempfile.mkdtemp(prefix="_restrict_expansion_")
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.t = _fwd(self.temp)
        self.assertTrue(self.rra.is_temp_path(self.temp))

    def fill(self) -> None:
        for name in ("a.txt", "b.txt", "1.txt", "2.txt", os.path.join("d1", "x.txt"), os.path.join("d2", "x.txt")):
            self.touch(self.temp, "src", name)
        self.touch(self.temp, "a.txt")
        for d in ("b", "c", "dst"):
            os.makedirs(os.path.join(self.temp, d))

    def test_relative_and_absolute(self):
        t = self.t
        self.fill()
        self.assertBashAllowed(["rm src/{a,b}.txt", "rm src/*/x.txt", "cp a.txt {b,c}/", "mv src/*.txt dst/",
                                "rm src/{1..3}.txt", "echo y > src/*/x.txt", "sed -i s/x/y/ src/*/x.txt"], cwd=self.temp)
        self.assertBashAllowed([f"rm {t}/src/{{a,b}}.txt", f"rm {t}/src/*/x.txt", f"cp {t}/a.txt {t}/{{b,c}}/",
                                f"mv {t}/src/*.txt {t}/dst/", f"rm {t}/src/{{1..3}}.txt", f"echo y > {t}/src/*/x.txt"])
        self.assertPsAllowed([f"Remove-Item {t}/src/*/x.txt", f"Set-Content {t}/src/*/x.txt y"])

    def test_link_out_of_temp_is_denied(self):
        self.link_dir(os.path.join(self.out, "dir"), os.path.join(self.temp, "escape"))
        t = self.t
        self.assertBashDenied([f"rm {t}/*/file.txt", f"echo y > {t}/*/file.txt", f"rm {t}/escape/../existing.txt"])
        self.assertPsDenied([f"Set-Content {t}/*/file.txt y"])


class TestRestrictFileToolsLiteralWildcard(Issue59SandboxMixin, unittest.TestCase):
    """ファイル操作ツールは `..` だけをたどり，ワイルドカード・波括弧は文字どおりの名前として扱う."""

    def test_wildcard_is_not_expanded_by_write(self):
        self.make_repo_escape()
        self.assertAllowed(self.tool("Write", {"file_path": os.path.join(self.out, "*", "file.txt"), "content": "y"}))
        self.assertAllowed(self.tool("Write", {"file_path": os.path.join(self.repo, "*", "file.txt"), "content": "y"}))
        self.assertAllowed(self.tool("Edit", {"file_path": os.path.join(self.repo, "*", "file.txt")}))

    def test_brace_is_not_expanded(self):
        path = self.r + "/{a,../outside/existing.txt}"
        self.assertAllowed(self.tool("Write", {"file_path": path, "content": "y"}))

    def test_dot_dot_after_link_is_followed(self):
        link = self.make_memory_escape()
        path = os.path.join(link, "..", "existing.txt")
        self.assertDenied(self.tool("Write", {"file_path": path, "content": "y"}))
        self.assertDenied(self.tool("Edit", {"file_path": path}))

    def test_via_main(self):
        link = self.make_memory_escape()
        path = os.path.join(link, "..", "existing.txt")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.repo)
        data = {"tool_name": "Write", "tool_input": {"file_path": path, "content": "y"}, "cwd": self.repo,
                "transcript_path": self.session}
        self.assertEqual(self.decision(self.run_main(data, env)), "deny")
        data["tool_input"] = {"file_path": self.note, "content": "y"}
        self.assertIsNone(self.decision(self.run_main(data, env)))


class TestRestrictTrailingSeparatorLinkInRepo(Issue59SandboxMixin, unittest.TestCase):
    """リポジトリ内の外へのリンク．Bash は末尾の / でリンク先の中身を消す．PowerShell はリンクしか消さない."""

    def setUp(self) -> None:
        super().setUp()
        self.make_repo_escape()

    def test_wildcard_with_trailing_slash_is_denied(self):
        r = self.r
        self.assertBashDenied([f"rm -rf {r}/esc*/", f"rm -rf {r}/*/", "rm -rf ./*/", "rm -rf esc*//", "rm -rf {esc*,sub}/"])

    def test_through_command_prefix_and_nested_shell_is_denied(self):
        r = self.r
        self.assertBashDenied(["sudo rm -rf */", f"bash -c 'rm -rf {r}/*/'"])

    def test_literal_link_with_trailing_slash_is_denied(self):
        r = self.r
        self.assertBashDenied([f"rm -rf {r}/escape/", f"rm -rf {r}/escape//", "rm -rf escape//",
                               f"rm -rf {r}/sub/../escape/", "rm -rf sub/../escape/"])

    def test_without_trailing_slash_is_allowed(self):
        r = self.r
        self.assertBashAllowed([f"rm {r}/*", f"rm -rf {r}/*", "rm -rf esc*", "rm -rf *", f"mv {r}/esc* {r}/sub/"])

    def test_powershell_removes_only_the_link(self):
        r = self.r
        self.assertPsAllowed([f"Remove-Item {r}/*/ -Recurse", f"Remove-Item {r}/esc*/ -Recurse",
                              "Remove-Item esc*/ -Recurse"])

    @unittest.skipUnless(os.name == "nt", "\\ が PowerShell のパスの末尾の区切りとして実測できているのは Windows だけ")
    def test_powershell_backslash_removes_only_the_link(self):
        self.assertPsAllowed([f"Remove-Item {self.r}/esc*\\ -Recurse", "Remove-Item esc*\\ -Recurse"])


class TestRestrictTrailingSeparatorNoLinkInRepo(Issue59SandboxMixin, unittest.TestCase):
    """リンクが無ければ，末尾の / があってもリポジトリ内の削除は誤拒否しない."""

    def setUp(self) -> None:
        super().setUp()
        self.touch(self.repo, "src", "a", "x.txt")

    def test_is_allowed(self):
        self.assertBashAllowed(["rm -rf src/*/", "rm -rf */", f"rm -rf {self.r}/src/"])


class TestRestrictTrailingSeparatorInTemp(Issue59SandboxMixin, unittest.TestCase):
    """一時ディレクトリの中の外へのリンク．文字列では一時ディレクトリの中でも，消えるのはリンク先の中身."""

    def setUp(self) -> None:
        super().setUp()
        self.temp = tempfile.mkdtemp(prefix="_restrict_trailing_")
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.t = _fwd(self.temp)
        self.assertTrue(self.rra.is_temp_path(self.temp))
        self.touch(self.temp, "src", "a", "x.txt")

    def test_link_with_trailing_slash_is_denied(self):
        self.link_dir(os.path.join(self.out, "dir"), os.path.join(self.temp, "escape"))
        t = self.t
        self.assertBashDenied([f"rm -rf {t}/*/", f"rm -rf {t}/esc*/", f"rm -rf {t}/escape/"])

    def test_no_link_is_allowed(self):
        t = self.t
        self.assertBashAllowed([f"rm -rf {t}/*/", f"rm -rf {t}/src/*/", f"rm -rf {t}/src/"])
        self.assertBashAllowed(["rm -rf src/*/", "rm -rf */"], cwd=self.temp)


class TestRestrictTrailingSeparatorInMemory(Issue59SandboxMixin, unittest.TestCase):
    """メモのフォルダの中の外へのリンク."""

    def test_wildcard_with_trailing_slash_is_denied(self):
        self.make_memory_escape()
        m = self.m
        self.assertBashDenied([f"rm -rf {m}/*/", f"rm -rf {m}/esc*/", f"rm -rf {m}/sub/*/"])

    def test_without_trailing_slash_is_allowed(self):
        self.make_memory_escape()
        self.assertBashAllowed([f"rm {self.m}/*"])

    def test_no_link_is_allowed(self):
        self.assertBashAllowed([f"rm -rf {self.m}/sub/"])


class TestRestrictPushdMinus(Issue59SandboxMixin, unittest.TestCase):
    """pushd - / Push-Location - は同じコマンド内で追跡できる場合だけ解決する（cwd はリポジトリ外で x が既存）."""

    def setUp(self) -> None:
        super().setUp()
        self.x = self.touch(self.out, "x")

    def test_bash_returns_to_outside_is_denied(self):
        s = f"{self.r}/sub"
        self.assertBashDenied([f"cd {s} && pushd - && rm x", f"cd {s}; pushd -; rm x",
                               f"bash -c 'cd {s} && pushd - && rm x'"], cwd=self.out)

    def test_powershell_returns_to_outside_is_denied(self):
        s = f"{self.r}/sub"
        self.assertPsDenied([f"Set-Location {s}; Push-Location -; Remove-Item x",
                             f"Push-Location {s}; Set-Location -; Remove-Item x"], cwd=self.out)

    def test_popd_returns_to_repo_is_allowed(self):
        self.assertBashAllowed([f"cd {self.r}/sub && pushd - && popd && rm x"], cwd=self.out)

    def test_popd_location_is_tracked(self):
        """popd の戻り先が未解決に落ちていないこと（未解決だと上の許可のテストも通ってしまう）."""
        self.assertBashDenied([f"cd {self.r}/sub && pushd - && popd && rm ../../outside/x"], cwd=self.out)

    def test_untrackable_stays_unresolved(self):
        """コマンドより前の移動先は分からないので，従来どおり未解決（判定しない）."""
        self.assertBashAllowed(["pushd - && rm x"], cwd=self.out)
        self.assertPsAllowed(["Push-Location -; Remove-Item x"], cwd=self.out)


# ---------------------------------------------------------------------------
# is_temp_path: Linux / macOS はリンクを解決した実パスで一時ディレクトリを判定する
# ---------------------------------------------------------------------------
REAL_PATH_TEMP_REASON = (
    "実パスでの一時ディレクトリの判定は Linux / macOS だけの仕様のため"
    "（Windows は Git Bash の /tmp を実パスに解決できず，従来の文字列判定のまま）"
)


class TempLinkSandboxMixin(Issue59SandboxMixin):
    """実際の一時ディレクトリの中にサンドボックスを作り，リンク先はその外（self.out）に作る.

    <temp>/file.txt・realdir/f.txt
    <temp>/escape     → outside/dir（既存の file.txt を含む）．make_escape で作る
    <temp>/filelink   → outside/existing.txt．make_filelink で作る
    <temp>/innerlink  → <temp>/realdir．make_innerlink で作る
    """

    temp_parent = None  # None は tempfile.gettempdir()

    def setUp(self) -> None:
        super().setUp()
        self.temp = tempfile.mkdtemp(prefix="_restrict_temp_link_", dir=self.temp_parent)
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.t = _fwd(self.temp)
        self.assertTrue(self.rra.is_temp_path(self.temp), "サンドボックスが一時ディレクトリの中にあること")
        self.assertFalse(self.rra.is_temp_path(self.out), "リンク先が一時ディレクトリの外にあること")
        self.touch(self.temp, "file.txt")
        self.touch(self.temp, "realdir", "f.txt")

    def make_escape(self) -> str:
        link = os.path.join(self.temp, "escape")
        self.link_dir(os.path.join(self.out, "dir"), link)
        self.assertTrue(os.path.isfile(os.path.join(link, "file.txt")), "リンク先は既存のファイルであること")
        return link

    def make_filelink(self) -> str:
        link = os.path.join(self.temp, "filelink")
        os.symlink(self.existing, link)
        self.assertTrue(os.path.isfile(link), "リンク先は既存のファイルであること")
        return link

    def make_innerlink(self) -> str:
        link = os.path.join(self.temp, "innerlink")
        self.link_dir(os.path.join(self.temp, "realdir"), link)
        return link


@unittest.skipIf(os.name == "nt", REAL_PATH_TEMP_REASON)
class TestRestrictTempLinkEscapeDenied(TempLinkSandboxMixin, unittest.TestCase):
    """一時ディレクトリの中に見えても，リンク越しに壊れるのが外のファイルなら拒否する."""

    def test_operations_through_directory_link(self):
        self.make_escape()
        t, r = self.t, self.r
        self.assertBashDenied([
            f"rm {t}/escape/file.txt",
            f"rm -rf {t}/escape/file.txt",
            f"echo y > {t}/escape/file.txt",
            f"cp {r}/file.txt {t}/escape/file.txt",
            f"mv {t}/escape/file.txt {t}/moved",
            f"sed -i s/x/y/ {t}/escape/file.txt",
        ])

    def test_relative_path_through_directory_link(self):
        self.make_escape()
        self.assertBashDenied(["rm escape/file.txt", "echo y > escape/file.txt"], cwd=self.temp)

    def test_operations_on_file_link(self):
        """> ・cp・truncate・shred はリンク自体ではなくリンク先のファイルを壊す."""
        self.make_filelink()
        t, r = self.t, self.r
        self.assertBashDenied([
            f"echo y > {t}/filelink",
            f"cp {r}/file.txt {t}/filelink",
            f"truncate -s 0 {t}/filelink",
            f"shred {t}/filelink",
        ])

    def test_find_delete_from_link(self):
        self.make_escape()
        self.assertBashDenied([f"find {self.t}/escape -delete"])

    def test_nested_shell_prefix_and_inline_code(self):
        self.make_escape()
        t = self.t
        self.assertBashDenied([
            f"bash -c 'rm {t}/escape/file.txt'",
            f"sudo rm {t}/escape/file.txt",
            f"python3 -c \"import os; os.remove('{t}/escape/file.txt')\"",
        ])

    def test_link_inside_the_link_target(self):
        """最後の要素がリンクでも，その親がリンク越しに外にあれば，リンク自体を消す操作も外の変更になる."""
        self.make_escape()
        os.symlink(self.existing, os.path.join(self.out, "dir", "inner"))
        self.assertBashDenied([f"rm {self.t}/escape/inner"])

    def test_dotdot_escape(self):
        rel = _fwd(os.path.relpath(self.existing, self.temp))
        self.assertTrue(rel.startswith(".."), rel)
        self.assertBashDenied([f"rm {self.t}/{rel}", f"rm -rf {self.t}/{rel}", f"echo y > {self.t}/{rel}"])

    def test_powershell_and_file_tools(self):
        """実パスでの判定はシェルの種類やツールによらない."""
        escape = self.make_escape()
        filelink = self.make_filelink()
        target = os.path.join(escape, "file.txt")
        self.assertPsDenied([f"Remove-Item {self.t}/escape/file.txt", f"Set-Content {self.t}/escape/file.txt y"])
        for path in (target, filelink):
            with self.subTest(path):
                self.assertDenied(self.tool("Write", {"file_path": path, "content": "y"}), path)
                self.assertDenied(self.tool("Edit", {"file_path": path, "old_string": "x", "new_string": "y"}), path)


@unittest.skipIf(os.name == "nt", REAL_PATH_TEMP_REASON + "．Windows のリンク自体の削除は keep_final_link の対象外")
class TestRestrictTempLinkItselfAllowed(TempLinkSandboxMixin, unittest.TestCase):
    """リンク自体を消す・動かすだけの操作はリンク先に触らないので，許可のまま."""

    def test_remove_link_itself(self):
        self.make_escape()
        self.make_filelink()
        for link in ("escape", "filelink"):
            f = f"{self.t}/{link}"
            self.assertBashAllowed([f"rm {f}", f"rm -rf {f}", f"unlink {f}", f"mv {f} {self.t}/other"])

    def test_relative_link_itself(self):
        self.make_escape()
        self.assertBashAllowed(["rm escape", "rm -rf escape", "unlink escape", "mv escape other"], cwd=self.temp)

    def test_wildcard_matching_links(self):
        self.make_escape()
        self.make_filelink()
        self.assertBashAllowed([f"rm {self.t}/*", f"rm -rf {self.t}/*"])


class TestRestrictTempWithoutEscapeAllowed(TempLinkSandboxMixin, unittest.TestCase):
    """外へのリンクが関わらない一時ディレクトリの操作は，どの OS でも許可のまま."""

    def test_delete_and_overwrite_without_link(self):
        t, r = self.t, self.r
        self.assertBashAllowed([
            f"rm {t}/file.txt",
            f"rm -rf {t}/realdir",
            f"rm -rf {t}/realdir/",
            f"echo y > {t}/file.txt",
            f"cp {r}/file.txt {t}/file.txt",
            f"mv {t}/file.txt {t}/moved",
            f"sed -i s/x/y/ {t}/file.txt",
            f"truncate -s 0 {t}/file.txt",
            f"shred {t}/file.txt",
        ])

    def test_through_link_to_inside_temp(self):
        self.make_innerlink()
        t, r = self.t, self.r
        self.assertBashAllowed([
            f"rm {t}/innerlink/f.txt",
            f"rm -rf {t}/innerlink/",
            f"echo y > {t}/innerlink/f.txt",
            f"cp {r}/file.txt {t}/innerlink/f.txt",
        ])

    def test_create_with_missing_parents(self):
        t, r = self.t, self.r
        self.assertBashAllowed([
            f"touch {t}/new.txt",
            f"echo y > {t}/no/such/parent/new.txt",
            f"mkdir -p {t}/no/such/parent",
            f"cp {r}/file.txt {t}/no/such/parent/new.txt",
            f"rm -rf {t}/no/such/parent",
        ])
        self.assertAllowed(self.tool("Write", {"file_path": os.path.join(self.temp, "no", "such", "new.txt"), "content": "y"}))


class TestRestrictTempSkillCommandShapes(TempLinkSandboxMixin, unittest.TestCase):
    """/sync-template・/set-mode が mktemp -d の配下で実行するコマンドの形."""

    def shapes(self, root: str) -> list:
        r = self.r
        return [
            f'git clone --depth 1 https://example.invalid/t.git "{root}/tpl"',
            f': > "{root}/overrides.tsv"',
            f'mkdir -p "{root}/3way/.claude" "{root}/merged/.claude"',
            f'cp "{r}/file.txt" "{root}/3way/ours"',
            f'cp "{root}/tpl/file.txt" "{r}/file.txt"',
            f"sed 's/\\r$//' \"{r}/file.txt\" > \"{root}/3way/ours\"",
            f'rm -rf "{root}/tpl" "{root}"',
            f'rm -rf "{root}"',
        ]

    def test_shapes_on_existing_files(self):
        root = os.path.join(self.temp, "work")
        for name in ("overrides.tsv", os.path.join("3way", "ours"), os.path.join("tpl", "file.txt")):
            self.touch(root, name)
        self.assertBashAllowed(self.shapes(_fwd(root)))

    @unittest.skipIf(os.name == "nt", "mktemp コマンドの実体を直接呼べるのは Linux / macOS だけのため（Windows は Git Bash 経由）")
    def test_shapes_under_real_mktemp(self):
        if shutil.which("mktemp") is None:
            self.skipTest("mktemp コマンドが無い")
        root = subprocess.check_output(["mktemp", "-d"], universal_newlines=True).strip()
        self.addCleanup(shutil.rmtree, root, True)
        for name in ("overrides.tsv", os.path.join("3way", "ours"), os.path.join("tpl", "file.txt")):
            self.touch(root, name)
        self.assertBashAllowed(self.shapes(root))

    def test_shapes_with_shell_variables(self):
        self.assertBashAllowed([
            'TEMP_DIR=$(mktemp -d)\ngit clone --depth 1 "$U" "$TEMP_DIR"\ncp "$TEMP_DIR/$f" "$f"\nrm -rf "$TEMP_DIR"',
            'WORK_DIR=$(mktemp -d)\n: > "$WORK_DIR/overrides.tsv"\nmkdir -p "$WORK_DIR/3way"\nrm -rf "$WORK_DIR"',
        ])


@unittest.skipIf(os.name == "nt", "基準の /tmp を実パスに解決できるのは Linux / macOS だけのため（Windows の /tmp は Git Bash の仮想パス）")
class TestRestrictTempLinkUnderSlashTmp(TempLinkSandboxMixin, unittest.TestCase):
    """基準の一方である /tmp を直接使う（gettempdir が別の場所の macOS 等でも /tmp を判定できること）."""

    temp_parent = "/tmp"

    def setUp(self) -> None:
        if not (os.path.isdir("/tmp") and os.access("/tmp", os.W_OK)):
            self.skipTest("/tmp が存在しないか書き込めない")
        super().setUp()

    def test_escape_is_denied(self):
        self.make_escape()
        self.make_filelink()
        t = self.t
        self.assertBashDenied([f"rm {t}/escape/file.txt", f"echo y > {t}/filelink", f"rm /tmp/..{_fwd(self.existing)}"])

    def test_without_escape_is_allowed(self):
        self.make_escape()
        t = self.t
        name = os.path.basename(self.temp)
        self.assertBashAllowed([f"rm {t}/escape", f"rm {t}/file.txt", f"echo y > {t}/no/such/parent/new.txt",
                                f"rm -rf {t}/no/such/parent", f"rm /tmp/{name}/file.txt"])

    def test_is_temp_path(self):
        self.make_escape()
        self.assertTrue(self.rra.is_temp_path("/tmp"))
        self.assertTrue(self.rra.is_temp_path(f"/tmp/{os.path.basename(self.temp)}/no/such/file"))
        self.assertFalse(self.rra.is_temp_path(f"{self.t}/escape/file.txt"))
        self.assertTrue(self.rra.is_temp_path(f"{self.t}/escape", True))
        self.assertFalse(self.rra.is_temp_path(f"{self.t}/escape"))
        self.assertFalse(self.rra.is_temp_path("/tmp/../etc"))


@unittest.skipUnless(os.name == "nt", "Git Bash 形式の /tmp は Windows にしか存在しないため")
class TestRestrictGitBashTmpOnWindows(RestrictSandboxMixin, unittest.TestCase):
    """Windows の /tmp は実パスに解決できないので，今までどおり文字列で許可する."""

    def test_is_temp_path(self):
        for path in ("/tmp", "/tmp/x", "/tmp/x/y.txt", "/tmp/tmp.AbCdEf/tpl", "\\tmp\\x"):
            with self.subTest(path):
                self.assertTrue(self.rra.is_temp_path(path))
                self.assertTrue(self.rra.is_temp_path(path, True), "keep_final_link は Windows では効かない")
        for path in ("/tmp/../etc", "/tmpfoo/x", "/etc/hosts"):
            with self.subTest(path):
                self.assertFalse(self.rra.is_temp_path(path))

    def test_commands(self):
        for cmd in ("rm /tmp/x", "rm -rf /tmp/x", "echo y > /tmp/x/y.txt", "cp file.txt /tmp/x", "mkdir -p /tmp/x/y",
                    'git clone --depth 1 https://example.invalid/t.git "/tmp/tmp.AbCdEf"', ': > "/tmp/tmp.AbCdEf/o.tsv"'):
            with self.subTest(cmd):
                self.assertAllowed(self.bash(cmd), cmd)


class TestContainedDeniedEntryInTemp(TempLinkSandboxMixin, unittest.TestCase):
    """再帰読み取りの起点の配下にある禁止パスの検出（Policy.contained_denied_entry）."""

    def setUp(self) -> None:
        super().setUp()
        self.secret = os.path.join(self.out, "dir")
        self.deny = [self.secret]

    def policy(self):
        return self.rra.Policy(self.repo, self.deny)

    @unittest.skipIf(os.name == "nt", REAL_PATH_TEMP_REASON)
    def test_link_to_outside_detects_denied_entry(self):
        link = os.path.join(self.temp, "toout")
        self.link_dir(self.out, link)
        self.assertEqual(self.policy().contained_denied_entry(link), self.secret)
        self.assertDenied(self.bash(f"grep -r x {_fwd(link)}"))
        self.assertDenied(self.tool("Grep", {"pattern": "x", "path": link}))
        self.assertDenied(self.tool("Glob", {"pattern": "*", "path": link}))

    def test_temp_without_link_detects_nothing(self):
        self.assertIsNone(self.policy().contained_denied_entry(self.temp))
        self.assertAllowed(self.bash(f"grep -r x {self.t}"))

    def test_denied_entry_inside_temp_is_not_contained(self):
        """一時ディレクトリは対象外なので，配下に禁止パスがあっても検出しない（検出の条件が効いていること）."""
        self.deny = [os.path.join(self.temp, "realdir")]
        self.assertIsNone(self.policy().contained_denied_entry(self.temp))


MACOS_TEMPDIR = "/var/folders/ab/cd/T"


def _fake_macos_realpath(path, **_):
    """macOS のリンク（/tmp → /private/tmp，/var → /private/var）と，/tmp/x/escape → /Users/me/data を模擬する."""
    path = posixpath.normpath(path)
    for link, target in (("/tmp/x/escape", "/Users/me/data"), ("/tmp", "/private/tmp"), ("/var", "/private/var")):
        if path == link or path.startswith(link + "/"):
            return _fake_macos_realpath(target + path[len(link):])
    return path


@contextlib.contextmanager
def _simulate_macos_paths(module):
    """module から見える os を POSIX 相当にし，posixpath.realpath と tempfile.gettempdir を macOS 相当にする.

    with を抜けると（例外時も）mock.patch が元に戻すので，他のテストには影響しない．
    """
    with _simulate_posix_host(module), \
            mock.patch.object(posixpath, "realpath", _fake_macos_realpath), \
            mock.patch.object(tempfile, "gettempdir", lambda: MACOS_TEMPDIR):
        yield


class TestIsTempPathMacOSSimulation(unittest.TestCase):
    """macOS のパス構成を差し替えで模擬する（実機は PR の CI で確認する）.

    os.name と os.path も POSIX 相当に差し替えるので，ホスト OS によらず実行できる．
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.rra = _load_restrict()

    def test_temp_paths_are_allowed(self):
        cases = ("/tmp", "/tmp/new/file", "/private/tmp", "/private/tmp/new/file",
                 MACOS_TEMPDIR, MACOS_TEMPDIR + "/tmp.abc/file",
                 "/private" + MACOS_TEMPDIR, "/private" + MACOS_TEMPDIR + "/tmp.abc/file")
        with _simulate_macos_paths(self.rra):
            for path in cases:
                with self.subTest(path):
                    self.assertTrue(self.rra.is_temp_path(path))

    def test_link_itself_keeps_final_component(self):
        with _simulate_macos_paths(self.rra):
            for path in ("/tmp/x/escape", MACOS_TEMPDIR + "/tmp.abc", "/tmp/tmp.abc"):
                with self.subTest(path):
                    self.assertTrue(self.rra.is_temp_path(path, True))

    def test_outside_paths_are_not_temp(self):
        cases = ("/tmp/x/escape", "/tmp/x/escape/file.txt", "/tmp/../etc", "/private/etc/hosts", "/var/log/x",
                 "/private/tmpfoo/x", "/tmpfoo/x", MACOS_TEMPDIR + "foo/x", "/private/var/folders")
        with _simulate_macos_paths(self.rra):
            for path in cases:
                with self.subTest(path):
                    self.assertFalse(self.rra.is_temp_path(path))

    def test_keep_final_link_does_not_hide_parent_link(self):
        with _simulate_macos_paths(self.rra):
            self.assertFalse(self.rra.is_temp_path("/tmp/x/escape/file.txt", True))

    def test_commands(self):
        repo = "/work/repo"
        with _simulate_macos_paths(self.rra):
            for cmd in ("rm /tmp/tmp.abc/file", f"rm -rf {MACOS_TEMPDIR}/tmp.abc", "rm -rf /tmp/x/escape",
                        "rm /private/tmp/tmp.abc/file"):
                with self.subTest(cmd):
                    self.assertIsNone(self.rra.check_bash_command(cmd, repo, repo, []))
            for cmd in ("rm /tmp/x/escape/file.txt", "rm /private/etc/hosts", "rm /tmp/../etc/hosts"):
                with self.subTest(cmd):
                    self.assertIsNotNone(self.rra.check_bash_command(cmd, repo, repo, []))

    def test_simulation_is_restored(self):
        real_realpath = posixpath.realpath
        real_gettempdir = tempfile.gettempdir
        with self.assertRaises(RuntimeError):
            with _simulate_macos_paths(self.rra):
                self.assertEqual(self.rra.os.path.realpath("/tmp"), "/private/tmp")
                self.assertEqual(tempfile.gettempdir(), MACOS_TEMPDIR)
                raise RuntimeError("x")
        self.assertIs(posixpath.realpath, real_realpath)
        self.assertIs(tempfile.gettempdir, real_gettempdir)
        self.assertIs(self.rra.os, os)


# ---------------------------------------------------------------------------
# restrict_repo_access.py: 想定外の例外・壊れた入力の拒否（Issue #62）
# ---------------------------------------------------------------------------
# 判定の結果は標準入力・標準出力・終了コードで見る（フックの入出力の仕様そのものなので）．
# 例外を起こすテストは，本物のプロセスの終了コードを見るために subprocess の中で関数を差し替える．
HOOK_DRIVER = (
    "import io, os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import restrict_repo_access as rra\n"
    "exec(sys.argv[2])\n"
    "rra.main()\n"
)
HOOK_RAISE = (
    "class ZzBoom(Exception):\n"
    "    pass\n"
    "def boom(*args, **kwargs):\n"
    "    raise ZzBoom('SECRET_MESSAGE')\n"
    "{target} = boom\n"
)
UNEXPECTED_PREFIX = "想定外の例外（"
NUL_REASON = "NUL 文字"
WRAPPER_COMMAND = [BASH, ".claude/hooks/run_python.sh", "--fail-closed", ".claude/hooks/restrict_repo_access.py"]


class HookIOMixin(RestrictSandboxMixin):
    """main に標準入力でバイト列を渡し，拒否の JSON・終了コード・stderr を確かめる."""

    def hook_env(self, project_dir: bool = True, **extra) -> dict:
        env = dict(os.environ)
        env.pop("CLAUDE_PROJECT_DIR", None)
        if project_dir:
            env["CLAUDE_PROJECT_DIR"] = self.repo
        env.update(extra)
        return env

    @staticmethod
    def raw(data) -> bytes:
        """Claude Code と同じ，ASCII エスケープしない生の UTF-8 の JSON."""
        return json.dumps(data, ensure_ascii=False).encode("utf-8")

    def feed(self, payload: bytes, env: dict | None = None, stdout=subprocess.PIPE, stderr=subprocess.PIPE):
        return subprocess.run([sys.executable, "-B", RESTRICT], input=payload, stdout=stdout, stderr=stderr,
                              env=self.hook_env() if env is None else env, cwd=self.repo, timeout=TIMEOUT)

    def feed_tool(self, tool_name, tool_input, env: dict | None = None, **extra):
        data = dict({"tool_name": tool_name, "tool_input": tool_input, "cwd": self.repo}, **extra)
        return self.feed(self.raw(data), env)

    def feed_driver(self, setup: str, payload: bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE):
        """フックの一部を差し替えてから main を実行する（setup は rra を名前で参照できる）."""
        return subprocess.run([sys.executable, "-B", "-c", HOOK_DRIVER, HOOKS_DIR, setup], input=payload,
                              stdout=stdout, stderr=stderr, env=self.hook_env(), cwd=self.repo, timeout=TIMEOUT)

    def feed_wrapper(self, payload: bytes, stdout=subprocess.PIPE):
        """settings.json と同じコマンド（run_python.sh --fail-closed 経由）で起動する."""
        return subprocess.run(WRAPPER_COMMAND, input=payload, stdout=stdout, stderr=subprocess.PIPE,
                              env=self.hook_env(), cwd=REPO_ROOT, timeout=TIMEOUT)

    def assertHookDenies(self, result, label="") -> str:
        """通常の拒否と同じ形の JSON（1 個だけ）と終了コード 0 を確かめ，理由を返す."""
        self.assertEqual(result.returncode, 0, (label, result.stdout, result.stderr))
        self.assertTrue(result.stdout.strip(), ("拒否の JSON が出力されていない（許可された）", label))
        output = json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse", label)
        self.assertEqual(output["permissionDecision"], "deny", label)
        self.assertTrue(output["permissionDecisionReason"], label)
        return output["permissionDecisionReason"]

    def assertHookDeniesUnexpected(self, result, label="") -> str:
        reason = self.assertHookDenies(result, label)
        self.assertIn(UNEXPECTED_PREFIX, reason, label)
        return reason

    def assertHookAllows(self, result, label="") -> None:
        self.assertEqual(result.returncode, 0, (label, result.stdout, result.stderr))
        self.assertEqual(result.stdout.strip(), b"", label)
        self.assertEqual(result.stderr.strip(), b"", label)

    @contextlib.contextmanager
    def unwritable(self, kind: str):
        """書き込みに失敗する標準出力・標準エラー用のファイル記述子を用意する."""
        if kind == "read-only file":
            path = os.path.join(self.base, "readonly.txt")
            with open(path, "wb"):
                pass
            with open(path, "rb") as f:
                yield f
        else:  # 読み手のいないパイプ
            read_end, write_end = os.pipe()
            os.close(read_end)
            try:
                yield write_end
            finally:
                os.close(write_end)

    UNWRITABLE_KINDS = ("read-only file", "closed pipe")


class TestHookUnexpectedException(HookIOMixin, unittest.TestCase):
    """判定中の想定外の例外は拒否になる（理由は型名だけで，コマンドや例外メッセージを含まない）."""

    def payload(self, tool_name="Bash", tool_input=None):
        if tool_input is None:
            tool_input = {"command": "echo SECRET_COMMAND"}
        return self.raw({"tool_name": tool_name, "tool_input": tool_input, "cwd": self.repo})

    def test_exception_is_denied_with_type_name(self):
        cases = {
            "shell check": ("rra.check_shell_command", "Bash", None),
            "powershell check": ("rra.check_shell_command", "PowerShell", None),
            "file check": ("rra.check_file_tool", "Read", {"file_path": "SECRET_COMMAND"}),
            "config": ("rra.load_deny_read", "Bash", None),
            "repo root": ("rra.get_repo_root", "Read", {"file_path": "SECRET_COMMAND"}),
            "realpath": ("os.path.realpath", "Bash", {"command": "rm ./SECRET_COMMAND"}),
        }
        for label, (target, tool_name, tool_input) in cases.items():
            with self.subTest(label):
                result = self.feed_driver(HOOK_RAISE.format(target=target), self.payload(tool_name, tool_input))
                reason = self.assertHookDenies(result, label)
                self.assertIn("想定外の例外（ZzBoom）", reason)
                self.assertNotIn("SECRET_COMMAND", reason)
                self.assertNotIn("SECRET_MESSAGE", reason)

    def test_traceback_goes_to_stderr(self):
        result = self.feed_driver(HOOK_RAISE.format(target="rra.check_shell_command"), self.payload())
        stderr = result.stderr.decode("utf-8", "replace")
        self.assertIn("Traceback", stderr)
        self.assertIn("ZzBoom", stderr)
        self.assertIn("SECRET_MESSAGE", stderr)

    def test_reason_points_to_the_hook(self):
        """全操作が止まる不具合のときに対処できるよう，理由にフックの場所が書かれている."""
        result = self.feed_driver(HOOK_RAISE.format(target="rra.check_shell_command"), self.payload())
        self.assertIn("restrict_repo_access.py", self.assertHookDenies(result))

    def test_exception_with_broken_str_is_denied(self):
        setup = ("class ZzBoom(Exception):\n"
                 "    def __str__(self):\n"
                 "        raise RuntimeError('broken')\n"
                 "def boom(*args, **kwargs):\n"
                 "    raise ZzBoom()\n"
                 "rra.check_shell_command = boom\n")
        self.assertIn("想定外の例外（ZzBoom）", self.assertHookDenies(self.feed_driver(setup, self.payload())))

    def test_via_wrapper(self):
        """settings.json と同じ起動方法でも拒否の JSON が届く."""
        reason = self.assertHookDeniesUnexpected(self.feed_wrapper(b"{"))
        self.assertIn("JSONDecodeError", reason)


class TestHookBrokenInput(HookIOMixin, unittest.TestCase):
    """壊れた入力は拒否になる."""

    def test_broken_json(self):
        cases = (b"{", b"}", b"not json", b'{"tool_name": "Bash"', b"{'tool_name': 'Bash'}",
                 b'{"tool_name": "Bash", "tool_input": {"command": "SECRET_COMMAND')
        for payload in cases:
            with self.subTest(payload):
                reason = self.assertHookDeniesUnexpected(self.feed(payload))
                self.assertIn("JSONDecodeError", reason)
                self.assertNotIn("SECRET_COMMAND", reason)

    def test_empty_input(self):
        for payload in (b"", b" ", b"\n", b"\r\n"):
            with self.subTest(payload):
                self.assertHookDeniesUnexpected(self.feed(payload))

    def test_json_that_is_not_an_object(self):
        for payload in (b"[]", b"[1]", b'[{"tool_name": "Bash"}]', b"123", b"1.5", b'"text"', b"null", b"true", b"false"):
            with self.subTest(payload):
                self.assertHookDeniesUnexpected(self.feed(payload))

    def test_undecodable_bytes(self):
        cases = (b"\xff", b"\x80abc", b"\xff\xfe\x00", b"\xc3",
                 b'{"tool_name": "Bash", "tool_input": {"command": "echo \xff"}}',
                 b'{"tool_name": "Bash", "tool_input": {"command": "echo \x82\xa0"}}')  # cp932 の「あ」
        for payload in cases:
            with self.subTest(payload):
                self.assertHookDeniesUnexpected(self.feed(payload))

    def test_too_deeply_nested_json(self):
        """RecursionError も Exception なので拒否になる."""
        self.assertHookDeniesUnexpected(self.feed(b"[" * 200000))

    def test_stdin_without_binary_buffer(self):
        for label, setup in (("StringIO", "sys.stdin = io.StringIO('{}')\n"), ("None", "sys.stdin = None\n")):
            with self.subTest(label):
                self.assertHookDeniesUnexpected(self.feed_driver(setup, b"{}"), label)

    def test_invalid_tool_input_type(self):
        for tool_name in ("Bash", "PowerShell", "Read", "Edit", "Write"):
            for tool_input in ("rm x", ["rm x"], [{"command": "rm x"}], 123, 1.5, True):
                with self.subTest(tool=tool_name, tool_input=tool_input):
                    self.assertHookDeniesUnexpected(self.feed_tool(tool_name, tool_input))

    def test_invalid_tool_input_type_holding_the_key_name(self):
        """文字列やリストの tool_input に項目名と同じ値があっても，tool_input の型不正として拒否する."""
        cases = (("Bash", "command"), ("Bash", ["command"]), ("PowerShell", "command"), ("Write", "file_path"),
                 ("Edit", ["file_path"]), ("NotebookEdit", "notebook_path"), ("NotebookEdit", ["notebook_path"]))
        for tool_name, tool_input in cases:
            with self.subTest(tool=tool_name, tool_input=tool_input):
                self.assertHookDeniesUnexpected(self.feed_tool(tool_name, tool_input))

    def test_invalid_tool_input_reason_has_no_command(self):
        reason = self.assertHookDeniesUnexpected(self.feed_tool("Bash", "rm SECRET_COMMAND"))
        self.assertNotIn("SECRET_COMMAND", reason)

    def bad_cwd_inputs(self):
        return {
            "Bash echo": ("Bash", {"command": "echo hi"}),
            "Bash rm": ("Bash", {"command": "rm ./a"}),
            "Read relative": ("Read", {"file_path": "file.txt"}),
            "Edit relative": ("Edit", {"file_path": "file.txt", "old_string": "x", "new_string": "y"}),
        }

    def bad_cwd_absolute_inputs(self):
        target = os.path.join(self.repo, "file.txt")
        return {
            "Read absolute": ("Read", {"file_path": target}),
            "Edit absolute": ("Edit", {"file_path": target, "old_string": "x", "new_string": "y"}),
        }

    def test_invalid_cwd_type_with_absolute_path(self):
        """不正な型の cwd を拒否するのは cwd を使う判定だけ．CLAUDE_PROJECT_DIR と絶対パスで判定できれば影響しない."""
        for label, (tool_name, tool_input) in self.bad_cwd_absolute_inputs().items():
            for cwd in (123, 1.5, True, ["x"], {"a": 1}):
                with self.subTest(label, cwd=cwd):
                    data = {"tool_name": tool_name, "tool_input": tool_input, "cwd": cwd}
                    self.assertHookAllows(self.feed(self.raw(data)), label)

    def test_invalid_cwd_type_keeps_decision_for_outside_absolute_path(self):
        """cwd を使わない判定なので，リポジトリ外の判定も cwd の型によらず変わらない."""
        edit = {"file_path": self.existing, "old_string": "x", "new_string": "y"}
        for cwd in (123, ["x"]):
            with self.subTest(cwd=cwd):
                reason = self.assertHookDenies(self.feed(self.raw({"tool_name": "Edit", "tool_input": edit, "cwd": cwd})))
                self.assertNotIn(UNEXPECTED_PREFIX, reason)
                read = {"tool_name": "Read", "tool_input": {"file_path": self.existing}, "cwd": cwd}
                self.assertHookAllows(self.feed(self.raw(read)))

    def test_invalid_cwd_type(self):
        for label, (tool_name, tool_input) in self.bad_cwd_inputs().items():
            for cwd in (123, 1.5, True, ["x"], {"a": 1}):
                with self.subTest(label, cwd=cwd):
                    data = {"tool_name": tool_name, "tool_input": tool_input, "cwd": cwd}
                    self.assertHookDeniesUnexpected(self.feed(self.raw(data)), label)

    def test_invalid_cwd_type_without_project_dir(self):
        inputs = dict(self.bad_cwd_inputs(), **self.bad_cwd_absolute_inputs())
        for label, (tool_name, tool_input) in inputs.items():
            for cwd in (123, ["x"]):
                with self.subTest(label, cwd=cwd):
                    data = {"tool_name": tool_name, "tool_input": tool_input, "cwd": cwd}
                    self.assertHookDeniesUnexpected(self.feed(self.raw(data), self.hook_env(project_dir=False)), label)


class TestHookInvalidValueType(HookIOMixin, unittest.TestCase):
    """判定の対象の値（コマンド・書き込み先のパス）が文字列でなければ拒否する."""

    INVALID_REASON = "型が不正"
    TARGETS = (("Bash", "command"), ("PowerShell", "command"), ("Write", "file_path"), ("Edit", "file_path"),
               ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path"))

    def outside_marker(self) -> str:
        return os.path.join(self.out, "zz_marker_in_list")

    def invalid_values(self):
        marker = self.outside_marker()
        return ([marker], ["rm", marker], ["rm -rf " + _fwd(marker)], [], 0, False, {}, None, 123, True)

    def test_invalid_type_is_denied(self):
        for tool_name, key in self.TARGETS:
            for value in self.invalid_values():
                with self.subTest(tool=tool_name, value=value):
                    result = self.feed_tool(tool_name, {key: value})
                    reason = self.assertHookDenies(result)
                    self.assertIn(self.INVALID_REASON, reason)
                    self.assertNotIn(UNEXPECTED_PREFIX, reason)
                    self.assertNotIn("zz_marker_in_list", reason)
                    self.assertEqual(result.stderr, b"")

    def test_invalid_type_is_denied_with_extra_fields(self):
        """他の項目が正常でも結果は同じ（Edit の old_string 等）."""
        edit = {"file_path": [self.outside_marker()], "old_string": "x", "new_string": "y"}
        self.assertIn(self.INVALID_REASON, self.assertHookDenies(self.feed_tool("Edit", edit)))
        write = {"file_path": 0, "content": "x"}
        self.assertIn(self.INVALID_REASON, self.assertHookDenies(self.feed_tool("Write", write)))

    def test_invalid_type_is_denied_without_project_dir(self):
        for tool_name, key in self.TARGETS:
            with self.subTest(tool_name):
                reason = self.assertHookDenies(self.feed_tool(tool_name, {key: []}, self.hook_env(project_dir=False)))
                self.assertIn(self.INVALID_REASON, reason)

    def test_invalid_type_is_denied_via_wrapper(self):
        payload = self.raw({"tool_name": "Bash", "tool_input": {"command": ["rm", self.outside_marker()]},
                            "cwd": self.repo})
        self.assertIn(self.INVALID_REASON, self.assertHookDenies(self.feed_wrapper(payload)))

    def test_invalid_type_with_unwritable_stdout_exits_2(self):
        for kind in self.UNWRITABLE_KINDS:
            with self.subTest(kind), self.unwritable(kind) as stdout:
                payload = self.raw({"tool_name": "Bash", "tool_input": {"command": []}, "cwd": self.repo})
                self.assertEqual(self.feed(payload, stdout=stdout).returncode, 2)

    def test_missing_key_or_empty_string_is_allowed(self):
        for tool_name, key in self.TARGETS:
            with self.subTest(tool=tool_name, value="missing"):
                self.assertHookAllows(self.feed_tool(tool_name, {"other": "x"}))
            with self.subTest(tool=tool_name, value="empty"):
                self.assertHookAllows(self.feed_tool(tool_name, {key: ""}))

    def test_read_side_paths_are_not_judged_by_type(self):
        cases = (("Read", "file_path", {}), ("Grep", "path", {"pattern": "x"}), ("Glob", "path", {"pattern": "*.py"}))
        for tool_name, key, others in cases:
            for value in self.invalid_values():
                with self.subTest(tool=tool_name, value=value):
                    self.assertHookAllows(self.feed_tool(tool_name, dict(others, **{key: value})))

    def test_other_tools_are_not_judged_by_type(self):
        marker = self.outside_marker()
        for tool_input in ({"command": ["rm", marker]}, {"file_path": [marker]}, {"notebook_path": [marker]}):
            with self.subTest(tool_input=tool_input):
                self.assertHookAllows(self.feed_tool("TodoWrite", tool_input))

    def test_keys_of_other_tools_are_not_judged(self):
        """見るのはそのツールの項目だけ（Bash の file_path や Write の command は判定の対象でない）."""
        self.assertHookAllows(self.feed_tool("Bash", {"command": "echo hi", "file_path": ["x"]}))
        self.assertHookAllows(self.feed_tool("Write", {"file_path": os.path.join(self.repo, "n.txt"), "command": ["x"]}))


class TestHookBrokenConfig(HookIOMixin, unittest.TestCase):
    """repo-access.json が壊れていて警告を出せないときの動作."""

    BROKEN_CONFIGS = ("{broken", json.dumps({"deny_read": [1, 2]}))

    def write_config(self, text: str) -> None:
        with open(os.path.join(self.repo, ".claude", "repo-access.json"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def rm_payload(self, target: str) -> bytes:
        return self.raw({"tool_name": "Bash", "tool_input": {"command": f'rm "{target}"'}, "cwd": self.repo})

    def test_rm_in_repo_is_denied_when_warning_cannot_be_written(self):
        """禁止リストを読めず警告も出せないので，判定できないものとして想定外の例外で拒否する."""
        for text in self.BROKEN_CONFIGS:
            self.write_config(text)
            for kind in self.UNWRITABLE_KINDS:
                with self.subTest(config=text, kind=kind), self.unwritable(kind) as stderr:
                    self.assertHookDeniesUnexpected(self.feed(self.rm_payload("file.txt"), stderr=stderr), kind)

    def test_rm_outside_is_denied_when_warning_cannot_be_written(self):
        target = _fwd(self.existing)
        for kind in self.UNWRITABLE_KINDS:
            self.write_config("{broken")
            with self.subTest(kind), self.unwritable(kind) as stderr:
                self.assertHookDenies(self.feed(self.rm_payload(target), stderr=stderr), kind)

    def test_rm_outside_with_stderr_none_outputs_only_deny_json(self):
        """sys.stderr が None のとき警告が stdout に混ざると，拒否の JSON が壊れて素通りする."""
        self.write_config("{broken")
        self.assertHookDenies(self.feed_driver("sys.stderr = None\n", self.rm_payload(_fwd(self.existing))))

    def test_rm_in_repo_with_stderr_none_keeps_stdout_clean(self):
        """許可のときも stdout に判定以外のものを出さない."""
        self.write_config("{broken")
        result = self.feed_driver("sys.stderr = None\n", self.rm_payload("file.txt"))
        if result.stdout.strip():
            self.assertHookDeniesUnexpected(result)
        else:
            self.assertEqual(result.returncode, 0)

    def test_rm_in_repo_is_allowed_when_warning_can_be_written(self):
        """stderr に書ければ従来どおり，禁止リストを空として扱って判定を続ける."""
        for text in self.BROKEN_CONFIGS:
            self.write_config(text)
            with self.subTest(config=text):
                result = self.feed(self.rm_payload("file.txt"))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), b"")
                self.assertTrue(result.stderr.strip(), "stderr に警告が出ること")


@unittest.skipUnless(os.path.exists("/dev/full"), "/dev/full は Linux 固有のデバイス（書き込みが必ず ENOSPC になる）のため．"
                     "他の OS でも書き込みの失敗は TestHookOutputFailure の閉じたパイプ等で確認している")
class TestHookDevFull(HookIOMixin, unittest.TestCase):
    """本物の書き込み失敗（ディスクフル相当）でも終了コードが 120 にならない."""

    def test_stdout_full_exits_2(self):
        with open("/dev/full", "wb") as stdout:
            self.assertEqual(self.feed(b"{", stdout=stdout).returncode, 2)
            payload = self.raw({"tool_name": "Bash", "tool_input": {"command": f'rm "{_fwd(self.existing)}"'},
                                "cwd": self.repo})
            self.assertEqual(self.feed(payload, stdout=stdout).returncode, 2)

    def test_stderr_full_still_denies(self):
        with open("/dev/full", "wb") as stderr:
            self.assertHookDeniesUnexpected(self.feed(b"{", stderr=stderr))


class TestHookToolInputNotJudged(HookIOMixin, unittest.TestCase):
    """判定しないツールの tool_input の型は問わず，空の tool_input は入力なしとして扱う."""

    def test_string_tool_input_of_other_tools(self):
        for tool_name in ("TodoWrite", "WebSearch"):
            with self.subTest(tool_name):
                self.assertHookAllows(self.feed_tool(tool_name, "text"))

    def test_empty_list_tool_input(self):
        for tool_name in ("Bash", "PowerShell", "Read", "Edit", "Write", "TodoWrite"):
            with self.subTest(tool_name):
                self.assertHookAllows(self.feed_tool(tool_name, []))


class TestHookOutputFailure(HookIOMixin, unittest.TestCase):
    """stdout に書けなければ終了コード 2 でブロックし，stderr に書けなくても拒否は続く."""

    BROKEN_WRITE = ("class Broken(io.StringIO):\n"
                    "    def write(self, text):\n"
                    "        raise OSError('broken')\n"
                    "{stream} = Broken()\n")
    BROKEN_FLUSH = ("class Broken(io.StringIO):\n"
                    "    def flush(self):\n"
                    "        raise OSError('broken')\n"
                    "{stream} = Broken()\n")

    def deny_payloads(self):
        rm_outside = {"tool_name": "Bash", "tool_input": {"command": f'rm "{_fwd(self.existing)}"'}, "cwd": self.repo}
        nul = {"tool_name": "Bash", "tool_input": {"command": "echo a\0b"}, "cwd": self.repo}
        return {"normal deny": self.raw(rm_outside), "NUL deny": self.raw(nul), "broken json": b"{", "empty": b""}

    def test_stdout_unwritable_exits_2(self):
        for kind in self.UNWRITABLE_KINDS:
            for label, payload in self.deny_payloads().items():
                with self.subTest(kind=kind, payload=label), self.unwritable(kind) as stdout:
                    self.assertEqual(self.feed(payload, stdout=stdout).returncode, 2)

    def test_stdout_replaced_with_broken_stream_exits_2(self):
        setups = {"None": "sys.stdout = None\n", "write": self.BROKEN_WRITE.format(stream="sys.stdout"),
                  "flush": self.BROKEN_FLUSH.format(stream="sys.stdout")}
        for label, setup in setups.items():
            for name, payload in self.deny_payloads().items():
                with self.subTest(stream=label, payload=name):
                    self.assertEqual(self.feed_driver(setup, payload).returncode, 2)

    def test_stdout_unwritable_via_wrapper_exits_2(self):
        for kind in self.UNWRITABLE_KINDS:
            with self.subTest(kind), self.unwritable(kind) as stdout:
                self.assertEqual(self.feed_wrapper(b"{", stdout=stdout).returncode, 2)

    def test_allowed_input_does_not_need_stdout(self):
        data = {"tool_name": "Read", "tool_input": {"file_path": "file.txt"}, "cwd": self.repo}
        for kind in self.UNWRITABLE_KINDS:
            with self.subTest(kind), self.unwritable(kind) as stdout:
                self.assertEqual(self.feed(self.raw(data), stdout=stdout).returncode, 0)

    def test_stderr_unwritable_still_denies(self):
        for kind in self.UNWRITABLE_KINDS:
            with self.subTest(kind), self.unwritable(kind) as stderr:
                self.assertHookDeniesUnexpected(self.feed(b"{", stderr=stderr), kind)

    def test_stderr_unwritable_keeps_normal_deny(self):
        """stderr に何も書かない通常の拒否も，stderr の状態に影響されない."""
        data = {"tool_name": "Bash", "tool_input": {"command": f'rm "{_fwd(self.existing)}"'}, "cwd": self.repo}
        for kind in self.UNWRITABLE_KINDS:
            with self.subTest(kind), self.unwritable(kind) as stderr:
                self.assertHookDenies(self.feed(self.raw(data), stderr=stderr), kind)

    def test_stderr_unwritable_with_injected_exception_still_denies(self):
        setup = HOOK_RAISE.format(target="rra.check_shell_command")
        payload = self.raw({"tool_name": "Bash", "tool_input": {"command": "echo hi"}, "cwd": self.repo})
        for kind in self.UNWRITABLE_KINDS:
            with self.subTest(kind), self.unwritable(kind) as stderr:
                self.assertHookDeniesUnexpected(self.feed_driver(setup, payload, stderr=stderr), kind)

    def test_stderr_replaced_with_broken_stream_still_denies(self):
        setups = {"None": "sys.stderr = None\n", "write": self.BROKEN_WRITE.format(stream="sys.stderr")}
        for label, setup in setups.items():
            with self.subTest(label):
                self.assertHookDeniesUnexpected(self.feed_driver(setup, b"{"), label)

    def test_both_unwritable_exits_2(self):
        for kind in self.UNWRITABLE_KINDS:
            with self.subTest(kind), self.unwritable(kind) as stream:
                self.assertEqual(self.feed(b"{", stdout=stream, stderr=stream).returncode, 2)


class TestHookDoesNotCatchExit(HookIOMixin, unittest.TestCase):
    """SystemExit・KeyboardInterrupt は捕捉しない（拒否に変えない）."""

    def run_main_with(self, target: str, tool_name: str, tool_input: dict, error: BaseException):
        payload = self.raw({"tool_name": tool_name, "tool_input": tool_input, "cwd": self.repo})
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": self.repo}), \
                mock.patch.object(self.rra, target, side_effect=error), \
                mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload))), \
                mock.patch.object(sys, "stdout", stdout):
            try:
                self.rra.main()
            finally:
                self.captured_stdout = stdout.getvalue()

    def cases(self):
        return (("check_shell_command", "Bash", {"command": "echo hi"}),
                ("check_file_tool", "Read", {"file_path": "file.txt"}))

    def test_keyboard_interrupt_propagates(self):
        for target, tool_name, tool_input in self.cases():
            with self.subTest(target):
                with self.assertRaises(KeyboardInterrupt):
                    self.run_main_with(target, tool_name, tool_input, KeyboardInterrupt())
                self.assertEqual(self.captured_stdout, "")

    def test_system_exit_keeps_its_code(self):
        for target, tool_name, tool_input in self.cases():
            with self.subTest(target):
                with self.assertRaises(SystemExit) as caught:
                    self.run_main_with(target, tool_name, tool_input, SystemExit(7))
                self.assertEqual(caught.exception.code, 7)
                self.assertEqual(self.captured_stdout, "")

    def test_exit_code_of_real_process(self):
        setup = ("def leave(*args, **kwargs):\n"
                 "    raise SystemExit(7)\n"
                 "rra.check_shell_command = leave\n")
        payload = self.raw({"tool_name": "Bash", "tool_input": {"command": "echo hi"}, "cwd": self.repo})
        result = self.feed_driver(setup, payload)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, b"")


class TestHookNulCommand(HookIOMixin, unittest.TestCase):
    """Bash / PowerShell のコマンドの NUL は OS・対象によらず拒否する（main を通す）."""

    def assertNulDenied(self, result, label=""):
        reason = self.assertHookDenies(result, label)
        self.assertIn(NUL_REASON, reason, label)
        self.assertNotIn(UNEXPECTED_PREFIX, reason, label)
        self.assertEqual(result.stderr, b"", label)

    def commands(self):
        outside = _fwd(self.out)
        return (("Bash", "rm /tmp/a\0b"), ("Bash", "rm ./a\0b"), ("Bash", f"rm {outside}/zz/a\0b"),
                ("Bash", "echo a\0b"), ("PowerShell", "Remove-Item ./a\0b"), ("PowerShell", "Write-Output a\0b"),
                ("Bash", "\0"), ("Bash", "\0echo a"), ("Bash", "echo a\0"), ("Bash", "rm ./a\0"),
                ("Bash", "echo 日本語\0"), ("PowerShell", f'Remove-Item "{self.existing}\0"'))

    def test_denied_with_escaped_json(self):
        for tool_name, command in self.commands():
            with self.subTest(tool=tool_name, command=command):
                self.assertNulDenied(self.run_main({"tool_name": tool_name, "tool_input": {"command": command},
                                                    "cwd": self.repo}, self.hook_env()))

    def test_denied_with_raw_utf8_json(self):
        for tool_name, command in self.commands():
            with self.subTest(tool=tool_name, command=command):
                self.assertNulDenied(self.feed_tool(tool_name, {"command": command}))

    def test_denied_without_project_dir(self):
        for tool_name, command in self.commands():
            with self.subTest(tool=tool_name, command=command):
                self.assertNulDenied(self.feed_tool(tool_name, {"command": command}, self.hook_env(project_dir=False)))

    def test_denied_via_wrapper(self):
        payload = self.raw({"tool_name": "Bash", "tool_input": {"command": "rm ./a\0b"}, "cwd": self.repo})
        self.assertNulDenied(self.feed_wrapper(payload))

    def test_raw_nul_byte_in_json_text_is_denied(self):
        """JSON の文字列に生の NUL は書けない（壊れた JSON）．どちらの理由でも拒否される."""
        self.assertHookDenies(self.feed(b'{"tool_name": "Bash", "tool_input": {"command": "echo a\x00b"}}'))

    def test_nul_outside_command_is_not_judged(self):
        """NUL の拒否の対象はコマンド文字列だけ（Write の内容や説明文は判定しない）."""
        self.assertHookAllows(self.feed_tool("Bash", {"command": "echo hi", "description": "a\0b"}))
        self.assertHookAllows(self.feed_tool("Write", {"file_path": os.path.join(self.repo, "n.txt"), "content": "a\0b"}))


class TestHookReadsUtf8Bytes(HookIOMixin, unittest.TestCase):
    """入力はロケールの文字コードによらず UTF-8 の JSON として読む."""

    # None はロケールの既定（日本語の Windows なら cp932）のまま起動する
    ENCODINGS = ("cp932", "utf-8", "ascii", "latin-1", None)

    def setUp(self) -> None:
        super().setUp()
        self.named_existing = os.path.join(self.out, "既存語.txt")
        with open(self.named_existing, "w", encoding="utf-8") as fh:
            fh.write("x")
        with open(os.path.join(self.repo, "日本語.txt"), "w", encoding="utf-8") as fh:
            fh.write("x")

    def encoding_env(self, encoding) -> dict:
        env = self.hook_env()
        for key in ("PYTHONIOENCODING", "PYTHONUTF8"):
            env.pop(key, None)
        if encoding is not None:
            env["PYTHONIOENCODING"] = encoding
        return env

    def each_encoding(self):
        for encoding in self.ENCODINGS:
            yield encoding, self.encoding_env(encoding)

    def test_premise_cp932_text_mode_breaks_this_input(self):
        """再現の前提: `語` の直後の `\\"` は，cp932 のテキストモードで読むと JSON として壊れる."""
        payload = self.raw({"tool_name": "Bash", "tool_input": {"command": 'echo "日本語"'}, "cwd": self.repo})
        self.assertIn(b'\xe8\xaa\x9e\\"', payload)
        result = subprocess.run([sys.executable, "-B", "-c", "import json, sys; json.load(sys.stdin)"], input=payload,
                                capture_output=True, env=self.encoding_env("cp932"), timeout=TIMEOUT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"JSONDecodeError", result.stderr)

    def test_quote_right_after_japanese_is_allowed(self):
        repo_file = os.path.join(self.repo, "日本語.txt")
        cases = {
            "echo": ("Bash", {"command": 'echo "日本語"'}),
            "escaped quote": ("Bash", {"command": 'echo "語\\"語"'}),
            "commit": ("Bash", {"command": 'git commit -m "[fix] 日本語"'}),
            "rm in repo": ("Bash", {"command": 'rm "./語"'}),
            "PowerShell": ("PowerShell", {"command": 'Write-Output "日本語"'}),
            "Write in repo": ("Write", {"file_path": repo_file, "content": '"日本語"'}),
            "Write new outside": ("Write", {"file_path": os.path.join(self.out, "新規語.txt"), "content": '"語"'}),
            "Edit in repo": ("Edit", {"file_path": repo_file, "old_string": '"語"', "new_string": '"日本語"'}),
        }
        for label, (tool_name, tool_input) in cases.items():
            for encoding, env in self.each_encoding():
                with self.subTest(label, encoding=encoding):
                    payload = self.raw({"tool_name": tool_name, "tool_input": tool_input, "cwd": self.repo})
                    self.assertIn(b"\xe8\xaa\x9e", payload)
                    self.assertHookAllows(self.feed(payload, env), label)

    def test_japanese_without_quote_is_allowed(self):
        for encoding, env in self.each_encoding():
            with self.subTest(encoding=encoding):
                self.assertHookAllows(self.feed_tool("Bash", {"command": "echo 日本語"}, env))
                self.assertHookAllows(self.feed_tool("Read", {"file_path": self.named_existing}, env))

    def test_japanese_outside_is_denied(self):
        outside = _fwd(self.out)
        named = _fwd(self.named_existing)
        cases = {
            "rm": ("Bash", {"command": f'rm "{outside}/日本語"'}, "日本語"),
            "rm -rf": ("Bash", {"command": f'rm -rf "{outside}/語"'}, "語"),
            "rm unquoted": ("Bash", {"command": f"rm {outside}/日本語"}, "日本語"),
            "redirect": ("Bash", {"command": f'echo 語 > "{named}"'}, "既存語"),
            "Remove-Item": ("PowerShell", {"command": f'Remove-Item "{outside}/日本語"'}, "日本語"),
            "Edit": ("Edit", {"file_path": self.named_existing, "old_string": "語", "new_string": "語"}, "既存語"),
        }
        for label, (tool_name, tool_input, shown) in cases.items():
            for encoding, env in self.each_encoding():
                with self.subTest(label, encoding=encoding):
                    reason = self.assertHookDenies(self.feed_tool(tool_name, tool_input, env), label)
                    self.assertNotIn(UNEXPECTED_PREFIX, reason, label)
                    self.assertIn(shown, reason, "文字化けせずに読めている")

    def test_write_over_existing_japanese_file_is_denied(self):
        """既存ファイルかどうかの判定は，名前を正しく読めていないと許可に倒れる."""
        for encoding, env in self.each_encoding():
            with self.subTest(encoding=encoding):
                tool_input = {"file_path": self.named_existing, "content": "語"}
                reason = self.assertHookDenies(self.feed_tool("Write", tool_input, env))
                self.assertNotIn(UNEXPECTED_PREFIX, reason)

    def test_via_wrapper(self):
        allow = self.raw({"tool_name": "Bash", "tool_input": {"command": 'echo "日本語"'}, "cwd": self.repo})
        self.assertHookAllows(self.feed_wrapper(allow))
        target = _fwd(os.path.join(self.out, "日本語"))
        denied = self.raw({"tool_name": "Bash", "tool_input": {"command": f'rm "{target}"'}, "cwd": self.repo})
        self.assertNotIn(UNEXPECTED_PREFIX, self.assertHookDenies(self.feed_wrapper(denied)))

    def test_reason_is_ascii_safe_under_any_output_encoding(self):
        """拒否の JSON は stdout の文字コードによらず UTF-8 として読める（assertHookDenies が読む）."""
        for encoding, env in self.each_encoding():
            with self.subTest(encoding=encoding):
                self.assertIn("JSONDecodeError", self.assertHookDeniesUnexpected(self.feed(b"{", env)))


class TestHookNormalDecisionsViaMain(HookIOMixin, unittest.TestCase):
    """例外の捕捉で，正常な入力の判定は変わらない."""

    def test_read(self):
        self.assertHookAllows(self.feed_tool("Read", {"file_path": os.path.join(self.repo, "file.txt")}))
        self.assertHookAllows(self.feed_tool("Read", {"file_path": self.existing}))

    def test_edit(self):
        edit = {"old_string": "x", "new_string": "y"}
        self.assertHookAllows(self.feed_tool("Edit", dict(edit, file_path=os.path.join(self.repo, "file.txt"))))
        reason = self.assertHookDenies(self.feed_tool("Edit", dict(edit, file_path=self.existing)))
        self.assertNotIn(UNEXPECTED_PREFIX, reason)

    def test_bash_rm(self):
        self.assertHookAllows(self.feed_tool("Bash", {"command": "rm file.txt"}))
        reason = self.assertHookDenies(self.feed_tool("Bash", {"command": f'rm "{_fwd(self.existing)}"'}))
        self.assertNotIn(UNEXPECTED_PREFIX, reason)

    def test_powershell_remove_item(self):
        reason = self.assertHookDenies(self.feed_tool("PowerShell", {"command": f'Remove-Item "{self.existing}"'}))
        self.assertNotIn(UNEXPECTED_PREFIX, reason)

    def test_no_tool_name(self):
        self.assertHookAllows(self.feed(b"{}"))
        self.assertHookAllows(self.feed(self.raw({"tool_input": {"command": "echo hi"}, "cwd": self.repo})))

    def test_missing_or_null_tool_input(self):
        for tool_name in ("Bash", "Read", "Edit"):
            with self.subTest(tool_name):
                self.assertHookAllows(self.feed(self.raw({"tool_name": tool_name, "cwd": self.repo})))
                self.assertHookAllows(self.feed_tool(tool_name, None))

    def test_ascii_escaped_json_is_read_the_same(self):
        data = {"tool_name": "Bash", "tool_input": {"command": 'echo "日本語"'}, "cwd": self.repo}
        self.assertHookAllows(self.run_main(data, self.hook_env()))

    def test_nul_in_transcript_path_does_not_deny_other_decisions(self):
        """使えない transcript_path は無いものとして扱う（どのパスの判定でも拒否にはしない）."""
        transcript = {"transcript_path": os.path.join(self.base, "session\0.jsonl")}
        self.assertHookAllows(self.feed_tool("Read", {"file_path": "file.txt"}, **transcript))
        self.assertHookAllows(self.feed_tool("Bash", {"command": "rm file.txt"}, **transcript))
        reason = self.assertHookDenies(self.feed_tool("Bash", {"command": f'rm "{_fwd(self.existing)}"'}, **transcript))
        self.assertNotIn(UNEXPECTED_PREFIX, reason)


# ---------------------------------------------------------------------------
# restrict_repo_access.py: 書き込み許可ディレクトリ allow_write（Issue #64）
# ---------------------------------------------------------------------------
# サンドボックス（一時ディレクトリの外）に repo と並べて sib/ を置き，repo-access.json の
# allow_write に "../sib" を書いて判定する．設定は load_allow_write（main と同じ読み込み）で読む．
class AllowWriteSandboxMixin(Issue59SandboxMixin):
    """sandbox/
         repo/                    仮のリポジトリ
         sib/                     書き込み許可ディレクトリ（allow_write: ../sib）
           file.txt・sub/deep.txt・secret/key.txt
         sib2/file.txt            名前が前方一致する別のディレクトリ（許可しない）
         outside/                 リポジトリ外（許可しない）
    """

    def setUp(self) -> None:
        super().setUp()
        self.sib = os.path.join(self.base, "sib")
        self.sib_file = self.touch(self.sib, "file.txt")
        self.sib_deep = self.touch(self.sib, "sub", "deep.txt")
        self.sib_secret = self.touch(self.sib, "secret", "key.txt")
        self.sib2_file = self.touch(self.base, "sib2", "file.txt")
        self.s = _fwd(self.sib)
        self.allow = []

    def write_config(self, config) -> None:
        text = config if isinstance(config, str) else json.dumps(config)
        with open(os.path.join(self.repo, ".claude", "repo-access.json"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def load(self, config) -> tuple:
        """設定を書いて（仮のホームで）load_allow_write・load_deny_read を呼び，結果と stderr を返す."""
        self.write_config(config)
        err = io.StringIO()
        with self.fake_home(), contextlib.redirect_stderr(err):
            allow = self.rra.load_allow_write(self.repo)
            deny = self.rra.load_deny_read(self.repo)
        return allow, deny, err.getvalue()

    def use(self, config) -> str:
        """設定を読み込んで判定に使う（以降の bash・ps・tool に効く）."""
        self.allow, self.deny, err = self.load(config)
        return err

    def bash(self, command: str, cwd: str | None = None):
        return self.rra.check_bash_command(command, cwd or self.repo, repo_root=self.repo, deny_read=self.deny,
                                           transcript_path=self.transcript_path, allow_write=self.allow)

    def ps(self, command: str, cwd: str | None = None):
        return self.rra.check_powershell_command(command, cwd or self.repo, repo_root=self.repo, deny_read=self.deny,
                                                 transcript_path=self.transcript_path, allow_write=self.allow)

    def tool(self, name: str, tool_input: dict, cwd: str | None = None):
        policy = self.rra.Policy(self.repo, self.deny, self.transcript_path, self.allow)
        return self.rra.check_file_tool(name, tool_input, cwd or self.repo, policy)

    def assertSame(self, loaded: list, expected: list) -> None:
        normed = [os.path.normcase(os.path.realpath(p)) for p in expected]
        self.assertEqual([os.path.normcase(p) for p in loaded], normed)


class TestAllowWriteLoad(AllowWriteSandboxMixin, unittest.TestCase):
    """設定の書き方（deny_read と同じ）と，危険・無効な指定の無視と警告."""

    def test_relative_sibling_entry(self):
        allow, _, err = self.load({"allow_write": ["../sib"]})
        self.assertSame(allow, [self.sib])
        self.assertEqual(err, "")

    def test_absolute_entries(self):
        for entry in (self.sib, _fwd(self.sib), _fwd(self.sib) + "/"):
            with self.subTest(entry):
                allow, _, err = self.load({"allow_write": [entry]})
                self.assertSame(allow, [self.sib])
                self.assertEqual(err, "")

    def test_home_entry(self):
        work = os.path.join(self.home, "work")
        os.makedirs(work)
        allow, _, err = self.load({"allow_write": ["~/work"]})
        self.assertSame(allow, [work])
        self.assertEqual(err, "")

    def test_repo_root_relative_entry(self):
        """リポジトリルート相対で解決する."""
        allow, _, _ = self.load({"allow_write": ["../outside/dir"]})
        self.assertSame(allow, [os.path.join(self.out, "dir")])

    @unittest.skipUnless(os.name == "nt", "Git Bash 形式のパス（/c/...）は Windows 固有のため")
    def test_gitbash_form_entry(self):
        allow, _, _ = self.load({"allow_write": [_gitbash(self.sib)]})
        self.assertSame(allow, [self.sib])

    def test_missing_config_or_key_is_empty_without_warning(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.rra.load_allow_write(self.repo), [])
        self.assertEqual(err.getvalue(), "")
        allow, _, err = self.load({"deny_read": ["~/.ssh"]})
        self.assertEqual(allow, [])
        self.assertEqual(err, "")

    def test_broken_config_is_empty_with_warning(self):
        """壊れた設定は空扱い（許可側に倒れない）．deny_read が不正なときも allow_write を空にする."""
        configs = ["{broken", json.dumps(["../sib"]), json.dumps("../sib"),
                   json.dumps({"deny_read": "~/.ssh", "allow_write": ["../sib"]}),
                   json.dumps({"deny_read": [1], "allow_write": ["../sib"]}),
                   json.dumps({"deny_read": None, "allow_write": ["../sib"]})]
        for text in configs:
            with self.subTest(text):
                allow, _, err = self.load(text)
                self.assertEqual(allow, [])
                self.assertTrue(err.strip(), "stderr に警告が出ること")

    def test_non_list_allow_write_is_ignored_with_warning(self):
        for value in ("../sib", {"a": "../sib"}, 1, True, None):
            with self.subTest(repr(value)):
                allow, _, err = self.load({"allow_write": value})
                self.assertEqual(allow, [])
                self.assertTrue(err.strip(), "stderr に警告が出ること")

    def test_non_string_element_is_ignored_but_others_kept(self):
        allow, _, err = self.load({"allow_write": [1, None, ["../sib"], "../sib"]})
        self.assertSame(allow, [self.sib])
        self.assertTrue(err.strip(), "stderr に警告が出ること")

    def assertIgnored(self, entries) -> None:
        """危険な指定は無視して警告し，同時に書いた正しい指定（../sib）は効くこと."""
        for entry in entries:
            with self.subTest(entry=entry):
                allow, _, err = self.load({"allow_write": [entry, "../sib"]})
                self.assertSame(allow, [self.sib])
                self.assertTrue(err.strip(), f"stderr に警告が出ること: {entry}")

    def test_filesystem_root_is_ignored(self):
        self.assertIgnored(["/", os.path.abspath(os.sep), _fwd(os.path.abspath(os.sep))])

    def test_home_itself_and_ancestors_are_ignored(self):
        self.assertIgnored(["~", "~/", self.home, _fwd(self.home) + "/", "~/.."])

    def test_dir_containing_home_is_ignored(self):
        """ホームの祖先（allow_write に書いたディレクトリの中にホームがある）は無視する."""
        home = os.path.join(self.sib, "userhome")
        os.makedirs(home)
        self.write_config({"allow_write": ["../sib"]})
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}), contextlib.redirect_stderr(err):
            self.assertEqual(self.rra.load_allow_write(self.repo), [])
        self.assertTrue(err.getvalue().strip())

    def test_repo_itself_and_ancestors_are_ignored(self):
        self.assertIgnored(["..", "../", ".", "./", "../..", self.repo, self.base, _fwd(self.base), "../sib/.."])

    def test_nonexistent_or_file_is_ignored(self):
        self.assertIgnored(["../no_such_dir", os.path.join(self.base, "no_such_dir"), "../sib/file.txt",
                            self.existing])

    def test_claude_config_folder_is_ignored(self):
        os.makedirs(os.path.join(self.home, ".claude", "projects"), exist_ok=True)
        self.assertIgnored(["~/.claude", "~/.claude/projects", os.path.join(self.home, ".claude")])

    def test_wildcard_brace_and_user_home_are_ignored(self):
        self.assertIgnored(["../si*", "../si?", "../s[i]b", "../{sib,outside}", "../sib{,2}", "~nosuchuser_x/dir"])

    def test_empty_string_is_ignored(self):
        allow, _, _ = self.load({"allow_write": ["", "   ", "../sib"]})
        self.assertSame(allow, [self.sib])

    def test_symlinked_allow_dir_is_resolved(self):
        link = os.path.join(self.base, "siblink")
        self.link_dir(self.sib, link)
        allow, _, _ = self.load({"allow_write": ["../siblink"]})
        self.assertSame(allow, [self.sib])

    def test_link_to_dangerous_dir_is_ignored(self):
        """リンク経由で書いてもリンク先（リポジトリの親）で判定する."""
        link = os.path.join(self.out, "to_base")
        self.link_dir(self.base, link)
        allow, _, err = self.load({"allow_write": ["../outside/to_base"]})
        self.assertEqual(allow, [])
        self.assertTrue(err.strip())


class TestAllowWriteOperations(AllowWriteSandboxMixin, unittest.TestCase):
    """allow_write の配下はリポジトリ外でも上書き・Edit・削除・移動を許可する."""

    def setUp(self) -> None:
        super().setUp()
        self.use({"allow_write": ["../sib"]})
        self.assertTrue(self.allow)

    def test_all_write_ops_allowed_inside(self):
        self.assertAllWriteOpsAllowed(self.sib_file)
        self.assertAllWriteOpsAllowed(self.sib_deep)

    def test_notebook_edit_allowed_inside(self):
        self.assertAllowed(self.tool("NotebookEdit", {"notebook_path": self.sib_file}))

    def test_delete_and_move_subdirectory(self):
        sub = _fwd(os.path.join(self.sib, "sub"))
        self.assertBashAllowed([f'rm -rf "{sub}"', f'mv "{sub}" "{self.s}/sub2"', f'rm -r "{self.s}/sub/"',
                                f'cp -r "{self.r}/sub" "{self.s}/sub"'])
        self.assertPsAllowed([f'Remove-Item "{os.path.join(self.sib, "sub")}" -Recurse -Force',
                              f'Move-Item "{os.path.join(self.sib, "sub")}" "{os.path.join(self.sib, "sub2")}"'])

    def test_cd_into_allow_dir_then_relative(self):
        self.assertBashAllowed([f'cd "{self.s}" && rm file.txt', f'cd "{self.s}" && rm -rf sub',
                                f'cd "{self.s}" && echo y > file.txt'])
        self.assertBashAllowed(["rm file.txt", "rm -rf sub", "sed -i s/x/y/ sub/deep.txt"], cwd=self.sib)
        self.assertPsAllowed([f'Set-Location "{self.sib}"; Remove-Item file.txt'])

    def test_allow_dir_itself_is_not_deletable(self):
        self.assertBashDenied([f'rm -rf "{self.s}"', f'rm -rf "{self.s}/"', f'rm -rf "{self.s}/."',
                               f'mv "{self.s}" "{self.o}/moved"', f'cd "{self.s}" && rm -rf .'])
        self.assertPsDenied([f'Remove-Item "{self.sib}" -Recurse -Force',
                             f'Move-Item "{self.sib}" "{os.path.join(self.out, "moved")}"'])

    def test_move_or_copy_onto_existing_outside_is_denied(self):
        self.assertBashDenied([f'mv "{_fwd(self.sib_file)}" "{_fwd(self.existing)}"',
                               f'cp "{_fwd(self.sib_file)}" "{_fwd(self.existing)}"'])

    def test_outside_and_similar_names_still_denied(self):
        self.assertAllWriteOpsDenied(self.existing)
        self.assertAllWriteOpsDenied(self.sib2_file)

    def test_ambiguous_paths_are_denied(self):
        s = self.s
        self.assertBashDenied([
            f'rm "{s}"/{{file.txt,sub/deep.txt}}',
            f'rm {s}/{{file.txt,x}}',
            f'rm "{s}/sub/../file.txt"',
            f'rm {s}/*/deep.txt',
            f'echo y > "{s}/sub/../file.txt"',
            f'cd {s}/* && rm deep.txt',
            f'cd "{s}/sub/.." && rm file.txt',
        ])
        self.assertDenied(self.tool("Edit", {"file_path": os.path.join(self.sib, "sub", "..", "file.txt")}))

    def test_relative_dotdot_in_command_is_denied(self):
        """コマンド中の `..`（../sib/...）は文字列から対象を確定できないとして許可しない（Issue #64）."""
        self.assertBashDenied(["rm ../sib/file.txt", "echo y > ../sib/file.txt"])
        self.assertPsDenied(["Remove-Item ../sib/file.txt"])

    def test_link_escaping_allow_dir_is_denied(self):
        link = os.path.join(self.sib, "escape")
        self.link_dir(os.path.join(self.out, "dir"), link)
        self.assertAllWriteOpsDenied(os.path.join(link, "file.txt"))
        self.assertBashDenied([f'rm -rf "{_fwd(link)}/"'])

    @unittest.skipIf(os.name == "nt", "リンク自体を消すだけかの判定は POSIX のシンボリックリンクで確かめる"
                                      "（Windows はシンボリックリンク作成に権限が要り，ジャンクションでは rm の挙動が異なるため）")
    def test_removing_link_itself_is_allowed(self):
        link = os.path.join(self.sib, "escape")
        os.symlink(os.path.join(self.out, "dir"), link, target_is_directory=True)
        self.assertBashAllowed([f'rm "{_fwd(link)}"', f'rm -f "{_fwd(link)}"', "rm escape"], cwd=self.sib)
        filelink = os.path.join(self.sib, "filelink")
        os.symlink(self.existing, filelink)
        self.assertBashAllowed([f'rm "{_fwd(filelink)}"'])
        self.assertBashDenied([f'echo y > "{_fwd(filelink)}"', f'sed -i s/x/y/ "{_fwd(filelink)}"'])
        self.assertDenied(self.tool("Edit", {"file_path": filelink}))

    def test_access_via_link_to_allow_dir_is_allowed(self):
        """実パスで判定するので，許可ディレクトリを指すリンク経由の操作は許可する."""
        link = os.path.join(self.out, "to_sib")
        self.link_dir(self.sib, link)
        self.assertAllowed(self.tool("Edit", {"file_path": os.path.join(link, "file.txt")}))
        self.assertAllowed(self.bash(f'rm "{_fwd(link)}/file.txt"'))

    def test_deny_read_takes_precedence(self):
        self.use({"deny_read": ["../sib/secret"], "allow_write": ["../sib"]})
        self.assertAllWriteOpsDenied(self.sib_secret)
        self.assertBashDenied([f'rm -rf "{self.s}/secret"', f'cat "{_fwd(self.sib_secret)}"'])
        self.assertAllWriteOpsAllowed(self.sib_file)

    def test_without_allow_write_behavior_unchanged(self):
        for config in ({"deny_read": []}, {}, {"allow_write": []}):
            with self.subTest(config=config):
                self.use(config)
                self.assertAllWriteOpsDenied(self.sib_file)
        self.assertAllowed(self.tool("Write", {"file_path": os.path.join(self.sib, "new.txt")}))
        self.assertAllowed(self.tool("Read", {"file_path": self.sib_file}))
        self.assertAllowed(self.bash("rm -rf sub"))

    def test_invalid_deny_read_disables_allow_write(self):
        self.use({"deny_read": [1], "allow_write": ["../sib"]})
        self.assertAllWriteOpsDenied(self.sib_file)

    def test_ignored_entries_grant_nothing(self):
        self.use({"allow_write": ["..", "~", "/", "../si*", "../{sib,outside}"]})
        self.assertEqual(self.allow, [])
        self.assertAllWriteOpsDenied(self.sib_file)
        self.assertAllWriteOpsDenied(self.existing)

    def test_does_not_extend_to_other_zones(self):
        """allow_write は他プロジェクトのメモ・~/.claude の判定を変えない."""
        self.assertAllWriteOpsDenied(self.other_note)
        self.assertAllWriteOpsDenied(self.settings)


@unittest.skipUnless(os.name == "nt", "パスの大文字小文字の同一視・Git Bash 形式は Windows 固有のため")
class TestAllowWriteWindowsCase(AllowWriteSandboxMixin, unittest.TestCase):
    def test_case_difference_in_config_and_command(self):
        self.use({"allow_write": ["../SIB"]})
        self.assertTrue(self.allow)
        self.assertAllWriteOpsAllowed(os.path.join(self.base, "SIB", "FILE.TXT"))
        self.assertAllWriteOpsAllowed(self.sib_file)
        self.assertAllWriteOpsDenied(self.sib2_file)
        self.assertBashDenied([f'rm -rf "{_fwd(os.path.join(self.base, "SIB"))}"'])

    def test_gitbash_form_in_command(self):
        self.use({"allow_write": ["../sib"]})
        self.assertAllowed(self.bash(f'rm "{_gitbash(self.sib_file)}"'))


class TestAllowWriteViaMain(AllowWriteSandboxMixin, HookIOMixin, unittest.TestCase):
    """main に標準入力で JSON を渡す経路で allow_write が読まれること."""

    def inputs(self, path: str) -> dict:
        f = _fwd(path)
        return {
            "Edit": ("Edit", {"file_path": path, "old_string": "x", "new_string": "y"}),
            "Write": ("Write", {"file_path": path, "content": "y"}),
            "Bash rm": ("Bash", {"command": f'rm "{f}"'}),
            "Bash mv": ("Bash", {"command": f'mv "{f}" "{f}.moved"'}),
            "Bash redirect": ("Bash", {"command": f'echo y > "{f}"'}),
            "PowerShell Remove-Item": ("PowerShell", {"command": f'Remove-Item "{path}"'}),
        }

    def assertAllAllowedViaMain(self, path: str) -> None:
        for label, (name, tool_input) in self.inputs(path).items():
            with self.subTest(label, path=path):
                self.assertHookAllows(self.feed_tool(name, tool_input), label)

    def assertAllDeniedViaMain(self, path: str) -> None:
        for label, (name, tool_input) in self.inputs(path).items():
            with self.subTest(label, path=path):
                self.assertHookDenies(self.feed_tool(name, tool_input), label)

    def test_allowed_with_config(self):
        self.write_config({"allow_write": ["../sib"]})
        self.assertAllAllowedViaMain(self.sib_file)
        self.assertAllDeniedViaMain(self.existing)
        self.assertHookDenies(self.feed_tool("Bash", {"command": f'rm -rf "{self.s}"'}))

    def test_denied_without_allow_write(self):
        self.write_config({"deny_read": []})
        self.assertAllDeniedViaMain(self.sib_file)

    def test_deny_read_wins_via_main(self):
        self.write_config({"deny_read": ["../sib/secret"], "allow_write": ["../sib"]})
        self.assertAllDeniedViaMain(self.sib_secret)
        self.assertAllAllowedViaMain(self.sib_file)

    def test_broken_deny_read_disables_allow_write_via_main(self):
        self.write_config({"deny_read": "~/.ssh", "allow_write": ["../sib"]})
        self.assertAllDeniedViaMain(self.sib_file)

    def test_dangerous_entry_warns_and_valid_entry_still_works(self):
        self.write_config({"allow_write": ["..", "../sib"]})
        result = self.feed_tool("Bash", {"command": f'rm "{_fwd(self.sib_file)}"'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), b"")
        self.assertTrue(result.stderr.strip(), "stderr に警告が出ること")
        self.assertHookDenies(self.feed_tool("Bash", {"command": f'rm "{_fwd(self.existing)}"'}))

    def test_parent_entry_does_not_allow_outside(self):
        self.write_config({"allow_write": [".."]})
        self.assertAllDeniedViaMain(self.existing)
        self.assertAllDeniedViaMain(self.sib_file)

    def test_warning_unwritable_is_denied(self):
        """警告を書けないときは判定できないものとして拒否する（リポジトリ内の操作でも）."""
        self.write_config({"allow_write": ["..", "../sib"]})
        payload = self.raw({"tool_name": "Bash", "tool_input": {"command": "rm file.txt"}, "cwd": self.repo})
        for kind in self.UNWRITABLE_KINDS:
            with self.subTest(kind), self.unwritable(kind) as stderr:
                self.assertHookDeniesUnexpected(self.feed(payload, stderr=stderr), kind)
        self.assertHookDenies(self.feed_driver("sys.stderr = None\n", payload))

    def test_via_wrapper_like_settings_json(self):
        self.write_config({"allow_write": ["../sib"]})
        data = {"tool_name": "Edit", "tool_input": {"file_path": self.sib_file, "old_string": "x", "new_string": "y"},
                "cwd": self.repo}
        result = self.feed_wrapper(self.raw(data))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), b"")
        data["tool_input"]["file_path"] = self.existing
        self.assertHookDenies(self.feed_wrapper(self.raw(data)))


if __name__ == "__main__":
    unittest.main()
