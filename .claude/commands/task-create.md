---
name: task-create
model: inherit
description: "GUIDE_06/07 に従い，Issue を作成しボードに追加する（着手はしない）．タスクの事前計画用．"
argument-hint: "<タスクの説明>"
---

あなたはタスク計画担当です．
GUIDE_06（チーム開発ルール）と GUIDE_07（Issues・Projects 運用ガイド）に従い，新しいタスクの Issue を作成してボードに並べてください．

本コマンドは**タスクを事前に定義する**ためのものです．着手は別途 `/task-start <Issue番号>` で行います（または手動で）．Issue 無しで進める作業はこのコマンドを使う必要はありません（GUIDE_06）．

## 前提確認 (Pre-check)

$ARGUMENTS（タスクの説明）が指定されているか確認する．指定がなければ「タスクの説明を指定してください」と伝えて終了する．

## ステップ 1: Project の特定 (Project Lookup)

1. リポジトリ所有者を取得する（`gh repo view --json owner --jq .owner.login`）．
2. Project を特定する（`gh project list --owner <owner>`）．複数ある場合はユーザーに確認する．見つからない場合はその旨を伝え，ステップ 3（ボード追加）は飛ばして進める（管理者の初期設定が未完了．GUIDE_06）．

## ステップ 2: Issue 作成 (Create Issue)

$ARGUMENTS の説明から Issue 本文を組み立てる．

- **やること**は必ず書く．
- **完了条件（受け入れ基準）**は非自明な場合のみ書く（GUIDE_06）．
- 担当者は**アサインしない**（誰が拾うかは着手時に決まる）．

`gh issue create --title "<タイトル>" --body "<本文>"` で作成する．

## ステップ 3: ボードに追加 (Add to Board)

GUIDE_07「Projects の操作」に従う（Project が無い場合は飛ばす）．

1. `gh project item-add <Project番号> --owner <owner> --url <Issue の URL>` でボードに追加する．
2. 追加されたアイテムの Status を `Todo` に設定する（`gh project field-list`・`item-list` で必要な ID を取得し `gh project item-edit`）．
3. `gh project` の書き込み系は成功しても無出力のことがある．`gh project item-list` で結果を確認する（GUIDE_07）．

## ステップ 4: 完了サマリー (Summary)

以下を提示する:

- **Issue**: 番号・タイトル・URL
- **ボード**: Todo
- **次の手順**: 「このタスクに着手する場合は `/task-start <Issue番号>` を実行してください．」

## 注意事項 (Notes)

- 複数タスクを一括で作る場合は，本コマンドを複数回実行してください．
- Projects 操作には `project` スコープが必要．スコープエラーが出たら `gh auth refresh -s project` を実行する（GUIDE_07）．
