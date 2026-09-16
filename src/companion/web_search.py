"""Transient web evidence contracts; never a memory or conversation record."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from configs.dialogue import context, web


class WebSearchError(RuntimeError):
    """Search transport or response failed; the dialogue uses a fixed receipt."""


@dataclass(frozen=True)
class WebRequest:
    query: str
    why: str


@dataclass(frozen=True)
class WebSearchItem:
    title: str
    url: str
    content: str
    engines: tuple[str, ...] = ()
    page: str = ""


@dataclass(frozen=True)
class WebSearchResult:
    query: str
    results: tuple[WebSearchItem, ...] = ()
    unresponsive_engines: tuple[str, ...] = ()

    @property
    def pages(self) -> int:
        return sum(bool(item.page) for item in self.results)


def compact(text: str) -> str:
    return ' '.join(text.split())


def render_results(
    result: WebSearchResult, *, prompt_tokens: int | None = None,
    context_tokens: int = context.CONTEXT_TOKENS, max_tokens: int | None = None,
) -> str:
    """Fit measured headroom; preserve snippets/URLs before adding page bodies.

    No timings means the existing configured cap. Missing output budget cannot
    arise in the companion runtime, so also keep that cap for standalone callers.
    An empty block means not even one cited excerpt fits; do not generate from it.
    """
    limit = web.BLOCK_CHARS
    if prompt_tokens is not None and max_tokens is not None:
        remaining = context_tokens - prompt_tokens - max_tokens - web.RESERVE_TOKENS
        limit = min(limit, max(0, int(remaining * web.CHARS_PER_TOKEN)))
    header = f'검색 결과 (검색어: {compact(result.query)}, {len(result.results)}건)'
    entries = []
    for index, item in enumerate(result.results, 1):
        site = urlsplit(item.url).hostname or item.url
        heading = f'\n[{index}] {compact(item.title)[:200]} — {site}\n'
        footer = f'\nURL: {item.url}'
        entries.append((heading, compact(item.content)[:web.PAGE_CHARS],
                        compact(item.page)[:web.PAGE_CHARS], footer))

    # Reserve at least one excerpt character per source. Drop trailing sources
    # only if their intact headings/URLs cannot fit even without page bodies.
    while entries and (len(header) + sum(len(h) + len(f) + 1 for h, _, _, f in entries)
                       > limit):
        entries.pop()
    if not entries:
        return ''
    overhead = len(header) + sum(len(h) + len(f) for h, _, _, f in entries)
    snippet_room = limit - overhead
    # If snippets alone exceed the budget, shorten them evenly, after all page
    # bodies have been removed. Never let the first long page crowd out snippets.
    snippet_cap = web.PAGE_CHARS
    while sum(min(len(s), snippet_cap) for _, s, _, _ in entries) > snippet_room:
        snippet_cap -= 1
    snippets = [s[:snippet_cap] for _, s, _, _ in entries]
    page_room = snippet_room - sum(map(len, snippets))
    block = header
    for (heading, _, page, footer), snippet in zip(entries, snippets, strict=True):
        body = ''
        label = '\n본문: '
        if page and page_room > len(label):
            body = label + page[:page_room - len(label)]
            page_room -= len(body)
        block += heading + snippet + body + footer
    return block
