---
name: task-handoff
model: inherit
description: "現在 In Progress の Issue に進捗メモ（完了済み・残タスク・確定事項・git 状態・再開方法）をコメントで投稿する．セッション中断・引継ぎ用．"
argument-hint: "<追加メモ（省略可）>"
---

あなたは作業引継ぎ担当です．現在 **In Progress** の Issue に，次セッション・次メンバーが再開できるだけの情報を進捗メモとして投稿します．

GUIDE_06（チーム開発ルール）の「進捗は Issues / Projects と git 履歴で追う」方針に沿い，引継ぎ情報を Issue コメントに集約します（`CLAUDE.md` に進捗は書かない）．

## 前提確認 (Pre-check)

リポジトリ所有者を取得する（`gh repo view --json owner --jq .owner.login`）．

## ステップ 1: 対象 Issue の特定 (Identify Issue)

1. Project を特定する（`gh project list --owner <owner>`）．見つからない場合は「Project 未設定のため進捗メモを投稿できません．管理者の初期設定が必要です（GUIDE_06）．」と伝えて終了する．
2. ボードから Status が `In Progress` の Issue を抽出する（`gh project item-list <Project番号> --owner <owner> --format json`）．
3. 件数で挙動を変える:
   - **0 件**: 「In Progress の Issue がありません．まず `/task-start <Issue番号>` で着手してから本コマンドを使ってください．」と伝えて終了．
   - **1 件**: そのまま対象とする．
   - **複数件**: 各 Issue 番号・タイトルを示し，「どれを対象にしますか？」とユーザーに確認する．

## ステップ 2: コンテキスト収集 (Collect Context)

以下を取得してドラフト生成材料とする．

- `gh issue view <Issue番号> --json number,title,body,comments` で Issue 本文と過去コメントを取得
- `git branch --show-current` で作業ブランチ
- `git rev-parse --short HEAD` で現在 HEAD
- `git log main..HEAD --oneline` で main から積んだコミット一覧
- `git status --porcelain` で未コミット変更の有無
- 直近のセッション会話履歴から「**確定した重要な設計判断**」と「**未決定で次に詰める点**」を抽出

## ステップ 3: ドラフト生成 (Draft)

以下のフォーマットで進捗メモを生成する．**該当なしのセクションは省略してよい**．

````markdown
## 進捗メモ (YYYY-MM-DD)

<!-- $ARGUMENTS が指定されていればここに「### 追加メモ」セクションで掲載 -->

### 完了済み

| フェーズ / タスク | 成果物 | コミット |
| --- | --- | --- |
| ... | ... | <短縮 SHA> |

### 残タスク

- ...

### 確定した重要な設計判断（再開時の文脈）

- ...

### 未決定で次に詰める点

- ...

### 現在の git 状態

- ブランチ: `<ブランチ名>`
- HEAD: `<短縮 SHA>`
- 未コミット変更: <あり / なし>

### 次セッションの再開方法

```bash
git checkout <ブランチ名>
```

<再開時の指針コメント．例: 「`/setup` を再実行するか，手動でフェーズ N から続行」>
````

### ドラフトの粒度方針

- **完了済み**: 当該セッションで積んだコミット中心．本 Issue に関わる範囲のみ．
- **残タスク**: Issue 本文の TODO や未着手項目を整理．
- **確定した設計判断**: セッション中にユーザーと合意して**今後の作業の前提**になるもの．既に Issue 本文や過去コメントに明記されている内容は重複させない（差分のみ）．
- **未決定で次に詰める点**: 次セッションで判断が必要なもの．
- **粒度感**: 1 セッション分の引継ぎ．週次レポートではない．

## ステップ 4: 承認 (Approval)

ドラフトをユーザーに提示し，「**この内容で投稿しますか？ 修正点があれば指示してください．**」と確認する．

ユーザーの明示的な同意（「OK」「投稿して」「これで」等）が得られるまで投稿しない．修正指示があれば反映して再提示する．

## ステップ 5: 投稿 (Post)

`gh issue comment <Issue番号> --body "<本文>"` で投稿する．投稿後，コメントの URL を表示する．

## 完了サマリー (Summary)

以下を提示する:

- **Issue**: 番号・タイトル・URL
- **コメント URL**
- **次のステップ**: 「次セッションで再開する場合は `git checkout <ブランチ名>` から始めてください．」

## 注意事項 (Notes)

- 本コマンドは Issue を **Done に動かさない**．In Progress のまま残す（作業継続前提）．完了させたい場合は別途 `/commit merge` で PR をマージ → `Closes #<番号>` で自動クローズ．
- 進捗メモは**コメント**として投稿する．Issue 本文は書き換えない．
- Projects 操作には `project` スコープが必要．スコープエラーが出たら `gh auth refresh -s project` を実行する（GUIDE_07）．
