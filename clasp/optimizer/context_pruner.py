"""
clasp/optimizer/context_pruner.py
=====================================
Message-history pruning for small-context providers (plan.md §14 "Phase 6
— Payload Optimization", "Context Pruner (`optimizer/context_pruner.py`)";
CLAUDE.md task 2).

``prune()`` is a strategy dispatcher matching the three names
`config/settings.py`'s `ContextPruningConfig.strategy` accepts
(`keep_edges` | `truncate` | `summarize`):

- **``keep_edges``** — fully implemented here. This is the strategy CLAUDE.md
  asked for, and the one Claude Code actually benefits from: preserve the
  conversation's *anchors* (its opening turns and its most recent turns)
  while dropping aged-out middle content, since an agentic coding session's
  early system-setting and current task context both matter far more than
  turn 47's now-irrelevant intermediate chatter.
- **``truncate``** — also implemented. Plan.md fully specifies its
  semantics in one line ("simple oldest-first truncation") and it's a
  handful of lines with no new concepts, so leaving it as a hard
  `NotImplementedError` for anyone who flips `strategy: truncate` in their
  config would be a needless landmine for something this fully specified.
- **``summarize``** — *not* implemented; raises `NotImplementedError` with
  an explanation. Summarizing the dropped section requires an actual LLM
  call (plan.md: "send dropped section to cheapest model for a 200-token
  summary"), which means wiring in `router/selector.py` and a provider —
  cross-cutting infrastructure this module shouldn't own, and well beyond
  "the prune() function with keep_edges strategy" as scoped for this turn.

``keep_edges`` algorithm
---------------------------
1. If the conversation already fits `max_tokens`, return it unchanged —
   no pruning, no restructuring, nothing to log. (plan.md test: "short
   history under the limit returns unchanged".)
2. Otherwise, partition messages into a *head* (first `keep_first`), a
   *tail* (last `keep_last`), and everything else in between.
3. Within that middle section, any message containing a `tool_use` or
   `tool_result` content block is unconditionally protected — never
   dropped, regardless of position (plan.md: "all tool_use/tool_result
   messages"). This is a flat per-message rule, not pair-matching logic:
   a `tool_use` message and its corresponding `tool_result` message both
   independently qualify as "a tool message", so a pair can never end up
   split across "kept" and "dropped" — the "never split apart" test
   guarantee falls out of this simpler rule for free, without needing to
   explicitly detect pairs at all.
4. Everything else in the middle is dropped, and replaced with a single
   note per *contiguous* dropped run — `"[CLASP: N messages omitted to
   fit context limit]"` — inserted exactly where that run was, so multiple
   separate gaps (when tool messages are scattered through the middle)
   each get their own correctly-counted note rather than one note for the
   whole pruned region.
5. If the result *still* exceeds `max_tokens` after that single pass, the
   head/tail boundaries themselves shrink by one message at a time (never
   below `MIN_EDGE_KEEP` each) and the algorithm re-runs, growing the
   "middle" window each iteration so more non-tool content becomes
   droppable — without ever touching a tool message, which remains
   protected at every iteration. This is what plan.md's test "still over
   limit after pruning → further trims from middle window" is checking:
   the *edges* concede ground before the *tool-message protection* ever
   does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover — shim for test environments
    import logging as _logging

    class _Shim:
        _log = _logging.getLogger("clasp.context_pruner")

        def debug(self, msg: str, **kw: Any) -> None:
            self._log.debug(msg + ("  " + str(kw) if kw else ""))

        def warning(self, msg: str, **kw: Any) -> None:
            self._log.warning(msg + ("  " + str(kw) if kw else ""))

    logger = _Shim()  # type: ignore[assignment]

from clasp.providers.common.token_counter import estimate_tokens

#: Never shrink an edge below this many messages, even when still over
#: budget — the very first message (usually foundational instructions) and
#: the very last (the user's current turn) are always worth keeping.
MIN_EDGE_KEEP = 1

#: Safety bound on the edge-shrinking loop — with MIN_EDGE_KEEP=1 on both
#: sides this is generous headroom; exists only to guarantee termination.
_MAX_SHRINK_ITERATIONS = 200

_NOTE_TEMPLATE = "[CLASP: {count} messages omitted to fit context limit]"


@dataclass
class PruneResult:
    """Result of a `prune()` call."""

    messages: list[dict[str, Any]]
    pruned: bool
    """False only when the input already fit `max_tokens` — i.e. step 1 short-circuited."""
    dropped_count: int = 0
    """Total messages dropped across every pass (0 if not pruned)."""
    notes_inserted: int = 0
    """Number of distinct `[CLASP: ... omitted]` notes inserted."""
    final_keep_first: int = 0
    """`keep_first` actually used, after any edge-shrinking (plan.md test: shrinks under pressure)."""
    final_keep_last: int = 0
    """`keep_last` actually used, after any edge-shrinking."""
    still_over_limit: bool = False
    """True if even at the MIN_EDGE_KEEP floor, the result doesn't fit `max_tokens`
    (everything left is either an edge at its floor or a protected tool message —
    `keep_edges` has no more ground to give; the caller may want to escalate to a
    different strategy, but that decision belongs to the caller, not this function)."""
    estimated_tokens: int = 0
    """Estimated token count of the returned `messages`."""


# ---------------------------------------------------------------------------
# Tool-message detection
# ---------------------------------------------------------------------------

def _is_tool_message(message: dict[str, Any]) -> bool:
    """True if any content block in *message* is a tool_use or tool_result."""
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result")
        for block in content
    )


# ---------------------------------------------------------------------------
# keep_edges — single pass
# ---------------------------------------------------------------------------

def _keep_edges_single_pass(
    messages: list[dict[str, Any]],
    keep_first: int,
    keep_last: int,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    One keep_edges pass for the given edge sizes.

    Returns ``(result_messages, dropped_count, notes_inserted)``. Pure —
    does not check `max_tokens` itself; the caller decides whether to loop.
    """
    n = len(messages)
    keep_first = min(keep_first, n)
    keep_last = min(keep_last, max(0, n - keep_first))

    head_end = keep_first
    tail_start = n - keep_last

    protected = [
        head_end <= i < tail_start and _is_tool_message(messages[i])
        for i in range(n)
    ]

    result: list[dict[str, Any]] = []
    dropped_count = 0
    notes_inserted = 0
    run_length = 0  # length of the current contiguous dropped run, in the middle

    def _flush_run() -> None:
        nonlocal run_length, notes_inserted
        if run_length > 0:
            result.append({"role": "user", "content": _NOTE_TEMPLATE.format(count=run_length)})
            notes_inserted += 1
            run_length = 0

    for i in range(n):
        in_middle = head_end <= i < tail_start
        if in_middle and not protected[i]:
            run_length += 1
            dropped_count += 1
            continue
        _flush_run()
        result.append(messages[i])
    _flush_run()

    return result, dropped_count, notes_inserted


# ---------------------------------------------------------------------------
# keep_edges — full strategy, with edge-shrinking retry loop
# ---------------------------------------------------------------------------

def _prune_keep_edges(
    messages: list[dict[str, Any]],
    max_tokens: int,
    keep_first: int,
    keep_last: int,
) -> PruneResult:
    current_first, current_last = keep_first, keep_last
    result_messages, dropped, notes = _keep_edges_single_pass(messages, current_first, current_last)
    estimated = estimate_tokens(result_messages)

    iterations = 0
    while estimated > max_tokens and iterations < _MAX_SHRINK_ITERATIONS:
        if current_last > MIN_EDGE_KEEP:
            current_last -= 1
        elif current_first > MIN_EDGE_KEEP:
            current_first -= 1
        else:
            break  # both edges at floor — nothing left to concede

        result_messages, dropped, notes = _keep_edges_single_pass(messages, current_first, current_last)
        estimated = estimate_tokens(result_messages)
        iterations += 1

    still_over_limit = estimated > max_tokens
    if still_over_limit:
        logger.warning(
            "context_pruner: keep_edges could not fit max_tokens even at the edge floor",
            max_tokens=max_tokens, estimated_tokens=estimated,
            final_keep_first=current_first, final_keep_last=current_last,
        )

    return PruneResult(
        messages=result_messages,
        pruned=True,
        dropped_count=dropped,
        notes_inserted=notes,
        final_keep_first=current_first,
        final_keep_last=current_last,
        still_over_limit=still_over_limit,
        estimated_tokens=estimated,
    )


# ---------------------------------------------------------------------------
# truncate
# ---------------------------------------------------------------------------

def _prune_truncate(messages: list[dict[str, Any]], max_tokens: int) -> PruneResult:
    """Simple oldest-first truncation (plan.md §14): drop from the front
    until the remainder fits, or there's only one message left."""
    n = len(messages)
    cut = 0
    remaining = messages
    estimated = estimate_tokens(remaining)
    while estimated > max_tokens and len(remaining) > 1:
        cut += 1
        remaining = messages[cut:]
        estimated = estimate_tokens(remaining)

    note_messages = remaining
    if cut > 0:
        note_messages = [{"role": "user", "content": _NOTE_TEMPLATE.format(count=cut)}] + remaining
        estimated = estimate_tokens(note_messages)

    return PruneResult(
        messages=note_messages,
        pruned=True,
        dropped_count=cut,
        notes_inserted=1 if cut > 0 else 0,
        final_keep_first=0,
        final_keep_last=len(remaining),
        still_over_limit=estimated > max_tokens,
        estimated_tokens=estimated,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def prune(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    strategy: str = "keep_edges",
    keep_first: int = 3,
    keep_last: int = 10,
) -> PruneResult:
    """
    Prune *messages* to fit *max_tokens*, using *strategy*.

    Parameters
    ----------
    messages:
        Anthropic-format message list (the conversation history only —
        not the system prompt or tool definitions; budget for those
        separately and pass the *remaining* budget as `max_tokens`).
    max_tokens:
        Token budget available for the message list itself.
    strategy:
        ``"keep_edges"`` (default, fully implemented), ``"truncate"``
        (fully implemented), or ``"summarize"`` (raises `NotImplementedError`
        — see module docstring).
    keep_first / keep_last:
        Message counts for the `keep_edges` strategy's protected edges.
        Ignored by other strategies.

    Returns
    -------
    PruneResult
        `pruned=False` and `messages` returned as-is when already under budget.
    """
    if not isinstance(messages, list):
        logger.warning("context_pruner: messages is not a list, returning as-is",
                       got_type=type(messages).__name__)
        return PruneResult(messages=messages, pruned=False, estimated_tokens=0)

    estimated = estimate_tokens(messages)
    if estimated <= max_tokens:
        return PruneResult(messages=messages, pruned=False, estimated_tokens=estimated)

    logger.debug("context_pruner: pruning triggered", strategy=strategy,
                estimated_tokens=estimated, max_tokens=max_tokens, message_count=len(messages))

    if strategy == "keep_edges":
        return _prune_keep_edges(messages, max_tokens, keep_first, keep_last)
    if strategy == "truncate":
        return _prune_truncate(messages, max_tokens)
    if strategy == "summarize":
        raise NotImplementedError(
            "context_pruner: 'summarize' strategy requires an LLM call to "
            "summarize the dropped section (plan.md §14) and isn't implemented "
            "yet — it needs router/selector.py + a provider wired in, which is "
            "out of scope for this module. Use 'keep_edges' or 'truncate' instead."
        )
    raise ValueError(f"context_pruner: unknown strategy {strategy!r}")