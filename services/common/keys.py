"""Redis key and channel names, in one place."""

COMMANDS = "mf:cmd"  # stream: gateway requests to the matcher
COMMANDS_GROUP = "matcher"
EPOCH = "mf:matcher:epoch"  # INCR on every matcher start
HEARTBEAT = "mf:matcher:heartbeat"  # set with a short TTL while the matcher runs
CATALOG = "mf:catalog"  # the backtest data catalog (M11), written by the worker at startup


def reply_stream(worker_id: str) -> str:
    return f"mf:reply:{worker_id}"


def book(symbol: str) -> str:
    return f"mf:book:{symbol}"  # latest snapshot, written with each update


def md_channel(symbol: str) -> str:
    return f"md:{symbol}"


def exec_channel(account_id: int) -> str:
    return f"exec:{account_id}"


def backtest_channel(run_id: str) -> str:
    return f"bt:{run_id}"


def rate(account_id: int, second: int) -> str:
    return f"mf:rate:{account_id}:{second}"


def practice(ip: str, hour: int) -> str:
    return f"mf:practice:{ip}:{hour}"  # practice accounts created from one client IP (M11)
