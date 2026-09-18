"""検証エージェント（verifier・/verify・/implement Phase 1b）のテンプレート化のテスト（Issue #40）.

実行（リポジトリルートで）:
    python -B -m unittest discover -s .claude/tests -v

仕様の正本:
    1. Issue #40「ブラウザ検証（verifier・/verify・/implement Phase 1b）をテンプレート化する」
       （やること / 揃っていなければスキップする / 完了条件）
    2. Issue 後にユーザーが承認した追加仕様（検証対象の環境をプロファイルで根拠つきに許可できる．
       本番は例外なく禁止．verifier は自己判断で環境を許可しない）

対象は実行コードではなく指示書（Markdown）であるため，各テストは
「仕様が要求する性質が，文書の構造・内容として満たされているか」を機械的に検証する．
外部依存なし（標準ライブラリの unittest のみ）．

`.claude/tests/` はテンプレート自身のメタテスト置き場であり，README.md 等テンプレート固有の内容を
前提とするため，`/sync-template` の同期対象外（SKIP_FILES）として派生プロジェクトには配布しない．
"""

from __future__ import annotations

import os
import re
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))

VERIFIER_AGENT = os.path.join(REPO_ROOT, ".claude", "agents", "verifier.md")
VERIFY_SKILL = os.path.join(REPO_ROOT, ".claude", "skills", "verify", "SKILL.md")
PROFILE_TEMPLATE = os.path.join(REPO_ROOT, ".claude", "skills", "verify", "profile-template.md")
IMPLEMENT_SKILL = os.path.join(REPO_ROOT, ".claude", "skills", "implement", "SKILL.md")
SETUP_SKILL = os.path.join(REPO_ROOT, ".claude", "skills", "setup", "SKILL.md")
SYNC_SKILL = os.path.join(REPO_ROOT, ".claude", "skills", "sync-template", "SKILL.md")
TEMPLATE_CUSTOMIZATION = os.path.join(REPO_ROOT, ".claude", "rules", "template-customization.md")
GUIDE_01 = os.path.join(REPO_ROOT, "docs", "01_GUIDE", "GUIDE_01_プロジェクト立ち上げフロー.md")
GUIDE_02 = os.path.join(REPO_ROOT, "docs", "01_GUIDE", "GUIDE_02_エージェント運用ルール.md")
CLAUDE_MD = os.path.join(REPO_ROOT, "CLAUDE.md")
README_MD = os.path.join(REPO_ROOT, "README.md")

# プロジェクト側が所有し，テンプレートには存在してはならないファイル
VERIFY_PROFILE = os.path.join(REPO_ROOT, ".claude", "verify-profile.md")

# 新規追加された 3 ファイル（Markdown 書式の機械チェック対象）
NEW_FILES = (VERIFIER_AGENT, VERIFY_SKILL, PROFILE_TEMPLATE)


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def rel(path: str) -> str:
    """リポジトリルートからの相対パス（失敗メッセージ・subTest のラベル用）."""
    return os.path.relpath(path, REPO_ROOT)


def lines_outside_fences(lines: list[str]) -> list[tuple[int, str]]:
    """フェンス付きコードブロックの外にある行を (行番号, 行) で返す."""
    result = []
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            result.append((i, line))
    return result


def strip_code_fences(text: str) -> str:
    """フェンス付きコードブロックの中身を除いた本文を返す."""
    return "\n".join(line for _, line in lines_outside_fences(text.splitlines()))


def headings(text: str) -> list[tuple[int, str]]:
    """(レベル, 見出しテキスト) の一覧をコードブロックの外から集める."""
    result = []
    for _, line in lines_outside_fences(text.splitlines()):
        m = HEADING_RE.match(line)
        if m:
            result.append((len(m.group(1)), m.group(2)))
    return result


def frontmatter(text: str) -> dict[str, object]:
    """先頭の YAML フロントマターを素朴に解釈する（スカラーとリストのみ）."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    data: dict[str, object] = {}
    key = None
    for line in lines[1:end]:
        if not line.strip():
            continue
        item = re.match(r"^\s*-\s+(.*\S)\s*$", line)
        if item and key is not None and isinstance(data.get(key), list):
            data[key].append(item.group(1))  # type: ignore[union-attr]
            continue
        kv = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if kv:
            key = kv.group(1)
            value = kv.group(2).strip()
            if value == "":
                data[key] = []
            else:
                data[key] = value.strip('"').strip("'")
    return data


def raw_section(text: str, title_contains: str) -> str:
    """見出しに title_contains を含む節の本文（次の同レベル以上の見出しまで）を返す.

    コードブロックはそのまま残す（出力フォーマット等の検証用）．見出しの探索は
    コードブロックの外だけを対象とする．
    """
    lines = text.splitlines()
    outside = lines_outside_fences(lines)
    start = None
    start_level = 0
    for i, line in outside:
        m = HEADING_RE.match(line)
        if m and title_contains in m.group(2):
            start = i + 1
            start_level = len(m.group(1))
            break
    if start is None:
        return ""
    for j, line in outside:
        m = HEADING_RE.match(line)
        if j >= start and m and len(m.group(1)) <= start_level:
            return "\n".join(lines[start:j])
    return "\n".join(lines[start:])


def section(text: str, title_contains: str) -> str:
    """節の本文を，コードブロックの中身を除いて返す."""
    return strip_code_fences(raw_section(text, title_contains))


def bullets(text: str) -> list[str]:
    """トップレベルの箇条書き項目を返す."""
    return [m.group(1) for m in re.finditer(r"^-\s+(.*\S)\s*$", text, flags=re.MULTILINE)]


def table_headers(text: str) -> list[list[str]]:
    """本文中のテーブルのヘッダー行（セルの一覧）をすべて返す."""
    result = []
    lines = text.splitlines()
    for i, line in enumerate(lines[:-1]):
        if line.strip().startswith("|") and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            result.append(cells)
    return result


class TestCompletionCriterionNoProjectSpecifics(unittest.TestCase):
    """完了条件 3: 汎用部分にプロジェクト固有の記述が含まれないこと.

    「`verifier.md`・`verify/SKILL.md`・`implement/SKILL.md` にプロジェクト固有の記述
    （特定のフレームワーク・DB・パス）が含まれない」（Issue #40 完了条件）
    """

    # Issue の「KarutaMatcher での例」に出てくる固有名詞・固有パスの類
    FORBIDDEN = (
        "KarutaMatcher",
        "Playwright",
        "Chromium",
        "Supabase",
        "supabase",
        "PostgreSQL",
        "Next.js",
        "Flutter",
        "Firebase",
        "launchLoggedIn",
        "waitForURL",
        "networkidle",
        "StrictMode",
        "localhost:3000",
        "localhost:5432",
        ".env.local",
        "browser-verify",
        "scripts/verify/run",
        "run.mjs",
    )

    def test_generic_files_have_no_project_specific_terms(self):
        for path in (VERIFIER_AGENT, VERIFY_SKILL, IMPLEMENT_SKILL):
            text = read(path)
            for term in self.FORBIDDEN:
                with self.subTest(path=rel(path), term=term):
                    self.assertNotIn(
                        term,
                        text,
                        f"{rel(path)} にプロジェクト固有の記述 '{term}' が含まれる",
                    )

    def test_generic_files_have_no_hardcoded_hosts(self):
        """特定のホスト名・URL を直接書かない（ローカルの例示も含めない）."""
        pattern = re.compile(r"https?://(?!github\.com)[\w.-]+")
        for path in (VERIFIER_AGENT, VERIFY_SKILL, IMPLEMENT_SKILL):
            with self.subTest(path=rel(path)):
                found = pattern.findall(read(path))
                self.assertEqual([], found, f"ホスト名が直接書かれている: {found}")

    def test_verifier_delegates_project_specifics_to_profile(self):
        """固有情報はプロファイルを読んで得る，と明記されていること."""
        text = read(VERIFIER_AGENT)
        self.assertIn(".claude/verify-profile.md", text)
        self.assertRegex(
            text,
            r"(本ファイルには書かれていない|プロジェクト固有[^\n]*(持た|書かず))",
            "固有情報を持たない旨が書かれていない",
        )
        self.assertRegex(text, r"検証プロファイル[^\n]*を読(み|む)", "プロファイルを読む指示が無い")


class TestCompletionCriterionSkipWithoutProfile(unittest.TestCase):
    """完了条件 1: プロファイル無しの派生直後プロジェクトで従来と同じ流れになること."""

    def test_template_does_not_ship_a_verify_profile(self):
        """テンプレートに `.claude/verify-profile.md` の実体があってはならない.

        実体があると派生直後のプロジェクトで「プロファイルあり」と判定され，
        Phase 1b がスキップされなくなる（雛形は別パスに置く）．
        """
        self.assertFalse(
            os.path.exists(VERIFY_PROFILE),
            ".claude/verify-profile.md がテンプレートに存在する（雛形は profile-template.md に置く）",
        )

    def test_profile_template_exists_at_documented_path(self):
        self.assertTrue(os.path.isfile(PROFILE_TEMPLATE))

    def test_phase1b_skip_conditions(self):
        """Issue「揃っていなければスキップする」の 4 条件が実行判定にあること."""
        body = section(read(IMPLEMENT_SKILL), "実行判定")
        self.assertNotEqual("", body, "Phase 1b に「実行判定」の節が無い")
        items = bullets(body)
        self.assertGreaterEqual(len(items), 4, f"スキップ条件が 4 件未満: {items}")
        joined = "\n".join(items)
        self.assertIn(".claude/verify-profile.md", joined)  # プロファイル未設定
        self.assertRegex(joined, r"観測可能な挙動")  # 挙動に関係しない変更
        self.assertRegex(joined, r"破壊的な操作")  # マイグレーション等
        self.assertRegex(body, r"起動せず")
        self.assertRegex(body, r"エラーにしない|エラーで止め")

    def test_phase1b_skip_keeps_legacy_request_to_human(self):
        """プロファイル不在時は従来の依頼文言のまま，理由 1 行だけを足すこと."""
        text = read(IMPLEMENT_SKILL)
        self.assertIn("ブラウザや実機で動作を確認してください．", text)
        body = section(text, "人間への提示")
        self.assertRegex(body, r"起動しなかった場合")
        self.assertRegex(body, r"理由")
        self.assertRegex(body, r"1 行")

    def test_implement_pipeline_structure_is_preserved(self):
        """Phase 1〜4・完了処理の見出しが従来の順序で揃っていること."""
        h2 = [t for level, t in headings(read(IMPLEMENT_SKILL)) if level == 2]
        expected = ["前提確認", "Phase 1:", "Phase 1b:", "Phase 2:", "Phase 3:", "Phase 4:", "完了処理"]
        index = -1
        for token in expected:
            matches = [i for i, t in enumerate(h2) if t.startswith(token)]
            self.assertTrue(matches, f"見出し '{token}' が無い: {h2}")
            self.assertGreater(matches[0], index, f"見出し '{token}' の位置が順序どおりでない: {h2}")
            index = matches[0]


class TestPhase1bBehaviour(unittest.TestCase):
    """Issue「やること」1 の implement/SKILL.md への追加分."""

    def test_phase1b_is_placed_right_after_phase1(self):
        h2 = [t for level, t in headings(read(IMPLEMENT_SKILL)) if level == 2]
        i1 = next(i for i, t in enumerate(h2) if t.startswith("Phase 1:"))
        self.assertTrue(h2[i1 + 1].startswith("Phase 1b"), f"Phase 1 の直後が Phase 1b でない: {h2}")

    def test_phase1b_has_four_result_branches(self):
        body = section(read(IMPLEMENT_SKILL), "結果による分岐")
        self.assertNotEqual("", body, "「結果による分岐」の節が無い")
        items = bullets(body)
        self.assertGreaterEqual(len(items), 4, f"分岐が 4 件未満: {items}")
        joined = "\n".join(items)
        self.assertRegex(joined, r"\*\*OK\*\*")
        self.assertRegex(joined, r"NG（今回の変更に起因")
        self.assertRegex(joined, r"NG（今回の変更と無関係")
        self.assertRegex(joined, r"検証不能")

    def test_phase1b_auto_retry_is_limited_to_two(self):
        body = section(read(IMPLEMENT_SKILL), "結果による分岐")
        self.assertRegex(body, r"coder", "変更起因の NG は coder へ差し戻すこと")
        self.assertRegex(body, r"2 回まで", "自動差し戻しの上限（2 回まで）が書かれていない")

    def test_unrelated_existing_issue_is_not_sent_back(self):
        body = section(read(IMPLEMENT_SKILL), "結果による分岐")
        m = re.search(r"^-\s+\*\*NG（今回の変更と無関係.*$", body, flags=re.MULTILINE)
        self.assertIsNotNone(m, "「変更と無関係な既存の問題」の分岐が無い")
        line = m.group(0)
        self.assertIn("差し戻さない", line)
        self.assertIn("/task-create", line)

    def test_presentation_to_human_has_two_parts(self):
        body = section(read(IMPLEMENT_SKILL), "人間への提示")
        self.assertIn("Claude が確認済みの項目", body)
        self.assertIn("あなたに確認してほしい項目", body)

    def test_verifier_ok_is_not_a_substitute_for_human_ok(self):
        text = read(IMPLEMENT_SKILL)
        self.assertIn("ユーザーの OK を得るまで Phase 2 に進まないこと", text)
        self.assertRegex(
            text,
            r"verifier の判定が OK でも[^\n]*OK の代わりにしてはならない",
            "「verifier の OK をユーザーの OK の代わりにしない」が明記されていない",
        )

    def test_phase1b_does_not_instruct_non_local_access(self):
        """委譲ブリーフでローカル以外の環境へのアクセスを指示しないこと（追加仕様）."""
        body = section(read(IMPLEMENT_SKILL), "委譲")
        self.assertRegex(body, r"検証プロファイル|『検証対象の環境』")
        for term in ("staging", "ステージング", "本番"):
            self.assertNotIn(
                term, body.replace("本番は対象外", ""), f"委譲ブリーフに '{term}' への指示がある"
            )


class TestVerifierAgent(unittest.TestCase):
    """`.claude/agents/verifier.md`（Issue「やること」1 と追加仕様）."""

    def setUp(self):
        self.text = read(VERIFIER_AGENT)
        self.fm = frontmatter(self.text)

    def test_frontmatter_shape(self):
        self.assertEqual("verifier", self.fm.get("name"))
        self.assertIsInstance(self.fm.get("description"), str)
        self.assertNotEqual("", self.fm.get("description", ""))
        self.assertIn("model", self.fm)
        self.assertIsInstance(self.fm.get("tools"), list)
        self.assertTrue(self.fm["tools"])

    def test_tools_allow_reading_but_not_editing(self):
        """コードを直さないエージェントなので Edit を持たない（Write は成果物・シナリオ用）."""
        tools = self.fm.get("tools", [])
        self.assertNotIn("Edit", tools, "verifier に Edit が許可されている（コードを直さない役割に反する）")
        for required in ("Read", "Glob", "Grep", "Write"):
            self.assertIn(required, tools)

    def test_tools_do_not_allow_commit_or_unrestricted_bash(self):
        tools = self.fm.get("tools", [])
        self.assertNotIn("Bash", tools, "Bash が無制限に許可されている")
        for tool in tools:
            if tool.startswith("Bash(git"):
                self.assertRegex(tool, r"Bash\(git (diff|status|log)", f"git の危険な操作が許可されている: {tool}")

    def test_workflow_reads_profile_first(self):
        body = section(self.text, "作業手順")
        self.assertNotEqual("", body)
        first = re.search(r"^1\.\s+(.*)$", body, flags=re.MULTILINE)
        self.assertIsNotNone(first, "作業手順が番号付きリストになっていない")
        self.assertIn(".claude/verify-profile.md", first.group(1), "手順 1 で検証プロファイルを読んでいない")

    def test_workflow_covers_issue_steps(self):
        """Issue の作業手順（変更の把握 → 仕様 → 列挙 → シナリオ → 実行 → 証拠確認 → 報告）を含む."""
        body = section(self.text, "作業手順")
        for token in ("変更を把握", "仕様", "確認項目を列挙", "シナリオ", "実行", "証拠", "後始末"):
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_checklist_covers_issue_viewpoints(self):
        """確認観点（正常系・入力の境界・状態の境界・操作の順序・権限・表示崩れ）."""
        body = section(self.text, "確認観点")
        self.assertNotEqual("", body, "「確認観点」の節が無い")
        for token in ("正常系", "入力の境界", "状態の境界", "操作の順序", "権限", "表示"):
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_output_format_has_six_sections(self):
        """出力フォーマットの 6 節（Issue で列挙された見出し）."""
        body = raw_section(self.text, "出力フォーマット")
        self.assertNotEqual("", body, "「出力フォーマット」の節が無い")
        expected = ["判定", "確認した項目", "発見した不具合", "気になった点", "人間が確認すべき残り項目", "後始末"]
        found = re.findall(r"^###\s+(.*\S)\s*$", body, flags=re.MULTILINE)
        self.assertEqual(expected, found, f"出力フォーマットの 6 節が揃っていない: {found}")

    def test_has_unverifiable_and_prohibited_sections(self):
        titles = [t for _, t in headings(self.text)]
        self.assertTrue(any("検証不能" in t for t in titles), f"「検証不能の扱い」の節が無い: {titles}")
        self.assertTrue(any("禁止事項" in t for t in titles), f"「禁止事項」の節が無い: {titles}")

    def test_unverifiable_covers_missing_profile(self):
        body = section(self.text, "検証不能")
        self.assertNotEqual("", body, "「検証不能の扱い」の節が無い")
        self.assertIn(".claude/verify-profile.md", body)

    def test_production_access_is_forbidden_without_exception(self):
        """本番には，プロファイルに書かれていてもアクセスしない（追加仕様）."""
        self.assertRegex(
            self.text,
            r"本番[^\n]*どんな場合も[^\n]*アクセスしない",
            "「本番にはどんな場合もアクセスしない」が書かれていない",
        )
        self.assertRegex(
            self.text,
            r"本番[^\n]*（?プロファイルに書かれていても",
            "「プロファイルに書かれていても本番は不可」が書かれていない",
        )
        prohibited = section(self.text, "禁止事項")
        self.assertRegex(prohibited, r"本番環境へのアクセス", "禁止事項に本番アクセスが無い")

    def test_must_not_self_authorize_environments(self):
        """プロファイルに無い環境を自分の判断で「影響なし」とみなさない（追加仕様）."""
        self.assertRegex(
            self.text,
            r"自分の判断で「影響なし」とみなしてはならない",
            "自己判断での環境許可の禁止が書かれていない",
        )
        self.assertRegex(self.text, r"人間が確認すべき残り項目", "許可外の確認を人間に回す旨が必要")

    def test_allowed_non_local_environment_constraints(self):
        """許可環境での書き込み制限・破壊的操作の禁止（追加仕様）."""
        self.assertRegex(
            self.text,
            r"自分が作ったデータだけ|自分が作った検証データ",
            "書き込みを自分が作ったデータに限る旨が書かれていない",
        )
        self.assertRegex(self.text, r"編集・削除しない", "既存データの編集・削除の禁止が書かれていない")
        self.assertRegex(
            self.text,
            r"(リセット|初期化)[^\n]*(マイグレーション|デプロイ|設定変更)",
            "環境全体に効く破壊的な操作の禁止が書かれていない",
        )

    def test_cleanup_must_report_data_left_outside_local(self):
        self.assertRegex(
            self.text,
            r"ローカル以外の環境に残したデータ[^\n]*(必ず書く|明記)",
            "ローカル以外に残したデータの報告義務が書かれていない",
        )
        self.assertIn("ローカル以外の環境に残したデータの有無", raw_section(self.text, "出力フォーマット"))

    def test_no_stale_local_only_policy(self):
        """旧方針（検証対象はローカルのみ・staging 一切禁止）の記述が残っていないこと."""
        stale = ("検証対象はローカル環境のみ", "検証対象はローカルのみ", "ローカル環境に限定する", "ローカル以外にはアクセスしない")
        for token in stale:
            with self.subTest(token=token):
                self.assertNotIn(token, self.text)
        # 検証対象の範囲を定義している行は，プロファイルによる許可の余地を必ず伴うこと
        scope_lines = [
            line
            for line in self.text.splitlines()
            if re.search(r"検証対象は|アクセスしてよい", line)
        ]
        self.assertTrue(scope_lines, "検証対象の範囲を定義する記述が無い")
        for line in scope_lines:
            with self.subTest(line=line.strip()[:40]):
                self.assertRegex(
                    line,
                    r"許可",
                    "検証対象の範囲が，プロファイルによる許可に触れずローカル限定で断定されている",
                )

    def test_does_not_replace_human_verification(self):
        self.assertRegex(self.text, r"人間の(動作)?確認[^\n]*(狭める|置き換え)")
        prohibited = section(self.text, "禁止事項")
        self.assertRegex(prohibited, r"git commit")
        self.assertRegex(prohibited, r"プロダクションコード[^\n]*変更")
        self.assertRegex(prohibited, r"PASS", "未確認を PASS と報告しない旨が必要")


class TestVerifySkill(unittest.TestCase):
    """`.claude/skills/verify/SKILL.md`（Issue「やること」1 と追加仕様）."""

    def setUp(self):
        self.text = read(VERIFY_SKILL)
        self.fm = frontmatter(self.text)

    def test_frontmatter_shape(self):
        self.assertEqual("verify", self.fm.get("name"))
        self.assertIsInstance(self.fm.get("description"), str)
        self.assertIn("argument-hint", self.fm)

    def test_does_not_launch_verifier_without_profile(self):
        body = section(self.text, "検証プロファイルの確認")
        self.assertNotEqual("", body, "プロファイル確認のステップが無い")
        self.assertIn(".claude/verify-profile.md", body)
        self.assertRegex(body, r"存在しない場合[^\n]*verifier を起動せず")
        self.assertIn("未設定", body)
        self.assertIn("profile-template.md", body, "セットアップ方法（雛形のコピー）の案内が無い")
        self.assertRegex(body, r"終了する")

    def test_scope_check_before_delegating(self):
        body = section(self.text, "対象の確認")
        self.assertNotEqual("", body, "差分から検証対象かを判定するステップが無い")
        self.assertRegex(body, r"git diff")
        self.assertRegex(body, r"ドキュメントのみ|挙動に関係しない|観測できる挙動に関係しない")

    def test_brief_is_self_contained_and_local_by_default(self):
        body = section(self.text, "verifier に委譲")
        self.assertNotEqual("", body, "委譲のステップが無い")
        self.assertRegex(body, r"自己完結")
        self.assertRegex(
            body,
            r"ブリーフでローカル以外の環境へのアクセスを指示しない",
            "ブリーフからローカル以外へのアクセスを指示しない旨が無い（追加仕様）",
        )
        self.assertRegex(body, r"許可はプロファイルでのみ")

    def test_relays_result_without_fixing(self):
        body = section(self.text, "結果の中継")
        self.assertNotEqual("", body, "結果の中継のステップが無い")
        self.assertRegex(body, r"自分では修正を始めない", "NG でも自分で修正しない旨が無い")
        self.assertIn("人間が確認すべき残り項目", body)
        self.assertIn("検証不能", body)
        self.assertIn("/task-create", body)

    def test_reports_data_left_on_shared_environments(self):
        self.assertRegex(
            self.text,
            r"ローカル以外の環境[^\n]*残した(データ)?",
            "共有環境に残ったデータをそのまま伝える旨が無い（追加仕様）",
        )

    def test_states_human_check_is_still_required(self):
        self.assertRegex(self.text, r"人間の動作確認の代わりにはならない|置き換えるものではない")


class TestProfileTemplate(unittest.TestCase):
    """`.claude/skills/verify/profile-template.md`（Issue の表 9 項目＋追加仕様）."""

    def setUp(self):
        self.text = read(PROFILE_TEMPLATE)
        self.titles = [t for level, t in headings(self.text) if level == 2]

    def test_has_all_nine_items_from_issue_table(self):
        """Issue の表の 9 項目に対応する節があること."""
        required = {
            "種別・検証手段": ("種別", "検証手段"),
            "実行コマンド": ("実行コマンド",),
            "前提条件と確認方法": ("前提条件",),
            "ログイン方法・ロール": ("ロール",),
            "テストデータの規約": ("テストデータ",),
            "待ち方・セレクタの癖": ("待ち方",),
            "既知のノイズ": ("既知のノイズ",),
            "禁止操作": ("禁止操作",),
            "シナリオのテンプレート": ("シナリオ",),
        }
        joined = "\n".join(self.titles)
        for item, tokens in required.items():
            with self.subTest(item=item):
                self.assertTrue(
                    any(all(tok in t for tok in tokens) for t in self.titles),
                    f"項目 '{item}' に対応する節が無い: {joined}",
                )

    def test_has_target_environments_and_runner_requirements(self):
        """追加仕様の「検証対象の環境」と Issue の実行基盤に対応する「実行基盤の要件」."""
        self.assertTrue(any("検証対象の環境" in t for t in self.titles), f"節が無い: {self.titles}")
        self.assertTrue(any("実行基盤の要件" in t for t in self.titles), f"節が無い: {self.titles}")

    def test_environment_section_defaults_to_local(self):
        body = section(self.text, "検証対象の環境")
        self.assertRegex(body, r"既定はローカルのみ")
        self.assertRegex(body, r"根拠の書かれていない環境は許可していない")
        self.assertRegex(body, r"判断の主体は人間|判断の主体は[^\n]*プロジェクト")

    def test_allowed_environment_table_columns(self):
        """許可環境の表に，接続先・根拠・資格情報・後始末の列があること（追加仕様）."""
        body = section(self.text, "検証対象の環境")
        tables = table_headers(body)
        self.assertTrue(tables, "許可リストの表が無い")
        cells = "｜".join(tables[0])
        for token in ("環境", "接続先", "根拠", "資格情報", "後始末"):
            with self.subTest(token=token):
                self.assertIn(token, cells, f"許可環境の表に '{token}' の列が無い: {cells}")
        self.assertIn("許可リスト", cells)

    def test_environment_section_has_deny_list_for_production(self):
        body = section(self.text, "検証対象の環境")
        self.assertIn("拒否リスト", body, "本番ホストの拒否リスト欄が無い（追加仕様）")
        self.assertRegex(body, r"本番[^\n]*(許可されない|アクセスしない)")

    def test_runner_requirements_include_connection_guard(self):
        """ランナー側に許可リスト以外の接続先を拒否するガードを持つこと（追加仕様）."""
        body = section(self.text, "実行基盤の要件")
        self.assertRegex(
            body,
            r"許可リスト以外[^\n]*拒否",
            "許可リスト以外の接続先を拒否するガードの要件が無い",
        )
        self.assertRegex(body, r"本番[^\n]*拒否リスト")
        self.assertRegex(body, r"指示書の禁止事項だけに頼らない")

    def test_runner_requirements_reflect_known_failures(self):
        """Issue「設計上の注意」の要点が要件になっていること."""
        body = section(self.text, "実行基盤の要件")
        for token in ("判断はエージェント", "後始末", "自分が起動したプロセス", "出力ディレクトリ"):
            with self.subTest(token=token):
                self.assertIn(token, body)

    def test_test_data_rules_require_prefix_and_hard_delete(self):
        body = section(self.text, "テストデータの規約")
        self.assertRegex(body, r"命名")
        self.assertRegex(body, r"物理削除")
        self.assertRegex(body, r"成否に関わらず")

    def test_reference_implementation_links(self):
        """参考実装として KarutaMatcher の PR #93・#94・#99 を挙げること."""
        body = section(self.text, "参考実装")
        self.assertNotEqual("", body, "「参考実装」の節が無い")
        for pr in ("93", "94", "99"):
            with self.subTest(pr=pr):
                self.assertRegex(body, rf"pull/{pr}\b", f"PR #{pr} へのリンクが無い")

    def test_states_project_ownership(self):
        self.assertRegex(self.text, r"プロジェクト所有")
        self.assertRegex(self.text, r"`?/sync-template`? で上書き・削除されない")

    def test_copy_destination_is_verify_profile(self):
        self.assertIn(".claude/verify-profile.md", self.text)
        self.assertRegex(self.text, r"cp .claude/skills/verify/profile-template\.md .claude/verify-profile\.md")

    def test_supports_non_web_projects(self):
        """種別を Web に限定しないこと（Issue: CLI・API も同じ枠に載せる）."""
        self.assertRegex(self.text, r"CLI")
        self.assertRegex(self.text, r"Web に限らない|Web に限定しない")


class TestDocumentationIsUpdated(unittest.TestCase):
    """Issue「やること」1 の GUIDE・setup・同期ルールへの反映."""

    def test_guide_02_agent_table_lists_verifier(self):
        text = read(GUIDE_02)
        rows = [line for line in text.splitlines() if line.strip().startswith("| `verifier`")]
        self.assertTrue(rows, "GUIDE_02 のエージェント一覧に verifier が無い")
        self.assertIn("Phase 1b", rows[0])

    def test_guide_02_pipeline_table_lists_phase1b(self):
        text = read(GUIDE_02)
        rows = [line for line in text.splitlines() if line.strip().startswith("| Phase 1b")]
        self.assertTrue(rows, "GUIDE_02 のパイプライン表に Phase 1b が無い")
        self.assertIn("verifier", rows[0])
        self.assertRegex(rows[0], r"スキップ")

    def test_guide_02_states_verifier_does_not_replace_human(self):
        text = read(GUIDE_02)
        self.assertRegex(text, r"verifier の OK を人間の OK の代わりにしてはならない")
        self.assertRegex(text, r"本番はどんな場合も対象外|本番は[^\n]*対象外")

    def test_guide_01_mentions_optional_verify_profile(self):
        text = read(GUIDE_01)
        self.assertIn(".claude/verify-profile.md", text)
        self.assertIn(".claude/skills/verify/profile-template.md", text)
        self.assertRegex(text, r"既定でローカルのみ|既定はローカルのみ")

    def test_guide_01_lists_verify_skill_in_common_layer(self):
        text = read(GUIDE_01)
        rows = [line for line in text.splitlines() if line.strip().startswith("| 共通層")]
        self.assertTrue(rows)
        self.assertIn("`verify`", rows[0], "GUIDE_01 の共通層の skill 一覧に verify が無い")

    def test_setup_skill_asks_about_verify_profile(self):
        text = read(SETUP_SKILL)
        titles = [t for _, t in headings(text)]
        self.assertTrue(any("検証プロファイル" in t for t in titles), f"節が無い: {titles}")
        body = section(text, "検証プロファイルの作成")
        self.assertRegex(body, r"任意|既定は「作らない」")
        self.assertIn(".claude/skills/verify/profile-template.md", body)
        self.assertRegex(body, r"ローカルのみ")

    def test_sync_template_excludes_verify_profile(self):
        text = read(SYNC_SKILL)
        self.assertIn(".claude/verify-profile.md", text)
        line = next(line for line in text.splitlines() if ".claude/verify-profile.md" in line)
        self.assertRegex(line, r"同期対象外|プロジェクト固有")

    def test_template_customization_excludes_verify_profile(self):
        text = read(TEMPLATE_CUSTOMIZATION)
        body = section(text, "適用条件")
        self.assertIn(".claude/verify-profile.md", body)
        line = next(line for line in body.splitlines() if ".claude/verify-profile.md" in line)
        self.assertIn("台帳登録は不要", line)

    def test_claude_md_describes_phase1b(self):
        text = read(CLAUDE_MD)
        self.assertIn("Phase 1b", text)
        self.assertIn("/verify", text)
        self.assertRegex(text, r"verifier の OK は人間の動作確認の代わりにならない|代わりにならない")

    def test_readme_lists_verify_command(self):
        text = read(README_MD)
        lines = [line for line in text.splitlines() if line.strip().startswith("- `/verify")]
        self.assertTrue(lines, "README の主なスラッシュコマンドに /verify が無い")
        self.assertIn("verify-profile.md", lines[0])


class TestReferencedPathsExist(unittest.TestCase):
    """新規 3 ファイルが参照する `.claude/` のパスが実在すること."""

    # 実在してはならない（プロジェクト側で作る）／生成物のため除外
    EXCLUDED = {".claude/verify-profile.md", ".claude/commit-context.md"}

    def test_referenced_claude_paths_exist(self):
        pattern = re.compile(r"`(\.claude/[^`]+)`")
        for path in NEW_FILES + (IMPLEMENT_SKILL,):
            text = read(path)
            for ref in sorted(set(pattern.findall(text))):
                if ref in self.EXCLUDED or "<" in ref or "*" in ref:
                    continue
                target = os.path.join(REPO_ROOT, *ref.split("/"))
                with self.subTest(source=rel(path), ref=ref):
                    self.assertTrue(
                        os.path.exists(target) or os.path.exists(target.rstrip(os.sep)),
                        f"{rel(path)} が参照する {ref} が存在しない",
                    )


class TestMarkdownStyle(unittest.TestCase):
    """新規 3 ファイルの書式（`.claude/rules/markdown-style.md`）."""

    def assert_each_new_file(self, check):
        """新規 3 ファイルの本文に check(text) を適用する（どのファイルで落ちたかを subTest で示す）."""
        for path in NEW_FILES:
            with self.subTest(path=rel(path)):
                check(read(path))

    @staticmethod
    def fence_lines(text: str) -> list[str]:
        """コードフェンスの行（``` で始まる行）を返す."""
        return [line.strip() for line in text.splitlines() if line.lstrip().startswith("```")]

    def test_no_japanese_full_stop_or_comma(self):
        def check(text):
            found = [line for line in text.splitlines() if "、" in line or "。" in line]
            self.assertEqual([], found, "「、」「。」は使用しない")

        self.assert_each_new_file(check)

    def test_no_full_width_colon(self):
        self.assert_each_new_file(lambda text: self.assertNotIn("：", text, "全角コロンは使用しない"))

    def test_single_h1_at_most(self):
        def check(text):
            h1 = [t for level, t in headings(text) if level == 1]
            self.assertLessEqual(len(h1), 1, f"H1 が複数ある: {h1}")

        self.assert_each_new_file(check)

    def test_heading_levels_do_not_skip(self):
        def check(text):
            previous = 0
            for level, title in headings(text):
                if previous:
                    self.assertLessEqual(level, previous + 1, f"見出しレベルが飛んでいる: {title}")
                previous = level

        self.assert_each_new_file(check)

    def test_file_ends_with_single_newline(self):
        def check(text):
            self.assertTrue(text.endswith("\n"), "ファイル末尾に改行が無い（MD047）")
            self.assertFalse(text.endswith("\n\n"), "ファイル末尾に余分な空行がある（MD047）")

        self.assert_each_new_file(check)

    def test_no_trailing_whitespace(self):
        def check(text):
            bad = [i + 1 for i, line in enumerate(text.splitlines()) if line != line.rstrip()]
            self.assertEqual([], bad, f"行末に空白がある行: {bad}（MD009）")

        self.assert_each_new_file(check)

    def test_no_consecutive_blank_lines(self):
        def check(text):
            lines = text.splitlines()
            bad = [i + 1 for i in range(1, len(lines)) if lines[i] == "" and lines[i - 1] == ""]
            self.assertEqual([], bad, f"連続する空行がある行: {bad}（MD012）")

        self.assert_each_new_file(check)

    def test_unordered_list_marker_is_hyphen(self):
        def check(text):
            bad = [
                line.strip()
                for line in strip_code_fences(text).splitlines()
                if re.match(r"^\s*[*+]\s+\S", line)
            ]
            self.assertEqual([], bad, "箇条書きは '- ' を使う（MD004）")

        self.assert_each_new_file(check)

    def test_code_fences_are_balanced(self):
        def check(text):
            self.assertEqual(0, len(self.fence_lines(text)) % 2, "コードフェンスが閉じていない")

        self.assert_each_new_file(check)

    def test_code_fences_are_labelled_outside_output_format(self):
        """コードブロックには言語名を付ける（MD040）.

        ※ 出力フォーマットのテンプレートを囲む素のフェンスは，既存エージェント
        （coder・tester・refactorer）と同じ書き方のため対象外とする．
        """

        def check(text):
            body = text.replace(raw_section(text, "出力フォーマット"), "")
            for opening in self.fence_lines(body)[::2]:
                self.assertNotEqual("```", opening, "コードブロックに言語名が無い（MD040）")

        self.assert_each_new_file(check)


if __name__ == "__main__":
    unittest.main()
