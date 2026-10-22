"""MarketForge matching engine bindings (M7).

The real C++ engine driven from Python: the same code that runs the live exchange
matches a backtest's orders. Commands and events are immutable values copied across the
boundary; ``Engine.process_batch`` runs many commands with the GIL released. Error
classes and their meaning: ``MarketForgeError`` and its subclasses (docs/learning/m7.md).
"""

from mfengine import _core
from mfengine._core import *  # noqa: F403

Command = (_core.NewOrder | _core.Cancel | _core.Replace | _core.SetSession
           | _core.SetInstrumentLimits | _core.SetAccountLimits | _core.KillSwitch)
Event = (_core.Ack | _core.Reject | _core.Fill | _core.Cancelled | _core.Trade | _core.Replaced
         | _core.SessionChanged | _core.AdminAck | _core.AdminReject)
