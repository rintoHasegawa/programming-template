---
name: task-start
model: inherit
description: "GUIDE_06/07 に従い，既存 Issue を拾って作業を開始する（アサイン・ボード In Progress・作業ブランチ作成）．"
argument-hint: "<Issue 番号>"
---

あなたはタスク着手の準備担当です．
GUIDE_06（チーム開発ルール）と GUIDE_07（Issues・Projects 運用ガイド）に従い，**既存 Issue を拾って**作業に取り掛かるための準備を整えてください．

本コマンドは既存 Issue から作業を始めるためのものです．新規 Issue を立てる場合は `/task-create` を使ってください．Issue 無しで小さい変更や試験的作業を進める場合はこのコマンドは不要です（GUIDE_06）．

## 前提確認 (Pre-check)

1. 作業ツリーの状態を確認する（`git status`）．未コミットの変更がある場合は，その旨をユーザーに伝え，先に片付けるか続行するか確認する．
2. 現在のブランチを確認する（`git branch --show-current`）．`main` 以外にいる場合は，そのブランチに未マージのコミットが残っていないか確認する（`git log main..HEAD --oneline`）．残っていれば「前の作業が未マージです．先にマージしてから着手するのが推奨です．続行しますか？」とユーザーに確認する（直列運用．GUIDE_06）．
3. $ARGUMENTS を確認する．Issue 番号（数値または `#数値`）が指定されていなければ「対象の Issue 番号を指定してください．新規 Issue を立てる場合は `/task-create` を使います」と伝えて終了する．

## ステップ 1: 直列運用チェック (Serial Check)

GUIDE_06「作業の直列化」より，同時に進行中のコード作業は原則 1 件とする．

1. リポジトリ所有者を取得する（`gh repo view --json owner --jq .owner.login`）．
2. Project を特定する（`gh project list --owner <owner>`）．複数ある場合はユーザーに確認する．見つからない場合はその旨を伝え，ステップ 3（ボード操作）は飛ばして進める（管理者の初期設定が未完了．GUIDE_06）．
3. ボードのアイテムで Status が `In Progress` のものを数える（`gh project item-list <Project番号> --owner <owner> --format json`）．対象 Issue 以外で 1 件以上ある場合は，該当タスクを示し「直列運用では進行中の作業は原則 1 件です．続行しますか？」とユーザーに確認する（禁止ではなく原則．GUIDE_06）．

## ステップ 2: 対象 Issue の確認・アサイン (Verify & Assign)

1. `gh issue view <Issue番号>` で Issue の内容を確認する．存在しない・クローズ済みの場合はユーザーに知らせて判断を仰ぐ．
2. 担当者が未設定，または自分以外なら，`gh issue edit <Issue番号> --add-assignee @me` で自分をアサインする（他人がアサインされている場合は，重複着手にならないかユーザーに確認）．

## ステップ 3: ボードを In Progress へ (Board)

GUIDE_07「Projects の操作」に従う（Project が無い場合はこのステップを飛ばす）．

1. 対象 Issue がボードに未追加なら `gh project item-add <Project番号> --owner <owner> --url <Issue の URL>` で追加する．
2. 対象アイテムの Status を `In Progress` に変更する（`gh project field-list`・`item-list` で必要な ID を取得し `gh project item-edit`）．
3. `gh project` の書き込み系は成功しても無出力のことがある．`gh project item-list` で結果を確認する（GUIDE_07）．

## ステップ 4: 作業ブランチ作成 (Branch)

GUIDE_04 に従う．

1. `git checkout main && git pull origin main` で `main` を最新化する．
2. GUIDE_04 の基本形式（`[プレフィックス][概要]`）でブランチ名を決める．プレフィックスはタスク種別（`feature` / `fix` / `refactor` / `docs` / `chore`），概要は内容を表す英単語 2〜4 語（kebab-case）とする．ブランチ名に Issue 番号は含めない（GUIDE_06）．
3. `git checkout -b <ブランチ名>` で作業ブランチを作成する．

## ステップ 5: 完了サマリー (Summary)

以下を提示する:

- **Issue**: 番号・タイトル・URL
- **ボード**: In Progress
- **ブランチ**: 作成したブランチ名
- **次の手順**: 「`/implement` に対象 Issue の内容を渡して実装を開始してください（`gh issue view <Issue番号>` で取得できます）．」

## 注意事項 (Notes)

- branch↔Issue の紐付けは，後の PR 本文の `Closes #<Issue番号>` で行う（GUIDE_06）．ブランチ名には番号を入れない．
- Projects 操作には `project` スコープが必要．スコープエラーが出たら `gh auth refresh -s project` を実行する（GUIDE_07）．
- 新規 Issue を立てる場合は `/task-create`，Issue 無しで進める場合は本コマンドを使わず直接 `/implement` 等へ．
