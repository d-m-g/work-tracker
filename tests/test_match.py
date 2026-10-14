"""The session -> task matcher: each step of the cascade, and its thresholds."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from support import TZ

from planner.match import AUTO, SUGGEST, History, Session, Task, candidates, normalise, similarity

NINE = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)


def session(label, start=NINE, minutes=60, sid="s"):
    return Session(sid, label, start, start + timedelta(minutes=minutes))


class TestText(unittest.TestCase):
    def test_normalise_keeps_words_only(self) -> None:
        self.assertEqual("req billing fix", normalise("ReQ: billing-fix!"))

    def test_typos_and_order_still_match(self) -> None:
        self.assertGreater(similarity("billing fix req", "ReQ billing fixes"), 0.5)
        self.assertGreater(similarity("Sweedish homework", "Swedish homework"), 0.5)

    def test_unrelated_names_do_not(self) -> None:
        self.assertLess(similarity("lunch", "Review ReQ billing PR"), 0.2)


class TestCascade(unittest.TestCase):
    def test_a_learned_alias_links_on_its_own(self) -> None:
        best = candidates(session("pr thing"), [Task(1, "Review ReQ billing PR")],
                          History(aliases={"pr thing": 1}))[0]
        self.assertGreaterEqual(best.score, AUTO)

    def test_a_session_inside_a_planned_block_links_even_unnamed(self) -> None:
        task = Task(1, "Review ReQ billing PR", blocks=((NINE, NINE + timedelta(minutes=90)),))
        best = candidates(session(None), [task], History())[0]
        self.assertEqual(1, best.task_id)
        self.assertGreaterEqual(best.score, AUTO)

    def test_carrying_on_after_a_short_break_links(self) -> None:
        before = session("copy", start=NINE - timedelta(hours=2), sid="a")
        best = candidates(session("copy"), [Task(7, "Pecto onboarding")],
                          History(linked=[(before, 7)]))[0]
        self.assertEqual(7, best.task_id)
        self.assertGreaterEqual(best.score, AUTO)

    def test_a_similar_name_alone_is_offered_not_linked(self) -> None:
        best = candidates(session("billing pr"), [Task(1, "Review ReQ billing PR")], History())[0]
        self.assertTrue(SUGGEST <= best.score < AUTO, best)

    def test_nothing_in_common_is_not_a_candidate(self) -> None:
        self.assertEqual([], candidates(session("lunch"), [Task(1, "Swedish homework")], History()))

    def test_the_best_candidate_comes_first(self) -> None:
        tasks = [Task(1, "Swedish homework"), Task(2, "Review ReQ billing PR")]
        found = candidates(session("ReQ billing"), tasks, History())
        self.assertEqual(2, found[0].task_id)


if __name__ == "__main__":
    unittest.main()
