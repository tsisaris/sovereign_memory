"""Letta's conversation compaction, translated from letta/services/summarizer.

It is the package that shortens a conversation by summarizing its oldest
messages, in the four modes Letta has (all, sliding_window, self_compact_all,
self_compact_sliding_window). What goes in is stored messages, the compaction
settings and a chat function; what comes out is a summary and the messages that
stay. It never picks a model and never reads a setting from anywhere itself.
"""
