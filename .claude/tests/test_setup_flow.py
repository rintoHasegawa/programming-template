"""/setup のフェーズごとのコミットと環境構築の実行のテスト.

実行（リポジトリルートで）:
    python -B -m unittest discover -s .claude/tests -v

仕様の正本（ユーザーが承認した追加仕様）:
    1. `/setup` は各フェーズが終わるたびに，専用ブランチ `chore/project-setup` へコミットする
       （push・PR 作成・マージ・`main` への直接コミットはしない．取り込みは完了後に人間が `/commit` で行う）
    2. フェーズ 3 で環境構築の手順書ができた時点で，その手順に従って実際に環境を構築する
       （リポジトリ内で完結する操作はそのまま実行／ツール導入は都度承認／外部サービス操作はユーザー）

対象は実行コードではなく指示書（Markdown）であるため，各テストは「仕様が要求する性質が，
文書の構造・内容として満たされているか」を機械的に検証する．外部依存なし（標準ライブラリのみ）．

`.claude/tests/` はテンプレート自身のメタテスト置き場であり，`/sync-template` の同期対象外
（`SKIP_FILES`）として派生プロジェクトには配布しない．
"""

from __future__ import annotations

import os
import re
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))

SETUP_SKILL = os.path.join(REPO_ROOT, ".claude", "skills", "setup", "SKILL.md")
SETUP_REFERENCE = os.path.join(REPO_ROOT, ".claude", "skills", "setup", "reference.md")
GUIDE_01 = os.path.join(REPO_ROOT, "docs", "01_GUIDE", "GUIDE_01_プロジェクト立ち上げフロー.md")
GUIDE_02 = os.path.join(REPO_ROOT, "docs", "01_GUIDE", "GUIDE_02_エージェント運用ルール.md")
CLAUDE_MD = os.path.join(REPO_ROOT, "CLAUDE.md")
README_MD = os.path.join(REPO_ROOT, "README.md")

SETUP_BRANCH = "chore/project-setup"

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def section(text: str, title_part: str) -> str:
    """見出しに `title_part` を含む節の本文（下位の見出しを含む）を返す."""
    lines = text.splitlines()
    start = None
    level = 0
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if not m:
            continue
        if start is None:
            if title_part in m.group(2):
                start, level = i + 1, len(m.group(1))
            continue
        if len(m.group(1)) <= level:
            return "\n".join(lines[start:i])
    if start is None:
        raise AssertionError(f"節が見つからない: {title_part}")
    return "\n".join(lines[start:])


class TestSetupSkillCommitsEachPhase(unittest.TestCase):
    """仕様 1: フェーズごとのコミットが `/setup` の手順に組み込まれていること."""

    @classmethod
    def setUpClass(cls):
        cls.text = read(SETUP_SKILL)

    def test_has_git_section_with_dedicated_branch(self):
        body = section(self.text, "立ち上げ中の Git 運用")
        self.assertIn(SETUP_BRANCH, body)
        self.assertIn("フェーズ", body)

    def test_states_no_push_pr_merge(self):
        body = section(self.text, "立ち上げ中の Git 運用")
        for word in ("push", "PR", "マージ"):
            with self.subTest(word=word):
                self.assertIn(word, body)
        self.assertIn("/commit push", body)

    def test_declares_exception_to_commit_prohibition(self):
        body = section(self.text, "立ち上げ中の Git 運用")
        self.assertIn("例外", body)
        self.assertIn("`/commit`", body)

    def test_phase_loop_includes_commit_step(self):
        body = section(self.text, "フェーズ 1〜6")
        steps = [line for line in body.splitlines() if re.match(r"^\d+\. ", line)]
        self.assertTrue(steps, "フェーズの手順が箇条書きで見つからない")
        self.assertTrue(
            any("コミット" in s for s in steps),
            f"フェーズの手順にコミットのステップが無い: {steps}",
        )

    def test_phase7_commits_and_hands_merge_to_user(self):
        body = section(self.text, "フェーズ 7")
        self.assertIn("コミット", body)
        self.assertIn(SETUP_BRANCH, body)
        self.assertRegex(body, r"/commit (push|merge)")

    def test_interruption_commits_before_stopping(self):
        body = section(self.text, "中断時の処理")
        self.assertIn("コミット", body)
        self.assertIn(SETUP_BRANCH, body)


class TestSetupReferenceDescribesCommitProcedure(unittest.TestCase):
    """仕様 1: 実行手順（ブランチの作り方・フェーズ対応表）が reference.md にあること."""

    @classmethod
    def setUpClass(cls):
        cls.text = read(SETUP_REFERENCE)
        cls.body = section(cls.text, "フェーズごとのコミット")

    def test_shows_branch_creation_command(self):
        self.assertIn(f"git switch -c {SETUP_BRANCH}", self.body)
        self.assertIn("git branch --show-current", self.body)

    def test_shows_commit_command_following_git_conventions(self):
        self.assertIn("git commit -m", self.body)
        self.assertIn(".claude/rules/git-conventions.md", self.body)

    def test_commit_table_covers_every_phase(self):
        rows = [line for line in self.body.splitlines() if line.startswith("| ")]
        table = "\n".join(rows)
        phases = ("1 方針決定", "2 技術選定", "3 環境構築", "4 仕様設計", "5 規約整備", "6 開発計画", "7 実装開始")
        for phase in phases:
            with self.subTest(phase=phase):
                self.assertIn(phase, table)

    def test_commit_message_examples_follow_tag_format(self):
        examples = re.findall(r"`(\[[a-z]+\][^`]*)`", self.body)
        self.assertTrue(examples, "コミットメッセージ例が無い")
        allowed = {"[add]", "[update]", "[fix]", "[remove]", "[clean]"}
        for example in examples:
            with self.subTest(example=example):
                self.assertIn(example.split("]")[0] + "]", allowed)
                self.assertFalse(example.endswith("．"), "末尾に句点を付けない（git-conventions）")

    def test_does_not_instruct_push_or_pr(self):
        self.assertIn("`git push`・PR 作成・マージ", self.body)
        merge_body = section(self.text, "取り込み (Merging into main)")
        self.assertIn("ユーザーが `/commit push`", merge_body)


class TestSetupExecutesEnvironmentSetup(unittest.TestCase):
    """仕様 2: 手順書の作成後に実際の環境構築を行うことが定義されていること."""

    @classmethod
    def setUpClass(cls):
        cls.skill = read(SETUP_SKILL)
        cls.reference = read(SETUP_REFERENCE)

    def test_skill_has_execution_section_in_phase3(self):
        body = section(self.skill, "環境構築の実行")
        self.assertIn("ENV_02", body)
        self.assertIn("ENV_03", body)
        self.assertRegex(body, r"実際に(この環境を)?構築する")

    def test_skill_requires_approval_for_tool_installation(self):
        body = section(self.skill, "環境構築の実行")
        self.assertIn("承認を得てから", body)
        self.assertIn("SDK", body)

    def test_skill_leaves_external_services_to_user(self):
        body = section(self.skill, "環境構築の実行")
        self.assertIn("外部サービス", body)
        self.assertIn("gh auth login", body)

    def test_skill_feeds_results_back_into_docs(self):
        body = section(self.skill, "環境構築の実行")
        self.assertIn("反映", body)
        self.assertIn("ENV_04", body)

    def test_phase3_note_points_to_execution(self):
        body = section(self.skill, "フェーズ 3（環境構築）の注意")
        self.assertIn("環境構築の実行", body)

    def test_reference_classifies_operations_in_three_tiers(self):
        body = section(self.reference, "環境構築の実行")
        rows = [line for line in body.splitlines() if line.startswith("| ")]
        self.assertGreaterEqual(len(rows), 5, "実行の分類の表が無い（ヘッダ＋区切り＋3 区分）")
        table = "\n".join(rows)
        self.assertIn("そのまま実行する", table)
        self.assertIn("承認を得てから", table)
        self.assertIn("ユーザーに依頼する", table)

    def test_reference_warns_about_gitignore_before_install(self):
        body = section(self.reference, "環境構築の実行")
        self.assertIn(".gitignore", body)
        self.assertIn("node_modules", body)

    def test_reference_stops_after_repeated_failures(self):
        body = section(self.reference, "環境構築の実行")
        self.assertIn("2 回失敗", body)


class TestDocumentationIsConsistent(unittest.TestCase):
    """GUIDE・CLAUDE.md・README が同じ 2 つの仕様を述べていること."""

    def test_guide_01_has_git_during_setup_section(self):
        body = section(read(GUIDE_01), "立ち上げ中の Git 運用")
        self.assertIn(SETUP_BRANCH, body)
        self.assertIn("/commit push", body)
        self.assertIn(".claude/skills/setup/reference.md", body)

    def test_guide_01_environment_section_requires_actual_setup(self):
        body = section(read(GUIDE_01), "環境構築 (Environment Setup)")
        self.assertIn("実際に環境を構築", body)
        self.assertIn("都度承認", body)
        self.assertIn(".claude/skills/setup/reference.md", body)

    def test_guide_02_lists_setup_as_commit_exception(self):
        body = section(read(GUIDE_02), "コミットルール")
        lines = [line for line in body.splitlines() if "`/setup`" in line]
        self.assertTrue(lines, "GUIDE_02 のコミットルールに /setup の例外が無い")
        self.assertIn(SETUP_BRANCH, lines[0])

    def test_claude_md_lists_setup_in_commit_exceptions(self):
        text = read(CLAUDE_MD)
        lines = [line for line in text.splitlines() if "`/setup`" in line and SETUP_BRANCH in line]
        self.assertTrue(lines, "CLAUDE.md の自律コミット例外に /setup が無い")

    def test_readme_mentions_both_behaviours(self):
        line = next(line for line in read(README_MD).splitlines() if line.startswith("- `/setup"))
        self.assertIn(SETUP_BRANCH, line)
        self.assertRegex(line, r"構築まで行う|実際に構築")


if __name__ == "__main__":
    unittest.main()
