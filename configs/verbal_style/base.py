"""Hand-written policy kept for comparison against the Reference baseline.

This is the behaviour the companion had before any Reference measurement. It is
retained so the two can be run against the same input and compared, not because
it is grounded in anything.
"""

VERBAL_STYLE = {
    "name": "hand-written-baseline",
    "register": "polite_casual",
    "max_sentences": 3,
    # The old policy said nothing on ordinary turns. Reproduced exactly so the
    # comparison is against what actually shipped, not a tidied version of it.
    "shared_instruction": False,
    "discourse_markers": (),
    "hesitation_markers": (),
    "hesitation_usage": "never",
    "acts": {
        "default": {
            "tone": "neutral",
            "directness": 0.7,
            "sentence_length": "normal",
            "instruction": None,
        },
        "memory_candidate": {
            "tone": "warm",
            "directness": 0.55,
            "sentence_length": "short",
            "instruction": "Respond briefly and warmly in Korean. Do not pressure the user.",
        },
        "warning": {
            "tone": "serious",
            "directness": 0.9,
            "sentence_length": "short",
            "instruction": (
                "Respond briefly and clearly in Korean. State the important point first."
            ),
        },
    },
}
