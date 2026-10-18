"""Context Assembler for Sovereign Memory.

Builds context blocks according to the Zep paper (Section 3).
Handles decay scaling and factual consolidation bounds.
"""

import math
from typing import Any, Dict, List


class ContextAssembler:
    """Assembles context blocks for prompting and incorporates decay metrics."""

    def __init__(self, decay_lower_bound: float = 0.3, decay_upper_bound: float = 1.5):
        """
        Initialize the assembler with decay parameters from published 0.3-1.5 bounds.
        """
        if not (0.0 <= decay_lower_bound <= decay_upper_bound):
            raise ValueError("Decay bounds must be properly ordered and non-negative")
        self.lower_bound = decay_lower_bound
        self.upper_bound = decay_upper_bound

    def assemble_context(
        self, active_facts: List[Dict[str, Any]], recent_events: List[Dict[str, Any]]
    ) -> str:
        """
        Assemble a single context block string for the current model prompt.

        This mimics Zep-style context assembly where long-term factual memories
        are summarized alongside recent episodic/temporal events.

        Args:
            active_facts: Processed facts retrieved from semantic search.
            recent_events: Recently captured episodic interactions.

        Returns:
            A formatted context block.
        """
        blocks = []

        if active_facts:
            blocks.append("--- Relevant Knowledge ---")
            for fact in active_facts:
                content = fact.get("content", fact.get("raw_event", "Unknown Fact"))
                blocks.append(f"- {content}")

        if recent_events:
            blocks.append("--- Recent Context ---")
            for evt in recent_events:
                # Naive text extraction
                content = evt.get("content", evt.get("raw_event", "Unknown Event"))
                blocks.append(f"- {content}")

        return "\n".join(blocks)

    def calculate_decay_score(
        self, base_score: float, time_diff_seconds: float, tau: float
    ) -> float:
        """
        Scale relevance by an exponential multiplier within the configured bounds.

        Args:
            base_score: The original relevance score.
            time_diff_seconds: Time elapsed since memory was recorded or last accessed.
            tau: Time constant for decay scaling, in seconds. The published method
                discloses no value, so the caller supplies it from its own configuration.

        Returns:
            The original score multiplied by the bounded decay factor.
        """
        decay_factor = math.exp(-time_diff_seconds / tau)
        return base_score * max(self.lower_bound, min(decay_factor, self.upper_bound))
