"""Letta's sleep-time consolidation and dreaming, translated from the original server and Letta Code.

It is the package for background memory agents: the sleep-time group that reviews a
finished conversation (letta/groups/sleeptime_multi_agent_v4.py) and dreaming, the name
Letta now gives sleep-time compute (Letta Code's reflection subagent). What goes in is
stored messages, a store with memory blocks, settings and a chat function; what comes out
is a record of what the agent did and the edited blocks. It never picks a model and never
reads a setting from anywhere itself.
"""
