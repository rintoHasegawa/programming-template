"""共通層のテストが team 層ファイルに依存しないことのテスト（Issue #43）.

実行（リポジトリルートで）:
    python -B -m unittest discover -s .claude/tests -v

仕様の正本:
    1. docs/01_GUIDE/GUIDE_01_プロジェクト立ち上げフロー.md「開発モードとファイル構成」のレイヤ表
       （共通層は solo・team 両方に存在する／team 層は team のみ）
    2. .claude/skills/sync-template/SKILL.md「モード依存ファイル」＋ reference.md の
       `TEAM_LAYER_FILES`（team 層は solo プロジェクトに配置・更新・削除いずれもしない）
       および「同期対象外ファイル」＝ `SKIP_FILES`（`.claude/tests/` のみ．`.claude/hooks/tests/` は同期される）
    3. Issue #43「solo モードのプロジェクトで test_hooks.py の TestCheckSync が必ず失敗する」
       （期待する挙動: solo では 3 件が skip，team では従来どおり実行される）

仕様から導かれる検証対象の性質:
    `.claude/hooks/tests/test_hooks.py` は共通層として solo プロジェクトにも配布されるため，
    team 層ファイル（`check_sync.sh`）を前提とするテストは，そのファイルが無い環境では
    「失敗」ではなく「skip」にならなければならない．逆にファイルがある環境（team・本テンプレート）
    では skip されず実行されなければならない．

検証方法:
    一時ディレクトリに solo 構成（team 層ファイル無し）と team 構成（有り）の疑似プロジェクトを作り，
    そこで `test_hooks.py` を子プロセスで走らせて振る舞いを観測する．実装（skipUnless か skipTest か）
    ではなく，外から見える結果（skip されるか／失敗しないか）を確認する．

`.claude/tests/` はテンプレート自身のメタテスト置き場であり，`/sync-template` の同期対象外
（`SKIP_FILES`）として派生プロジェクトには配布しない．
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))

HOOKS_DIR = os.path.join(REPO_ROOT, ".claude", "hooks")
HOOKS_TESTS_DIR = os.path.join(HOOKS_DIR, "tests")
TEST_HOOKS = os.path.join(HOOKS_TESTS_DIR, "test_hooks.py")
SYNC_SKILL = os.path.join(REPO_ROOT, ".claude", "skills", "sync-template", "SKILL.md")
SYNC_REFERENCE = os.path.join(REPO_ROOT, ".claude", "skills", "sync-template", "reference.md")
GUIDE_01 = os.path.join(REPO_ROOT, "docs", "01_GUIDE", "GUIDE_01_プロジェクト立ち上げフロー.md")

TIMEOUT = 300


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def team_layer_files() -> list[str]:
    """仕様（sync-template/reference.md の TEAM_LAYER_FILES）から team 層ファイルの一覧を読む."""
    text = read(SYNC_REFERENCE)
    m = re.search(r"TEAM_LAYER_FILES=\((.*?)\)", text, flags=re.DOTALL)
    assert m is not None, "reference.md に TEAM_LAYER_FILES の定義が見つからない"
    return re.findall(r'"([^"]+)"', m.group(1))


def team_layer_hook_files() -> list[str]:
    """team 層のうち `.claude/hooks/` 配下のもの（共通層テストが触れうる対象）."""
    return [f for f in team_layer_files() if f.startswith(".claude/hooks/")]


def sim_env() -> dict:
    """疑似プロジェクトで test_hooks.py を動かすための環境変数.

    test_hooks.py はモジュール読み込み時に bash を探し，見つからないと SkipTest を投げる
    （= 観測したい skip 件数がマシン依存になる）．HOOK_TEST_BASH はその探索を上書きする
    テスト用フックなので，ダミー値を渡して結果を決定的にする．solo・team いずれの検証でも
    実際に bash を起動するテストは実行しないため，値は使われない．
    """
    env = dict(os.environ)
    env["HOOK_TEST_BASH"] = os.path.join("no-such-dir", "bash")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def make_project(root: str, include_team_layer: bool) -> str:
    """疑似プロジェクトを作る．共通層（hooks のテスト等）を配置し，team 層は指定に応じて置く.

    戻り値はプロジェクトルート（`.claude/` の親）．
    """
    project = os.path.join(root, "team" if include_team_layer else "solo")
    dest_hooks = os.path.join(project, ".claude", "hooks")
    shutil.copytree(HOOKS_DIR, dest_hooks, ignore=shutil.ignore_patterns("__pycache__"))
    if not include_team_layer:
        for rel_path in team_layer_hook_files():
            target = os.path.join(project, *rel_path.split("/"))
            if os.path.exists(target):
                os.remove(target)
    return project


def run_check_sync_tests(project: str) -> subprocess.CompletedProcess:
    """疑似プロジェクトで check_sync 関連のテストだけを実行する."""
    tests_dir = os.path.join(project, ".claude", "hooks", "tests")
    return subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", tests_dir, "-k", "CheckSync", "-v"],
        capture_output=True,
        cwd=project,
        env=sim_env(),
        timeout=TIMEOUT,
    )


INSPECT_SCRIPT = """
import json, os, sys
sys.path.insert(0, sys.argv[1])
import test_hooks

cls = getattr(test_hooks, "TestCheckSync", None)
print(json.dumps({
    "found": cls is not None,
    "skipped": bool(getattr(cls, "__unittest_skip__", False)),
    "reason": str(getattr(cls, "__unittest_skip_why__", "")),
    "check_sync_path_exists": os.path.exists(test_hooks.CHECK_SYNC),
}))
"""


def inspect_check_sync_class(project: str) -> dict:
    """疑似プロジェクトで test_hooks を読み込み，TestCheckSync の skip 状態を観測する."""
    tests_dir = os.path.join(project, ".claude", "hooks", "tests")
    result = subprocess.run(
        [sys.executable, "-B", "-c", INSPECT_SCRIPT, tests_dir],
        capture_output=True,
        cwd=project,
        env=sim_env(),
        timeout=TIMEOUT,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    assert result.returncode == 0, "test_hooks の読み込みに失敗した:\n" + stderr
    return json.loads(stdout.strip().splitlines()[-1])


class TempProjectMixin:
    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp(prefix="mode-layers-")
        self.addCleanup(shutil.rmtree, self.root, True)


# ---------------------------------------------------------------------------
# 前提: 共通層／team 層の切り分けが仕様どおりであること
# ---------------------------------------------------------------------------
class TestLayerPremises(unittest.TestCase):
    def test_check_sync_is_team_layer(self):
        """check_sync.sh は team 層として仕様の 3 箇所（reference・SKILL・GUIDE_01）に載っている."""
        self.assertIn(".claude/hooks/check_sync.sh", team_layer_files())
        self.assertRegex(read(SYNC_SKILL), r"\|\s*`\.claude/hooks/check_sync\.sh`\s*\|\s*team\s*\|")
        self.assertIn(".claude/hooks/check_sync.sh", read(GUIDE_01))

    def test_hooks_tests_are_synced_to_all_projects(self):
        """`.claude/hooks/tests/` は同期対象外でも team 層でもない（= 共通層として solo にも配る）."""
        skip_files = re.search(r"SKIP_FILES=\((.*?)\)", read(SYNC_REFERENCE), flags=re.DOTALL)
        self.assertIsNotNone(skip_files, "reference.md に SKIP_FILES の定義が見つからない")
        entries = re.findall(r'"([^"]+)"', skip_files.group(1))
        for entry in entries:
            self.assertFalse(
                ".claude/hooks/tests/".startswith(entry),
                "`.claude/hooks/tests/` が同期対象外になっている: " + entry,
            )
        for entry in team_layer_files():
            self.assertFalse(
                ".claude/hooks/tests/test_hooks.py".startswith(entry.rstrip("/")),
                "test_hooks.py が team 層扱いになっている: " + entry,
            )


# ---------------------------------------------------------------------------
# 本体: solo（team 層無し）では skip，team（有り）では実行される
# ---------------------------------------------------------------------------
class TestCheckSyncGuard(TempProjectMixin, unittest.TestCase):
    def test_solo_project_skips_instead_of_failing(self):
        """team 層が無い構成でも失敗せず，check_sync のテストがすべて skip される（Issue #43 の期待値）."""
        project = make_project(self.root, include_team_layer=False)
        result = run_check_sync_tests(project)
        output = result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, "team 層が無い環境でテストが失敗した:\n" + output)
        self.assertNotIn("FAILED", output, output)
        ran = re.search(r"Ran (\d+) tests?", output)
        self.assertIsNotNone(ran, output)
        self.assertGreaterEqual(int(ran.group(1)), 3, "check_sync のテストが収集されていない:\n" + output)
        skipped = re.search(r"OK \(skipped=(\d+)\)", output)
        self.assertIsNotNone(skipped, "skip されずに実行されている:\n" + output)
        self.assertEqual(
            int(skipped.group(1)),
            int(ran.group(1)),
            "team 層が無い環境で実行されてしまったテストがある:\n" + output,
        )

    def test_solo_skip_reason_mentions_team_layer(self):
        """skip の理由が「team 層ファイルが無いこと」だと分かる（原因不明の skip にしない）."""
        project = make_project(self.root, include_team_layer=False)
        info = inspect_check_sync_class(project)
        self.assertTrue(info["found"], "TestCheckSync が見つからない")
        self.assertFalse(info["check_sync_path_exists"], "疑似 solo 構成に check_sync.sh が残っている")
        self.assertTrue(info["skipped"], "team 層が無いのに skip されない: " + json.dumps(info))
        self.assertTrue(
            re.search(r"check_sync|team", info["reason"]),
            "skip 理由から team 層依存だと分からない: " + repr(info["reason"]),
        )

    def test_team_project_runs_the_tests(self):
        """team 層がある構成では skip されない（ガードが過剰に効いていない）."""
        project = make_project(self.root, include_team_layer=True)
        info = inspect_check_sync_class(project)
        self.assertTrue(info["found"], "TestCheckSync が見つからない")
        self.assertTrue(info["check_sync_path_exists"], "疑似 team 構成に check_sync.sh が無い")
        self.assertFalse(info["skipped"], "team 層があるのに skip されている: " + json.dumps(info))

    def test_template_itself_runs_the_tests(self):
        """本テンプレート（team 層を含む正本）でも skip されない．"""
        info = inspect_check_sync_class(REPO_ROOT)
        self.assertTrue(info["check_sync_path_exists"])
        self.assertFalse(info["skipped"], "テンプレート自身で TestCheckSync が skip されている")


# ---------------------------------------------------------------------------
# 一般化: 共通層テストが team 層ファイルに無防備に依存していないこと
# ---------------------------------------------------------------------------
class TestCommonTestsDoNotRequireTeamLayer(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse(read(TEST_HOOKS), filename=TEST_HOOKS)

    def team_layer_constants(self) -> dict[str, str]:
        """モジュール直下の定数のうち，team 層ファイルを指すもの（定数名 -> 相対パス）."""
        basenames = {os.path.basename(f): f for f in team_layer_hook_files()}
        found = {}
        for node in self.tree.body:
            if not isinstance(node, ast.Assign):
                continue
            literals = [n.value for n in ast.walk(node.value) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            for name, rel_path in basenames.items():
                if name in literals:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            found[target.id] = rel_path
        return found

    def test_team_layer_paths_are_referenced_by_constants(self):
        """前提の確認: team 層ファイルはモジュール定数として定義されている（未定義なら依存が無い）."""
        constants = self.team_layer_constants()
        self.assertIn(
            "CHECK_SYNC",
            constants,
            "check_sync.sh を指す定数が test_hooks.py に見つからない（仕様の前提が変わった可能性）",
        )

    def test_classes_using_team_layer_files_are_guarded(self):
        """team 層ファイルを使うテストクラスには存在ガード（skipUnless/skipIf）が付いている."""
        constants = self.team_layer_constants()
        self.assertTrue(constants, "team 層ファイルを指す定数が無い")
        checked = 0
        for node in self.tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} & set(constants)
            if not used:
                continue
            checked += 1
            with self.subTest(cls=node.name, uses=sorted(used)):
                decorators = []
                for dec in node.decorator_list:
                    target = dec.func if isinstance(dec, ast.Call) else dec
                    decorators.append(target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", ""))
                self.assertTrue(
                    {"skipUnless", "skipIf"} & set(decorators),
                    node.name + " が team 層ファイル " + ", ".join(sorted(used)) + " を存在ガード無しで使っている",
                )
                guard_source = " ".join(ast.dump(dec) for dec in node.decorator_list)
                self.assertIn(
                    "exists",
                    guard_source,
                    node.name + " のガードがファイルの存在判定になっていない",
                )
        self.assertGreater(checked, 0, "team 層ファイルを使うテストクラスが見つからない")

    def test_no_module_level_access_to_team_layer_files(self):
        """モジュール読み込み時点で team 層ファイルを開かない（インポートだけで壊れない）."""
        constants = self.team_layer_constants()
        for node in self.tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
                func = call.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name not in ("open", "run", "check_output", "check_call"):
                    continue
                used = {n.id for n in ast.walk(call) if isinstance(n, ast.Name)} & set(constants)
                self.assertFalse(
                    used,
                    "モジュール直下で team 層ファイルを使っている: " + ", ".join(sorted(used)),
                )


if __name__ == "__main__":
    unittest.main()
