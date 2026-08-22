# deps-update リファレンス (deps-update Reference)

`/deps-update`（`SKILL.md`）から参照される，`gh` コマンド・GraphQL クエリ・分類規則・ローカル検証手順・台帳と PR コメントの書式．`{owner}/{repo}` は `gh` がカレントリポジトリから自動補完する．

## 前提の確認 (Prerequisites)

```bash
gh auth status
gh repo view --json nameWithOwner -q .nameWithOwner
git status --short                                          # 空であること
git branch --show-current                                   # 開始時のブランチを控える
gh api repos/{owner}/{repo}/vulnerability-alerts            # 204 なら Dependabot alerts 有効（404 なら無効）
gh api repos/{owner}/{repo}/automated-security-fixes        # {"enabled":true,...} なら security updates 有効
test -f .github/dependabot.yml && echo "dependabot.yml: あり" || echo "dependabot.yml: なし"
ls .github/workflows/*.yml 2>/dev/null                      # CI の有無
```

## PR の収集 (Collect PRs)

```bash
gh pr list --author app/dependabot --state open --limit 100 \
  --json number,title,body,url,headRefName,baseRefName,labels,mergeable,mergeStateStatus,statusCheckRollup,createdAt,isDraft
```

- 個別の詳細: `gh pr view <番号> --json number,title,body,mergeable,mergeStateStatus,statusCheckRollup,files,commits`
- チェック状況: `gh pr checks <番号>`（CI が無いリポジトリでは何も返らない）
- 変更ファイル: `gh pr diff <番号> --name-only`（マニフェストとロックファイルだけのはず．それ以外が含まれていたら報告に回す）
- 本スキルが既にコメント済みか: `gh pr view <番号> --json comments -q '.comments[].body' | grep -c "<!-- deps-update -->"`

## alert の収集 (Collect Alerts)

GraphQL で「紐づく PR」と「PR を作れなかった理由」まで一度に取る（REST の `/deps-update/alerts` には PR との紐づけが無い）．

```bash
OWNER=$(gh repo view --json owner -q .owner.login)
REPO=$(gh repo view --json name -q .name)
gh api graphql -f owner="$OWNER" -f repo="$REPO" -f query='
query($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) {
    vulnerabilityAlerts(states: OPEN, first: 100) {
      nodes {
        number
        vulnerableManifestPath
        vulnerableRequirements
        securityVulnerability {
          package { name ecosystem }
          severity
          vulnerableVersionRange
          firstPatchedVersion { identifier }
        }
        securityAdvisory { ghsaId summary permalink }
        dependabotUpdate {
          pullRequest { number url }
          error { title body errorType }
        }
      }
    }
  }
}'
```

- `dependabotUpdate.pullRequest` があれば，その PR は **security update**
- `dependabotUpdate.error` があれば，Dependabot は修正 PR を作れなかった（推移的依存・対応外マニフェスト・上限超過等）．`errorType` と `body` を報告に転記する
- `firstPatchedVersion` が `null` なら修正版が無い
- フォールバック（GraphQL が使えない場合）: `gh api "repos/{owner}/{repo}/dependabot/alerts?state=open&per_page=100"`．PR との紐づけは PR 本文の GHSA リンク（`securityAdvisory.ghsaId`）で照合する

## 更新規模の分類 (Classifying the Bump)

### 単独 PR (Single-package PR)

タイトル `Bump <pkg> from <A> to <B>`（`Update <pkg> requirement from ... to ...` の形もある）から版を取り，semver で比較する．

| 条件 | 規模 |
| --- | --- |
| メジャーが上がる（`1.x` → `2.x`） | `major` |
| メジャーが `0` でマイナーが上がる（`0.3.x` → `0.4.x`） | `major` 相当（破壊的変更を含みうる） |
| メジャー同じでマイナーが上がる | `minor` |
| パッチだけ上がる | `patch` |
| semver として解釈できない（日付版・ハッシュ・範囲指定等） | `unknown` |

- プレリリース（`-beta` 等）への更新は `unknown` として扱う
- `github-actions` の `v3` → `v4` のようなメジャータグ更新は `major`

### グループ PR (Grouped PR)

タイトル `Bump the <group> group (across N directories )?with N updates` の PR は，本文の `Updates \`<pkg>\` from <A> to <B>` 行（または本文の表）を全て取り，**最大の規模**で判定する．テンプレートの `dependabot.yml` では `minor-and-patch` グループがマイナー／パッチだけを含むので定義上 `minor` になるが，本文から実際に確認する．

### 依存スコープ (Dependency Scope)

マニフェストのセクションで判定する（`package.json` の `devDependencies`，`pubspec.yaml` の `dev_dependencies`，`pyproject.toml` の dev グループ／`[project.optional-dependencies]`，`Cargo.toml` の `[dev-dependencies]` 等）．`github-actions`・`docker` は CI／実行環境への影響として本番扱いにする．

## CI の完了待ち (Waiting for CI)

```bash
gh pr checks <番号> --watch --interval 30        # 全チェック完了まで待つ（Bash ツールの timeout を 10 分程度にする）
```

- 待ち切れなければ，CI が無い場合と同じく「ローカル検証」に切り替えるか，今回は見送って「CI 待ち」として報告する
- マージ直後は Dependabot が残りの PR を自動 rebase して CI が再実行される．次の PR に進む前にチェック状況を取り直す

## ローカル検証 (Local Verification)

CI が無い／CI 待ちに使う．**最新 `main` を取り込んだ状態**で検証し，終わったら必ず元に戻す．push は一切しない．

```bash
START_BRANCH=$(git branch --show-current)
git fetch origin
gh pr checkout <番号>                               # PR のブランチをローカルに取得して切り替え
PR_BRANCH=$(git branch --show-current)
git merge --no-edit origin/main                     # 失敗（コンフリクト）なら: git merge --abort → この PR は見送り（@dependabot rebase）
# 依存インストール → 検証スイート（ENV_04_開発コマンド.md のコマンド）
# 例: npm ci && npm test && npm run build / flutter pub get && flutter test && flutter analyze / cargo test / go test ./...
git checkout "$START_BRANCH"
git branch -D "$PR_BRANCH"
git status --short                                  # 空であることを確認
```

- 依存インストール後にロックファイル等が変わる（`git status` に差分が出る）場合は，`git checkout -- .` で破棄してからブランチを戻す
- 検証結果の解釈:
  - テスト＋（あれば）ビルド／型チェック／リンタが全緑 → **Tier A**
  - テストが無いがインストールとビルド／型チェックは通る → **Tier B**
  - インストール自体が失敗／検証コマンドが特定できない → **Tier C**
  - 赤 → 「CI 赤」と同じ扱いで報告（失敗したコマンドと要約を添える）

## マージ (Merge)

```bash
gh pr merge <番号> --merge --delete-branch          # Git 規約どおりマージコミット
git checkout main && git pull origin main            # ローカル main を追従（開始時のブランチが main でない場合は戻す）
```

- ブランチ保護（必須レビュー）で拒否された場合は，その PR を「マージ可と判定したが権限不足」として報告する
- `--delete-branch` が「既に削除済み」で失敗しても無視してよい（Dependabot 側の設定でブランチが自動削除されることがある）

## Dependabot へのコメント指示 (Dependabot Commands)

PR コメントで Dependabot を操作できる．本スキルが使ってよいのは次の 2 つだけ．

```bash
gh pr comment <番号> --body "@dependabot rebase"     # base に追従させる（競合・BEHIND のとき）
gh pr comment <番号> --body "@dependabot recreate"   # PR を作り直させる（一時的なビルド失敗・壊れたブランチのとき）
```

- `@dependabot ignore this major version` / `@dependabot ignore this dependency` / `@dependabot close` は**人間が判断して投稿する**（本スキルは推奨として提示するだけ）

## メジャー更新の影響分析 (Analyzing a Major Bump)

報告に回す PR（`major`・`0.x` マイナー・`unknown`）は，人間が「そのままマージ」「`/implement` で追従」「見送り」を選べるだけの材料を揃える．

1. **変更内容**: PR 本文の Release notes / Changelog / Commits 節から **Breaking changes** を抜き出す．本文に無ければ `gh api repos/<upstream-owner>/<upstream-repo>/releases` や CHANGELOG を参照する（取れなければ「リリースノート未確認」と明記する）
2. **利用箇所**: コードベース内で当該パッケージを import／呼び出している箇所を Grep で列挙し，破壊的変更に該当する API を使っているか確認する
3. **影響範囲と推奨**:
   - 該当 API を使っていない／開発依存のみ → 「そのままマージ可（検証 Tier と根拠を添える）」
   - 該当 API を使っている → 「`/implement` で追従が必要（修正箇所の一覧）」
   - 互換性が取れない・移行コストが見合わない → 「見送り（`@dependabot ignore this major version` を人間が投稿）」
4. `github-actions` のメジャー更新は workflow の入力・出力の変更点を確認する（CI が通っていれば「マージ可」でよい）

## PR コメントの書式 (PR Comment Format)

報告に回した PR には，通常モードで次の書式のコメントを 1 回だけ投稿する（先頭の HTML コメントは二重投稿防止のマーカー．削除しない）．

```markdown
<!-- deps-update -->
## /deps-update による分析

- **判定**: 自動マージ対象外（{メジャー更新 / 0.x マイナー更新 / CI 赤 / 検証不能}）
- **更新**: `{pkg}` {A} → {B}（{本番依存 / 開発依存}，{security / version}）
- **検証**: {CI 緑 / ローカル検証緑 / ビルドのみ / 未検証}
- **破壊的変更**: {抜き出した要点，無ければ「記載なし」}
- **影響箇所**: {ファイル:行 の一覧，無ければ「該当 API の利用なし」}
- **推奨**: {そのままマージ可 / `/implement` で追従後にマージ / 見送り（`@dependabot ignore this major version`）}
```

## 台帳の書式 (Ledger Formats)

いずれも `.claude/` 直下のローカル運用状態（`.gitignore` 対象）．1 行 1 エントリ．

- `.claude/deps-update-merged.md`:
  - `- [merged] #<番号> | <pkg> <A>→<B>（グループは件数） | <security / version> | <patch / minor> | <検証: CI / ローカル / ビルドのみ> | <推奨する動作確認（無ければ -）> | <日付>`
- `.claude/deps-update-report.md`:
  - `- [major] #<番号> | <pkg> <A>→<B> | <本番 / 開発> | <破壊的変更の要点> | <影響箇所> | <推奨> | <日付>`
  - `- [ci-fail] #<番号> | <pkg> <A>→<B> | <失敗したチェック／コマンドと要約> | <推奨> | <日付>`
  - `- [alert] <pkg>（<ecosystem>） | <重大度> | <GHSA> | <修正版 or 修正版なし> | <PR を作れなかった理由> | <推奨対応> | <日付>`
  - `- [other] #<番号> | <内容（依存以外のファイル変更・unknown 規模 等）> | <推奨> | <日付>`
- `.claude/deps-update-skip.md`（ユーザーが編集する）:
  - `- #<番号> または <pkg> | <理由> | <日付>`
