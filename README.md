# MLB選手デイリートラッカー

指定したMLB選手について、試合前・試合後にDiscordへ自動通知し、最新成績を
GitHub Pagesの固定URLで自動更新するツールです。サーバー・ドメインは不要、
GitHub Actionsの無料枠のみで動作します。

## できること

- 試合開始90分前に「対戦相手・開始時刻(JST)」をDiscordへ通知
- 試合終了(Final)後に「その試合の成績・シーズン累計・OPS」をDiscordへ通知
- 上記と同じ内容を`index.html`として毎回再生成し、GitHub Pagesで公開
- 複数選手を1つのページ・1つのDiscordチャンネルでまとめて管理
- 各選手にMLB公式ページ・Baseball Savant(Statcast詳細指標)へのリンクを表示
- API取得失敗時は自動スキップ、次回実行(30分後)で復帰
- 試合がない日は静かにスキップ(通知なし)

データ源はMLB公式のMLB Stats API(statsapi.mlb.com、APIキー不要)です。

---

## セットアップ手順

### 1. Discord Webhook URLを取得する

1. 通知を送りたいDiscordチャンネルの設定を開く
2. 「連携サービス」→「ウェブフックを作成」
3. 表示された Webhook URL をコピーしておく(`https://discord.com/api/webhooks/...`)

### 2. GitHub Secretsに登録する

このリポジトリの Settings → Secrets and variables → Actions → New repository secret

- Name: `DISCORD_WEBHOOK_URL`
- Value: 手順1でコピーしたURL

### 3. 追跡する選手を設定する

選手名(ローマ字)からIDを調べます。

```bash
python scripts/find_player_id.py "Shohei Ohtani"
```

表示された `id` を [config.json](config.json) の `players` 配列に追記・編集します。

```json
{
  "id": 660271,
  "name_ja": "大谷翔平",
  "name_en": "Shohei Ohtani"
}
```

**Claude Codeに頼む場合**: このリポジトリには `add-mlb-player` スキルを同梱しています。
mlb-trackerディレクトリで作業中に「〇〇選手を追加して」「△△選手を外して」と伝えるだけで、
選手ID検索・config.json編集・コミット・pushまで自動で行います。

- `name_ja` はDiscord通知・Webページに表示される表記です
- 移籍しても選手IDは変わらないため、IDで管理しています(現在所属チームはAPIから毎回自動取得)

### 4. GitHub Pagesを有効化する

Settings → Pages → Build and deployment

- Source: `Deploy from a branch`
- Branch: `main` / `/ (root)`

保存すると `https://<ユーザー名>.github.io/<リポジトリ名>/` が固定URLになります。
Actionsが `index.html` を更新するたびに、このURLの中身も自動で更新されます。

### 5. 動作確認

Actions タブ → `MLB Tracker` → `Run workflow` で手動実行できます。
成功すると `index.html` と `state.json` がコミットされ、Pagesに反映されます。

---

## ローカルでの動作確認

```bash
pip install -r requirements.txt
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxxx"  # 任意(未設定でも動く。通知だけスキップ)
python scripts/fetch_and_notify.py
```

`index.html` が生成され、開くとカードUIで確認できます。

---

## ファイル構成

```
config.json               追跡する選手のリスト・タイムゾーン等の設定
state.json                通知済み状態のキャッシュ(自動生成・自動更新)
index.html                公開されるページ本体(自動生成・自動更新)
scripts/fetch_and_notify.py  取得・判定・通知・HTML生成のメイン処理
scripts/find_player_id.py    選手名→選手IDを調べるツール
.github/workflows/tracker.yml  30分おき自動実行のワークフロー
```

## 動作の仕組み

- GitHub Actionsが30分おき(および手動実行)に `scripts/fetch_and_notify.py` を実行
- スケジュール実行は遅延・スキップがあり得るため、「試合開始90分以内」という
  幅を持たせた条件で判定(ちょうどのタイミング狙い撃ちにしない)
- 「どの試合をどの段階まで通知したか」は `state.json` の `notified` に記録し、
  リポジトリへコミットして次回実行に引き継ぐ。日付(JST)が変わると自動リセット
- API取得に失敗した選手がいても他の選手の処理は継続し、失敗した選手は
  前回成功時のキャッシュ(`state.json` の `cache`)でページ表示を維持する

## WAR・詳細セイバー指標について

MLB Stats APIの公開エンドポイントにはWAR(Wins Above Replacement)が
含まれていないため、値そのものは今回は非対応です。代わりに各選手カード・
Discord通知に「MLB公式」「Baseball Savant」への外部リンクを付けており、
Statcastベースの高度な指標(xwOBA・バレル率など)はそちらで確認できます。

厳密なbWAR/fWARはBaseball-ReferenceやFanGraphsが算出していますが、
これらはMLB Stats APIとは別のID体系(bbrefID等)を使っており、
選手ID(personId)から確実に変換する公式な方法がないため、リンク切れの
リスクを避けて今回は対象外にしています。将来的に対応する場合は
Chadwick Bureau Register等のID対応表を別途取り込む必要があります。

`process_player()` が返す `card` の辞書に項目を追加し、`render_card()` の
表示とDiscord埋め込みに1行足せば拡張できる構造にしてあります。
