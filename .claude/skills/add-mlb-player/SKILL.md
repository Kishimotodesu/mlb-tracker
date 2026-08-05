---
name: add-mlb-player
description: mlb-trackerで追跡するMLB選手を追加・削除したいとき(「〇〇選手を追加して」「トラッキング対象から外して」「もう1人追いたい」等)に起動。選手名からMLB Stats APIで選手IDを検索し、config.jsonに追記/削除してコミット・pushまで行う。
version: 1.0.0
---

# add-mlb-player

mlb-trackerが追跡する選手を [config.json](../../../config.json) に追加/削除し、
コミット・pushするスキル。GitHub Pages・Discord通知への反映は次回の
GitHub Actions自動実行(最大30分以内)で行われるため、デプロイ操作は不要。

## 発火条件

- 「(選手名)を追加して」「トラッキングに入れて」「もう1人追いたい」等、追跡対象選手の追加依頼
- 「(選手名)を外して」「削除して」「もう追わなくていい」等の除外依頼

## 実行ステップ

1. **選手の特定**
   - 選手名(日本語でも可、内部でローマ字に変換して検索)を確認
   - 同姓同名の可能性がある場合は所属球団も確認しておく

2. **選手IDを検索**(追加の場合)
   ```bash
   python scripts/find_player_id.py "選手名(英語表記)"
   ```
   - 複数ヒットした場合は所属球団・年齢等でユーザーに確認してから確定する
   - MLB非所属(NPB・独立リーグ等)の選手はMLB Stats APIに試合データが無いため追跡不可。
     その場合はその旨をユーザーに伝えて依頼を保留する

3. **config.jsonを編集**
   - 追加: `players` 配列に以下の形式で追記
     ```json
     { "id": <id>, "name_ja": "<日本語表記>", "name_en": "<英語表記>" }
     ```
   - 削除: 該当選手のエントリを配列から削除

4. **コミット・push**
   ```bash
   git add config.json
   git commit -m "feat: <選手名>を追跡対象に追加"   # 削除時は「除外」等に言い換え
   git push
   ```

5. **完了報告**
   - 追加/削除した選手名と、次回のActions自動実行(最大30分以内)から反映される旨を伝える

## 注意点

- 選手IDで管理しているため、移籍しても`config.json`の変更は不要
  (所属チームは実行のたびにMLB Stats APIから自動取得される)
- `state.json`・`index.html`は自動生成ファイルなので手動編集しない
