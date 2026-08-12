"""Say the next sentence while the last one is still being converted.

The voice is two models in a row: stage 1 says the words, stage 2 makes them
hers. Run whole-answer-at-a-time, a two-sentence reply costs about ten seconds
before the first sound — six saying it, two and a half converting it, and the
model's own thinking on top.

Nothing about that ordering is necessary. The sentences are independent, so
stage 2 can be converting sentence one while stage 1 is still saying sentence
two, and playback can start as soon as the first piece is converted. The first
sound arrives after one sentence rather than after all of them.

Three stages, two threads
-------------------------
Synthesis and conversion each get a thread and hand work along a queue; the
caller consumes converted pieces in order. Order needs no bookkeeping because
each stage processes its input sequentially — the pipelining is between stages,
not within one.

The queues are bounded. Unbounded, a fast stage in front of a slow one would
hold an entire answer's audio in memory for no benefit, and the useful lead is
one piece: what matters is that the next stage never waits, not that the first
runs to completion.

Failures travel with the work rather than being raised on a thread nobody is
watching, where they would appear as a queue that simply stops.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
import queue
import threading
from typing import TypeVar

# The pipeline does not care what flows through it — audio here, but the shape
# is "run these two steps over a list, overlapped" and the types say so.
In = TypeVar("In")
Mid = TypeVar("Mid")
Out = TypeVar("Out")

# One piece of lead is enough to keep the next stage busy. More only buys memory.
QUEUE_DEPTH = 1


class _Done:
    """End of stream. A distinct type, so a falsy piece cannot end the stream."""


def _pump(
    items: Iterable[In],
    work: Callable[[In], Mid],
    out: queue.Queue[object],
) -> None:
    """Run ``work`` over each item, passing results and failures downstream."""
    try:
        for item in items:
            out.put(work(item))
    except BaseException as error:  # noqa: BLE001 - handed to the consumer
        out.put(error)
    finally:
        out.put(_Done())


def _drain(source: queue.Queue[object], _kind: type[Mid]) -> Iterator[Mid]:
    while True:
        item = source.get()
        if isinstance(item, _Done):
            return
        if isinstance(item, BaseException):
            raise item
        yield item  # type: ignore[misc]


def stream(
    sentences: Sequence[str],
    synthesize: Callable[[str], Mid],
    convert: Callable[[Mid], Out],
) -> Iterator[Out]:
    """Yield converted pieces in order, with the two stages overlapping.

    Both callables are passed in rather than reached for: this file is about
    the ordering, and which models sit behind it is the caller's business.
    """
    spoken: queue.Queue[object] = queue.Queue(maxsize=QUEUE_DEPTH)
    converted: queue.Queue[object] = queue.Queue(maxsize=QUEUE_DEPTH)

    speaking = threading.Thread(
        target=_pump, args=(list(sentences), synthesize, spoken), daemon=True
    )
    converting = threading.Thread(
        target=_pump, args=(_drain(spoken, object), convert, converted), daemon=True
    )
    speaking.start()
    converting.start()
    yield from _drain(converted, object)  # type: ignore[misc]
