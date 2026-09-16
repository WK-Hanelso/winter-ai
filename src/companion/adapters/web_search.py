"""SearXNG plus bounded, best-effort HTML/plain-text excerpts (stdlib only)."""

from dataclasses import replace
from html.parser import HTMLParser
from http.client import HTTPException
import json
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from configs.dialogue import web

from companion.web_search import WebSearchError, WebSearchItem, WebSearchResult

PAGE_BYTES = 300_000
SEARCH_BYTES = 2_000_000


def compact(text: str) -> str:
    return ' '.join(text.split())


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {'script', 'style', 'nav'}:
            self.hidden.append(tag)
        if not self.hidden:
            self.parts.append(' ')

    def handle_endtag(self, tag: str) -> None:
        if tag in self.hidden:
            self.hidden = self.hidden[:self.hidden.index(tag)]
        if not self.hidden:
            self.parts.append(' ')

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


class SearXNGWebSearch:
    def __init__(self, base: str = web.SEARXNG_URL) -> None:
        self.base = base.rstrip('/')

    def search(self, query: str) -> WebSearchResult:
        url = self.base + '/search?' + urlencode({
            'q': query, 'format': 'json', 'language': 'ko-KR', 'safesearch': 0,
        })
        try:
            with urlopen(url, timeout=web.TIMEOUT_SECONDS) as response:
                raw = response.read(SEARCH_BYTES + 1)
                if len(raw) > SEARCH_BYTES:
                    raise ValueError('search response exceeds size limit')
                payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
                raise ValueError('missing results')
            items = []
            for row in payload['results'][:web.MAX_RESULTS]:
                if (not isinstance(row, dict) or not all(isinstance(row.get(k), str)
                        for k in ('title', 'url', 'content'))):
                    raise ValueError('invalid search result')
                parsed = urlsplit(row['url'])
                if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
                    continue
                engines = row.get('engines', [])
                items.append(WebSearchItem(row['title'], row['url'], row['content'],
                    tuple(e for e in engines if isinstance(e, str))
                    if isinstance(engines, list) else ()))
            failures = payload.get('unresponsive_engines', [])
            unavailable = tuple(str(e) for e in failures) if isinstance(failures, list) else ()
        except (OSError, HTTPException, ValueError, TypeError) as error:
            raise WebSearchError('search unavailable') from error
        for index, item in enumerate(items[:web.MAX_PAGES]):
            items[index] = replace(item, page=self._page(item.url))
        return WebSearchResult(query, tuple(items), unavailable)

    @staticmethod
    def _page(url: str) -> str:
        try:
            request = Request(url, headers={'User-Agent': 'Winter/1.0',
                                            'Accept': 'text/html,text/plain'})
            with urlopen(request, timeout=6) as response:
                mime = response.headers.get_content_type()
                if mime not in {'text/html', 'text/plain'}:
                    return ''
                text = response.read(PAGE_BYTES).decode(
                    response.headers.get_content_charset() or 'utf-8', errors='replace')
            if mime == 'text/html':
                parser = PageText()
                parser.feed(text)
                text = ''.join(parser.parts)
            return compact(text)[:web.PAGE_CHARS]
        except (OSError, HTTPException, ValueError, LookupError):
            return ''
