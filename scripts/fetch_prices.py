"""
디램익스체인지 첫 화면의 현물 시세를 읽어 시세 엑셀에 추가한다.
사용법:
  python scripts/fetch_prices.py

기록 규칙
  DRAM 현물(정품 칩, eTT, DDR5): 표의 Last Update가 마감 세션(18시 이후, GMT+8)일 때만 그 날짜로 기록한다.
                                 장중 세션 값은 최종값이 아니므로 건너뛰고 다음 실행에서 받는다.
  NAND 웨이퍼(주 1회 갱신):      표의 Last Update 날짜가 엑셀에 없으면 기록한다.
  같은 품목·같은 날짜가 이미 있으면 건너뛴다.
"""
import html
import re
import sys
import urllib.request
from datetime import datetime

from update_prices import EXCEL_PATH, fix_and_load_xlsx

URL = "https://www.dramexchange.com/"
MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
NUM = r"(-?\d[\d,]*\.?\d*)"
CLOSING_HOUR = 18          # 마감 세션은 18:10 (GMT+8)

# 엑셀 Item 이름: (속한 표, 화면의 품목명 정규식, 엑셀 Category)
ITEMS = {
    "DDR4 8Gb (1Gx8) 3200": ("DRAM Spot Price", r"DDR4 8Gb \(1Gx8\) 3200", "DRAM Spot"),
    "DDR4 8Gb (1Gx8) eTT": ("DRAM Spot Price", r"DDR4 8Gb \(1Gx8\) eTT", "DRAM Spot"),
    "DDR5 16Gb (2Gx8) 4800/5600": ("DRAM Spot Price", r"DDR5 16Gb \(2Gx8\) 4800/5600", "DRAM Spot"),
    "512Gb TLC": ("Wafer Spot Price", r"512Gb TLC", "NAND Wafer Spot"),
}


def page_text(raw):
    """HTML → 칸을 ' | '로 구분한 한 줄 텍스트"""
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)</t[dh]>", " | ", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw))


def table_update(text, label):
    """표 제목 옆의 'Last Update: Oct.2 2026 18:10' → ('2026-10-02', 18). 없으면 None."""
    m = re.search(label + r"\s*\|?\s*Last Update:\s*([A-Za-z]{3})\.?\s*(\d{1,2})\s+(\d{4})\s+(\d{1,2}):\d{2}", text)
    if not m:
        return None
    return f"{m.group(3)}-{MONTHS[m.group(1).title()]:02d}-{int(m.group(2)):02d}", int(m.group(4))


def parse_item(text, pattern):
    """품목 행의 숫자 6개: 고가, 저가, 세션 고가, 세션 저가, 세션 평균, 변동률(%)"""
    m = re.search(pattern + r"\s*\|\s*" + r"\s*\|\s*".join([NUM] * 5) + r"\s*\|\s*" + NUM + r"\s*%", text)
    return [float(x.replace(",", "")) for x in m.groups()] if m else None


def new_rows(text, have):
    """화면에서 엑셀에 추가할 행을 고른다. have: 이미 있는 {(품목, 'YYYY-MM-DD')}

    (추가할 행 목록, 건너뛴 사유 목록)을 돌려준다.
    """
    rows, skipped = [], []
    for item, (table, pattern, category) in ITEMS.items():
        updated, values = table_update(text, table), parse_item(text, pattern)
        if not updated or not values:
            skipped.append(f"{item}: 화면에서 찾지 못함")
            continue
        day, hour = updated
        if (item, day) in have:
            continue
        if category == "DRAM Spot" and hour < CLOSING_HOUR:
            skipped.append(f"{item}: {day} 장중 값({hour}시)이라 마감 후 기록")
            continue
        high, low, s_high, s_low, avg, chg = values
        date = datetime.fromisoformat(day)
        if category == "DRAM Spot":
            rows.append([date, category, item, high, low, s_high, s_low, avg, round(chg / 100, 6), None, None, None, None])
        else:   # 웨이퍼 표는 앞의 두 값이 Weekly High/Low
            rows.append([date, category, item, None, None, s_high, s_low, avg, round(chg / 100, 6), high, low, None, None])
    return rows, skipped


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return page_text(urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace"))


def main():
    text = fetch()
    wb = fix_and_load_xlsx(EXCEL_PATH)
    ws = wb["Spot Price"]
    have = {(r[2], r[0].strftime("%Y-%m-%d")) for r in ws.iter_rows(min_row=2, values_only=True)
            if isinstance(r[0], datetime)}
    rows, skipped = new_rows(text, have)
    for row in rows:
        ws.append(row)
        print(f"추가: {row[0]:%Y-%m-%d} {row[2]} avg={row[7]}")
    for s in skipped:
        print(f"건너뜀: {s}")
    if rows:
        wb.save(EXCEL_PATH)     # 엑셀이 열려 있으면 PermissionError — 다음 실행에서 다시 시도한다
    return len(rows)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
