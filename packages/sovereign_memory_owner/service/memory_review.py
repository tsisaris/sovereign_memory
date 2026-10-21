"""The review screen: cases where models disagreed, shown beside the original, and the owner's judgment.

It lists the cases where the models' first answers differ, plus cases the owner
chose to judge. For one case it shows the original text beside each model's
answer, and it records the owner's pick or typed answer as a judgment. It
follows the same visibility rules as every other read: a case is shown only when
every event it points to is live (not removed, not expired) and visible to the
signed-in principal, so a forgotten item never appears. The screen is served as
data and as one HTML page by the Owner's own interface. It never trains, never
runs a model and never edits a case, an output or an earlier judgment.
"""

import html
from datetime import datetime, timezone
from typing import Any

from duckdb_sqlalchemy_async.duckdb_sqlalchemy_async_bridge import AsyncConnection

from service.memory_ground_truth import (
    MODEL_OUTPUT,
    case_row,
    judgments_of,
    latest_judgment,
    list_case_ids,
    outputs_of,
    record_judgment,
)
from service.memory_owner_core import OwnerCore
from service.memory_processor_seam import AuthenticatedPrincipal, VisibilityPolicy
from service.memory_tier_support import event_text


def disagree(outputs: list[dict[str, Any]]) -> bool:
    """Say whether the models' first-ranked items differ; pure.

    Only "model" outputs count, and a model with an empty ranking has no first
    answer to differ with. Fewer than two answering models never disagree.
    """
    firsts = {
        out["output"][0]["item_id"]
        for out in outputs
        if out["role"] == MODEL_OUTPUT and isinstance(out["output"], list) and out["output"]
    }
    return len(firsts) > 1


class ReviewScreen:
    """List, show and judge cases for one principal, under the ordinary visibility rules.

    Goes in: the owner's connection and visibility policy. Comes out: lists and
    views containing only cases the principal may see. It never shows a removed
    or forgotten item, and a judgment it records is a new superseding row.
    """

    def __init__(self, conn: AsyncConnection, policy: VisibilityPolicy) -> None:
        """Bind the owner's connection and the visibility policy."""
        self.conn = conn
        self.core = OwnerCore(conn)
        self.policy = policy

    async def _visible(self, principal: AuthenticatedPrincipal, case: dict[str, Any]) -> bool:
        """Say whether every event of a case is live and permitted to the principal."""
        if case["source_removed"]:
            return False
        visibility = await self.policy.resolve(principal)
        producers = await self.core.live_producers(case["events"])
        return all(
            event_id in producers and visibility.permits(producers[event_id], principal.agent_id)
            for event_id in case["events"]
        )

    async def list_cases(self, principal: AuthenticatedPrincipal) -> list[dict[str, Any]]:
        """Return the cases to review: disagreements and owner-selected cases, visible to the principal.

        Each entry has the case id, kind, memory kind, input kind, when it arose,
        whether models disagree, whether it is already judged and the latest
        verdict. Cases it may not show are absent, not blanked.
        """

        def read(handle):
            """Gather every case with its outputs and latest judgment on the owner's worker."""
            return [
                (
                    case_row(handle, case_id),
                    outputs_of(handle, case_id),
                    latest_judgment(handle, case_id),
                )
                for case_id in list_case_ids(handle)
            ]

        listed = []
        for case, outputs, judgment in await self.conn.run(lambda: read(self.conn.native)):
            disagreement = disagree(outputs)
            if not (disagreement or case["selected_by_owner"]):
                continue
            if not await self._visible(principal, case):
                continue
            listed.append(
                {
                    "case_id": case["case_id"],
                    "kind": case["kind"],
                    "memory_kind": case["memory_kind"],
                    "input_kind": case["input_kind"],
                    "created_at": case["created_at"],
                    "disagreement": disagreement,
                    "selected_by_owner": case["selected_by_owner"],
                    "judged": judgment is not None,
                    "verdict": None if judgment is None else judgment["verdict"],
                }
            )
        return listed

    async def show(self, principal: AuthenticatedPrincipal, case_id: str) -> dict[str, Any]:
        """Return one case with the original beside each model's answer.

        "original" holds the text of each event the case points to; every model
        output lists its items, each with the original text of that item's event.
        Raises PermissionError when the principal may not see the case or its
        source was removed (the same refusal as any other read), and ValueError
        for an unknown case.
        """
        raw = await self.conn.run(
            lambda: (
                case_row(self.conn.native, case_id),
                outputs_of(self.conn.native, case_id),
                judgments_of(self.conn.native, case_id),
            )
        )
        case, outputs, judgments = raw
        if not await self._visible(principal, case):
            raise PermissionError("The case is not visible to this principal")
        originals: dict[str, str] = {}
        for event_id in sorted(
            {*case["events"], *(e["event_id"] for o in outputs for e in _items(o))}
        ):
            event, payload = await self.core.load_event(event_id)
            originals[event_id] = event_text(event, payload)[0]
        return {
            "case": case,
            "original": {event_id: originals[event_id] for event_id in case["events"]},
            "answers": [
                {
                    "key": out["key"],
                    "role": out["role"],
                    "combiner_id": out["combiner_id"],
                    "items": [
                        {**item, "original": originals[item["event_id"]]} for item in _items(out)
                    ],
                    "scores": out["output"] if isinstance(out["output"], dict) else None,
                }
                for out in outputs
            ],
            "judgments": judgments,
            "disagreement": disagree(outputs),
        }

    async def judge(
        self,
        principal: AuthenticatedPrincipal,
        case_id: str,
        *,
        verdict: str,
        chosen_items: list[str],
        typed_answer: str | None,
        note: str | None,
    ) -> str:
        """Record the principal's judgment of a case it may see; return the new judgment id.

        The judgment is a new row that supersedes the earlier one. Raises
        PermissionError when the case is not visible, ValueError for a malformed
        judgment (see record_judgment). Nothing already recorded is changed.
        """
        case = await self.conn.run(lambda: case_row(self.conn.native, case_id))
        if not await self._visible(principal, case):
            raise PermissionError("The case is not visible to this principal")
        judged_at = datetime.now(timezone.utc).isoformat()

        def write() -> str:
            """Append the judgment in one transaction on the owner's worker."""
            handle = self.conn.native
            handle.begin()
            try:
                judgment_id = record_judgment(
                    handle,
                    case_id=case_id,
                    judged_by=principal.agent_id,
                    judged_at=judged_at,
                    verdict=verdict,
                    chosen_items=chosen_items,
                    typed_answer=typed_answer,
                    note=note,
                )
                handle.commit()
                return judgment_id
            except BaseException:
                handle.rollback()
                raise

        return await self.conn.run(write)

    @staticmethod
    def render(view: dict[str, Any]) -> str:
        """Render one case view as an HTML page, the original on the left and each model's answer beside it.

        Every value is escaped. Goes in: the dict show returned. Comes out: a
        complete HTML document. It reads nothing and writes nothing.
        """
        case = view["case"]
        originals = "".join(
            f"<h3>{html.escape(event_id)}</h3><pre>{html.escape(text)}</pre>"
            for event_id, text in view["original"].items()
        )
        answers = ""
        for answer in view["answers"]:
            items = "".join(
                f"<li><code>{html.escape(item['item_id'])}</code> "
                f"(event {html.escape(item['event_id'])}, score {html.escape(str(item.get('score')))})"
                f"<pre>{html.escape(item['original'])}</pre></li>"
                for item in answer["items"]
            )
            scores = (
                f"<pre>{html.escape(str(answer['scores']))}</pre>"
                if answer["scores"] is not None
                else ""
            )
            answers += (
                f"<section><h3>{html.escape(answer['key'])} "
                f"<small>({html.escape(answer['role'])}, combiner "
                f"{html.escape(answer['combiner_id'])})</small></h3><ol>{items}</ol>{scores}</section>"
            )
        judged = "".join(
            f"<li>{html.escape(j['judged_by'])} at {html.escape(j['judged_at'])}: "
            f"{html.escape(j['verdict'])} {html.escape(str(j['chosen_items']))} "
            f"{html.escape(j['typed_answer'] or '')} {html.escape(j['note'] or '')}</li>"
            for j in view["judgments"]
        )
        return (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<title>Review {html.escape(case['case_id'])}</title></head><body>"
            f"<h1>Case {html.escape(case['case_id'])}</h1>"
            f"<p>{html.escape(case['kind'])} / {html.escape(case['memory_kind'])}; "
            f"input ({html.escape(case['input_kind'])}): {html.escape(case['input'])}; "
            f"models disagree: {html.escape(str(view['disagreement']))}</p>"
            "<div style='display:flex;gap:2em'>"
            f"<div><h2>Original</h2>{originals}</div><div><h2>Answers</h2>{answers}</div></div>"
            f"<h2>Judgments</h2><ol>{judged}</ol></body></html>"
        )


def _items(output: dict[str, Any]) -> list[dict[str, Any]]:
    """Return an output's ranked items, or an empty list for class scores; pure."""
    return output["output"] if isinstance(output["output"], list) else []
