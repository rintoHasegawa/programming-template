"""委譲先サブエージェントへのコミット・PR 帰属行の受け渡しのテスト（Issue #54）.

実行（リポジトリルートで）:
    python -B -m unittest discover -s .claude/tests -v

仕様の正本（Issue #54 の要件）:
    1. ops-runner は，呼び出し元から Co-Authored-By 行・PR 帰属行が渡されたら，自身の帰属指定より
       優先して一字一句そのまま使い，渡されなければ自身の帰属指定に従う
    2. `/commit` の司令塔は，自分に与えられたコミット帰属行と PR 帰属行を ops-runner への
       委譲プロンプトに必ず含める
    3. `/auto-refactor`・`/auto-audit` の項目オーケストレータにもコミット帰属行を渡し，
       同じ優先ルールに従う
    4. 具体的なモデル名をハードコードしない

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

OPS_RUNNER = os.path.join(REPO_ROOT, ".claude", "agents", "ops-runner.md")
COMMIT_SKILL = os.path.join(REPO_ROOT, ".claude", "skills", "commit", "SKILL.md")
COMMIT_REFERENCE = os.path.join(REPO_ROOT, ".claude", "skills", "commit", "reference.md")
AUTO_REFACTOR = os.path.join(REPO_ROOT, ".claude", "skills", "auto-refactor", "SKILL.md")
AUTO_AUDIT = os.path.join(REPO_ROOT, ".claude", "skills", "auto-audit", "SKILL.md")

ALL_FILES = (OPS_RUNNER, COMMIT_SKILL, COMMIT_REFERENCE, AUTO_REFACTOR, AUTO_AUDIT)
AUTO_LOOPS = (AUTO_REFACTOR, AUTO_AUDIT)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

# 具体的なモデル名（ファミリー名・帰属行の実例）．frontmatter の `model:` 指定は帰属とは無関係なので除外する
MODEL_NAME_RE = re.compile(r"\b(Opus|Sonnet|Haiku)\b|Claude\s+[A-Z][a-z]+\s+\d", re.IGNORECASE)
PRECEDENCE_RE = re.compile(r"自身の帰属指定.{0,10}(より)?優先")
VERBATIM = "一字一句そのまま"


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def body_without_frontmatter(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1)


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


class TestOpsRunnerAttributionPrecedence(unittest.TestCase):
    """仕様 1: ops-runner は渡された帰属行を優先して一字一句使い，無ければ自身の指定に従う."""

    @classmethod
    def setUpClass(cls):
        cls.body = section(read(OPS_RUNNER), "帰属行")

    def test_covers_commit_and_pr(self):
        self.assertIn("Co-Authored-By:", self.body)
        self.assertIn("PR", self.body)

    def test_passed_lines_take_precedence_verbatim(self):
        self.assertRegex(self.body, PRECEDENCE_RE)
        self.assertIn(VERBATIM, self.body)

    def test_does_not_append_own_attribution(self):
        # 渡された行に自身の帰属行を併記しない（二重の Co-Authored-By を防ぐ）
        self.assertRegex(self.body, r"併記(を)?しない")

    def test_falls_back_to_own_attribution_when_not_passed(self):
        self.assertRegex(self.body, r"渡されなかった.*自身の帰属指定に従う")

    def test_partial_fallback_per_line(self):
        # 一方だけ渡された場合，渡されなかった側だけフォールバックすること
        self.assertRegex(self.body, r"一方のみ|片方")


class TestCommitSkillPassesAttribution(unittest.TestCase):
    """仕様 2: `/commit` 司令塔は委譲プロンプトにコミット・PR 帰属行を必ず含める."""

    @classmethod
    def setUpClass(cls):
        cls.step2 = section(read(COMMIT_SKILL), "ステップ 2")

    def test_delegation_prompt_requires_attribution(self):
        items = [l for l in self.step2.splitlines() if l.startswith("- ") and "帰属行" in l]
        self.assertEqual(len(items), 1, f"委譲プロンプト項目に帰属行が無い／重複: {items}")
        item = items[0]
        self.assertIn("必須", item)
        self.assertIn("Co-Authored-By:", item)
        self.assertIn("PR 帰属行", item)
        self.assertIn(VERBATIM, item)

    def test_points_to_ops_runner_rule(self):
        self.assertIn("コミット・PR の帰属行", self.step2)


class TestCommitReferenceHandlesAttribution(unittest.TestCase):
    """仕様 1（手順書側）: ops-runner が読む reference.md でもコミット・PR 双方で同じ優先ルールが書かれている."""

    @classmethod
    def setUpClass(cls):
        cls.text = read(COMMIT_REFERENCE)

    def test_commit_message_step_uses_passed_line(self):
        body = section(self.text, "ステップ 3: コミットメッセージの生成")
        self.assertIn("Co-Authored-By:", body)
        self.assertRegex(body, PRECEDENCE_RE)
        self.assertIn(VERBATIM, body)
        self.assertRegex(body, r"渡されなかった.*自身の帰属指定に従う")

    def test_pr_creation_uses_passed_line(self):
        body = section(self.text, "プルリクエスト (PR) の作成")
        self.assertIn("PR 帰属行", body)
        self.assertRegex(body, PRECEDENCE_RE)
        self.assertIn(VERBATIM, body)
        self.assertRegex(body, r"渡されなければ自身の帰属指定に従う")


class TestAutoLoopsPassAttribution(unittest.TestCase):
    """仕様 3: `/auto-refactor`・`/auto-audit` は項目オーケストレータへコミット帰属行を渡し，同じ優先ルールに従わせる."""

    def test_m3_handoff_includes_commit_attribution(self):
        for path in AUTO_LOOPS:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                m3 = section(read(path), "ステップ M3")
                items = [l for l in m3.splitlines() if l.startswith("- ") and "帰属行" in l]
                self.assertEqual(len(items), 1, f"M3 の受け渡しに帰属行が無い／重複: {items}")
                self.assertIn("Co-Authored-By:", items[0])
                self.assertIn(VERBATIM, items[0])

    def test_charter_has_precedence_rule(self):
        for path in AUTO_LOOPS:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                charter = section(read(path), "項目オーケストレータの憲章")
                rule = section(charter, "コミットの帰属行")
                self.assertIn("Co-Authored-By:", rule)
                self.assertRegex(rule, PRECEDENCE_RE)
                self.assertIn(VERBATIM, rule)
                self.assertRegex(rule, r"渡されなかった.*自身の帰属指定に従う")

    def test_charter_rule_is_identical_in_both_loops(self):
        # 2 つのループで同じルールが食い違っていないこと
        rules = [section(section(read(p), "項目オーケストレータの憲章"), "コミットの帰属行").strip()
                 for p in AUTO_LOOPS]
        self.assertEqual(rules[0], rules[1])


class TestNoHardcodedModelNames(unittest.TestCase):
    """仕様 4: 帰属行の指示に具体的なモデル名をハードコードしない."""

    def test_no_model_names_in_instruction_bodies(self):
        for path in ALL_FILES:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                body = body_without_frontmatter(read(path))
                hits = [l for l in body.splitlines() if MODEL_NAME_RE.search(l)]
                self.assertEqual(hits, [], f"具体的なモデル名が含まれる: {hits}")

    def test_no_literal_coauthor_line_with_identity(self):
        # `Co-Authored-By: <名前> <メール>` 形式の具体的な帰属行の実例を書かない
        for path in ALL_FILES:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                self.assertNotRegex(read(path), r"Co-Authored-By:\s*[^`\s][^`\n]*<[^>]+@[^>]+>")


if __name__ == "__main__":
    unittest.main()
