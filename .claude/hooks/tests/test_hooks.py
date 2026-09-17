"""`.claude/hooks/` のフック起動まわりのテスト（Issue #35: Python フックの起動ラッパー）.

実行（リポジトリルートで）:
    python -B -m unittest discover -s .claude/hooks/tests -v

外部依存なし（標準ライブラリの unittest のみ）．bash が必要（Windows では Git for Windows の bash）．
notify.py は NTFY_TOPIC 未設定，またはローカルの一時 HTTP サーバー宛てでのみ実行し，ntfy.sh には送信しない．
"""

from __future__ import annotations

import ast
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

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

    def test_blocks_read_outside_repo(self):
        data = {"tool_name": "Read", "tool_input": {"file_path": self.OUTSIDE}, "cwd": REPO_ROOT}
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


if __name__ == "__main__":
    unittest.main()
