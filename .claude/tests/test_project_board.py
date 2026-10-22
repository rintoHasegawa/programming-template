"""Project ボードをオプトイン化（`.claude/project-board`）したことのテスト（Issue #56）.

実行（リポジトリルートで）:
    python -B -m unittest discover -s .claude/tests -v

仕様の正本（Issue #56 の「方針」「完了条件」）:
    1. 既定では Projects に一切触れない（`gh project` を呼ばない．`project` スコープも案内しない）
    2. `.claude/project-board` が存在するときだけ Projects 操作を行う．書式は `owner=<所有者>` / `number=<番号>`
    3. 番号を明示するため `gh project list` による探索は不要（「Project が複数あると停止」も解消）
    4. ボードの有効化は `/set-mode` とは独立
    5. task-start/reference.md の /task-create・/task-start・/task-handoff の各手順は最初に
       `.claude/project-board` の有無を判定する．無ければボード操作を飛ばし，進行中作業は
       `gh issue list --assignee @me --state open` で数える．あればファイルの owner/number に対し
       Projects 手順を行い，スコープエラー時は `gh auth refresh -s project` を案内して停止
    6. 「必要なスコープ」: `project` スコープは `.claude/project-board` を置いた場合のみ必要
    7. 3 つの SKILL.md: ファイルが無ければ「ボード未使用」と表記
    8. GUIDE_03: 「Project ボード」節は `.claude/project-board` を置いたときのみ使う．
       「管理者の初期設定」にボード開始手順（Project 作成 → `.claude/project-board` 作成 →
       全員が `gh auth refresh -s project`）
    9. `/sync-template`: `.claude/project-board` はプロジェクト所有ファイル（テンプレートに含めず，同期で触らない）

対象は実行コードではなく指示書（Markdown）であるため，各テストは「仕様が要求する性質が，
文書の構造・内容として満たされているか」を機械的に検証する．外部依存なし（標準ライブラリのみ）．

`.claude/tests/` はテンプレート自身のメタテスト置き場であり，`/sync-template` の同期対象外
（`SKIP_FILES`）として派生プロジェクトには配布しない．
"""

from __future__ import annotations

import os
import re
import subprocess
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))

SKILLS_DIR = os.path.join(REPO_ROOT, ".claude", "skills")
TASK_REFERENCE = os.path.join(SKILLS_DIR, "task-start", "reference.md")
TASK_CREATE = os.path.join(SKILLS_DIR, "task-create", "SKILL.md")
TASK_START = os.path.join(SKILLS_DIR, "task-start", "SKILL.md")
TASK_HANDOFF = os.path.join(SKILLS_DIR, "task-handoff", "SKILL.md")
SET_MODE = os.path.join(SKILLS_DIR, "set-mode", "SKILL.md")
SYNC_SKILL = os.path.join(SKILLS_DIR, "sync-template", "SKILL.md")
SYNC_REFERENCE = os.path.join(SKILLS_DIR, "sync-template", "reference.md")
TEMPLATE_CUSTOMIZATION = os.path.join(REPO_ROOT, ".claude", "rules", "template-customization.md")
GUIDE_03 = os.path.join(REPO_ROOT, "docs", "01_GUIDE", "GUIDE_03_チーム開発ルール.md")
BOARD_FILE_REL = ".claude/project-board"
BOARD_FILE = os.path.join(REPO_ROOT, ".claude", "project-board")

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
BOARD = "`.claude/project-board`"
ASSIGNED_OPEN = "gh issue list --assignee @me --state open"
REFRESH = "gh auth refresh -s project"
# `project` スコープへの言及（「`project` スコープ」「-s project」）
PROJECT_SCOPE_RE = re.compile(r"`project`\s*スコープ|-s project")

PROCEDURES = ("/task-create 実行手順", "/task-start 実行手順", "/task-handoff 実行手順")


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def section(text: str, title_part: str) -> str:
    """見出しに `title_part` を含む節の本文（下位の見出しを含む）を返す."""
    lines = text.splitlines()
    start = level = None
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if not m:
            continue
        if start is None:
            if title_part in m.group(2):
                start, level = i, len(m.group(1))
        elif len(m.group(1)) <= level:
            return "\n".join(lines[start:i])
    if start is None:
        raise AssertionError(f"見出し「{title_part}」が見つからない")
    return "\n".join(lines[start:])


def numbered_steps(text: str) -> list[str]:
    """`1. ...` 形式の手順行を順に返す."""
    return [l for l in text.splitlines() if re.match(r"^\d+\.\s", l)]


def paragraphs(text: str) -> list[str]:
    """空行区切りの段落（箇条書きは 1 項目ずつ）に分ける."""
    out, cur = [], []
    for line in text.splitlines():
        if not line.strip() or re.match(r"^\s*([-*]|\d+\.)\s", line):
            if cur:
                out.append("\n".join(cur))
            cur = [line] if line.strip() else []
        else:
            cur.append(line)
    if cur:
        out.append("\n".join(cur))
    return out


class TestBoardConfigSection(unittest.TestCase):
    """仕様 2・5: reference.md に `.claude/project-board` による判定の定義がある."""

    @classmethod
    def setUpClass(cls):
        cls.sec = section(read(TASK_REFERENCE), "ボード設定の判定")

    def test_mentions_board_file(self):
        self.assertIn(BOARD, self.sec)

    def test_format_owner_and_number(self):
        self.assertRegex(self.sec, r"(?m)^owner=")
        self.assertRegex(self.sec, r"(?m)^number=")

    def test_absent_means_no_gh_project_and_assigned_issue_count(self):
        absent = [p for p in paragraphs(self.sec) if "無い" in p and BOARD in p]
        self.assertTrue(absent, "ファイルが無い場合の扱いが書かれていない")
        joined = "\n".join(absent)
        self.assertIn("gh project", joined, "無い場合に `gh project` を呼ばない旨が無い")
        self.assertRegex(joined, r"呼ばない|触れない|使わない")
        self.assertIn(ASSIGNED_OPEN, joined)
        self.assertIn("ボード未使用", joined)

    def test_present_uses_owner_number_without_listing(self):
        present = [p for p in paragraphs(self.sec) if "ある" in p and BOARD in p and "owner" in p]
        self.assertTrue(present, "ファイルがある場合の扱いが書かれていない")
        joined = "\n".join(present)
        self.assertIn("number", joined)
        self.assertRegex(joined, r"gh project list.{0,20}探索(は)?しない")

    def test_scope_error_stops_with_refresh_guidance(self):
        self.assertIn("スコープエラー", self.sec)
        self.assertIn(REFRESH, self.sec)
        self.assertIn("停止", self.sec)


class TestProcedures(unittest.TestCase):
    """仕様 1・3・5: 3 手順は最初にボード設定を判定し，探索・複数停止を行わない."""

    @classmethod
    def setUpClass(cls):
        cls.ref = read(TASK_REFERENCE)

    def test_each_procedure_checks_board_file_first(self):
        for title in PROCEDURES:
            with self.subTest(procedure=title):
                steps = numbered_steps(section(self.ref, title))
                self.assertTrue(steps, "手順が無い")
                # /task-start は前提確認（git 状態）が先にあり得るが，ボードに触れる最初の手順で判定する
                board_steps = [s for s in steps if re.search(r"ボード|Project|item-list|gh project", s)]
                self.assertTrue(board_steps)
                self.assertIn(BOARD, board_steps[0], f"ボードに触れる最初の手順で判定していない: {board_steps[0]}")
                self.assertIn("ボード設定の判定", board_steps[0])

    def test_task_create_first_step_is_board_check(self):
        steps = numbered_steps(section(self.ref, "/task-create 実行手順"))
        self.assertIn(BOARD, steps[0])

    def test_handoff_collect_first_step_is_board_check(self):
        steps = numbered_steps(section(self.ref, "/task-handoff 実行手順"))
        self.assertIn(BOARD, steps[0])

    def test_no_project_list_discovery_in_procedures(self):
        for title in PROCEDURES:
            with self.subTest(procedure=title):
                sec = section(self.ref, title)
                self.assertNotIn("gh project list", sec)
                self.assertNotIn("gh repo view --json owner", sec)
                self.assertNotRegex(sec, r"Project を特定")

    def test_no_stop_on_multiple_projects(self):
        for title in PROCEDURES:
            with self.subTest(procedure=title):
                sec = section(self.ref, title)
                self.assertNotRegex(sec, r"Project.{0,10}複数|複数なら停止")

    def test_board_operations_are_conditional(self):
        """手順中のボード操作（Projects の操作・item-*）は `.claude/project-board` 条件付き."""
        for title in PROCEDURES:
            sec = section(self.ref, title)
            for step in numbered_steps(sec):
                if re.search(r"Projects の操作|item-add|item-list|item-edit|In Progress へ|ボードに追加", step):
                    with self.subTest(procedure=title, step=step[:30]):
                        self.assertIn(BOARD, step)

    def test_in_progress_count_falls_back_to_assigned_issues(self):
        for title in ("/task-start 実行手順", "/task-handoff 実行手順"):
            with self.subTest(procedure=title):
                self.assertIn(ASSIGNED_OPEN, section(self.ref, title))

    def test_report_board_state_unused(self):
        for title in ("/task-create 実行手順", "/task-start 実行手順"):
            with self.subTest(procedure=title):
                report = [s for s in numbered_steps(section(self.ref, title)) if "報告" in s and "ボード状態" in s]
                self.assertTrue(report)
                self.assertIn("ボード未使用", report[0])

    def test_projects_section_is_gated(self):
        sec = section(self.ref, "Projects の操作")
        head = "\n".join(sec.splitlines()[:6])
        self.assertIn(BOARD, head)
        self.assertRegex(head, r"ある場合のみ")

    def test_project_list_only_outside_procedures(self):
        """`gh project list` が残るのは手順外（参照・トラブルシューティング），または「探索しない」旨の否定のみ."""
        lines = self.ref.splitlines()
        current_h2 = ""
        for line in lines:
            m = re.match(r"^##\s+(.*)$", line)
            if m:
                current_h2 = m.group(1)
            if "gh project list" not in line:
                continue
            with self.subTest(heading=current_h2, line=line.strip()[:40]):
                self.assertFalse(any(t in current_h2 for t in PROCEDURES))
                if "Projects の操作" not in current_h2 and "トラブルシューティング" not in current_h2:
                    self.assertRegex(line, r"探索(は)?しない|使わない")


class TestRequiredScopes(unittest.TestCase):
    """仕様 1・6: `project` スコープは `.claude/project-board` を置いた場合のみ必要."""

    def test_scope_table_row_is_conditional(self):
        sec = section(read(TASK_REFERENCE), "必要なスコープ")
        rows = [l for l in sec.splitlines() if l.startswith("|") and "`project`" in l]
        self.assertTrue(rows, "project スコープの行が無い")
        for row in rows:
            self.assertIn(BOARD, row)

    def test_scope_section_says_unneeded_by_default(self):
        sec = section(read(TASK_REFERENCE), "必要なスコープ")
        self.assertRegex(sec, r"不要")

    def test_scope_mentions_are_conditional_everywhere(self):
        """skill・GUIDE で `project` スコープを案内する箇所は，必ず `.claude/project-board` 条件付き."""
        targets = [TASK_CREATE, TASK_START, TASK_HANDOFF, GUIDE_03]
        for path in targets:
            lines = read(path).splitlines()
            for i, line in enumerate(lines):
                if PROJECT_SCOPE_RE.search(line):
                    # 入れ子の箇条書き等，親項目で条件が示される場合を許すため直前数行も文脈に含める
                    context = "\n".join(lines[max(0, i - 4): i + 1])
                    with self.subTest(file=os.path.basename(path), line=line.strip()[:40]):
                        self.assertIn("project-board", context)

    def test_no_unconditional_scope_guidance_in_other_docs(self):
        """他の skill・agent・GUIDE が `project` スコープを無条件に案内していない."""
        allowed = {os.path.normcase(p) for p in (TASK_REFERENCE, TASK_CREATE, TASK_START, TASK_HANDOFF, GUIDE_03)}
        roots = [os.path.join(REPO_ROOT, ".claude"), os.path.join(REPO_ROOT, "docs")]
        for root in roots:
            for dirpath, _, files in os.walk(root):
                if os.path.normcase(TESTS_DIR) in os.path.normcase(dirpath):
                    continue
                for name in files:
                    if not name.endswith(".md"):
                        continue
                    path = os.path.join(dirpath, name)
                    if os.path.normcase(path) in allowed:
                        continue
                    for para in paragraphs(read(path)):
                        if PROJECT_SCOPE_RE.search(para) or "gh project" in para:
                            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                                self.assertIn("project-board", para)


class TestSkillDocs(unittest.TestCase):
    """仕様 7: 3 つの SKILL.md はファイルの有無でボードを扱い，無ければ「ボード未使用」."""

    def test_each_skill_mentions_board_file(self):
        for path in (TASK_CREATE, TASK_START, TASK_HANDOFF):
            with self.subTest(skill=os.path.basename(os.path.dirname(path))):
                self.assertIn(BOARD, read(path))

    def test_summary_board_line_says_unused_when_absent(self):
        for path in (TASK_CREATE, TASK_START):
            with self.subTest(skill=os.path.basename(os.path.dirname(path))):
                lines = [l for l in read(path).splitlines() if "**ボード**" in l]
                self.assertTrue(lines, "完了サマリーにボード行が無い")
                for l in lines:
                    self.assertIn(BOARD, l)
                    self.assertIn("ボード未使用", l)

    def test_handoff_does_not_touch_projects_by_default(self):
        text = read(TASK_HANDOFF)
        paras = [p for p in paragraphs(text) if BOARD in p and "無ければ" in p]
        self.assertTrue(paras)
        self.assertRegex("\n".join(paras), r"Projects には触れない|ボード未使用")

    def test_no_stale_multiple_project_wording(self):
        for path in (TASK_CREATE, TASK_START, TASK_HANDOFF):
            with self.subTest(skill=os.path.basename(os.path.dirname(path))):
                self.assertNotRegex(read(path), r"Project が複数|Project が無い場合")


class TestGuide03(unittest.TestCase):
    """仕様 4・8: GUIDE_03 の「Project ボード」節と「管理者の初期設定」."""

    @classmethod
    def setUpClass(cls):
        cls.text = read(GUIDE_03)

    def test_board_section_gated_by_file(self):
        sec = section(self.text, "Project ボード")
        self.assertIn(BOARD, sec)
        self.assertRegex(sec, r"置いた(とき|場合)のみ")

    def test_board_section_documents_format(self):
        sec = section(self.text, "Project ボード")
        self.assertRegex(sec, r"(?m)^owner=")
        self.assertRegex(sec, r"(?m)^number=")

    def test_board_independent_of_set_mode(self):
        sec = section(self.text, "Project ボード")
        self.assertIn("/set-mode", sec)
        self.assertIn("独立", sec)

    def test_admin_setup_has_ordered_start_steps(self):
        sec = section(self.text, "管理者の初期設定")
        i_create = sec.find("Project を作成")
        i_file = sec.find(BOARD + " を作成")
        i_refresh = sec.find(REFRESH)
        self.assertGreaterEqual(i_create, 0, "Project 作成手順が無い")
        self.assertGreaterEqual(i_file, 0, "`.claude/project-board` 作成手順が無い")
        self.assertGreaterEqual(i_refresh, 0, "`gh auth refresh -s project` の手順が無い")
        self.assertLess(i_create, i_file)
        self.assertLess(i_file, i_refresh)
        refresh_para = [p for p in paragraphs(sec) if REFRESH in p][0]
        self.assertIn("全員", refresh_para)


class TestSetModeIndependence(unittest.TestCase):
    """仕様 4: `/set-mode` はボードの有効化に関与しない."""

    def test_set_mode_does_not_manage_board_file(self):
        self.assertNotIn("project-board", read(SET_MODE))


class TestTemplateOwnership(unittest.TestCase):
    """仕様 9: `.claude/project-board` はプロジェクト所有でテンプレートに含めない."""

    def test_template_has_no_board_file(self):
        self.assertFalse(os.path.exists(BOARD_FILE), "テンプレートに .claude/project-board の実体がある")

    def test_board_file_not_tracked(self):
        try:
            out = subprocess.run(
                ["git", "ls-files", "--", BOARD_FILE_REL],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git が利用できない")
        if out.returncode != 0:
            self.skipTest("git リポジトリではない")
        self.assertEqual(out.stdout.strip(), "")

    def test_sync_docs_declare_board_project_owned(self):
        for path in (SYNC_SKILL, SYNC_REFERENCE):
            with self.subTest(file=os.path.basename(path)):
                paras = [p for p in paragraphs(read(path)) if BOARD in p]
                self.assertTrue(paras, ".claude/project-board への言及が無い")
                self.assertRegex("\n".join(paras), r"テンプレートに含まれ")

    def test_template_customization_lists_board_as_out_of_scope(self):
        paras = [p for p in paragraphs(read(TEMPLATE_CUSTOMIZATION)) if BOARD in p]
        self.assertTrue(paras)
        self.assertRegex("\n".join(paras), r"台帳登録は不要")

    def test_board_file_not_ignored(self):
        """共有設定としてコミットする想定のため .gitignore で除外されていない."""
        try:
            out = subprocess.run(
                ["git", "check-ignore", "-q", BOARD_FILE_REL],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git が利用できない")
        self.assertNotEqual(out.returncode, 0, ".claude/project-board が .gitignore で無視されている")


if __name__ == "__main__":
    unittest.main()
