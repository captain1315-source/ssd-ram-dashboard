"""재고·과부족 계산 (순수 로직 — DB·엑셀 접근 없음)

제조사별 재고를 용도(국내/해외/공용)에 따라 소진시키며 월말 재고를 전망한다.
- 국내 소요: 국내 전용 재고 → 공용 재고 순으로 차감
- 해외 소요: 해외 전용 재고 → 공용 재고 순으로 차감
- 같은 용도 안에서는 설정 파일에 적힌 순서대로 소진
- 공용 재고는 국내 소요가 먼저 쓴다 (국내용으로 쓸 수 있는 재고가 공용뿐이기 때문)
"""
import re

GRADE_RANK = {"critical": 0, "serious": 1, "warning": 2, "good": 3}
UNASSIGNED = "미지정"     # 제조사를 알 수 없는 재고·입고
UNKNOWN = "미분류"        # 국내/해외를 알 수 없는 소요
USAGE_ORDER = ("국내", "해외", UNKNOWN)


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


def worst(grades):
    return min(grades, key=GRADE_RANK.__getitem__)


def model_token(code):
    """모델 코드의 '/' 뒤 영문 표기. 예: 'SAPH-660/RCRB57NNB95' → 'RCRB'

    '/'가 없는 코드(완제품 모델이 아닌 조립품 등, 예: 'SA95-70998A')는 코드 자체를 돌려준다.
    '/'는 있는데 영문으로 시작하지 않으면 '(표기 없음)', 코드가 비어 있으면 '(모델 없음)'.
    """
    if not code:
        return "(모델 없음)"
    if "/" not in code:
        return code
    m = re.match(r"[A-Za-z]+", code.split("/", 1)[1])
    return m.group(0) if m else "(표기 없음)"


def usage_sums(by_model, model_usage):
    """{모델코드: 수량} → ({용도: 수량}, {설정에 없는 표기: 수량})"""
    sums, unknown = {}, {}
    for code, qty in by_model.items():
        token = model_token(code)
        usage = model_usage.get(token)
        if usage is None:
            usage = UNKNOWN
            unknown[token] = unknown.get(token, 0) + qty
        sums[usage] = sums.get(usage, 0) + qty
    return sums, unknown


def split_demand(target, raw, fallback):
    """월 소요 합계(target)를 용도별 비율로 나눈다.

    raw는 그 달의 모델별 소요를 용도로 묶은 값. 합이 0이면(모델별 자료가 없는 달)
    fallback 비율을 쓰고, 그것도 없으면 전부 미분류로 둔다.
    """
    if not target:
        return {}
    share = {u: v for u, v in raw.items() if v > 0}
    if not share:
        share = {u: v for u, v in fallback.items() if v > 0}
    total = sum(share.values())
    if not total:
        return {UNKNOWN: target}
    return {u: target * v / total for u, v in share.items()}


def consume(lots, usage, qty):
    """용도에 맞는 재고에서 qty만큼 차감한다. 모자라면 그 용도의 마지막 재고에 음수로 남긴다."""
    if qty <= 0 or not lots:
        return
    if usage == UNKNOWN:
        dedicated, eligible = [], lots
    else:
        dedicated = [l for l in lots if l["usage"] == usage]
        eligible = (dedicated + [l for l in lots if l["usage"] == "공용"]) or lots
    for lot in eligible:
        take = min(max(lot["stock"], 0), qty)
        lot["stock"] -= take
        qty -= take
    if qty > 1e-9:
        (dedicated or eligible)[-1]["stock"] -= qty


def project(lots, steps):
    """제조사별 월말 재고 전망.

    lots:  [{'maker', 'usage', 'stock'}] — 시작 재고, 적힌 순서가 소진 순서
    steps: 월별 [{'inflow': {제조사: 수량}, 'demand': {용도: 수량}}]
    돌려주는 값은 lots와 같은 순서의 월말 재고 목록.
    """
    state = [dict(l) for l in lots]
    ends = [[] for _ in state]
    for step in steps:
        for lot in state:
            lot["stock"] += step["inflow"].get(lot["maker"], 0)
        for usage in USAGE_ORDER:
            consume(state, usage, step["demand"].get(usage, 0))
        for i, lot in enumerate(state):
            ends[i].append(lot["stock"])
    return ends


def net_received(pos, receipts, since):
    """발주 자료가 만들어진 날(since) 이후의 입고를, 같은 품목·거래처의 미입고 발주에서 차감한다.

    발주 자료가 입고 자료보다 드물게 올라와, 이미 들어온 물량이 입고대기에 남는 것을 바로잡는다.
    - 납기가 빠른 발주부터 차감
    - 입고일보다 나중에 낸 발주, 단가가 1% 넘게 다른 발주는 대상이 아니다
    - 발주 자료를 올린 당일의 입고는 그 발주에 입고 실적이 전혀 없을 때만 차감한다
      (실적이 있으면 그 입고가 이미 반영됐을 수 있다)
    (보정한 발주 목록, 차감 내역 [{'code', 'vendor', 'due', 'qty', 'date'}])을 돌려준다.
    """
    pos = [dict(p, netted=0) for p in pos]
    log = []
    if not since:
        return pos, log
    for r in sorted((r for r in receipts if r["입고일"] >= since and r["수량"]), key=lambda r: r["입고일"]):
        left = r["수량"]
        price = r.get("단가") or (r["금액"] / r["수량"] if r.get("금액") else None)
        mine = [p for p in pos if p["item_code"] == r["품번"] and p["vendor_name"] == r["거래처"]]
        for p in sorted(mine, key=lambda p: p["due"]):
            backlog = p["order_qty"] - p["received_qty"]
            if left <= 0:
                break
            if backlog <= 0 or (p.get("order_date") and p["order_date"] > r["입고일"]):
                continue
            if r["입고일"] == since and p["received_qty"] - p["netted"] > 0:
                continue
            same_currency = not p.get("currency") or not r.get("통화") or p["currency"] == r["통화"]
            if price and p.get("unit_price") and same_currency and abs(price - p["unit_price"]) > 0.01 * p["unit_price"]:
                continue
            take = min(left, backlog)
            p["received_qty"] += take
            p["netted"] += take
            left -= take
            log.append({"code": p["item_code"], "vendor": p["vendor_name"], "due": p["due"], "qty": take, "date": r["입고일"]})
    return pos, log


def total_ends(open_, received, pending, demand, inflow):
    """품목 합계의 월말 재고. demand[0]은 당월, inflow[k]는 M+k 입고(inflow[0]은 쓰지 않음)."""
    ends, stock = [], open_ + received + pending
    for k, d in enumerate(demand):
        stock += inflow[k] if k else 0
        stock -= d
        ends.append(stock)
    return ends
