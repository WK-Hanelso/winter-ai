"""스테이지 2가 억양을 얼마나 보존하는지 잰다.

천우가 "목소리는 좋은데 억양이 고정된다"고 했고, 그것이 두 가지로 갈렸다.
둘 다 측정으로 확인됐다 (2026-08-14, 파인튜닝 체크포인트 + chosen 참조):

  (가) 의문문과 평서문이 서로 닮아진다
       소스에서 문장 끝 기울기 차이가 12.1반음인데 변환 뒤 1.1반음이 된다
  (나) 한 문장 안의 오르내림이 눌린다
       억양 폭이 3.2에서 1.2반음으로 절반 이하가 된다

이 스크립트는 그 두 숫자를 같은 방법으로 다시 낸다. 학습 데이터를 늘린 뒤
새 체크포인트가 얼마나 회복했는지는 귀만으로 판단할 수 없고, 이 두 숫자가
올라오지 않으면 데이터를 늘린 효과가 없다는 뜻이다.

소스와 변환은 반드시 같은 생성분에서 잰다
------------------------------------------
스테이지 1은 같은 문장을 매번 다르게 읽는다. 같은 여섯 문장을 하루 뒤에 다시
만들었더니 의문문-평서문 차이가 12.1에서 8.1반음으로 바뀌었고, 의문문 하나는
아예 내려갔다. 그러므로 **어제 적어둔 소스 값과 오늘의 변환 값을 비교하면 안
된다.** 볼 것은 절대값이 아니라 한 생성분 안에서의 보존율이다 — 변환이 소스의
차이를 몇 퍼센트 남겼는가.

왜 pyin인가
-----------
전에 ``torchaudio.functional.detect_pitch_frequency``로 쟀을 때 평서문을 상승조로
보고 349 Hz 옥타브 오류를 냈다. 그 값으로는 결론을 낼 수 없어서 실패를 보고했다.
pyin은 유성/무성 판정을 따로 주고 옥타브 오류가 훨씬 적다.

문장 끝을 왜 30%로 보는가
-------------------------
한국어 의문문의 상승은 마지막 어절에 실린다. 유성 구간의 뒤 30%는 짧은 문장에서
대략 그 어절에 해당하고, 문장 길이가 달라도 같은 비율이라 비교가 된다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import librosa
import numpy as np

# 사람 목소리의 범위. 노래가 아니라 말이므로 위쪽을 넉넉히 잡을 이유가 없다.
MINIMUM_HZ = 80.0
MAXIMUM_HZ = 500.0
# 유성 구간의 뒤 이만큼을 문장 끝으로 본다.
TAIL_SHARE = 0.3
# 이보다 유성 프레임이 적으면 기울기를 믿지 않는다.
MINIMUM_FRAMES = 20


def contour(path: Path) -> np.ndarray | None:
    """반음 단위 음높이 곡선. 중앙값을 0으로 놓아 화자 간 비교가 되게 한다."""
    audio, rate = librosa.load(str(path), sr=22050, mono=True)
    frequency, voiced, _ = librosa.pyin(
        audio, fmin=MINIMUM_HZ, fmax=MAXIMUM_HZ, sr=rate, frame_length=1024
    )
    frequency = frequency[voiced & ~np.isnan(frequency)]
    if len(frequency) < MINIMUM_FRAMES:
        return None
    return 12 * np.log2(frequency / np.median(frequency))


def end_slope(semitones: np.ndarray) -> float:
    """문장 끝에서 음이 얼마나 오르내리는가. 양수면 올라간다."""
    tail = semitones[int(len(semitones) * (1 - TAIL_SHARE)) :]
    return float(np.polyfit(np.arange(len(tail)), tail, 1)[0] * len(tail))


def measure(path: Path) -> dict[str, float] | None:
    semitones = contour(path)
    if semitones is None:
        return None
    return {"end_slope": end_slope(semitones), "width": float(semitones.std())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pairs",
        type=Path,
        required=True,
        help="평서문/의문문 짝이 들어 있는 디렉토리. s1.wav q1.wav s2.wav q2.wav ...",
    )
    parser.add_argument(
        "--converted-suffix",
        default="_변환",
        help="변환 결과 파일 이름에 붙는 꼬리. s1.wav의 짝은 s1_변환.wav",
    )
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()

    rows: list[dict[str, object]] = []
    print(f"{'문장':<10} {'소스 끝기울기':>13} {'변환 끝기울기':>13} {'소스 폭':>8} {'변환 폭':>8}")
    for source in sorted(options.pairs.glob("[sq][0-9].wav")):
        converted = source.with_name(f"{source.stem}{options.converted_suffix}.wav")
        before = measure(source)
        after = measure(converted) if converted.exists() else None
        if before is None:
            print(f"{source.stem:<10} 유성 구간 부족")
            continue
        kind = "평서" if source.stem.startswith("s") else "의문"
        row = {"name": source.stem, "kind": kind, "source": before, "converted": after}
        rows.append(row)
        if after is None:
            print(f"{source.stem}({kind}) {before['end_slope']:>12.2f} {'변환 없음':>14}")
            continue
        print(
            f"{source.stem}({kind}) {before['end_slope']:>12.2f} {after['end_slope']:>13.2f}"
            f" {before['width']:>8.2f} {after['width']:>8.2f}"
        )

    if not rows:
        raise SystemExit(
            f"{options.pairs}에서 s1.wav/q1.wav 같은 짝을 찾지 못했습니다. "
            "평서문은 s로, 의문문은 q로 시작하는 이름이어야 합니다."
        )

    def gap(stage: str) -> float | None:
        """의문문과 평서문의 끝 기울기 차이. 이것이 두 문형을 가르는 값이다."""
        questions = [r[stage]["end_slope"] for r in rows if r["kind"] == "의문" and r[stage]]
        statements = [r[stage]["end_slope"] for r in rows if r["kind"] == "평서" and r[stage]]
        if not questions or not statements:
            return None
        return float(np.mean(questions) - np.mean(statements))

    def width(stage: str) -> float | None:
        values = [r[stage]["width"] for r in rows if r[stage]]
        return float(np.mean(values)) if values else None

    summary = {
        "question_statement_gap_source": gap("source"),
        "question_statement_gap_converted": gap("converted"),
        "width_source": width("source"),
        "width_converted": width("converted"),
    }
    print()
    print("의문문-평서문 끝 기울기 차이 (클수록 두 문형이 구별된다)")
    print(f"  소스   {summary['question_statement_gap_source']:.2f} 반음")
    if summary["question_statement_gap_converted"] is not None:
        kept = summary["question_statement_gap_converted"] / summary["question_statement_gap_source"]
        print(f"  변환   {summary['question_statement_gap_converted']:.2f} 반음  (보존 {kept:.0%})")
    print("억양 폭 (클수록 오르내림이 살아 있다)")
    print(f"  소스   {summary['width_source']:.2f} 반음")
    if summary["width_converted"] is not None:
        print(f"  변환   {summary['width_converted']:.2f} 반음  "
              f"(보존 {summary['width_converted'] / summary['width_source']:.0%})")
    print()
    print("보존율만 비교할 것. 소스 절대값은 생성할 때마다 달라진다.")
    print("2026-08-14, 25.2분으로 학습한 체크포인트: 차이 보존 9%, 폭 보존 50%")

    if options.report:
        options.report.write_text(
            json.dumps({"summary": summary, "sentences": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"기록: {options.report}")


if __name__ == "__main__":
    main()
