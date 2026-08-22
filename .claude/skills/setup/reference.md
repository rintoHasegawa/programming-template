# setup リファレンス (Setup Reference)

`/setup`（`SKILL.md`）から参照される，GitHub リポジトリまわりの設定手順集（`gh` CLI で行うリポジトリ設定と，`.github/dependabot.yml` の生成規則）．`/setup` の途中で AI が実行するほか，`/setup` 完了後にリポジトリを作成した場合などに管理者（人間）が単体で実行する runbook としても使う．

## GitHub リポジトリのセキュリティ設定 (GitHub Repository Security Settings)

GitHub の Settings → Code security にある **Dependabot alerts**（既知脆弱性の検出．UI では "Vulnerabilities" として表示される）と **Dependabot security updates**（脆弱性を直す PR の自動作成）は**リポジトリごとに既定で OFF** であり，有効化しないと依存パッケージの脆弱性が一切通知されない．モード（solo / team）に関わらず，プロジェクトの GitHub リポジトリが存在する時点で有効化する．

### 前提 (Prerequisites)

- `gh auth login` 済みであること（既定スコープ `repo` で足りる）
- 実行者がリポジトリの **admin 権限**を持つこと（無いと 403）
- カレントディレクトリがそのリポジトリの clone であること（`{owner}/{repo}` は `gh` がリモートから自動補完する）

### 手順 (Procedure)

1. リモートの有無を確認する:

   ```bash
   gh repo view --json nameWithOwner -q .nameWithOwner
   ```

   - 失敗する（リモート未設定・リポジトリ未作成・`gh` 未認証）場合はこの手順をスキップする．`/setup` 中であれば「GitHub リポジトリ作成後に `/setup` を再実行するか，ENV_03 に記載したコマンドを実行してください」とユーザーに伝え，Phase 7 でもう一度試す
2. 有効化する（冪等なので再実行してよい）:

   ```bash
   gh api -X PUT repos/{owner}/{repo}/vulnerability-alerts        # Dependabot alerts（依存グラフも同時に有効化される）
   gh api -X PUT repos/{owner}/{repo}/automated-security-fixes    # Dependabot security updates
   ```

3. 有効化を検証する:

   ```bash
   gh api repos/{owner}/{repo}/vulnerability-alerts               # 成功（204）なら有効．404 なら無効
   gh api repos/{owner}/{repo}/automated-security-fixes           # {"enabled":true,...} なら有効
   ```

4. 結果をユーザーに報告する

### トラブルシューティング (Troubleshooting)

| 症状 | 原因 | 対処 |
| --- | --- | --- |
| `gh repo view` が失敗 | リモート未設定・リポジトリ未作成・`gh` 未認証 | リポジトリ作成と `git remote add origin ...` 後に再実行．`gh auth status` で認証を確認 |
| `PUT` が 403 | リポジトリの admin 権限が無い，またはトークンのスコープ不足 | 管理者に依頼するか，GitHub の Settings → Code security → Dependabot から手動で ON にする |
| `PUT` が 404 | `{owner}/{repo}` の補完に失敗（リモート URL が GitHub でない等） | `gh api -X PUT repos/<owner>/<repo>/vulnerability-alerts` のように明示する |

### 補足 (Notes)

- `.github/dependabot.yml` は「依存バージョンの定期更新 PR」の設定であり，上記の脆弱性検出とは別物（次節）．上記の有効化は `dependabot.yml` の有無に関係なく動く
- アカウント設定（Settings → Code security → "Automatically enable for new repositories"）を ON にしておけば以後の新規リポジトリは自動で有効になるが，テンプレートとしてはリポジトリ単位で確実に有効化する前提で運用する

## 依存バージョン更新の設定 (Dependabot Version Updates)

`.github/dependabot.yml` を置くと，Dependabot が依存パッケージの新バージョンを定期的に検出して更新 PR を作る（脆弱性の有無に関係なく）．`/setup` のフェーズ 3 で，技術スタック（`ENV_01_技術スタック.md`）に合わせて**必ず生成する**（作るかどうかはユーザーに聞かない．内容だけドラフトとして確認する）．PR のノイズを抑えるため，週 1 回・マイナー／パッチをまとめる・同時オープン数を絞る，を既定とする．

### エコシステム対応表 (Ecosystem Mapping)

技術スタックから `package-ecosystem` を決める．`directory` はマニフェスト（`package.json`・`pubspec.yaml` 等）があるディレクトリ（ルートなら `/`）．モノレポ等で複数箇所にある場合はエントリを分けるか `directories` を使う．

| 技術スタック | `package-ecosystem` | マニフェスト例 |
| --- | --- | --- |
| Node.js（npm / pnpm / yarn） | `npm` | `package.json` |
| Dart / Flutter | `pub` | `pubspec.yaml` |
| Python（pip / Poetry / pipenv） | `pip` | `requirements.txt`・`pyproject.toml`・`Pipfile` |
| Python（uv） | `uv` | `pyproject.toml` + `uv.lock` |
| Rust | `cargo` | `Cargo.toml` |
| Go | `gomod` | `go.mod` |
| Ruby | `bundler` | `Gemfile` |
| PHP | `composer` | `composer.json` |
| Java / Kotlin（Gradle） | `gradle` | `build.gradle(.kts)` |
| Java（Maven） | `maven` | `pom.xml` |
| .NET | `nuget` | `*.csproj` |
| Swift | `swift` | `Package.swift` |
| Docker を使う場合 | `docker` | `Dockerfile` |
| Dev Containers を使う場合 | `devcontainers` | `.devcontainer/devcontainer.json` |
| GitHub Actions（CI） | `github-actions` | `.github/workflows/*.yml` |

- `github-actions` は**常に含める**（立ち上げ時点で workflow が無くても害は無く，後から CI を足した時に自動で対象になる）
- Docker / Dev Containers は技術スタックまたは環境構築方針（GUIDE_01「環境構築」の基本方針）で採用している場合に含める

### 雛形 (Template)

以下を基に，対応表で決めたエコシステムごとに `updates` エントリを 1 つずつ作る（`{...}` を置き換える）．

```yaml
# 依存パッケージの定期更新 PR（Dependabot version updates）
# 脆弱性検出（Dependabot alerts / security updates）はリポジトリ設定で有効化済み（本ファイルとは独立に動く）
version: 2
updates:
  - package-ecosystem: "{npm / pub / pip ...}"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 5
    commit-message:
      prefix: "[update]"
    groups:
      minor-and-patch:
        update-types:
          - "minor"
          - "patch"
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    commit-message:
      prefix: "[update]"
    groups:
      actions:
        patterns:
          - "*"
```

- `interval`: 既定は `weekly`．更新 PR を減らしたいプロジェクトは `monthly` にしてよい
- `groups`: マイナー／パッチ更新を 1 PR にまとめる．メジャー更新は破壊的変更を含みうるため個別 PR のまま残す
- `open-pull-requests-limit`: 既定 5．放置された更新 PR が溜まるのを防ぐ
- `commit-message.prefix`: Git 規約（`.claude/rules/git-conventions.md`）の `[タグ] 内容` に合わせて `[update]` を付ける．内容部分は Dependabot が英語で生成する（例: `[update] Bump foo from 1.2.0 to 1.3.0`）．これは規約の許容例外とし，手で書き直さない
- Dependabot の PR も通常の PR と同じく `main` へ直接は入らない．CI（あれば）の結果とリリースノートを確認してマージする

### 動作確認 (Verification)

`main` に push された後，GitHub の Insights → Dependency graph → Dependabot で各エコシステムの「Last checked」と結果を確認できる．YAML の構文エラーはここにも表示される．
