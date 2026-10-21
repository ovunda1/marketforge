"""Naive reference order book for differential testing against the C++ engine.

Deliberately simple: plain lists and linear scans, no clever data structures, so its
correctness is easy to check by reading. See docs/event-format.md for the formats.
"""
