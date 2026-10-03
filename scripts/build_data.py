"""
원본 엑셀 → data/dashboard.json 변환 스크립트
사용법:
  python scripts/build_data.py

입력 (원본/):
  Contract, Spot Price.xlsx          시세 (Spot Price / Contract Price 시트)
  주간업데이트_입력템플릿*.xlsx      재고현황 / 월별과부족 / 단가현황(참고) / 입고이력 (가장 최근 저장본)
  market.json                        시장 동향 (주간 루틴이 작성, 없으면 생략)

숫자는 엑셀 값을 그대로 옮기고, 재고는 운영지침 STEP 1.5 정합성 검사를 통과한 경우에만
확정본(confirmed)으로 승격한다. 실패하면 직전 확정본을 유지하고 최신 입력본은 pending으로 둔다.
"""

import json
import re
import sys
import warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import openpyxl

from update_prices import fix_and_load_xlsx

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "원본"
OUT = ROOT / "data" / "dashboard.json"
PRICE_XLSX = SRC / "Contract, Spot Price.xlsx"
MARKET_JSON = SRC / "market.json"
TEMPLATE_GLOB = "주간업데이트_입력템플릿*.xlsx"

# 운영지침 "관리 품목 (5종 고정)"
MANAGED = ["JK50-10024A", "JK50-10022A", "JK50-10033A", "JK51-10012A", "JK51-10012B"]

SPOT_ITEMS = {
    "DDR4 8Gb (1Gx8) 3200": "ddr4_spot",
    "DDR5 16Gb (2Gx8) 4800/5600": "ddr5_spot",
    "512Gb TLC": "nand_spot",
}
CONTRACT_ITEMS = {
    "DDR4 8GB SO-DIMM": "ddr4_contract",
    "DDR5 16GB Module": "ddr5_contract",
}

TOL = 0.5          # 수량 비교 허용 오차 (소요 소수점 반올림)
PRICE_GAP = 1.5    # 최종입고단가 vs 재고단가 괴리 표기 기준 (STEP 1.5-5)


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def r2(v):
    if v is None:
        return None
    v = round(v, 2)
    return int(v) if v == int(v) else v


def comma(v):
    return f"{r2(v):,}"


def iso(d):
    return d.strftime("%Y-%m-%d")


def header(row):
    return [str(c).strip() if c is not None else "" for c in row]


# ---------------------------------------------------------------- 시세

def read_prices(path):
    wb = fix_and_load_xlsx(str(path))
    out = {key: {} for key in list(SPOT_ITEMS.values()) + list(CONTRACT_ITEMS.values())}

    # Date | Category | Item | Daily High | Daily Low | Session High | Session Low | Session Avg | ...
    for r in wb["Spot Price"].iter_rows(min_row=2, values_only=True):
        if not isinstance(r[0], datetime) or r[2] not in SPOT_ITEMS or r[7] is None:
            continue
        # NAND Wafer는 Daily High/Low가 없어 Session High/Low를 쓴다
        high = r[3] if r[3] is not None else r[5]
        low = r[4] if r[4] is not None else r[6]
        out[SPOT_ITEMS[r[2]]][iso(r[0])] = {"d": iso(r[0]), "avg": r2(r[7]), "high": r2(high), "low": r2(low)}

    # Date | Mode | Item | High | Low | Average | High Chg | Low Chg | Avg Chg | History
    for r in wb["Contract Price"].iter_rows(min_row=2, values_only=True):
        if not isinstance(r[0], datetime) or r[2] not in CONTRACT_ITEMS or r[5] is None:
            continue
        out[CONTRACT_ITEMS[r[2]]][iso(r[0])] = {
            "d": iso(r[0]), "avg": r2(r[5]), "high": r2(r[3]), "low": r2(r[4]), "src": r[9],
        }

    # 같은 날짜가 여러 번 있으면 마지막 행을 쓴다
    return {key: [rows[d] for d in sorted(rows)] for key, rows in out.items()}


# ---------------------------------------------------------------- 재고

def parse_stock(ws):
    """재고현황 시트 → [{code, name, cat, as_of, by_supplier, total}]"""
    rows = list(ws.iter_rows(values_only=True))
    head = header(rows[0])
    i_code, i_name, i_cat, i_date = (head.index(h) for h in ("품목코드", "품목명", "구분", "기준일자"))
    i_total = next(i for i, h in enumerate(head) if h.startswith("재고 합계"))
    suppliers = head[i_date + 1:i_total]
    out = []
    for r in rows[1:]:
        if not r[i_code]:
            break
        out.append({
            "code": r[i_code], "name": r[i_name], "cat": r[i_cat],
            "as_of": iso(r[i_date]) if isinstance(r[i_date], datetime) else None,
            "by_supplier": {s: r2(num(r[i_date + 1 + k])) for k, s in enumerate(suppliers)},
            "total": r2(num(r[i_total])),
        })
    return suppliers, out


def parse_balance(ws):
    """월별과부족 시트 → (월 라벨, [{code, name, cat, lots: [...]}])

    같은 품목코드가 여러 행이면 매입 로트 분할(일반 / 커널앤코어)로 본다.
    """
    rows = list(ws.iter_rows(values_only=True))
    head = header(rows[0])
    i_code, i_cat, i_name, i_note = (head.index(h) for h in ("품목코드", "구분", "품목명", "비고"))
    i_open = next(i for i, h in enumerate(head) if re.fullmatch(r"\d+월기초", h))
    i_in = next(i for i, h in enumerate(head) if h.startswith("입고"))
    i_last = head.index("최종입고단가")
    i_stockp = head.index("재고단가")
    demand_cols = [i for i, h in enumerate(head) if re.fullmatch(r"\d+월소요", h)]
    end_cols = [i for i, h in enumerate(head) if re.fullmatch(r"\d+월말재고", h)]
    months = [head[i].replace("재고", "") for i in end_cols]

    items = {}
    for r in rows[1:]:
        if not r[i_code]:
            break
        it = items.setdefault(r[i_code], {"code": r[i_code], "name": r[i_name], "cat": r[i_cat], "lots": []})
        it["lots"].append({
            "label": r[i_note],
            "open": r2(num(r[i_open])), "incoming": r2(num(r[i_in])),
            "last_price": r2(r[i_last]) if isinstance(r[i_last], (int, float)) else None,
            "stock_price": r2(r[i_stockp]) if isinstance(r[i_stockp], (int, float)) else None,
            "demand": [r2(num(r[i])) for i in demand_cols],
            "end": [r2(num(r[i])) for i in end_cols],
        })

    for it in items.values():
        if len(it["lots"]) > 1:
            for l in it["lots"]:
                l["label"] = l["label"] or "일반"
            it["note"] = None
        else:
            it["note"] = it["lots"][0]["label"]
            it["lots"][0]["label"] = None
    return {"open": head[i_open], "ends": months}, list(items.values())


def grade(end, demand):
    """운영지침 STEP 3: 최초 부족(마이너스) 전환 시점으로 등급 판정.

    end/demand는 당월~+5개월 월말재고·월소요. (등급, 최초 부족 월 인덱스)를 돌려준다.
    """
    neg = next((k for k, v in enumerate(end) if v < 0), None)
    if neg is not None:
        if neg <= 2:
            return "critical", neg   # 3개월 이내
        if neg <= 4:
            return "serious", neg    # 4~5개월
        return "warning", neg        # 6개월
    if end and demand and end[-1] < demand[-1]:
        return "warning", None       # 표 범위 밖 다음 달에 부족 전환
    return "good", None


def issue(level, code, check, msg):
    return {"level": level, "code": code, "check": check, "msg": msg}


def check_stock_row(row):
    """STEP 1.5-1: 재고현황 합계 = 공급사별 수량 합"""
    ssum = sum(row["by_supplier"].values())
    if abs(ssum - row["total"]) > TOL:
        return [issue("error", row["code"], "stock_sum",
                      f"재고현황 합계 {comma(row['total'])} ≠ 공급사별 합 {comma(ssum)}")]
    return []


def check_item(it, stock_row):
    """STEP 1.5-2~5. (재고현황이 맞춘 기준, 이슈 목록)을 돌려준다."""
    issues = []
    code = it["code"]

    # 3. 체인: 기초 + 입고대기 − 소요 = 월말재고
    for l in it["lots"]:
        prev = l["open"] + l["incoming"]
        for k, (d, e) in enumerate(zip(l["demand"], l["end"])):
            if abs(prev - d - e) > TOL:
                lot = f" ({l['label']})" if l["label"] else ""
                issues.append(issue("error", code, "chain",
                                    f"월별과부족{lot} {k + 1}번째 달: {comma(prev)} − 소요 {comma(d)} ≠ 월말 {comma(e)}"))
                break
            prev = e

    # 2·4. 재고현황 합계가 월별과부족의 어느 기준과 맞는지 (로트 분할 품목은 합계 기준)
    basis = None
    if stock_row is not None:
        open_sum = sum(l["open"] for l in it["lots"])
        in_sum = sum(l["incoming"] for l in it["lots"])
        first_end = sum(l["end"][0] for l in it["lots"]) if it["lots"][0]["end"] else None
        total = stock_row["total"]
        if first_end is not None and abs(total - first_end) <= TOL:
            basis = "first_end"
        elif abs(total - open_sum) <= TOL:
            basis = "open"
        elif in_sum and abs(total - open_sum - in_sum) <= TOL:
            basis = "open_plus_incoming"
        else:
            issues.append(issue("error", code, "stock_vs_balance",
                                f"재고현황 합계 {comma(total)} ≠ 월별과부족 첫 월말 {comma(first_end)} / 기초 {comma(open_sum)}"))

    # 5. 최종입고단가 vs 재고단가 괴리 (표기만, 보류 사유 아님)
    for l in it["lots"]:
        a, b = l["last_price"], l["stock_price"]
        if a and b and max(a, b) / min(a, b) >= PRICE_GAP:
            issues.append(issue("warn", code, "price_gap",
                                f"최종입고단가 {comma(a)} vs 재고단가 {comma(b)} ({max(a, b) / min(a, b):.1f}배)"))
    return basis, issues


BASIS_LABEL = {"first_end": "첫 월말", "open": "기초", "open_plus_incoming": "기초+입고대기"}


def build_snapshot(path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")   # 범위를 벗어난 날짜 셀 경고
        wb = openpyxl.load_workbook(path, data_only=True)

    suppliers, stock = parse_stock(wb["재고현황"])
    months, items = parse_balance(wb["월별과부족"])
    stock_by_code = {s["code"]: s for s in stock}

    issues = []
    bases = {}
    for s in stock:
        issues += check_stock_row(s)

    for it in items:
        it["managed"] = it["code"] in MANAGED
        s = stock_by_code.get(it["code"])
        basis, found = check_item(it, s)
        if it["managed"]:
            issues += found
            if basis:
                bases[it["code"]] = basis
        it["total"] = {
            "open": r2(sum(l["open"] for l in it["lots"])),
            "incoming": r2(sum(l["incoming"] for l in it["lots"])),
            "demand": [r2(sum(v)) for v in zip(*(l["demand"] for l in it["lots"]))],
            "end": [r2(sum(v)) for v in zip(*(l["end"] for l in it["lots"]))],
        }
        it["grade"], it["short_idx"] = grade(it["total"]["end"], it["total"]["demand"])
        if s:
            it["name"] = s["name"]
            it["by_supplier"] = s["by_supplier"]
            it["stock_total"] = s["total"]
            it["single_source"] = sum(1 for v in s["by_supplier"].values() if v > 0) == 1

    # 2. 관리 품목이 모두 같은 기준이어야 한다
    if len(set(bases.values())) > 1:
        detail = ", ".join(f"{c} {BASIS_LABEL[b]}" for c, b in bases.items())
        issues.append(issue("error", None, "mixed_basis", f"재고현황 기준 혼재: {detail}"))

    for code in MANAGED:
        if code not in stock_by_code:
            issues.append(issue("error", code, "missing", "재고현황 시트에 없음"))
        if code not in {it["code"] for it in items}:
            issues.append(issue("error", code, "missing", "월별과부족 시트에 없음"))

    as_of = max((s["as_of"] for s in stock if s["as_of"]), default=None)
    first = re.match(r"(\d+)월", months["ends"][0]) if months["ends"] else None
    if as_of and first and int(first.group(1)) != int(as_of[5:7]):
        issues.append(issue("warn", None, "month_label",
                            f"기준일자는 {int(as_of[5:7])}월인데 월별과부족 첫 열은 {months['ends'][0]} — 월 표기 확인 필요"))

    order = {c: i for i, c in enumerate(MANAGED)}
    items.sort(key=lambda it: (not it["managed"], order.get(it["code"], 99)))
    snapshot = {
        "as_of": as_of,
        "source": path.name,
        "saved": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "suppliers": suppliers,
        "months": months,
        "items": items,
        "validation": {"ok": not any(i["level"] == "error" for i in issues), "issues": issues},
    }
    return wb, snapshot


def merge_snapshot(new, prev):
    """검사를 통과한 입력본만 확정본으로 승격한다."""
    if new["validation"]["ok"]:
        return {"confirmed": new, "pending": None}
    return {"confirmed": (prev or {}).get("confirmed"), "pending": new}


# ---------------------------------------------------------------- 입고단가

def day(d):
    return iso(d) if isinstance(d, datetime) else d


def unit_price(qty, amt, price):
    """입고이력의 단가 열을 우선하고, 비어 있으면 금액 ÷ 수량"""
    return price if isinstance(price, (int, float)) and price else amt / qty


def monthly_receipts(rows, codes):
    """(입고일, 품번, 통화, 수량, 금액, 단가) → 품번별 월 가중평균 USD 단가"""
    acc = defaultdict(lambda: [0, 0])
    for d, code, cur, qty, amt, price in rows:
        if code not in codes or cur != "USD" or not qty or not amt:
            continue
        acc[(code, day(d)[:7])][0] += unit_price(qty, amt, price) * qty
        acc[(code, day(d)[:7])][1] += qty
    out = {c: [] for c in codes}
    for (code, m), (amt, qty) in sorted(acc.items()):
        out[code].append({"m": m, "price": round(amt / qty, 2), "qty": r2(qty)})
    return out


def check_receipts(rows, codes):
    """수량 × 단가가 금액과 1% 넘게 어긋나는 입고 행"""
    out = []
    for d, code, cur, qty, amt, price in rows:
        if code in codes and cur == "USD" and qty and amt and price and abs(qty * price - amt) > 0.01 * amt:
            out.append(issue("warn", code, "receipt_amount",
                             f"입고이력 {day(d)}: 수량 {comma(qty)} × 단가 {comma(price)} ≠ 금액 {comma(amt)}"))
    return out


def read_purchase(wb):
    ws = wb["입고이력"]
    rows = list(ws.iter_rows(values_only=True))
    head = header(rows[0])
    i_d, i_code, i_cur, i_qty, i_amt, i_price, i_note = (
        head.index(h) for h in ("입고일", "품번", "통화", "입고수량", "입고금액", "단가", "비고"))
    data = [r for r in rows[1:] if isinstance(r[i_d], datetime)]
    slim = [(r[i_d], r[i_code], r[i_cur], r[i_qty], r[i_amt], r[i_price]) for r in data]
    receipts = monthly_receipts(slim, MANAGED)
    recent = [
        {"d": iso(r[i_d]), "code": r[i_code], "qty": r2(r[i_qty]),
         "price": round(unit_price(r[i_qty], r[i_amt], r[i_price]), 2), "lot": r[i_note]}
        for r in sorted(data, key=lambda r: r[i_d])
        if r[i_code] in MANAGED and r[i_cur] == "USD" and r[i_qty] and r[i_amt]
    ][-10:]

    # 단가현황(참고): 구분 | 스펙 | 제조사 | 월별 단가...
    ws = wb["단가현황(참고)"]
    rows = list(ws.iter_rows(values_only=True))
    month_cols = [i for i, c in enumerate(rows[0][:rows[0].index(None)]) if isinstance(c, datetime)]
    quotes = {"months": [rows[0][i].strftime("%Y-%m") for i in month_cols], "rows": []}
    for r in rows[1:]:
        if not r[0]:
            break
        quotes["rows"].append({"cat": r[0], "spec": r[1], "maker": r[2],
                               "p": [r2(r[i]) if isinstance(r[i], (int, float)) else None for i in month_cols]})
    return {"receipts": receipts, "recent": recent[::-1], "quotes": quotes, "issues": check_receipts(slim, MANAGED)}


# ---------------------------------------------------------------- main

def latest_template():
    files = [p for p in SRC.glob(TEMPLATE_GLOB) if not p.name.startswith("~$")]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def main():
    if not PRICE_XLSX.exists():
        sys.exit(f"시세 파일 없음: {PRICE_XLSX}")
    template = latest_template()
    if template is None:
        sys.exit(f"입력템플릿 없음: {SRC / TEMPLATE_GLOB}")

    prev = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}

    prices = read_prices(PRICE_XLSX)
    wb, snapshot = build_snapshot(template)
    inventory = merge_snapshot(snapshot, prev.get("inventory"))
    market = json.loads(MARKET_JSON.read_text(encoding="utf-8")) if MARKET_JSON.exists() else None

    purchase = read_purchase(wb)
    data = {"prices": prices, "inventory": inventory, "purchase": purchase, "market": market}
    text = json.dumps(data, ensure_ascii=False, indent=1)

    OUT.parent.mkdir(exist_ok=True)
    changed = not OUT.exists() or OUT.read_text(encoding="utf-8") != text
    if changed:
        OUT.write_text(text, encoding="utf-8")

    last = {k: v[-1]["d"] for k, v in prices.items() if v}
    print(f"시세 기준일: {last}")
    print(f"재고 입력본: {template.name} (기준일 {snapshot['as_of']})")
    v = snapshot["validation"]
    print(f"정합성 검사: {'통과' if v['ok'] else '보류'}")
    for i in v["issues"] + purchase["issues"]:
        print(f"  [{i['level']}] {i['code'] or '-'} {i['msg']}")
    print(f"시장 동향: {market['updated'] if market else '없음'}")
    print(f"{'저장' if changed else '변경 없음'}: {OUT}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
