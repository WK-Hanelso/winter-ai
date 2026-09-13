"""Text companion wording; length follows the request, without numeric caps."""

VERBAL_STYLE = {
    "name": "companion_text",
    "register": "plain",
    "polite_ratio": None,
    "max_sentences": None,
    "max_words_per_sentence": None,
    "shared_instruction": True,
    "discourse_markers": (),
    "hesitation_markers": (),
    "hesitation_usage": "never",
    "acts": {
        "default": {
            "tone": "neutral",
            "directness": 0.7,
            "sentence_length": "normal",
            "instruction": (
                "호칭은 천우로 해. 짧게 답할 때와 길게 설명할 때를 구분해. "
                "잡담은 짧게, 설명·비교·교정은 충분히 해. 영어 요청에는 필요한 영어로 답해."
            ),
        },
    },
}
