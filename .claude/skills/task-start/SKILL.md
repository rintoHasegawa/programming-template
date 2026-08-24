---
name: task-start
model: inherit
description: "既存 Issue を拾って作業を開始する（アサイン・ボード In Progress・作業ブランチ作成）．solo / team 両モードで使用できる．"
argument-hint: "<Issue 番号>"
---

あなたはタスク着手の司令塔です．着手準備（アサイン・ボード操作・作業ブランチ作成）の実行は **ops-runner エージェント**（軽量モデル）に委譲し，あなた自身はユーザー確認の仲介と結果の報告だけを行います（使用量節約のため，git・gh 操作を自分では行わない）．

本スキルは **solo / team 両モードで使用できる**．team モードでは GUIDE_03（チーム開発ルール）にも従うこと．solo モードでは GUIDE_03 は存在しないため，本スキルと本スキルの `reference.md` の記述を基準とする．

本コマンドは既存 Issue から作業を始めるためのものです．新規 Issue を立てる場合は `/task-create` を使ってください．Issue 無しで小さい変更や試験的作業を進める場合はこのコマンドは不要です．

## ステップ 1: 前提確認 (Pre-check)

$ARGUMENTS を確認する．Issue 番号（数値または `#数値`）が指定されていなければ「対象の Issue 番号を指定してください．新規 Issue を立てる場合は `/task-create` を使います」と伝えて終了する．

## ステップ 2: 委譲 (Delegate)

ops-runner エージェントを起動し，プロンプトに以下を含める:

- 手順書: 本スキルの `reference.md`（`.claude/skills/task-start/reference.md`）の「/task-start 実行手順」を読んで従うこと
- 対象の Issue 番号
- **ブランチ名**: Issue の内容を把握している場合（同セッションで `/task-create` した直後等）は `.claude/rules/git-conventions.md` の命名規則で決めて渡す．把握していなければ ops-runner に規約どおり提案・作成させ，報告で名前を確認する
- 確認が必要な事態（未コミット変更・未マージブランチ・進行中タスクあり・他人アサイン等）では，該当項目をまとめて停止・報告すること

## ステップ 3: 確認の仲介 (Confirm)

エージェントが確認事項で停止した場合は，状況をユーザーに提示して判断を仰ぎ，承認された項目を「ユーザー承認済み」と明示して再委譲する．

## ステップ 4: 完了サマリー (Summary)

エージェントの報告をもとに以下を提示する:

- **Issue**: 番号・タイトル・URL
- **ボード**: In Progress（Project が無い場合は「アサインのみ（ボード未使用）」と表記する）
- **ブランチ**: 作成したブランチ名
- **次の手順**: 「`/implement` に対象 Issue の内容を渡して実装を開始してください（`gh issue view <Issue番号>` で取得できます）．」

## 注意事項 (Notes)

- branch↔Issue の紐付けは，後の PR 本文の `Closes #<Issue番号>` で行う．ブランチ名には番号を入れない
- Projects 操作には `project` スコープが必要．スコープエラーの停止報告が来たら `gh auth refresh -s project` をユーザーに案内する（`reference.md`「必要なスコープ」）
- 新規 Issue を立てる場合は `/task-create`，Issue 無しで進める場合は本コマンドを使わず直接 `/implement` 等へ
