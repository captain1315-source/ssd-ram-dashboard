"""입고이력 누적 저장소(원본/입고이력.csv)와 제조사 판별

MRP 앱의 입고 내역은 업로드할 때마다 통째로 교체되므로, 실행할 때마다 새 건만 골라 여기에 쌓는다.
"""
import csv
from collections import defaultdict
from datetime import datetime

FIELDS = ["입고일", "입고번호", "거래처", "품번", "통화", "환율", "수량", "금액", "단가", "비고"]
NUMERIC = ("환율", "수량", "금액", "단가")
# 제조사 표기가 자료마다 달라 비교할 때는 공백·대소문자를 무시한다
MAKER_ALIAS = {"커널앤코어": "kernel&core", "k&c": "kernel&core"}


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def load(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k: (_num(r.get(k)) if k in NUMERIC else (r.get(k) or "")) for k in FIELDS}
                for r in csv.DictReader(f)]


def save(path, rows):
    rows = sorted(rows, key=lambda r: (r["입고일"], r["입고번호"], r["품번"]))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r[k] is None else r[k]) for k in FIELDS})


def key(r):
    return (r["입고번호"], r["품번"], round(r["수량"] or 0, 2))


def merge(rows, new_rows):
    """이미 있는 건(입고번호·품번·수량이 같은 건)은 건너뛰고 새 건만 더한다. 더한 건수를 돌려준다."""
    seen = {key(r) for r in rows}
    added = 0
    for r in new_rows:
        if r["품번"] and r["수량"] and key(r) not in seen:
            rows.append(r)
            seen.add(key(r))
            added += 1
    return added


def from_scm(db_rows):
    return [{"입고일": r["receipt_date"], "입고번호": r["receipt_no"] or "", "거래처": r["vendor_name"] or "",
             "품번": r["item_code"], "통화": r["currency"] or "", "환율": r["fx_rate"], "수량": r["qty"],
             "금액": r["amount"], "단가": None, "비고": ""} for r in db_rows]


def from_sheet(ws):
    """ERP 입고 내역 시트(머리글에 입고일·품번·입고수량이 있는 표)를 읽는다. 해당 표가 아니면 빈 목록."""
    rows = ws.iter_rows(values_only=True)
    for _ in range(5):
        head = [str(c).strip() if c is not None else "" for c in next(rows, [])]
        if {"입고일", "품번", "입고수량"} <= set(head):
            break
    else:
        return []
    col = {h: i for i, h in reversed(list(enumerate(head))) if h}
    get = lambda r, name: r[col[name]] if name in col and col[name] < len(r) else None
    out = []
    for r in rows:
        d = get(r, "입고일")
        if not isinstance(d, datetime):
            continue
        out.append({"입고일": d.strftime("%Y-%m-%d"), "입고번호": str(get(r, "입고번호") or ""),
                    "거래처": str(get(r, "거래처") or ""), "품번": get(r, "품번"),
                    "통화": str(get(r, "통화") or ""), "환율": _num(get(r, "환율")),
                    "수량": _num(get(r, "입고수량")), "금액": _num(get(r, "입고금액")),
                    "단가": _num(get(r, "단가")), "비고": str(get(r, "비고") or "")})
    return out


def norm_maker(name):
    n = str(name or "").lower().replace(" ", "")
    return MAKER_ALIAS.get(n, n)


def resolve_maker(code, vendor, cfg, receipt_no=None, note=None):
    """입고·발주 건의 제조사. 그 품목에 등록된 제조사 이름으로 돌려주고, 알 수 없으면 None.

    우선순위: 설정의 입고제조사(건별 지정) → 입고 비고의 제조사 표기 → 거래처 기본 제조사(품목 지정이 우선)
    """
    registered = {norm_maker(m["maker"]): m["maker"] for m in cfg["makers"].get(code, [])}
    default = cfg["vendor_maker"].get((vendor, code)) or cfg["vendor_maker"].get((vendor, None))
    for cand in (cfg["receipt_maker"].get((receipt_no, code)), note, default):
        if cand and norm_maker(cand) in registered:
            return registered[norm_maker(cand)]
    return None


def unit_price(qty, amt, price):
    """입고이력의 단가 열을 우선하고, 비어 있으면 금액 ÷ 수량"""
    return price if isinstance(price, (int, float)) and price else amt / qty


def monthly_receipts(rows, codes):
    """(입고일, 품번, 통화, 수량, 금액, 단가) → 품번별 월 가중평균 USD 단가"""
    acc = defaultdict(lambda: [0, 0])
    for d, code, cur, qty, amt, price in rows:
        if code not in codes or cur != "USD" or not qty or not amt:
            continue
        acc[(code, d[:7])][0] += unit_price(qty, amt, price) * qty
        acc[(code, d[:7])][1] += qty
    out = {c: [] for c in codes}
    for (code, m), (amt, qty) in sorted(acc.items()):
        out[code].append({"m": m, "price": round(amt / qty, 2), "qty": int(qty) if qty == int(qty) else qty})
    return out


def amount_mismatches(rows, codes):
    """수량 × 단가가 금액과 1% 넘게 어긋나는 입고 행: [(입고일, 품번, 수량, 단가, 금액)]"""
    return [(d, code, qty, price, amt) for d, code, cur, qty, amt, price in rows
            if code in codes and cur == "USD" and qty and amt and price and abs(qty * price - amt) > 0.01 * amt]
