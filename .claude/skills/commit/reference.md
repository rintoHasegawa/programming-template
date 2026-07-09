# Git 開発フロー・トラブルシューティング (Git Workflow Reference)

`/commit` の push・PR・マージ処理で参照する詳細手順と，問題発生時の対処手順．
ブランチ命名・コミットメッセージ等の規約は `.claude/rules/git-conventions.md` に従う．

## 開発フロー (Development Workflow)

作業ブランチをリモートへ送り，`main` にマージされるまでの手順．

### リモートへのプッシュ

作業ブランチをリモートリポジトリへ送信する．

```bash
git push origin feature/new-function
```

### プルリクエスト (PR) の作成

GitHub CLI (`gh`) を使用して PR を作成する．

```bash
gh pr create --title "[add] 新機能を実装" --body "概要"
```

- `--title` はコミットメッセージと同じ書式（`[タグ] 内容`）とする．
- 必要に応じて `--reviewer` でレビュワーを指定する．

### レビューと修正の対応

- レビュワーのコメントを確認し，修正が必要な場合はローカルで修正・コミット・プッシュを繰り返す．
- 全ての指摘に対応し，レビュワーから Approve をもらう．

### マージの実行

- マージ方式は「Create a merge commit」を使用する（作業ブランチの全コミット履歴が `main` に残る）．
  - 「Squash and merge」「Rebase and merge」は使用しない．

```bash
gh pr merge --merge --delete-branch
```

### ローカル環境のクリーンアップ

マージ完了後はローカル環境も最新状態に戻し，古いブランチを削除する．

```bash
git checkout main
git pull origin main
git branch -d feature/new-function
```

## トラブルシューティング (Troubleshooting)

### コンフリクトが発生した場合

他メンバーの変更と競合した場合の対処手順．

**main の取り込み**

作業ブランチに `main` の最新内容をリベースして競合箇所を洗い出す．`merge` ではなく `rebase` を使うことで，履歴が線形に保たれレビューしやすくなる．

```bash
git checkout main
git pull origin main
git checkout feature/new-function
git rebase main
```

**競合の解消**

エディタ上で `<<<<<<<`，`=======`，`>>>>>>>` で囲まれた箇所を手動で修正する．修正後，以下のコマンドでリベースを続行する．

```bash
git add [修正したファイル]
git rebase --continue
```

**AI 補助によるコンフリクト解消**

Claude Code にコンフリクトを解消させる場合は，衝突の種類に応じて進め方を変える．

- **自明・テキスト的な衝突**（import の競合，隣接行の変更など，双方の意図が両立するもの）
  - AI が解消してよい．人間は解消結果の要点を確認するだけでよい．
- **意味的な衝突**（両側がロジックを非互換に変更しているもの）
  - AI に単一の解決結果を提示させて追認してはならない．AI は以下を提示し，人間が選択する．
    - 両側が各々何をしたかったか（自分の Issue と，相手のコミット・Issue の目的）
    - 解決候補を 2 つ以上と，各案を採った場合の帰結
    - 影響範囲と壊れうる箇所

解消後は衝突の種類によらず，必ずテスト（`/implement` の Tester）と作者による Phase 1 動作確認を行う．人間の判断は「どの意図が正しいか」を，テストは「実際に動くか」を担保するものであり，どちらか一方を省略してはならない．

**プッシュ**

全ての競合が解消されたらプッシュする．リベース後は履歴が書き換わるため通常の push は拒否されるが，他メンバーが push 済みの履歴を誤って上書きしないよう，`-f` ではなく `--force-with-lease` を用いる（自分が最後に fetch した時点のリモート状態と一致する場合のみ上書きし，ズレていれば拒否される）．

```bash
git push --force-with-lease origin feature/new-function
```

### 間違えて main にコミットしてしまった場合

**まだプッシュしていない場合**

コミットを取り消し，変更内容を保持したまま新しいブランチへ移動する．

```bash
git reset --soft HEAD^
git checkout -b feature/new-function
git commit -m "[add] ..."
```

**すでにプッシュしてしまった場合**

`main` への force push はチーム全員の履歴を壊す危険があるため，自分では対処しない．直ちにチームメンバーに報告し，全員で対応方針を決める．
