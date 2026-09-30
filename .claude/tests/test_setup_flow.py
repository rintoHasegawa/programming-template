"""/setup の「書き出してからレビュー」・コミットの人間への委譲・環境構築の実行のテスト.

実行（リポジトリルートで）:
    python -B -m unittest discover -s .claude/tests -v

仕様の正本（Issue #47 および直前の承認済み仕様）:
    1. `/setup` は各フェーズの成果物を最初から保存先ファイルに書き出し，チャットには
       「書き出したファイル一覧」と「判断してほしい点・Claude が決めた点」だけを示す（全文を出さない）．
       修正指示はファイルに直接反映する
    2. `/setup` はフェーズごとの自動コミットをしない．コミットは人間が `/commit` で行う
       （`chore/project-setup` ブランチの作成は `/setup` がしてよい．push・PR・マージもしない）
    3. 次のフェーズへは作業ツリーがクリーンなことを確認してから進む．未コミットのまま進むかは人間に確認する
    4. `/setup` は `/commit` 自発実行禁止の例外ではない（CLAUDE.md・GUIDE_02 の例外リストに無い．
       無人運転ループ `/auto-refactor`・`/auto-audit` の例外は残る）
    5. フェーズ 3 で環境構築の手順書ができた時点で，その手順に従って実際に環境を構築し，
       結果を手順書に反映してからレビューを受ける
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


def numbered_steps(body: str) -> list[str]:
    """節の直下（最初の下位見出しより前）にある番号付き手順を返す."""
    steps = []
    for line in body.splitlines():
        if HEADING_RE.match(line):
            break
        if re.match(r"^\d+\. ", line):
            steps.append(line)
    return steps


class TestSetupSkillWritesFilesBeforeReview(unittest.TestCase):
    """仕様 1: 成果物を最初からファイルに書き出し，チャットには一覧と要確認点だけを示すこと."""

    @classmethod
    def setUpClass(cls):
        cls.text = read(SETUP_SKILL)
        cls.rules = section(cls.text, "基本ルール")

    def test_basic_rules_write_to_file_first(self):
        self.assertRegex(self.rules, r"最初から.*書き出")
        self.assertIn("レビュー", self.rules)

    def test_basic_rules_forbid_full_text_in_chat(self):
        self.assertRegex(self.rules, r"全文を出さ(ない|ず)")
        self.assertIn("書き出したファイルの一覧", self.rules)
        self.assertIn("判断してほしい点", self.rules)
        self.assertIn("Claude が決めた点", self.rules)

    def test_basic_rules_apply_fixes_to_file_directly(self):
        self.assertIn("ファイルに直接反映", self.rules)

    def test_no_longer_drafts_in_chat_before_writing(self):
        # 旧仕様「ドラフトを提示し，修正を反映してからファイルに書き出す」が残っていないこと
        self.assertNotRegex(self.text, r"ドラフトを提示")
        self.assertNotIn("反映してからファイルに書き出す", self.text)

    def test_phase_loop_writes_file_before_user_review(self):
        steps = numbered_steps(section(self.text, "フェーズ 1〜6"))
        write_idx = next((i for i, s in enumerate(steps) if "書き出" in s), None)
        review_idx = next((i for i, s in enumerate(steps) if "修正指示" in s), None)
        self.assertIsNotNone(write_idx, f"書き出しのステップが無い: {steps}")
        self.assertIsNotNone(review_idx, f"修正指示のステップが無い: {steps}")
        self.assertLess(write_idx, review_idx, "書き出しがレビュー（修正指示）より後になっている")
        self.assertRegex(steps[write_idx], r"全文.*出さない")
        self.assertIn("ファイルに反映", steps[review_idx])

    def test_dialogue_template_lists_files_and_points(self):
        body = section(self.text, "台詞テンプレート")
        for word in ("書き出したファイル", "判断してほしい点", "Claude が決めた点", "git diff"):
            with self.subTest(word=word):
                self.assertIn(word, body)


class TestSetupSkillLeavesCommitToHuman(unittest.TestCase):
    """仕様 2〜4: `/setup` は自動コミットせず，人間の `/commit` を案内し，クリーン確認をすること."""

    @classmethod
    def setUpClass(cls):
        cls.text = read(SETUP_SKILL)
        cls.git = section(cls.text, "立ち上げ中の Git 運用")

    def test_has_git_section_with_dedicated_branch(self):
        self.assertIn(SETUP_BRANCH, self.git)
        self.assertIn("フェーズ", self.git)

    def test_may_create_setup_branch(self):
        lines = [line for line in self.git.splitlines() if SETUP_BRANCH in line and "作成" in line]
        self.assertTrue(lines, "ブランチ作成を /setup が行ってよい旨が無い")

    def test_states_no_commit_push_pr_merge(self):
        lines = [line for line in self.git.splitlines() if "行わない" in line]
        self.assertTrue(lines, "行わない操作の明記が無い")
        joined = "\n".join(lines)
        for word in ("git commit", "push", "PR", "マージ"):
            with self.subTest(word=word):
                self.assertIn(word, joined)
        self.assertIn("/commit push", self.git)

    def test_commit_is_done_by_user_via_commit_skill(self):
        self.assertRegex(self.git, r"ユーザー(自身)?が\s*`/commit`")

    def test_does_not_declare_exception_to_commit_prohibition(self):
        self.assertNotIn("例外", self.git)
        self.assertNotIn("自律コミット", self.text)
        self.assertNotIn("自動コミット", self.text)

    def test_never_instructs_git_commit_command(self):
        self.assertNotIn("git commit -m", self.text)
        self.assertNotIn("git add", self.text)

    def test_checks_clean_worktree_before_next_phase(self):
        self.assertIn("git status --porcelain", self.git)
        self.assertIn("クリーン", self.git)

    def test_asks_user_when_uncommitted_changes_remain(self):
        lines = [line for line in self.git.splitlines() if "未コミット" in line]
        self.assertTrue(lines, "未コミットの場合の扱いが無い")
        self.assertIn("ユーザーに確認", lines[0])

    def test_phase_loop_guides_commit_then_checks_clean(self):
        steps = numbered_steps(section(self.text, "フェーズ 1〜6"))
        commit_idx = next((i for i, s in enumerate(steps) if "`/commit`" in s), None)
        clean_idx = next((i for i, s in enumerate(steps) if "クリーン" in s), None)
        self.assertIsNotNone(commit_idx, f"/commit を案内するステップが無い: {steps}")
        self.assertIsNotNone(clean_idx, f"クリーン確認のステップが無い: {steps}")
        self.assertIn("案内", steps[commit_idx])
        self.assertLess(commit_idx, clean_idx)
        self.assertIn("ユーザーに確認", steps[clean_idx])
        self.assertEqual(clean_idx, len(steps) - 1, "クリーン確認が次フェーズへ進む直前のステップでない")

    def test_dialogue_template_guides_commit_with_message_example(self):
        body = section(self.text, "台詞テンプレート")
        self.assertIn("`/commit`", body)
        self.assertIn("メッセージ例", body)

    def test_phase7_guides_commit_and_hands_merge_to_user(self):
        body = section(self.text, "フェーズ 7")
        self.assertIn("`/commit`", body)
        self.assertIn(SETUP_BRANCH, body)
        self.assertRegex(body, r"/commit (push|merge)")
        self.assertIn("本スキルでは行わず", body)

    def test_interruption_guides_commit_without_committing(self):
        body = section(self.text, "中断時の処理")
        self.assertIn("本スキルではコミットしない", body)
        self.assertIn("`/commit`", body)
        self.assertIn(SETUP_BRANCH, body)
        self.assertIn("未コミット", body)

    def test_resume_checks_uncommitted_changes(self):
        body = section(self.text, "前提確認")
        self.assertIn("git status --porcelain", body)
        self.assertIn("/commit", body)


class TestSetupPhase3ReviewsAfterExecution(unittest.TestCase):
    """仕様 5: フェーズ 3 は構築を実行し，結果を手順書に反映してからレビューを受けること."""

    def test_phase3_note_orders_execute_reflect_review(self):
        body = section(read(SETUP_SKILL), "フェーズ 3（環境構築）の注意")
        i_exec = body.find("構築を実行")
        i_reflect = body.find("反映", i_exec)
        i_review = body.find("レビュー", i_reflect)
        self.assertNotEqual(i_exec, -1, "構築を実行する旨が無い")
        self.assertNotEqual(i_reflect, -1, "実行後に手順書へ反映する旨が無い")
        self.assertNotEqual(i_review, -1, "反映後にレビューを受ける旨が無い")

    def test_reference_phase3_single_commit_after_reflection(self):
        body = section(read(SETUP_REFERENCE), "フェーズごとのコミット")
        line = next((l for l in body.splitlines() if l.startswith("- フェーズ 3")), "")
        self.assertIn("反映してからレビュー", line)


class TestSetupReferenceDescribesCommitProcedure(unittest.TestCase):
    """仕様 2・3: ブランチの作り方・コミットの案内・クリーン確認・フェーズ対応表が reference.md にあること."""

    @classmethod
    def setUpClass(cls):
        cls.text = read(SETUP_REFERENCE)
        cls.body = section(cls.text, "フェーズごとのコミット")

    def test_shows_branch_creation_command(self):
        self.assertIn(f"git switch -c {SETUP_BRANCH}", self.body)
        self.assertIn("git branch --show-current", self.body)

    def test_guides_user_commit_following_git_conventions(self):
        self.assertIn("`/commit`", self.body)
        self.assertRegex(self.body, r"ユーザー(自身)?が\s*`/commit`")
        self.assertIn(".claude/rules/git-conventions.md", self.body)

    def test_no_git_commit_command_for_setup_to_run(self):
        self.assertNotIn("git commit -m", self.body)
        self.assertNotIn("git add", self.body)
        self.assertRegex(self.body, r"`git commit`[^\n]*行わない")

    def test_shows_clean_check_command(self):
        self.assertIn("git status --porcelain", self.body)
        self.assertIn("ユーザーに確認", self.body)

    def test_does_not_claim_exception(self):
        self.assertNotIn("例外", self.body)

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
    """仕様 5: 手順書の作成後に実際の環境構築を行うことが定義されていること."""

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
    """GUIDE・CLAUDE.md・README が同じ仕様を述べていること."""

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

    def test_guide_01_git_section_leaves_commit_to_human(self):
        body = section(read(GUIDE_01), "立ち上げ中の Git 運用")
        self.assertRegex(body, r"人間が\s*`/commit`")
        self.assertNotIn("例外", body)
        self.assertIn("クリーン", body)
        self.assertRegex(body, r"最初から.*書き出")
        self.assertIn("判断してほしい点", body)
        self.assertIn("Claude が決めた点", body)

    def test_guide_02_does_not_list_setup_as_commit_exception(self):
        body = section(read(GUIDE_02), "コミットルール")
        lines = [line for line in body.splitlines() if "`/setup`" in line]
        self.assertFalse(lines, f"GUIDE_02 のコミットルールに /setup の例外が残っている: {lines}")
        self.assertNotIn("例外（プロジェクト立ち上げ）", body)
        self.assertNotIn(SETUP_BRANCH, body)

    def test_guide_02_keeps_unattended_loop_exceptions(self):
        body = section(read(GUIDE_02), "コミットルール")
        self.assertIn("例外（無人運転）", body)
        self.assertIn("`/auto-refactor`", body)
        self.assertIn("`/auto-audit`", body)

    def _claude_md_exception_block(self) -> list[str]:
        lines = read(CLAUDE_MD).splitlines()
        start = next(i for i, l in enumerate(lines) if l.lstrip().startswith("- **例外**:"))
        block = [lines[start]]
        for line in lines[start + 1:]:
            if line.startswith("    - "):
                block.append(line)
            else:
                break
        return block

    def test_claude_md_does_not_list_setup_in_commit_exceptions(self):
        block = self._claude_md_exception_block()
        self.assertFalse([l for l in block if "/setup" in l], f"例外リストに /setup が残っている: {block}")
        self.assertNotIn("プロジェクト立ち上げ", "\n".join(block))
        text = read(CLAUDE_MD)
        self.assertNotIn(SETUP_BRANCH, text)

    def test_claude_md_keeps_unattended_loop_exceptions(self):
        block = "\n".join(self._claude_md_exception_block())
        self.assertIn("`/auto-refactor`", block)
        self.assertIn("`/auto-audit`", block)

    def test_readme_mentions_new_behaviours(self):
        line = next(line for line in read(README_MD).splitlines() if line.startswith("- `/setup"))
        self.assertIn(SETUP_BRANCH, line)
        self.assertRegex(line, r"構築まで行う|実際に構築")
        self.assertIn("書き出", line)
        self.assertRegex(line, r"人間が\s*`/commit`")
        self.assertNotRegex(line, r"ブランチへコミット")


if __name__ == "__main__":
    unittest.main()
