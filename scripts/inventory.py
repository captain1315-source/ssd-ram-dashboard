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

    '/'는 있는데 영문으로 시작하지 않으면 '(표기 없음)', 모델 코드 자체가 없으면 None.
    """
    if not code or "/" not in code:
        return None
    m = re.match(r"[A-Za-z]+", code.split("/", 1)[1])
    return m.group(0) if m else "(표기 없음)"


def usage_sums(by_model, model_usage):
    """{모델코드: 수량} → ({용도: 수량}, 설정에 없는 표기 집합)"""
    sums, unknown = {}, set()
    for code, qty in by_model.items():
        token = model_token(code) or "(모델 없음)"
        usage = model_usage.get(token)
        if usage is None:
            usage = UNKNOWN
            unknown.add(token)
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


def total_ends(open_, received, pending, demand, inflow):
    """품목 합계의 월말 재고. demand[0]은 당월, inflow[k]는 M+k 입고(inflow[0]은 쓰지 않음)."""
    ends, stock = [], open_ + received + pending
    for k, d in enumerate(demand):
        stock += inflow[k] if k else 0
        stock -= d
        ends.append(stock)
    return ends
