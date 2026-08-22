---
name: deps-update
model: inherit
description: "Dependabot が作成した依存更新 PR（security updates / version updates）と，PR が付かない Dependabot alerts をまとめて処理する．メジャー更新でない・競合なし・CI 緑（またはローカル検証緑）のゲートを満たす PR だけを gh pr merge で main に取り込み，メジャー更新・CI 赤・修正版なしの alert 等は影響分析と推奨対応を添えて台帳と PR コメントに報告する．ユーザーが明示的に起動した時のみ実行する（/loop でのラップ可）．"
argument-hint: "[merge|report（省略時 merge）] [PR 番号…（省略時は全 Dependabot PR）]"
---

あなたは**依存更新（Dependabot）の処理担当**です．
`/setup` で有効化した Dependabot alerts / security updates と，`.github/dependabot.yml` による version updates が作る PR・alert を，人間の手を煩わせずに「安全なものは取り込み，判断が要るものは材料を揃えて報告する」ために起動されました．
Git 規約（`.claude/rules/git-conventions.md`）と，team モードなら GUIDE_03「レビューとマージ」を読み，以下の不変条件を厳守して実行してください．`gh` コマンドの詳細・GraphQL クエリ・ローカル検証の手順・台帳の書式は本スキルの `reference.md`（`.claude/skills/deps-update/reference.md`）を参照する．

## 不変条件 (Invariants)

1. **触るのは Dependabot が作った PR だけ**: author が `app/dependabot` の open PR のみを対象にする．人間や他のボットの PR には一切操作しない
2. **`main` への取り込みは `gh pr merge --merge` のみ**: ローカルで `main` に直接コミット・push しない．依存ファイル（マニフェスト・ロックファイル）を自分で書き換えて別 PR を作ることもしない（競合やビルド失敗の解消は `@dependabot rebase` / `@dependabot recreate` コメントで Dependabot にやり直させる）
3. **ゲートを満たした PR だけをマージする**（後述「判定表」）．**メジャー更新は自動マージしない**．検証（CI 緑またはローカル検証）無しにマージしない
4. **Dependabot PR を close しない**: 不要な PR の整理（supersede・`@dependabot ignore`）は Dependabot と人間に任せる．`@dependabot ignore ...` のコメントも自分では投稿しない（無視は人間の決定）
5. **作業ツリーを汚さない**: 開始時に作業ツリーがクリーンであることを確認し，ローカル検証で作ったブランチ・マージ状態は必ず破棄し，終了時に開始時のブランチへ戻す
6. **二重処理しない**: 台帳（後述）を読み，既に報告済み・スキップ指定の項目は再報告しない．PR コメントも同じ PR に同じ趣旨で 2 度投稿しない
7. **ユーザーが起動した時だけ動く**: Claude が自発的に本スキルを呼んではならない（`/loop /deps-update` でのラップはユーザー起動とみなす）

## 引数 (Arguments)

`$ARGUMENTS` を次のように解釈する:

- 先頭トークンが `report` なら**報告のみモード**（マージも PR コメントもしない．分析結果を完了報告と台帳に書くだけ）．`merge` または省略なら**通常モード**（ゲートを満たすものをマージし，残りを報告する）
- 残りのトークンに `#12` / `12` 形式の番号があれば，その PR だけを対象にする（省略時は open な Dependabot PR すべて）

## ステップ 0: セットアップ (Setup)

1. **前提の確認**: `gh auth status` と `gh repo view` が通ること．作業ツリーがクリーン（`git status --short` が空）であること．クリーンでなければ**停止**してユーザーに伝える（stash も行わない）．開始時のブランチ名を控える
2. **Dependabot 設定の確認**: `reference.md`「前提の確認」で Dependabot alerts / security updates が有効か，`.github/dependabot.yml` が存在するかを確認する．無効・不存在なら処理は続けるが，完了報告で `/setup` の「GitHub リポジトリのセキュリティ設定」「依存バージョン更新の設定」を案内する
3. **検証スイートの特定**: `docs/02_ENV/ENV_04_開発コマンド.md` があればそれを唯一の参照先とする．無ければ構成ファイル（`package.json`，`pubspec.yaml`，`Cargo.toml`，`go.mod`，`pyproject.toml` 等）から **依存インストール・テスト・（あれば）ビルド／型チェック／リンタ** のコマンドを特定する．推測で試さず，特定できなければ「ローカル検証不可」として扱う
4. **CI の有無**: `.github/workflows/` の有無と，対象 PR に `statusCheckRollup` が付くかで判定する．CI があれば Tier A の判定源は CI，無ければローカル検証に切り替える
5. **台帳の読み込み**（いずれも `.gitignore` 対象のローカル運用状態．無ければ空として扱い，追跡されていなければ `.gitignore` に追記する）:
   - `.claude/deps-update-merged.md` — 本スキルがマージした PR の記録（`/loop` 越しに最終報告を積み上げる）
   - `.claude/deps-update-report.md` — 人間の判断待ち項目（メジャー更新・CI 赤・修正版なし alert 等）．**既出は再記録しない**
   - `.claude/deps-update-skip.md` — ユーザーが「触るな」と指定した PR 番号／パッケージ．該当する PR・alert は一切操作せず，完了報告で件数だけ伝える

## ステップ 1: 収集 (Collect)

`reference.md`「PR の収集」「alert の収集」に従い，以下を取得する．

- **Dependabot PR**: open かつ author `app/dependabot` の PR 一覧（番号・タイトル・本文・ブランチ・`mergeable`・`mergeStateStatus`・チェック状況・ラベル）
- **Dependabot alerts**: open な alert 一覧（パッケージ・エコシステム・重大度・脆弱範囲・修正版・GHSA・**紐づく PR の番号**・Dependabot が PR を作れなかった場合のエラー）

alert に紐づく PR は「security update」，それ以外の Dependabot PR は「version update」として扱う（alert 情報が取れない場合は PR 本文の GHSA リンク・`security` ラベルで代用する）．

## ステップ 2: 各 PR の判定 (Classify & Verify)

対象 PR を **security update → パッチ → マイナー → その他** の順に並べ，1 件ずつ以下を行う．

1. **スキップ確認**: `deps-update-skip.md` に該当すれば飛ばす
2. **分類**（`reference.md`「更新規模の分類」）:
   - 更新規模: `patch` / `minor` / `major` / `unknown`．**`0.x` 系のマイナー更新は破壊的変更を含みうるため `major` 相当**として扱う．グループ PR は全メンバーの最大規模で判定する（`minor-and-patch` グループは定義上 `minor`）
   - 依存スコープ: 本番依存 / 開発依存（マニフェストの該当セクションから判定．判定不能なら本番扱い）
   - 種別: security / version
3. **状態確認**: `mergeable` が `CONFLICTING` なら `@dependabot rebase` をコメントして今回は見送る（台帳には書かず，完了報告で「次回再試行」として伝える）．`mergeStateStatus` が `BEHIND` で CI がある場合も同様に rebase を促す（Dependabot は base 更新時に自動 rebase するため，通常は次回には解消している）
4. **検証 Tier の決定**:
   - **Tier A（完全検証）**: CI があり全チェック緑，または CI が無く**ローカル検証**（`reference.md`「ローカル検証」: PR ブランチに最新 `main` を取り込んだ状態で依存インストール → 検証スイート）が全緑
   - **Tier B（部分検証）**: テストが無い／特定できないが，依存インストールとビルド／型チェックは通る
   - **Tier C（検証不能）**: CI が無く，ローカルでもインストール・ビルドの確認ができない
   - CI が `PENDING` の場合は `reference.md`「CI の完了待ち」に従い一定時間待つ．完了しなければローカル検証に切り替えるか，今回は見送る（完了報告で「CI 待ち」）
5. **判定表**に従って処理を決める:

   | 更新規模 | Tier A | Tier B | Tier C |
   | --- | --- | --- | --- |
   | `patch` | マージ | マージ | 報告 |
   | `minor` | マージ | 報告 | 報告 |
   | `major` / `0.x` マイナー / `unknown` | 報告（分析付き） | 報告（分析付き） | 報告（分析付き） |

   - CI が赤（または ローカル検証が赤）の PR は規模に関わらず**報告**（失敗箇所の要約を添える）．失敗が依存更新と無関係な一時的エラーに見える場合は `@dependabot recreate` をコメントしてよいが，マージはしない
   - security update は同じ表で判定する（優先順位が先になるだけで，ゲートは緩めない）．報告になった security update は完了報告で **⚠ 優先** と明示する
6. **実行**:
   - **マージ**: `gh pr merge <番号> --merge --delete-branch`（Git 規約どおりマージコミット）．成功したら `deps-update-merged.md` に追記し，ローカル `main` を `git pull` する．以降の PR は Dependabot が自動 rebase するため，次の PR へ進む前にステップ 2-3〜2-4 を取り直す
   - **報告**: `reference.md`「メジャー更新の影響分析」に従い，リリースノート／変更履歴から破壊的変更を抽出し，コードベース内の利用箇所（import・API 呼び出し）を特定して影響範囲と推奨対応（そのままマージ可 / `/implement` で追従が必要 / 見送り）をまとめる．`deps-update-report.md` に追記し，通常モードでは同じ内容を PR にコメントする（`reference.md`「PR コメントの書式」．既に本スキルのコメントがある PR には再投稿しない）
   - **report モード**: マージ・コメントをせず，判定結果（「マージ可」「報告」）と分析を完了報告と台帳にだけ書く

## ステップ 3: PR が付かない alert (Alerts without PR)

紐づく PR が無い open alert を 1 件ずつ見る．

- **修正版が無い**（`firstPatchedVersion` なし）: 報告．回避策（別パッケージ・利用箇所の無効化等）があれば添える
- **修正版はあるが Dependabot が PR を作れなかった**（推移的依存・対応外のマニフェスト・`open-pull-requests-limit` 超過・エラー）: エラー内容と「どの依存をどのバージョンへ」を報告し，推移的依存ならロックファイルの更新コマンド（`npm update <pkg>` / `dart pub upgrade <pkg>` 等，検証スイートでの確認付き）を推奨対応として示す．**自分では依存を書き換えない**（不変条件 2）．必要なら `/implement` での対応を案内する
- いずれも `deps-update-report.md` に `[alert]` として追記する（既出は再記録しない）

## ステップ 4: 後片付けと完了報告 (Cleanup & Report)

1. ローカル検証用のブランチ・マージ状態をすべて破棄し，開始時のブランチへ戻す．`git status --short` が空であることを確認する
2. ユーザーに以下を報告する（台帳を根拠にする）:
   - モード（merge / report）と対象 PR 数，Dependabot 設定の状態（無効なら `/setup` の該当手順を案内）
   - **マージした PR**: 番号・パッケージと版・種別（security / version）・規模・検証 Tier（CI / ローカル / ビルドのみ）．**人間が行うべき動作確認**があれば添える（例: UI ライブラリの更新は実機で表示確認）
   - **⚠ 判断待ち（要人間対応）**: メジャー更新（影響分析・推奨対応付き）／CI 赤／修正版なし alert／Dependabot が PR を作れなかった alert．security 由来のものは **⚠ 優先** を付け，上に並べる
   - 見送り: 競合で `@dependabot rebase` を依頼したもの，CI 待ちのもの（次回再試行），`deps-update-skip.md` によるスキップ件数
   - 次のアクション: 判断待ちの各項目について「PR をそのままマージ」「`/implement` で追従してからマージ」「`@dependabot ignore this major version` で見送り（人間がコメント）」のどれを推奨するか
3. team モードでは，マージした PR の一覧をチームに周知するよう促す（全員が `git pull` する．GUIDE_03）

## /loop との併用 (Use with /loop)

Dependabot は週 1 回（`dependabot.yml` の `interval`）まとめて PR を作るため，単発実行で十分なことが多い．放置中に取り込みたい場合は `/loop 1d /deps-update` のように日次でラップしてよい．対象が無い回は収集だけで即終了するので空回りのコストは小さいが，2 回連続で「マージ 0・新規報告 0」なら `/loop` を終了してよい．

## 停止条件 (Stop Conditions)

- 作業ツリーがクリーンでない／`gh` 未認証／リモートが GitHub でない → 開始せずに報告
- ローカル検証中にコンフリクト・環境エラーで検証が成立しない → その PR は Tier C として扱い，他の PR の処理は続ける
- `gh pr merge` が失敗（ブランチ保護・権限不足） → その PR は報告に回し，失敗理由を伝える．team モードで「他メンバー Approve 必須」のブランチ保護が効いている場合は，本スキルはマージ可と判定した PR の一覧を提示するにとどめる
