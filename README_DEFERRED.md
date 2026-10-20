# Deferred Model/Provider Requirements

The Letta memory model and SDK wrappers intrinsically require initialized `LettaConfig` contexts that establish the underlying `AgentState`, LLM endpoints, Embedders, and tools mappings required by the upstream test suite `test_memory.py`.

As per the user instructions ("Mark acceptance that genuinely requires a selected real model as DEFERRED pending later selection, not as a code failure... Do not download arbitrary models... Any supplied numerical vectors used for non-model database operations are not real-model quality proof"):

The actual operation and runtime validation of `test_memory.py` is marked as **DEFERRED** pending configuration of the actual LLMs and global consensus dependencies required by the upstream test context. The structural DuckDB dialect and relational components are extracted, mapped, and preserved alongside exact licensing, but execution coverage must await integration with a real LLM framework configuration on the workspace.
