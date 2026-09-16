"""Incrementally expose only the first root reply_draft string.

This is a display lexer, not validation: the complete JSON still goes through
parse_output before any proposal executes. Invalid string escapes stop display.
"""

import json


class ReplyDraftExtractor:
    def __init__(self, *, ignore_web: bool = False) -> None:
        self.ignore_web = ignore_web
        self.prefix = ''
        self.depth = 0
        self.phase = 'root'
        self.key = ''
        self.in_string = False
        self.is_key = False
        self.capture = False
        self.done = False
        self.escape = ''
        self.high_surrogate: int | None = None

    def feed(self, piece: str) -> str:
        output: list[str] = []
        for char in piece:
            if self.done:
                break
            self.prefix += char
            if self.in_string:
                if self.escape:
                    self.escape += char
                    if self.escape == '\\u':
                        continue
                    if self.escape.startswith('\\u') and len(self.escape) < 6:
                        continue
                    try:
                        decoded = json.loads('"' + self.escape + '"')
                    except ValueError:
                        self.done = True
                        break
                    self.escape = ''
                    self._decoded(decoded, output)
                elif char == '\\':
                    self.escape = char
                elif char == '"':
                    self.in_string = False
                    if self.capture:
                        self.done = True
                    elif self.is_key:
                        self.phase = 'colon'
                    elif self.depth == 1:
                        self.phase = 'after'
                elif ord(char) < 32:
                    self.done = True
                else:
                    self._decoded(char, output)
                continue
            if char.isspace():
                continue
            if char == '"':
                self.in_string = True
                self.is_key = self.depth == 1 and self.phase == 'key'
                self.capture = (self.depth == 1 and self.phase == 'value'
                                and self.key == 'reply_draft' and self._may_display())
                if self.is_key:
                    self.key = ''
            elif char in '{[':
                if self.phase == 'root':
                    if char != '{':
                        self.done = True
                        continue
                    self.phase = 'key'
                self.depth += 1
            elif char in '}]':
                self.depth -= 1
                if self.depth == 0:
                    self.done = True
                elif self.depth == 1:
                    self.phase = 'after'
            elif self.depth == 1:
                if char == ':' and self.phase == 'colon':
                    self.phase = 'value'
                elif char == ',':
                    self.phase = 'key'
                else:
                    self.phase = 'after'
        return ''.join(output)

    def _may_display(self) -> bool:
        if self.ignore_web:
            return True
        try:
            # The root prefix ends at the opening reply quote. Parse its completed
            # preceding values; missing/reordered/non-null web_request stays hidden.
            values = json.loads(self.prefix[:-1] + 'null}')
        except ValueError:
            return False
        return 'web_request' in values and values['web_request'] is None

    def _decoded(self, char: str, output: list[str]) -> None:
        if not (self.is_key or self.capture):
            return
        code = ord(char)
        if self.high_surrogate is not None:
            if not 0xDC00 <= code <= 0xDFFF:
                self.done = True
                return
            char = chr(0x10000 + ((self.high_surrogate - 0xD800) << 10) + code - 0xDC00)
            self.high_surrogate = None
        elif 0xD800 <= code <= 0xDBFF:
            self.high_surrogate = code
            return
        elif 0xDC00 <= code <= 0xDFFF:
            self.done = True
            return
        if self.is_key:
            self.key += char
        else:
            output.append(char)
