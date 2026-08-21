# setup リファレンス (Setup Reference)

`/setup`（`SKILL.md`）から参照される，`gh` CLI で実行する GitHub リポジトリ設定の手順集．`/setup` の途中で AI が実行するほか，`/setup` 完了後にリポジトリを作成した場合などに管理者（人間）が単体で実行する runbook としても使う．

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

- `.github/dependabot.yml` は「依存バージョンの定期更新 PR」の設定であり，上記の脆弱性検出とは別物．エコシステム（npm / pub / pip / cargo 等）がプロジェクト固有のため `/setup` では作らず，必要ならユーザーの指示で追加する
- アカウント設定（Settings → Code security → "Automatically enable for new repositories"）を ON にしておけば以後の新規リポジトリは自動で有効になるが，テンプレートとしてはリポジトリ単位で確実に有効化する前提で運用する
