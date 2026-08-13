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

The first piece is not overlapped
--------------------------------
Both stages share one card. Measured on the 2060, converting a sentence takes
1.42s alone and 2.58s while the next sentence is being synthesised beside it —
the overlap that helps every later sentence is taken out of the one sound 천우
is actually waiting for. So stage 1 holds after the first sentence until that
sentence has come out the far end, and overlaps freely from then on: by that
point the first sound is already playing, and later sentences have its duration
to be ready in.

Failures travel with the work rather than being raised on a thread nobody is
watching, where they would appear as a queue that simply stops.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
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
    sentences: Iterable[str],
    synthesize: Callable[[str], Mid],
    convert: Callable[[Mid], Out],
) -> Iterator[Out]:
    """Yield converted pieces in order, with the two stages overlapping.

    Both callables are passed in rather than reached for: this file is about
    the ordering, and which models sit behind it is the caller's business.
    """
    spoken: queue.Queue[object] = queue.Queue(maxsize=QUEUE_DEPTH)
    converted: queue.Queue[object] = queue.Queue(maxsize=QUEUE_DEPTH)
    first_is_out = threading.Event()

    def one_at_a_time_until_first_sound(items: Iterable[In]) -> Iterator[In]:
        for index, item in enumerate(items):
            if index == 1:
                first_is_out.wait()
            yield item

    # Not materialised: the sentences arrive as the model writes them, and
    # taking a list here would wait for the last one before saying the first.
    speaking = threading.Thread(
        target=_pump,
        args=(one_at_a_time_until_first_sound(sentences), synthesize, spoken),
        daemon=True,
    )
    converting = threading.Thread(
        target=_pump, args=(_drain(spoken, object), convert, converted), daemon=True
    )
    speaking.start()
    converting.start()
    try:
        for piece in _drain(converted, object):
            # Before handing it over, not after: stage 1 can start on the next
            # sentence while the caller is still writing this one out.
            first_is_out.set()
            yield piece  # type: ignore[misc]
    finally:
        # A failure or an abandoned stream must not leave stage 1 waiting for a
        # first sound that is never coming.
        first_is_out.set()
