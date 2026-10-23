"""WebSocket fan-out in an API worker (M10). publish() never waits for a client: each
subscriber has a bounded queue. An overflow empties it and makes that client resnapshot;
repeated overflows close it. Other clients are unaffected."""

from __future__ import annotations

import asyncio
import inspect

from services.api.hub import CLOSED, RESNAPSHOT, Hub, Subscriber


def msg(n: int) -> dict:
    return {"type": "update", "symbol": "AAA", "epoch": 1, "md_seq": n}


def drain(sub: Subscriber) -> list:
    async def go() -> list:
        out = []
        while sub.pending():
            out.append(await sub.get())
        return out
    return asyncio.run(go())


def test_publish_is_synchronous_and_reaches_every_subscriber_in_order() -> None:
    hub = Hub()
    assert not inspect.iscoroutinefunction(hub.publish)
    a, b = Subscriber(maxsize=100), Subscriber(maxsize=100)
    hub.subscribe("md:AAA", a)
    hub.subscribe("md:AAA", b)
    hub.subscribe("md:BBB", Subscriber(maxsize=100))
    for n in range(1, 51):
        assert hub.publish("md:AAA", msg(n)) == 2
    assert drain(a) == drain(b) == [msg(n) for n in range(1, 51)]


def test_a_slow_subscriber_is_told_to_resnapshot_and_does_not_affect_others() -> None:
    hub = Hub()
    slow, fast = Subscriber(maxsize=8), Subscriber(maxsize=1000)
    hub.subscribe("md:AAA", slow)
    hub.subscribe("md:AAA", fast)
    for n in range(1, 21):  # nobody reads `slow`
        hub.publish("md:AAA", msg(n))
    got = drain(slow)
    assert got[0] is RESNAPSHOT
    assert got[1:] == [msg(n) for n in range(17, 21)]  # what came after the overflow
    assert drain(fast) == [msg(n) for n in range(1, 21)]


def test_repeated_overflows_close_the_subscriber_and_unsubscribe_it() -> None:
    now = [0.0]
    hub = Hub()
    sub = Subscriber(maxsize=4, max_overflows=3, window_s=10.0, clock=lambda: now[0])
    hub.subscribe("md:AAA", sub)
    for n in range(1, 4 * 5):
        hub.publish("md:AAA", msg(n))
    assert sub.closed and sub.close_reason == "slow consumer"
    assert drain(sub)[-1] is CLOSED
    assert hub.publish("md:AAA", msg(99)) == 0


def test_overflows_outside_the_window_do_not_add_up() -> None:
    now = [0.0]
    sub = Subscriber(maxsize=2, max_overflows=2, window_s=1.0, clock=lambda: now[0])
    for round_ in range(10):
        now[0] += 5.0
        for n in range(3):
            sub.offer(msg(round_ * 10 + n))
        drain(sub)
    assert not sub.closed


def test_an_initial_subscriber_starts_with_a_snapshot_request() -> None:
    sub = Subscriber(maxsize=10, initial_snapshot=True)
    sub.offer(msg(1))
    got = drain(sub)
    assert got[0] is RESNAPSHOT and got[1:] == [msg(1)]
