"""
選手名からMLB Stats APIの選手ID（personId）を調べるツール。

使い方:
    python scripts/find_player_id.py "Shohei Ohtani"
    python scripts/find_player_id.py "Okamoto"

英語表記（ローマ字）で検索してください。日本語名では見つかりません。
ヒットした選手のうち、目的の選手の id を config.json の "id" に設定します。
"""

import sys
import urllib.parse
import urllib.request
import json

SEARCH_URL = "https://statsapi.mlb.com/api/v1/people/search?names={query}"


def search_player(name: str) -> list[dict]:
    url = SEARCH_URL.format(query=urllib.parse.quote(name))
    with urllib.request.urlopen(url, timeout=10) as res:
        data = json.load(res)
    return data.get("people", [])


def main() -> None:
    if len(sys.argv) < 2:
        print("使い方: python scripts/find_player_id.py \"選手名(英語表記)\"")
        sys.exit(1)

    name = " ".join(sys.argv[1:])
    people = search_player(name)

    if not people:
        print(f"「{name}」に一致する選手が見つかりませんでした。表記を変えて再度試してください。")
        sys.exit(1)

    print(f"「{name}」の検索結果: {len(people)}件\n")
    for p in people:
        team = p.get("currentTeam", {}).get("name", "所属チーム不明")
        active = "現役" if p.get("active") else "引退/非現役"
        print(f"  id: {p['id']:<8} name: {p['fullName']:<28} team: {team:<24} ({active})")

    print("\n目的の選手の id を config.json の該当エントリの \"id\" に設定してください。")


if __name__ == "__main__":
    main()
