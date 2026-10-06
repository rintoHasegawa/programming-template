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

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    # --- 判定ヘルパー ---
    def bash(self, command: str, cwd: str | None = None):
        return self.rra.check_bash_command(command, cwd or self.repo, repo_root=self.repo, deny_read=self.deny)

    def ps(self, command: str, cwd: str | None = None):
        return self.rra.check_powershell_command(command, cwd or self.repo, repo_root=self.repo, deny_read=self.deny)

    def tool(self, name: str, tool_input: dict, cwd: str | None = None):
        policy = self.rra.Policy(self.repo, self.deny)
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


if __name__ == "__main__":
    unittest.main()
