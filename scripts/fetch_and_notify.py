"""
MLB選手デイリートラッカー メイン処理。

30分おきにGitHub Actionsから実行される想定。
1. config.json に設定した選手ごとに、当日(JST)の試合予定・直近の試合結果・シーズン成績を取得
2. 試合開始90分前(未通知なら)と、試合終了(Final、未通知なら)のタイミングでDiscordへ通知
3. 通知済み状態を state.json に保存(次回実行に引き継ぐ。日付が変わればリセット)
4. 取得結果をもとに index.html を再生成(GitHub Pagesで公開)

APIが失敗した選手は警告を出してスキップし、可能な限り前回成功時のキャッシュ値でHTMLを描画する。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
HTML_PATH = ROOT / "index.html"

API_BASE = "https://statsapi.mlb.com/api/v1"
JST = ZoneInfo("Asia/Tokyo")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def fetch_json(url: str, retries: int = 2, timeout: int = 10) -> dict:
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mlb-tracker/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.load(res)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
            last_err = err
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"API取得失敗: {url} ({last_err})")


# ---------------------------------------------------------------------------
# state / config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"date": "", "notified": {}, "cache": {}}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# MLB Stats API アクセス
# ---------------------------------------------------------------------------

def get_person_info(person_id: int) -> dict | None:
    """currentTeamとnameSlug(外部サイトリンク用)を含む選手情報を取得する。"""
    data = fetch_json(f"{API_BASE}/people/{person_id}?hydrate=currentTeam")
    people = data.get("people", [])
    if not people:
        return None
    return people[0]


def get_team_schedule(team_id: int, start_date: str, end_date: str) -> list[dict]:
    url = (
        f"{API_BASE}/schedule?sportId=1&teamId={team_id}"
        f"&startDate={start_date}&endDate={end_date}"
        f"&hydrate=team,linescore,decisions"
    )
    data = fetch_json(url)
    games = []
    for d in data.get("dates", []):
        games.extend(d.get("games", []))
    return games


def get_boxscore_player_stats(game_pk: int, person_id: int) -> dict | None:
    data = fetch_json(f"{API_BASE}/game/{game_pk}/boxscore")
    for side in ("home", "away"):
        players = data.get("teams", {}).get(side, {}).get("players", {})
        player = players.get(f"ID{person_id}")
        if player:
            return player
    return None


def get_season_stats(person_id: int, season: int) -> dict:
    url = (
        f"{API_BASE}/people/{person_id}/stats"
        f"?stats=season&group=hitting,pitching&season={season}"
    )
    data = fetch_json(url)
    result = {"hitting": None, "pitching": None}
    for group in data.get("stats", []):
        group_name = group.get("group", {}).get("displayName")
        splits = group.get("splits", [])
        if not splits:
            continue
        stat = splits[0].get("stat", {})
        if group_name == "hitting":
            result["hitting"] = stat
        elif group_name == "pitching":
            result["pitching"] = stat
    return result


# ---------------------------------------------------------------------------
# 判定ロジック
# ---------------------------------------------------------------------------

def parse_utc(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))


def to_jst(dt_utc: datetime) -> datetime:
    return dt_utc.astimezone(JST)


def format_jst_time(dt_utc: datetime) -> str:
    return to_jst(dt_utc).strftime("%m/%d %H:%M")


def team_side_and_opponent(game: dict, team_id: int) -> tuple[str, str, dict, dict]:
    home = game["teams"]["home"]
    away = game["teams"]["away"]
    if home["team"]["id"] == team_id:
        return "home", away["team"]["name"], home, away
    return "away", home["team"]["name"], away, home


def batting_summary(stat: dict) -> str | None:
    if not stat:
        return None
    at_bats = stat.get("atBats", 0)
    if at_bats == 0 and stat.get("plateAppearances", 0) == 0:
        return None
    hits = stat.get("hits", 0)
    hr = stat.get("homeRuns", 0)
    rbi = stat.get("rbi", 0)
    so = stat.get("strikeOuts", 0)
    bb = stat.get("baseOnBalls", 0)
    return f"{at_bats}打数{hits}安打 本塁打{hr} 打点{rbi} 三振{so} 四球{bb}"


def pitching_summary(stat: dict) -> str | None:
    if not stat:
        return None
    ip = stat.get("inningsPitched", "0.0")
    if ip in ("0.0", "", None):
        return None
    runs = stat.get("runs", 0)
    er = stat.get("earnedRuns", 0)
    so = stat.get("strikeOuts", 0)
    pitches = stat.get("numberOfPitches", stat.get("pitchesThrown", "?"))
    return f"{ip}回 {runs}失点(自責{er}) 奪三振{so} 球数{pitches}"


def hitting_season_line(stat: dict | None) -> str | None:
    if not stat:
        return None
    avg = stat.get("avg", "-")
    hr = stat.get("homeRuns", "-")
    rbi = stat.get("rbi", "-")
    ops = stat.get("ops", "-")
    return f"打率{avg} 本塁打{hr} 打点{rbi} OPS {ops}"


def pitching_season_line(stat: dict | None) -> str | None:
    if not stat:
        return None
    era = stat.get("era", "-")
    w = stat.get("wins", "-")
    l = stat.get("losses", "-")
    so = stat.get("strikeOuts", "-")
    return f"防御率{era} {w}勝{l}敗 奪三振{so}"


# ---------------------------------------------------------------------------
# 選手ごとの処理
# ---------------------------------------------------------------------------

def process_player(player_cfg: dict, season: int, jst_today) -> tuple[dict, list[dict]]:
    """戻り値: (card_data, discord_events)"""
    person_id = player_cfg["id"]
    name_ja = player_cfg["name_ja"]

    person = get_person_info(person_id)
    if not person or not person.get("currentTeam"):
        raise RuntimeError(f"{name_ja}: 所属チーム情報が取得できませんでした")
    team_id = person["currentTeam"]["id"]
    team_name = person["currentTeam"].get("name", "")
    name_slug = person.get("nameSlug", f"player-{person_id}")
    mlb_url = f"https://www.mlb.com/player/{name_slug}"
    savant_url = f"https://baseballsavant.mlb.com/savant-player/{person_id}"

    window_start = (jst_today - timedelta(days=8)).isoformat()
    window_end = (jst_today + timedelta(days=2)).isoformat()
    games = get_team_schedule(team_id, window_start, window_end)

    # gameDateをJSTに変換してソートしておく
    for g in games:
        g["_start_jst"] = to_jst(parse_utc(g["gameDate"]))

    games.sort(key=lambda g: g["_start_jst"])

    today_games = [g for g in games if g["_start_jst"].date() == jst_today]
    finished_games = [g for g in games if g.get("status", {}).get("abstractGameState") == "Final"]
    recent_game = finished_games[-1] if finished_games else None

    now_utc = datetime.now(timezone.utc)
    events: list[dict] = []

    today_game_display = []
    for g in today_games:
        side, opponent, _, _ = team_side_and_opponent(g, team_id)
        state_abstract = g.get("status", {}).get("abstractGameState")
        today_game_display.append(
            {
                "game_pk": g["gamePk"],
                "opponent": opponent,
                "is_home": side == "home",
                "start_jst": g["_start_jst"].strftime("%H:%M"),
                "status": state_abstract,
            }
        )

        minutes_until = (parse_utc(g["gameDate"]) - now_utc).total_seconds() / 60
        pregame_notify_minutes = player_cfg.get("_pregame_notify_minutes", 90)

        if state_abstract == "Preview" and 0 <= minutes_until <= pregame_notify_minutes:
            events.append(
                {
                    "type": "pregame",
                    "game_pk": g["gamePk"],
                    "player": player_cfg,
                    "opponent": opponent,
                    "is_home": side == "home",
                    "start_jst": g["_start_jst"].strftime("%Y/%m/%d %H:%M"),
                    "mlb_url": mlb_url,
                    "savant_url": savant_url,
                }
            )

        if state_abstract == "Final":
            events.append(
                {
                    "type": "postgame",
                    "game_pk": g["gamePk"],
                    "player": player_cfg,
                    "opponent": opponent,
                    "mlb_url": mlb_url,
                    "savant_url": savant_url,
                }
            )

    recent_game_display = None
    if recent_game:
        side, opponent, _, _ = team_side_and_opponent(recent_game, team_id)
        box = get_boxscore_player_stats(recent_game["gamePk"], person_id)
        bat_line = None
        pitch_line = None
        if box:
            bat_line = batting_summary(box.get("stats", {}).get("batting"))
            pitch_line = pitching_summary(box.get("stats", {}).get("pitching"))
        recent_game_display = {
            "date_jst": recent_game["_start_jst"].strftime("%m/%d"),
            "opponent": opponent,
            "batting": bat_line,
            "pitching": pitch_line,
        }

    season_stats = get_season_stats(person_id, season)

    card = {
        "name_ja": name_ja,
        "name_en": player_cfg.get("name_en", ""),
        "team": team_name,
        "today_games": today_game_display,
        "recent_game": recent_game_display,
        "season_hitting": hitting_season_line(season_stats.get("hitting")),
        "season_pitching": pitching_season_line(season_stats.get("pitching")),
        "mlb_url": mlb_url,
        "savant_url": savant_url,
        "updated_at": datetime.now(JST).strftime("%Y/%m/%d %H:%M"),
    }
    return card, events


# ---------------------------------------------------------------------------
# Discord通知
# ---------------------------------------------------------------------------

def link_line(event: dict) -> str:
    return f"[MLB公式]({event['mlb_url']}) ・ [Baseball Savant]({event['savant_url']})"


def build_pregame_embed(event: dict) -> dict:
    p = event["player"]
    vs = "vs" if event["is_home"] else "@"
    return {
        "title": f"⚾ {p['name_ja']} 試合前告知",
        "description": (
            f"{vs} {event['opponent']}\n開始: {event['start_jst']} (JST)\n\n{link_line(event)}"
        ),
        "color": 0x1D428A,
    }


def build_postgame_embed(event: dict, card: dict) -> dict:
    p = event["player"]
    lines = []
    if card["recent_game"]:
        if card["recent_game"].get("batting"):
            lines.append(f"打撃: {card['recent_game']['batting']}")
        if card["recent_game"].get("pitching"):
            lines.append(f"投球: {card['recent_game']['pitching']}")
    if card.get("season_hitting"):
        lines.append(f"シーズン(打): {card['season_hitting']}")
    if card.get("season_pitching"):
        lines.append(f"シーズン(投): {card['season_pitching']}")
    lines.append("")
    lines.append(link_line(event))

    return {
        "title": f"🏟️ {p['name_ja']} 試合結果速報",
        "description": f"vs {event['opponent']}\n" + "\n".join(lines),
        "color": 0xBF2F38,
    }


def send_discord_embed(embed: dict) -> bool:
    """1件のembedを送信する。成功したら True を返す(呼び出し側はこれを見てstateに反映する)。"""
    if not DISCORD_WEBHOOK_URL:
        print("警告: DISCORD_WEBHOOK_URL が未設定のため通知をスキップします")
        return False

    payload = json.dumps({"username": "MLBトラッカー", "embeds": [embed]}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "mlb-tracker/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            res.read()
        return True
    except urllib.error.URLError as err:
        print(f"警告: Discord通知の送信に失敗しました: {err}")
        return False


# ---------------------------------------------------------------------------
# HTML生成
# ---------------------------------------------------------------------------

def render_card(card: dict) -> str:
    if card["today_games"]:
        today_html = "".join(
            f'<div class="game-line">{"vs" if g["is_home"] else "@"} '
            f'{g["opponent"]} ・ {g["start_jst"]} JST ・ '
            f'<span class="status status-{g["status"].lower()}">{g["status"]}</span></div>'
            for g in card["today_games"]
        )
    else:
        today_html = '<div class="game-line muted">本日の試合予定はありません</div>'

    if card["recent_game"]:
        rg = card["recent_game"]
        parts = [f'{rg["date_jst"]} vs {rg["opponent"]}']
        if rg.get("batting"):
            parts.append(rg["batting"])
        if rg.get("pitching"):
            parts.append(rg["pitching"])
        recent_html = "<br>".join(parts)
    else:
        recent_html = '<span class="muted">直近の試合データがありません</span>'

    season_lines = []
    if card.get("season_hitting"):
        season_lines.append(card["season_hitting"])
    if card.get("season_pitching"):
        season_lines.append(card["season_pitching"])
    season_html = "<br>".join(season_lines) if season_lines else '<span class="muted">シーズン成績なし</span>'

    return f"""
    <div class="card">
      <div class="card-header">
        <div class="player-name">{card['name_ja']}</div>
        <div class="team-name">{card['team']}</div>
      </div>
      <div class="section">
        <div class="section-title">本日の試合</div>
        {today_html}
      </div>
      <div class="section">
        <div class="section-title">直近の試合結果</div>
        <div class="game-line">{recent_html}</div>
      </div>
      <div class="section">
        <div class="section-title">シーズン累計</div>
        <div class="game-line">{season_html}</div>
      </div>
      <div class="links">
        <a href="{card['mlb_url']}" target="_blank" rel="noopener">MLB公式</a>
        <a href="{card['savant_url']}" target="_blank" rel="noopener">Baseball Savant</a>
      </div>
      <div class="updated-at">更新: {card['updated_at']}</div>
    </div>
    """


def build_html(cards: list[dict], generated_at: str) -> str:
    cards_html = "\n".join(render_card(c) for c in cards)
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MLB選手デイリートラッカー</title>
<style>
  :root {{
    color-scheme: light dark;
  }}
  body {{
    margin: 0;
    padding: 16px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Kaku Gothic ProN", sans-serif;
    background: #0f1115;
    color: #e8e8ec;
  }}
  header {{
    text-align: center;
    margin-bottom: 20px;
  }}
  header h1 {{
    font-size: 1.3rem;
    margin: 0 0 4px;
  }}
  header .generated {{
    font-size: 0.8rem;
    color: #9a9aa5;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 14px;
    max-width: 1100px;
    margin: 0 auto;
  }}
  .card {{
    background: #1a1d24;
    border-radius: 12px;
    padding: 14px 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.3);
  }}
  .card-header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    border-bottom: 1px solid #2a2e38;
    padding-bottom: 8px;
    margin-bottom: 8px;
  }}
  .player-name {{
    font-size: 1.1rem;
    font-weight: 700;
  }}
  .team-name {{
    font-size: 0.75rem;
    color: #9a9aa5;
  }}
  .section {{
    margin-top: 8px;
  }}
  .section-title {{
    font-size: 0.7rem;
    color: #7d8ba1;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 2px;
  }}
  .game-line {{
    font-size: 0.88rem;
    line-height: 1.5;
  }}
  .muted {{
    color: #6e7280;
  }}
  .status {{
    font-size: 0.7rem;
    padding: 1px 6px;
    border-radius: 8px;
    background: #2a2e38;
  }}
  .links {{
    margin-top: 10px;
    display: flex;
    gap: 12px;
    font-size: 0.78rem;
  }}
  .links a {{
    color: #6ea8fe;
    text-decoration: none;
  }}
  .links a:hover {{
    text-decoration: underline;
  }}
  .updated-at {{
    margin-top: 10px;
    font-size: 0.68rem;
    color: #565a66;
    text-align: right;
  }}
  @media (prefers-color-scheme: light) {{
    body {{ background: #f4f5f7; color: #1a1d24; }}
    .card {{ background: #ffffff; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
    .card-header {{ border-bottom-color: #e5e7eb; }}
    .status {{ background: #eef0f4; }}
  }}
</style>
</head>
<body>
<header>
  <h1>⚾ MLB選手デイリートラッカー</h1>
  <div class="generated">最終更新: {generated_at} (JST)</div>
</header>
<div class="grid">
{cards_html}
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    config = load_config()
    state = load_state()
    season = config.get("season", datetime.now(JST).year)
    pregame_notify_minutes = config.get("pregame_notify_minutes", 90)

    jst_today_str = datetime.now(JST).strftime("%Y-%m-%d")
    jst_today = datetime.now(JST).date()

    if state.get("date") != jst_today_str:
        state["date"] = jst_today_str
        state["notified"] = {}
    state.setdefault("notified", {})
    state.setdefault("cache", {})

    cards: list[dict] = []
    all_events: list[dict] = []

    for player_cfg in config["players"]:
        player_cfg = dict(player_cfg)
        player_cfg["_pregame_notify_minutes"] = pregame_notify_minutes
        pid = str(player_cfg["id"])
        try:
            card, events = process_player(player_cfg, season, jst_today)
            state["cache"][pid] = card
            cards.append(card)

            for ev in events:
                key = f'{ev["game_pk"]}:{ev["type"]}'
                if state["notified"].get(key):
                    continue
                all_events.append((key, ev, card))
        except Exception as err:  # noqa: BLE001 - 1選手の失敗で全体を止めない
            print(f"警告: {player_cfg.get('name_ja')} の取得に失敗しました: {err}", file=sys.stderr)
            cached = state["cache"].get(pid)
            if cached:
                cards.append(cached)

    sent_count = 0
    for key, ev, card in all_events:
        embed = build_pregame_embed(ev) if ev["type"] == "pregame" else build_postgame_embed(ev, card)
        if send_discord_embed(embed):
            state["notified"][key] = True
            sent_count += 1
        # 失敗時はstateに記録しない → 次回実行で再送を試みる

    save_state(state)

    html = build_html(cards, datetime.now(JST).strftime("%Y/%m/%d %H:%M"))
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"完了: {len(cards)}名分のカードを生成、通知{sent_count}/{len(all_events)}件送信")


if __name__ == "__main__":
    main()
