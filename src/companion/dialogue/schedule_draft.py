"""Detect explicit receipt contradictions without requiring a factual recital.

This is a bounded Korean/date/time checker, not a general semantic entailment model.
Missing facts are allowed. Query and clarification retain their separate display policy.
"""

from datetime import datetime
import re

from companion.conversation_recall import _terms
from companion.schedules import (
    DATE_EXPRESSIONS,
    KST,
    ChangeReceipt,
    Schedule,
    resolve_date,
    resolve_time,
)

_DATE = DATE_EXPRESSIONS
_TIME = re.compile(r'\d{1,2}:\d{2}|(?:(?:오전|오후|저녁|밤|새벽)\s*)?\d{1,2}시(?:\s*\d{1,2}분)?')
# Extract only explicit place/participant mentions; don't demand their presence.
_PLACE = re.compile(
    r'([가-힣A-Za-z0-9]+(?:\s+[가-힣A-Za-z0-9]+){0,2})에서|'
    r'([가-힣A-Za-z0-9]+(?:\s+[가-힣A-Za-z0-9]+){0,2}?)(?:으)?로\s*'
    r'(?:변경|바꿔|옮겨)|장소(?:는|를|가)?\s+([가-힣A-Za-z0-9]+(?:\s+[가-힣A-Za-z0-9]+)?)'
)
_PEOPLE = re.compile(r'([가-힣A-Za-z0-9]+?)(?:이랑|랑|와|과)(?=\s|$|이야|야)')
_UNKNOWN = {'time': r'시간(?:은|이)?\s*(?:아직\s*)?(?:미정|안\s*정|정하지\s*않)',
            'place': r'장소(?:는|가)?\s*(?:아직\s*)?(?:미정|안\s*정|정하지\s*않)',
            'participants_text': r'참여자(?:는|가)?\s*(?:아직\s*)?(?:미정|안\s*정|정하지\s*않)'}


def _mentioned_value_conflicts(mention: str, confirmed: str | None) -> bool:
    if confirmed is None:
        return True
    # The shortest place mention may omit a prefix (도서관 vs 시청 도서관).
    # Regex captures can include preceding conversational words; compare the noun tail.
    mention = re.sub(r'(?:이야|야|이고|고)$', '', mention)
    tokens = mention.split()
    known = confirmed.replace(' ', '').casefold()
    if tokens[-1].casefold() not in known:
        return True
    if len(tokens) > 1 and len(confirmed.split()) > 1:
        return ''.join(tokens[-len(confirmed.split()):]).casefold() not in known
    return False


def draft_conflicts(draft: str, receipt: ChangeReceipt, received_at: datetime) -> bool:
    if not receipt.schedules:
        return False
    item = receipt.schedules[0]
    for match in _DATE.finditer(draft):
        word = match.group()
        # "오늘은 쉬고 내일 일정은 저장했어" can mention unrelated dates. This check
        # conservatively treats explicit temporal mentions as claims about the receipt.
        if re.fullmatch(r'[월화수목금토일]요일', word):
            if datetime.fromisoformat(item.date).weekday() != '월화수목금토일'.index(word[0]):
                return True
            continue
        if '월' in word and '일' in word and '요일' not in word:
            numbers = [int(v) for v in re.findall(r'\d+', word)]
            actual = datetime.fromisoformat(item.date)
            expected = [actual.year, actual.month, actual.day] if len(numbers) == 3 else [
                actual.month, actual.day]
            if numbers != expected:
                return True
            continue
        day = resolve_date(word, received_at)
        if day is not None and day.isoformat() != item.date:
            return True
    for match in _TIME.finditer(draft):
        word = match.group()
        if item.time is None:
            return True
        if re.match(r'오[전후]|저녁|밤|새벽', word):
            word = re.sub(r'^(오전|오후)\s*', r'\1 ', word)
            resolved = resolve_time(word)
            if resolved != item.time:
                return True
        elif ':' in word:
            hour, minute = map(int, word.split(':'))
            if f'{hour:02d}:{minute:02d}' != item.time:
                return True
        else:
            numbers = [int(v) for v in re.findall(r'\d+', word)]
            hour, minute = numbers[0], numbers[1] if len(numbers) > 1 else 0
            actual_hour, actual_minute = map(int, item.time.split(':'))
            if minute != actual_minute or (hour != actual_hour and not (
                    1 <= hour <= 12 and hour % 12 == actual_hour % 12)):
                return True
    for name, pattern in _UNKNOWN.items():
        if getattr(item, name) is not None and re.search(pattern, draft):
            return True
    # Remove known non-place uses of 에서, and temporal/subject prefixes before extraction.
    place_text = re.sub(r'(?:내\s*)?일정에서', '', draft)
    place_text = re.sub('(?:' + _TIME.pattern + r')(?:에)?', '', place_text)
    place_text = _PEOPLE.sub('', place_text)
    place_text = re.sub('(?:' + _DATE.pattern + r')(?:에)?', '', place_text)
    place_text = re.sub(r'\b(?:다음에|이번에|나중에|이따가|아까)\s*', '', place_text)
    for match in _PLACE.finditer(place_text):
        mention = next(value for value in match.groups() if value)
        # 날짜/시간 changes ending in 로 are handled above, not treated as places.
        if _DATE.search(mention) or _TIME.search(mention):
            continue
        if _mentioned_value_conflicts(mention, item.place):
            return True
    people_text = re.sub(r'참여자(?:는|가|:)?\s+([가-힣A-Za-z0-9]+?)(?:이야|야|다)(?=[.!?]|$)',
                         r'\1랑 ', draft)
    for match in _PEOPLE.finditer(people_text):
        mention = match.group(1)
        if _mentioned_value_conflicts(mention, item.participants_text):
            return True
    return False


def query_draft_matches(
    draft: str, schedules: tuple[Schedule, ...], received_at: datetime,
) -> bool:
    """Accept natural partial recitals, but require an actual identifying fact.

    Each explicit mention must fit one confirmed row; a bare acknowledgement is
    not a query answer. Multi-row exhaustive recitals retain the display fallback.
    """
    for item in schedules:
        receipt = ChangeReceipt('applied', 'query', 'display', 1, schedules=(item,))
        if draft_conflicts(draft, receipt, received_at):
            continue
        if item.status == 'cancelled' and '취소' not in draft:
            continue
        if identifies_schedule(draft, item) or any(
                value and value in draft for value in (item.date, item.time)):
            return True
    return False


def identifies_schedule(draft: str, item: Schedule) -> bool:
    """A partial title noun is sufficient; generic schedule words alone aren't."""
    terms = _terms(item.title, canonical=True) - {'일정', '예약', '약속', '모임'}
    return bool(terms & _terms(draft, canonical=True)) or any(
        value and value in draft for value in (item.title, item.place, item.participants_text)
    )


def correct_context_relative_date(
    draft: str, rows: tuple[Schedule, ...], received_at: datetime,
) -> str:
    """Correct one relative reference only when all remaining facts identify one row."""
    words = list(_DATE.finditer(draft))
    if len(words) != 1 or words[0].group() not in {'오늘', '내일', '모레'}:
        return draft
    without_date = _DATE.sub('', draft)
    candidates = [item for item in rows if identifies_schedule(without_date, item)
                  and not draft_conflicts(without_date, ChangeReceipt(
                      'applied', 'query', 'context', 1, schedules=(item,)), received_at)]
    if len(candidates) != 1:
        return draft
    delta = (datetime.fromisoformat(candidates[0].date).date()
             - received_at.astimezone(KST).date()).days
    replacement = {0: '오늘', 1: '내일', 2: '모레'}.get(delta)
    if replacement is None:
        return draft
    match = words[0]
    return draft[:match.start()] + replacement + draft[match.end():]
