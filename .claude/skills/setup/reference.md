# setup リファレンス (Setup Reference)

`/setup`（`SKILL.md`）から参照される実行手順集（`gh` CLI で行うリポジトリ設定，`.github/dependabot.yml` の生成規則，フェーズごとのコミットの案内，環境構築の実行）．`/setup` の途中で AI が実行するほか，`/setup` 完了後にリポジトリを作成した場合などに管理者（人間）が単体で実行する runbook としても使う．

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

`.github/dependabot.yml` を置くと，Dependabot が依存パッケージの新バージョンを定期的に検出して更新 PR を作る（脆弱性の有無に関係なく）．`/setup` のフェーズ 3 で，技術スタック（`ENV_01_技術スタック.md`）に合わせて**必ず生成する**（作るかどうかはユーザーに聞かない．書き出したファイルで内容だけ確認してもらう）．PR のノイズを抑えるため，週 1 回・マイナー／パッチをまとめる・同時オープン数を絞る，を既定とする．

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

## フェーズごとのコミット (Per-phase Commit)

立ち上げは専用ブランチ `chore/project-setup` の上で行い，各フェーズの成果物をユーザーがエディタで確認した後，**ユーザー自身が `/commit` でコミットする**（フェーズごとに 1 コミットを推奨）．`/setup` はブランチの作成と，コミットの案内（メッセージ例の提示）・コミット済みの確認だけを行い，`git commit`・`git push`・PR 作成・マージは行わない．

### 手順 (Procedure)

1. `/setup` が最初にファイルを書き換える前にブランチを用意する（再開時は作成済みのブランチをそのまま使う）:

   ```bash
   git branch --show-current            # chore/project-setup なら，そのまま使う
   git switch -c chore/project-setup    # main にいる場合のみ作成する
   ```

   - コミットが 1 つも無い（`git log` が失敗する）場合はブランチを作れないため，ユーザーに最初のコミットを依頼してからブランチを切る
2. `/setup` はフェーズの成果物を書き出してレビューを受けた後，下表のメッセージ例を添えてユーザーに `/commit` を案内する（`.env` やクレデンシャル・生成物がコミット対象に入らないよう，`.gitignore` の整備を先に済ませる）
3. ユーザーが `/commit` を実行する（書式は `.claude/rules/git-conventions.md`: `[タグ] 内容`・日本語・末尾の句点なし）
4. `/setup` は次のフェーズに進む前に作業ツリーがクリーンか確認する．未コミットの変更が残っていれば，コミットしてから進むか，そのまま進むかをユーザーに確認する

   ```bash
   git status --porcelain   # 何も出力されなければクリーン
   ```

### フェーズとコミットの対応 (Commit per Phase)

| フェーズ | 主な対象 | コミットメッセージ例 |
| --- | --- | --- |
| 前提確認・モード選択 | `CLAUDE.md`（プロジェクト名・概要），`.claude/project-mode`，（team の場合）`settings.json`・`.gitignore` | `[update] プロジェクト名と開発モードを設定` |
| 1 方針決定 | `docs/03_PLAN/PLAN_01_要件定義書.md` | `[add] 要件定義書を作成` |
| 2 技術選定 | `docs/02_ENV/ENV_01_技術スタック.md` | `[add] 技術スタックを選定` |
| 3 環境構築 | `ENV_02`・`ENV_03`・`ENV_04`，`.gitignore`，`.github/dependabot.yml`，構築用ファイル（Dockerfile・devcontainer.json 等），（任意）`.claude/verify-profile.md` | `[add] 環境構築手順と設定ファイルを整備` |
| 4 仕様設計 | `docs/04_SPEC/SPEC_01_*.md` | `[add] 仕様設計書を作成` |
| 5 規約整備 | `.claude/rules/*.md`（コーディング規約等） | `[add] コーディング規約を整備` |
| 6 開発計画 | `docs/03_PLAN/PLAN_02_開発ステップ.md` | `[add] 開発ステップを策定` |
| 7 実装開始 | `CLAUDE.md`（最終更新），`docs/PROGRESS.md` | `[update] CLAUDE.md を立ち上げ完了状態に更新` |

- フェーズ 3 は構築を実行し，その結果を手順書に反映してからレビューを受けるため，通常は 1 コミットでよい．ユーザーが分けたい場合は分けてよい（例: `[add] 環境構築手順と設定ファイルを整備` → `[update] 実際の構築結果を環境構築手順に反映`）
- 進捗（`CLAUDE.md` の進捗欄・`docs/PROGRESS.md`）を更新した場合は，そのフェーズのコミットに含めるよう案内する
- 承認を得た成果物がまだ無いフェーズ（対話だけで終わった）ではコミットを案内しない（空コミットを作らない）

### 取り込み (Merging into main)

全フェーズ完了後，ユーザーが `/commit push`（PR 作成まで）または `/commit merge`（マージまで）で `main` に取り込む（フェーズ 7 の最終更新が未コミットなら，そのコミットもまとめて行われる）．`/setup` からは案内のみを行う．

## 環境構築の実行 (Executing the Environment Setup)

`/setup` のフェーズ 3 で `ENV_02_環境構築手順.md`・`ENV_03_管理者用環境構築手順.md` を書き出した後，その手順に従って実際に開発環境を構築する．手順書だけで終わらせないのは，未検証の手順書は誤りや前提の抜けを含みやすく，最初に詰まるのがその場のユーザーになるため．

### 実行の分類 (What to Execute)

| 区分 | 例 | 扱い |
| --- | --- | --- |
| リポジトリ内で完結する操作 | プロジェクトの初期化（`npm create`・`flutter create`・`cargo new` 等），依存インストール，設定ファイル生成，`.gitignore` 整備，ビルド・起動・テストの疎通確認，`gh` で行うリポジトリ設定（本書「GitHub リポジトリのセキュリティ設定」） | そのまま実行する |
| ツール導入（システムに入るもの） | SDK・ランタイム・CLI・パッケージマネージャのインストール，グローバル設定の変更，`docker build`，dev container の起動 | **実行するコマンドを提示して承認を得てから**実行する |
| ユーザーにしかできない操作 | 外部サービスのアカウント作成・コンソール操作・セキュリティルール変更，対話ログイン（`gh auth login`・`firebase login` 等），クレデンシャルの発行と貼り付け，実機・ブラウザでの最終確認 | 実行せずユーザーに依頼する（CLAUDE.md「手動確認が必要な作業」） |

### 進め方 (Procedure)

1. `ENV_02`（メンバー向け手順）を上から順に実行する．各手順の実行前に，上表のどの区分かを判定する
2. ツール導入は 1 つずつ，**実行するコマンド・OS とパッケージマネージャ・バージョン・入る場所（システム全体かプロジェクト内か）**を示して承認を得る．既に入っているものはバージョン確認（`node -v` 等）だけで済ませ，入れ直さない
3. ユーザーにしかできない操作に来たら，手順の番号と依頼内容を伝えて待つ．対話ログイン等は `! gh auth login` のように `!` を付けてその場で実行できることを案内する
4. `ENV_03`（管理者用手順）のうち `gh` で実行できるもの（リポジトリのセキュリティ設定）は実行する．リモート未作成なら Phase 7 で再試行する
5. 実行結果を記録し，**手順書を実行結果に合わせて直す**（誤ったコマンド・抜けている前提・OS 依存の差異・必要なバージョン）．解決できなかった手順は該当箇所に「※ 未検証（{エラーの要点}）」と明記する
6. 確定した起動・テスト・lint・ビルドのコマンドを `ENV_04_開発コマンド.md` に書き出す（無ければ作成する）
7. 構築結果をユーザーに報告する（実行した手順／承認を得て導入したツール／ユーザーに依頼した手順／未検証のまま残した手順）

### 注意 (Notes)

- 生成物（`node_modules/`・ビルド出力・`.venv` 等）を作る前に `.gitignore` を整備する．先にインストールを走らせると `git add` が汚れる
- 失敗したコマンドを推測で変形して繰り返さない．2 回失敗したら手を止め，エラー全文と試したことを添えてユーザーに判断を仰ぐ
- 既存ファイルを上書きする初期化コマンドやグローバル設定の書き換えは，対象を確認したうえで承認を得る（空ではないディレクトリでの `create` 系は特に注意する）
- 環境構築が済んだら，疎通確認で使ったコマンド（起動・テスト）が `ENV_04` の記載と一致していることを確認する．一致しない場合は実行できたコマンドを正とする
