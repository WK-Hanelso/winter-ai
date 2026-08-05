from companion.core import CompanionCore
from companion.response import CompanionResponse


class CliOrchestrator:
    def __init__(self, core: CompanionCore) -> None:
        self._core = core

    def handle_text(self, text: str) -> CompanionResponse:
        return self._core.respond_to_text(text)
