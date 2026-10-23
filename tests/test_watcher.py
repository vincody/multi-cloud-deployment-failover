from scripts import watcher_core as watcher


def test_evaluate_needs_both_checks_to_fail():
    assert watcher.evaluate(0, True, True, 3) == (0, "healthy")
    assert watcher.evaluate(2, True, False, 3) == (2, "suspect")
    assert watcher.evaluate(2, False, True, 3) == (2, "suspect")
    assert watcher.evaluate(0, False, False, 3) == (1, "failing")
    assert watcher.evaluate(2, False, False, 3) == (3, "failover")


def drive(webs, dbs, threshold=3, blocks=None, failover=None):
    calls, log = [], []
    web_iter, db_iter = iter(webs), iter(dbs)
    block_iter = iter(blocks or [None] * len(webs))
    outcome = watcher.run(lambda: next(web_iter), lambda: next(db_iter), failover or (lambda: calls.append("failover")),
                          0, threshold, blocked=lambda: next(block_iter), log=log.append, sleep=lambda _: None)
    return outcome, calls


def test_fails_over_after_threshold_of_agreeing_failures():
    outcome, calls = drive([True, False, False, False], [True, False, False, False])
    assert (outcome, calls) == ("failed-over", ["failover"])


def test_one_sided_failures_never_fail_over():
    outcome, calls = drive([True, False, False, False, True], [True, True, True, True, True], blocks=[None] * 5 + ["done"])
    assert (outcome, calls) == ("already-failed-over", [])


def test_a_recovery_resets_the_count():
    outcome, calls = drive([False, False, True, False, False], [False, False, True, False, False], blocks=[None] * 5 + ["done"])
    assert (outcome, calls) == ("already-failed-over", [])


def test_planned_operation_resets_the_count():
    # Two failures, then a switchover in progress (busy), then one more failure: the run is broken.
    outcome, calls = drive([False, False, False], [False, False, False], blocks=[None, None, "busy", None, "done"])
    assert (outcome, calls) == ("already-failed-over", [])


def test_fence_failure_stops_and_asks_for_operator():
    def refuse():
        raise RuntimeError("EC2 did not stop")

    outcome, calls = drive([False] * 3, [False] * 3, failover=refuse)
    assert outcome == "operator-needed"
