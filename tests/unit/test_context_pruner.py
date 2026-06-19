"""
tests/unit/test_context_pruner.py
=====================================
Tests for `clasp/optimizer/context_pruner.py`'s `prune()`. Covers plan.md's
full `test_context_pruner.py` spec (§ Unit Tests):

- Short history (under limit) → returned unchanged.
- Long history → keeps first 3 + last 10 + all tool messages.
- Truncated section → note message inserted at correct position.
- Tool chain in middle → never dropped.
- Still over limit after pruning → further trims from middle window.

...plus additional edge-case coverage (multiple separate gaps, the
`truncate` strategy, `summarize` raising clearly, malformed input).
"""

import unittest

from clasp.optimizer.context_pruner import prune, MIN_EDGE_KEEP


def _text_msg(role: str, text: str) -> dict:
    return {"role": role, "content": text}


def _filler_msgs(n: int, label: str, *, pad: int = 200) -> list[dict]:
    """n alternating user/assistant messages, padded to consume real tokens."""
    return [
        _text_msg("user" if i % 2 == 0 else "assistant", f"{label} {i} " + ("x" * pad))
        for i in range(n)
    ]


def _tool_use_msg(tool_use_id: str = "tu_1") -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_use_id, "name": "Read", "input": {"path": "f.py"}}],
    }


def _tool_result_msg(tool_use_id: str = "tu_1") -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "file contents here"}],
    }


class TestShortHistoryUnchanged(unittest.TestCase):
    """plan.md: 'Short history (under limit) → returned unchanged.'"""

    def test_short_history_returned_unchanged(self):
        messages = _filler_msgs(5, "msg", pad=5)
        result = prune(messages, max_tokens=100_000)
        self.assertFalse(result.pruned)
        self.assertEqual(result.messages, messages)
        self.assertIs(result.messages, messages)  # not even copied

    def test_short_history_zero_dropped_zero_notes(self):
        messages = _filler_msgs(3, "msg", pad=5)
        result = prune(messages, max_tokens=100_000)
        self.assertEqual(result.dropped_count, 0)
        self.assertEqual(result.notes_inserted, 0)
        self.assertFalse(result.still_over_limit)

    def test_empty_message_list_is_unchanged(self):
        result = prune([], max_tokens=1000)
        self.assertFalse(result.pruned)
        self.assertEqual(result.messages, [])


class TestKeepEdgesBasic(unittest.TestCase):
    """plan.md: 'Long history → keeps first 3 + last 10 + all tool messages.'"""

    def setUp(self):
        self.head = _filler_msgs(3, "head", pad=20)
        self.filler = _filler_msgs(30, "filler", pad=20)
        self.tail = _filler_msgs(10, "tail", pad=20)
        self.messages = self.head + self.filler + self.tail
        # head(3)+note+tail(10) ≈ 103 tokens; full unpruned ≈ 320 tokens.
        # 150 sits comfortably between them: pruning triggers, but the
        # edges fit without needing to shrink — isolating "basic keep_edges"
        # from the separate "edges shrink under pressure" behavior tested
        # in TestStillOverLimitShrinksEdges.
        self.max_tokens = 150

    def test_pruning_is_triggered(self):
        result = prune(self.messages, max_tokens=self.max_tokens, keep_first=3, keep_last=10)
        self.assertTrue(result.pruned)
        self.assertGreater(result.dropped_count, 0)

    def test_head_messages_all_present(self):
        result = prune(self.messages, max_tokens=self.max_tokens, keep_first=3, keep_last=10)
        for h in self.head:
            self.assertIn(h, result.messages)

    def test_tail_messages_all_present(self):
        result = prune(self.messages, max_tokens=self.max_tokens, keep_first=3, keep_last=10)
        for t in self.tail:
            self.assertIn(t, result.messages)
        self.assertEqual(result.final_keep_last, 10)  # confirms no shrinking occurred

    def test_filler_messages_all_absent(self):
        result = prune(self.messages, max_tokens=self.max_tokens, keep_first=3, keep_last=10)
        for f in self.filler:
            self.assertNotIn(f, result.messages)

    def test_exactly_one_note_for_one_contiguous_gap(self):
        result = prune(self.messages, max_tokens=self.max_tokens, keep_first=3, keep_last=10)
        self.assertEqual(result.notes_inserted, 1)

    def test_note_count_matches_dropped_count(self):
        result = prune(self.messages, max_tokens=self.max_tokens, keep_first=3, keep_last=10)
        note_texts = [m["content"] for m in result.messages if isinstance(m["content"], str)
                      and m["content"].startswith("[CLASP:")]
        self.assertEqual(len(note_texts), 1)
        self.assertIn(f"{result.dropped_count} messages omitted", note_texts[0])

    def test_order_is_head_then_note_then_tail(self):
        result = prune(self.messages, max_tokens=self.max_tokens, keep_first=3, keep_last=10)
        msgs = result.messages
        self.assertEqual(msgs[:3], self.head)
        self.assertTrue(msgs[3]["content"].startswith("[CLASP:"))
        self.assertEqual(msgs[4:], self.tail)


class TestNotePositionAndContent(unittest.TestCase):
    """plan.md: 'Truncated section → note message inserted at correct position.'"""

    def test_note_inserted_exactly_at_drop_boundary(self):
        head = _filler_msgs(2, "head", pad=20)
        filler = _filler_msgs(10, "filler", pad=20)
        tail = _filler_msgs(2, "tail", pad=20)
        messages = head + filler + tail

        # head+note+tail ≈ 40 tokens; full unpruned ≈ 102 tokens. 70 triggers
        # pruning without requiring any edge-shrinking.
        result = prune(messages, max_tokens=70, keep_first=2, keep_last=2)
        msgs = result.messages

        note_index = next(i for i, m in enumerate(msgs)
                          if isinstance(m["content"], str) and m["content"].startswith("[CLASP:"))
        # Note must sit immediately after the head and immediately before the tail.
        self.assertEqual(msgs[:note_index], head)
        self.assertEqual(msgs[note_index + 1:], tail)

    def test_note_count_is_exact_not_approximate(self):
        head = _filler_msgs(1, "head", pad=20)
        filler = _filler_msgs(17, "filler", pad=20)
        tail = _filler_msgs(1, "tail", pad=20)
        messages = head + filler + tail

        # head+note+tail ≈ 26 tokens; full unpruned ≈ 143 tokens.
        result = prune(messages, max_tokens=60, keep_first=1, keep_last=1)
        note = next(m["content"] for m in result.messages
                   if isinstance(m["content"], str) and m["content"].startswith("[CLASP:"))
        self.assertEqual(note, f"[CLASP: {result.dropped_count} messages omitted to fit context limit]")

    def test_multiple_separate_gaps_each_get_their_own_correctly_counted_note(self):
        head = _filler_msgs(2, "head", pad=20)
        gap1 = _filler_msgs(5, "gap1", pad=20)
        tool_pair = [_tool_use_msg(), _tool_result_msg()]
        gap2 = _filler_msgs(7, "gap2", pad=20)
        tail = _filler_msgs(2, "tail", pad=20)
        messages = head + gap1 + tool_pair + gap2 + tail

        # kept-without-shrinking ≈ 62 tokens; full unpruned ≈ 122 tokens.
        result = prune(messages, max_tokens=90, keep_first=2, keep_last=2)
        notes = [m["content"] for m in result.messages
                if isinstance(m["content"], str) and m["content"].startswith("[CLASP:")]
        self.assertEqual(len(notes), 2)
        self.assertIn("[CLASP: 5 messages omitted to fit context limit]", notes)
        self.assertIn("[CLASP: 7 messages omitted to fit context limit]", notes)

    def test_gaps_are_positioned_around_the_preserved_tool_pair(self):
        head = _filler_msgs(2, "head", pad=20)
        gap1 = _filler_msgs(5, "gap1", pad=20)
        tool_pair = [_tool_use_msg(), _tool_result_msg()]
        gap2 = _filler_msgs(7, "gap2", pad=20)
        tail = _filler_msgs(2, "tail", pad=20)
        messages = head + gap1 + tool_pair + gap2 + tail

        result = prune(messages, max_tokens=90, keep_first=2, keep_last=2)
        msgs = result.messages
        tool_use_idx = next(i for i, m in enumerate(msgs)
                            if isinstance(m["content"], list) and m["content"][0]["type"] == "tool_use")
        self.assertTrue(msgs[tool_use_idx - 1]["content"].startswith("[CLASP: 5"))
        self.assertTrue(msgs[tool_use_idx + 2]["content"].startswith("[CLASP: 7"))


class TestToolMessagesNeverDropped(unittest.TestCase):
    """plan.md: 'Tool chain in middle → never dropped.'"""

    def test_tool_use_and_tool_result_both_survive(self):
        head = _filler_msgs(3, "head")
        filler1 = _filler_msgs(15, "filler1")
        tool_pair = [_tool_use_msg(), _tool_result_msg()]
        filler2 = _filler_msgs(15, "filler2")
        tail = _filler_msgs(10, "tail")
        messages = head + filler1 + tool_pair + filler2 + tail

        result = prune(messages, max_tokens=300, keep_first=3, keep_last=10)
        for tm in tool_pair:
            self.assertIn(tm, result.messages)

    def test_tool_pair_never_split_apart(self):
        head = _filler_msgs(3, "head")
        filler1 = _filler_msgs(15, "filler1")
        tool_pair = [_tool_use_msg(), _tool_result_msg()]
        filler2 = _filler_msgs(15, "filler2")
        tail = _filler_msgs(10, "tail")
        messages = head + filler1 + tool_pair + filler2 + tail

        result = prune(messages, max_tokens=300, keep_first=3, keep_last=10)
        msgs = result.messages
        tool_use_idx = next(i for i, m in enumerate(msgs)
                            if isinstance(m["content"], list) and m["content"][0]["type"] == "tool_use")
        # The very next kept message must be the matching tool_result —
        # i.e. nothing was inserted between them, the pair is adjacent.
        self.assertEqual(msgs[tool_use_idx + 1], tool_result_msg_for_test := tool_pair[1])

    def test_multiple_tool_pairs_in_middle_all_survive(self):
        head = _filler_msgs(2, "head")
        filler = _filler_msgs(20, "filler")
        pair_a = [_tool_use_msg("tu_a"), _tool_result_msg("tu_a")]
        more_filler = _filler_msgs(20, "filler2")
        pair_b = [_tool_use_msg("tu_b"), _tool_result_msg("tu_b")]
        tail = _filler_msgs(2, "tail")
        messages = head + filler + pair_a + more_filler + pair_b + tail

        result = prune(messages, max_tokens=200, keep_first=2, keep_last=2)
        for tm in pair_a + pair_b:
            self.assertIn(tm, result.messages)

    def test_tool_message_at_very_edge_of_middle_boundary_still_protected(self):
        # Tool pair positioned immediately adjacent to the head/tail boundary,
        # not buffered by filler on either side.
        head = _filler_msgs(3, "head")
        tool_pair = [_tool_use_msg(), _tool_result_msg()]
        filler = _filler_msgs(20, "filler")
        tail = _filler_msgs(10, "tail")
        messages = head + tool_pair + filler + tail

        result = prune(messages, max_tokens=300, keep_first=3, keep_last=10)
        for tm in tool_pair:
            self.assertIn(tm, result.messages)


class TestStillOverLimitShrinksEdges(unittest.TestCase):
    """plan.md: 'Still over limit after pruning → further trims from middle window.'"""

    def test_edges_shrink_when_keep_edges_result_still_exceeds_budget(self):
        # Edges alone (3 + 10 = 13 padded messages) already exceed a very tight budget.
        head = _filler_msgs(3, "head")
        filler = _filler_msgs(30, "filler")
        tail = _filler_msgs(10, "tail")
        messages = head + filler + tail

        result = prune(messages, max_tokens=50, keep_first=3, keep_last=10)
        self.assertTrue(result.final_keep_first < 3 or result.final_keep_last < 10)

    def test_tool_messages_still_protected_even_while_edges_shrink(self):
        head = _filler_msgs(3, "head")
        filler1 = _filler_msgs(15, "filler1")
        tool_pair = [_tool_use_msg(), _tool_result_msg()]
        filler2 = _filler_msgs(15, "filler2")
        tail = _filler_msgs(10, "tail")
        messages = head + filler1 + tool_pair + filler2 + tail

        result = prune(messages, max_tokens=50, keep_first=3, keep_last=10)
        for tm in tool_pair:
            self.assertIn(tm, result.messages)

    def test_edges_never_shrink_below_min_edge_keep(self):
        head = _filler_msgs(3, "head")
        filler = _filler_msgs(50, "filler")
        tail = _filler_msgs(10, "tail")
        messages = head + filler + tail

        # Absurdly tiny budget — forces maximum shrinkage.
        result = prune(messages, max_tokens=5, keep_first=3, keep_last=10)
        self.assertGreaterEqual(result.final_keep_first, MIN_EDGE_KEEP)
        self.assertGreaterEqual(result.final_keep_last, MIN_EDGE_KEEP)

    def test_still_over_limit_flag_set_when_floor_reached_and_still_too_big(self):
        # Tool messages alone are large enough to blow the budget even at
        # the MIN_EDGE_KEEP floor — keep_edges has no more ground to give.
        head = _filler_msgs(3, "head")
        huge_tool_pair = [_tool_use_msg(), _tool_result_msg()]
        huge_tool_pair[1]["content"][0]["content"] = "x" * 5000
        tail = _filler_msgs(10, "tail")
        messages = head + huge_tool_pair + tail

        result = prune(messages, max_tokens=10, keep_first=3, keep_last=10)
        self.assertTrue(result.still_over_limit)
        # The protected tool pair survived anyway — keep_edges' absolute
        # guarantee holds even when it can't fully satisfy the budget.
        for tm in huge_tool_pair:
            self.assertIn(tm, result.messages)

    def test_shrinking_converges_to_a_smaller_result_than_no_shrinking_would(self):
        head = _filler_msgs(3, "head")
        filler = _filler_msgs(30, "filler")
        tail = _filler_msgs(10, "tail")
        messages = head + filler + tail

        loose = prune(messages, max_tokens=100_000, keep_first=3, keep_last=10)
        tight = prune(messages, max_tokens=50, keep_first=3, keep_last=10)
        self.assertLess(tight.estimated_tokens, loose.estimated_tokens)


class TestTruncateStrategy(unittest.TestCase):
    """plan.md: 'Strategy truncate: simple oldest-first truncation.'"""

    def test_truncate_drops_from_the_front(self):
        messages = _filler_msgs(20, "msg")
        result = prune(messages, max_tokens=300, strategy="truncate")
        self.assertTrue(result.pruned)
        # The most recent message must always survive truncation.
        self.assertEqual(result.messages[-1], messages[-1])

    def test_truncate_inserts_a_single_note(self):
        messages = _filler_msgs(20, "msg")
        result = prune(messages, max_tokens=300, strategy="truncate")
        self.assertEqual(result.notes_inserted, 1)
        self.assertTrue(result.messages[0]["content"].startswith("[CLASP:"))

    def test_truncate_short_history_unchanged(self):
        messages = _filler_msgs(3, "msg", pad=5)
        result = prune(messages, max_tokens=100_000, strategy="truncate")
        self.assertFalse(result.pruned)


class TestSummarizeNotImplemented(unittest.TestCase):
    def test_summarize_raises_not_implemented(self):
        messages = _filler_msgs(20, "msg")
        with self.assertRaises(NotImplementedError):
            prune(messages, max_tokens=300, strategy="summarize")

    def test_unknown_strategy_raises_value_error(self):
        messages = _filler_msgs(20, "msg")
        with self.assertRaises(ValueError):
            prune(messages, max_tokens=300, strategy="not_a_real_strategy")


class TestMalformedInput(unittest.TestCase):
    def test_non_list_messages_returned_as_is(self):
        result = prune("not a list", max_tokens=100)  # type: ignore[arg-type]
        self.assertFalse(result.pruned)
        self.assertEqual(result.messages, "not a list")


if __name__ == "__main__":
    unittest.main()