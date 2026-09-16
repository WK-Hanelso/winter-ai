"""Transient web evidence contracts; never a memory or conversation record."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from configs.dialogue import web


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


def render_results(result: WebSearchResult) -> str:
    """Keep each included URL intact; shorten only labels/excerpts to fit."""
    block = f'검색 결과 (검색어: {compact(result.query)}, {len(result.results)}건)'
    for index, item in enumerate(result.results, 1):
        site = urlsplit(item.url).hostname or item.url
        heading = f'\n[{index}] {compact(item.title)[:200]} — {site}\n'
        footer = f'\nURL: {item.url}'
        room = web.BLOCK_CHARS - len(block) - len(heading) - len(footer)
        if room <= 0:
            break
        excerpt = compact(item.page or item.content)[:min(web.PAGE_CHARS, room)]
        block += heading + excerpt + footer
    return block[:web.BLOCK_CHARS]
