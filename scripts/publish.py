"""
자동 갱신: 시세 수집 → 대시보드 데이터 변환 → 바뀐 것이 있으면 GitHub에 올린다.
Windows 작업 스케줄러가 매시간 pythonw로 실행한다 (창이 뜨지 않는다).

  pythonw scripts/publish.py          평소 실행 (작업 스케줄러)
  python  scripts/publish.py --now    시세 수집 시각 제한 없이 지금 실행하고 결과를 화면에도 보여준다

기록은 logs/publish.log 에 남는다. 실패해도 다음 실행에서 다시 시도한다.
"""
import contextlib
import io
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "publish.log"
LOG_KEEP = 600                      # 남겨 둘 줄 수
# 디램익스체인지는 마감 시세가 19:10(KST)에 올라온다. 그 뒤 두 번, 놓쳤을 때를 대비해 다음 날 두 번 확인한다
FETCH_HOURS = {8, 12, 20, 22}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

sys.path.insert(0, str(Path(__file__).resolve().parent))


def git(*args):
    r = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr).strip()


def captured(fn):
    """fn의 출력과 성공 여부를 돌려준다. 실패해도 예외를 밖으로 내지 않는다."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            fn()
        return True, buf.getvalue()
    except SystemExit as e:
        return False, buf.getvalue() + f"중단: {e}\n"
    except Exception:
        return False, buf.getvalue() + traceback.format_exc()


def summary(text):
    """변환 출력에서 품목별 상세 줄은 빼고 요약만 남긴다."""
    return [l for l in text.splitlines() if l and (not l.startswith(" ") or l.lstrip().startswith("[확인]"))]


def main():
    now, force = datetime.now(), "--now" in sys.argv
    lines = [f"=== {now:%Y-%m-%d %H:%M} ==="]

    if force or now.hour in FETCH_HOURS:
        import fetch_prices
        ok, out = captured(fetch_prices.main)
        lines += out.splitlines() if ok else ["시세 수집 실패 (다음 실행에서 다시 시도):", *out.splitlines()[-3:]]

    import build_data
    ok, out = captured(build_data.main)
    lines += summary(out) if ok else ["변환 실패, 올리지 않음:", *out.splitlines()[-6:]]

    if ok:
        _, changed = git("status", "--porcelain", "--", "data")
        if changed:
            git("add", "data/dashboard.json")
            code, msg = git("commit", "-m", f"데이터 갱신 {now:%Y-%m-%d %H:%M}")
            lines.append("커밋 완료" if code == 0 else f"커밋 실패: {msg}")
        _, ahead = git("rev-list", "--count", "origin/main..main")
        if ahead.strip().isdigit() and int(ahead) > 0:
            code, msg = git("push", "origin", "main")
            lines.append("GitHub에 올림" if code == 0 else f"올리기 실패 (다음 실행에서 다시 시도): {msg.splitlines()[-1] if msg else ''}")
        elif not changed:
            lines.append("바뀐 것 없음")

    LOG.parent.mkdir(exist_ok=True)
    old = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
    LOG.write_text("\n".join((old + lines)[-LOG_KEEP:]) + "\n", encoding="utf-8")
    if force and sys.stdout:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print("\n".join(lines))


if __name__ == "__main__":
    main()
