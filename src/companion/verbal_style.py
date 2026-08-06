"""Interface-independent wording policy for Companion responses."""
from companion.response import VerbalStylePlan


class VerbalStylePlanner:
    def plan(self, dialogue_act: str) -> VerbalStylePlan:
        if dialogue_act == "memory_candidate":
            return VerbalStylePlan(tone="warm", directness=0.55, sentence_length="short")
        if dialogue_act == "warning":
            return VerbalStylePlan(tone="serious", directness=0.9, sentence_length="short")
        return VerbalStylePlan()

    def instruction(self, dialogue_act: str) -> str | None:
        if dialogue_act == "memory_candidate":
            return "Respond briefly and warmly in Korean. Do not pressure the user."
        if dialogue_act == "warning":
            return "Respond briefly and clearly in Korean. State the important point first."
        return None
