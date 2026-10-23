"""单页成本策略：真实耗时、邻点确认、有限提示与保守退回。"""
from types import SimpleNamespace

import pytest

from app.routers.stream.preview_adaptive import RecoveryPolicy


DEFAULTS = (10, 30, 60, 120)
TARGETS = tuple(range(260, 320, 10))


def observed(elapsed):
    return SimpleNamespace(elapsed=elapsed)


def recover(policy, *, cost=100, guard_elapsed=12, span=12, target=250, before=10):
    policy.begin_point()
    if cost > guard_elapsed:
        policy.record_attempt(observed(cost - guard_elapsed))
    log = observed(guard_elapsed)
    policy.record_attempt(log)
    policy.recovered(target, before, max(0, target - before), span, log)


def arm(policy, *, elapsed=72):
    policy.observe_segment(TARGETS, 240, elapsed, len(TARGETS))
    policy.direct_result(320, False)


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf"), "10", None, True])
def test_invalid_interval_cannot_create_a_cost_model(interval):
    with pytest.raises(ValueError):
        RecoveryPolicy(interval)


@pytest.mark.parametrize("capacity,base,expected", [(25, 6, 6), (4, 6, 4),
                                                   (25, 25, 25), (17, 25, 17), (50, 25, 25)])
def test_first_short_batch_and_long_recovery_keep_their_existing_limits(capacity, base, expected):
    policy = RecoveryPolicy(10)
    recover(policy, cost=1000)
    assert policy.segment_size(260, 240, capacity, base) == expected


@pytest.mark.parametrize("cost,expected", [(100, 25), (50, 12), (20, 6)])
def test_repeated_difficulty_selects_25_12_or_6_by_measured_cost(cost, expected):
    policy = RecoveryPolicy(10)
    recover(policy, cost=cost)
    arm(policy)
    # With rate=1, C(25)/25=322/25 and C(12)/12=192/12.
    assert policy.segment_size(320, 240, 25, 6) == expected


@pytest.mark.parametrize("cost,expected", [(4 * 322 / 25, 25), (2 * 192 / 12, 12)])
def test_cost_threshold_equality_qualifies(cost, expected):
    policy = RecoveryPolicy(10)
    recover(policy, cost=cost)
    arm(policy)
    assert policy.segment_size(320, 240, 25, 6) == expected


@pytest.mark.parametrize("capacity,expected", [(25, 25), (17, 17), (8, 8), (6, 6), (4, 4)])
def test_expansion_never_exceeds_the_callers_input_time_capacity(capacity, expected):
    policy = RecoveryPolicy(10)
    recover(policy, cost=1000)
    arm(policy)
    assert policy.segment_size(320, 240, capacity, 6) == expected


def test_first_uncovered_direct_success_prevents_expansion_after_expensive_recovery():
    policy = RecoveryPolicy(10)
    recover(policy, cost=1000)
    policy.observe_segment(TARGETS, 240, 72, len(TARGETS))
    policy.direct_result(320, True)
    policy.direct_result(330, False)
    assert policy.segment_size(330, 250, 25, 6) == 6


def test_only_the_first_uncovered_target_can_unlock_expansion():
    policy = RecoveryPolicy(10)
    recover(policy, cost=1000)
    policy.observe_segment(TARGETS, 240, 72, len(TARGETS))
    policy.direct_result(330, False)
    policy.direct_result(320, False)
    assert policy.segment_size(320, 240, 25, 6) == 6


def test_armed_expansion_survives_further_failed_probes_until_direct_success():
    policy = RecoveryPolicy(10)
    recover(policy, cost=1000)
    arm(policy)
    policy.direct_result(330, False)
    assert policy.segment_size(330, 250, 25, 6) == 25
    policy.direct_result(340, True)
    assert policy.segment_size(350, 270, 25, 6) == 6


@pytest.mark.parametrize("guard_elapsed,segment_elapsed", [(120, 72), (12, 720)])
def test_more_expensive_recent_decode_rate_limits_expansion(guard_elapsed, segment_elapsed):
    policy = RecoveryPolicy(10)
    recover(policy, cost=150, guard_elapsed=guard_elapsed)
    arm(policy, elapsed=segment_elapsed)
    assert policy.segment_size(320, 240, 25, 6) == 6


def test_recovery_cost_includes_failed_attempts_instead_of_only_final_guard():
    policy = RecoveryPolicy(10)
    policy.begin_point()
    for elapsed in (20, 25, 30, 12):
        policy.record_attempt(observed(elapsed))
    policy.recovered(250, 10, 240, 12, observed(12))
    arm(policy)
    assert policy.segment_size(320, 240, 25, 6) == 25


@pytest.mark.parametrize("bad", [None, 0, -1, float("nan"), float("inf"), "2", object(), True])
def test_any_unusable_attempt_disables_learning_and_cannot_refresh_a_hint(bad):
    policy = RecoveryPolicy(10)
    recover(policy, cost=100, before=60)
    assert policy.prerolls(260, DEFAULTS) == (10, 60, 30, 120)
    policy.begin_point()
    policy.record_attempt(observed(bad))
    policy.record_attempt(observed(12))
    policy.recovered(270, 120, 150, 122, observed(12))
    assert policy.prerolls(280, DEFAULTS) == (10, 60, 30, 120)
    assert policy.prerolls(290, DEFAULTS) == DEFAULTS
    arm(policy)
    assert policy.segment_size(320, 240, 25, 6) == 6


def test_missing_elapsed_and_empty_attempt_history_cannot_learn():
    policy = RecoveryPolicy(10)
    policy.record_attempt(SimpleNamespace())
    policy.recovered(250, 60, 190, 62, observed(12))
    assert policy.prerolls(260, DEFAULTS) == DEFAULTS
    policy.begin_point()
    policy.recovered(250, 60, 190, 62, observed(12))
    assert policy.prerolls(260, DEFAULTS) == DEFAULTS


def test_invalid_new_recovery_cannot_borrow_old_armed_cost_even_after_another_full_segment():
    policy = RecoveryPolicy(10)
    recover(policy, cost=1000)
    arm(policy)
    assert policy.segment_size(320, 240, 25, 6) == 25
    policy.begin_point()
    policy.record_attempt(observed(0))
    policy.recovered(330, 10, 320, 12, observed(12))
    assert policy.segment_size(340, 320, 25, 6) == 6
    policy.observe_segment(tuple(range(340, 400, 10)), 320, 72, 6)
    policy.direct_result(400, False)
    assert policy.segment_size(400, 320, 25, 6) == 6
    recover(policy, cost=1000, target=400)
    assert policy.segment_size(410, 390, 25, 6) == 25


def test_overflowing_total_or_decode_rate_cannot_enable_a_cost_hint():
    policy = RecoveryPolicy(10)
    policy.begin_point()
    for elapsed in (1e308, 1e308):
        policy.record_attempt(observed(elapsed))
    policy.recovered(250, 60, 190, 62, observed(12))
    assert policy.prerolls(260, DEFAULTS) == DEFAULTS
    policy.begin_point()
    policy.record_attempt(observed(1e308))
    policy.recovered(250, 60, 190, 1e-300, observed(1e308))
    assert policy.prerolls(260, DEFAULTS) == DEFAULTS


def test_hint_is_consumed_twice_and_uses_the_recovery_target_as_its_distance_origin():
    policy = RecoveryPolicy(10)
    recover(policy, before=60)
    assert policy.prerolls(250, DEFAULTS) == DEFAULTS
    assert policy.prerolls(240, DEFAULTS) == DEFAULTS
    assert policy.prerolls(371, DEFAULTS) == DEFAULTS
    assert policy.prerolls(260, DEFAULTS) == (10, 60, 30, 120)
    assert policy.prerolls(370, DEFAULTS) == (10, 60, 30, 120)
    assert policy.prerolls(280, DEFAULTS) == DEFAULTS


def test_preferred_failure_discards_hint_without_retrying_a_different_before():
    policy = RecoveryPolicy(10)
    recover(policy, before=60)
    assert policy.prerolls(260, DEFAULTS) == (10, 60, 30, 120)
    policy.failed_preroll(270, 60)
    policy.failed_preroll(260, 60)
    assert policy.prerolls(270, DEFAULTS) == DEFAULTS
    recover(policy, target=270, before=120)
    assert policy.prerolls(280, DEFAULTS) == (10, 120, 30, 60)


@pytest.mark.parametrize("before,expected", [(60, (10, 60, 30, 120)), (120, (10, 120, 30, 60))])
def test_shortest_probe_failure_preserves_a_long_hint_without_direct_success(before, expected):
    policy = RecoveryPolicy(10)
    recover(policy, before=before)
    policy.direct_result(260, False)
    assert policy.prerolls(260, DEFAULTS) == expected
    policy.failed_preroll(260, 10)
    assert policy.prerolls(270, DEFAULTS) == expected


@pytest.mark.parametrize("before", [60, 120])
def test_any_direct_success_clears_hint_even_when_the_next_direct_probe_fails(before):
    policy = RecoveryPolicy(10)
    recover(policy, before=before)
    policy.direct_result(260, True)
    assert policy.prerolls(270, DEFAULTS) == DEFAULTS
    policy.direct_result(270, False)
    assert policy.prerolls(270, DEFAULTS) == DEFAULTS


@pytest.mark.parametrize("before,defaults,expected", [
    (10, DEFAULTS, DEFAULTS),
    (30, DEFAULTS, DEFAULTS),
    (60, (10, 30, 30, 60, 10, 120), (10, 60, 30, 120)),
    (120, DEFAULTS, (10, 120, 30, 60)),
    (60, (5, 10, 30, 60, 120), (5, 60, 10, 30, 120)),
])
def test_preroll_order_preserves_default_first_probe_and_deduplicates_every_before(before, defaults, expected):
    policy = RecoveryPolicy(10)
    recover(policy, before=before)
    assert policy.prerolls(260, defaults) == expected


@pytest.mark.parametrize("accepted,elapsed", [(0, 72), (2, 1), (5, 1), (7, 72),
                                            (6, 0), (6, -1), (6, float("nan")),
                                            (6, float("inf")), (6, "72")])
def test_incomplete_or_unmeasured_segments_clear_qualification(accepted, elapsed):
    policy = RecoveryPolicy(10)
    recover(policy, cost=1000)
    arm(policy)
    policy.observe_segment(tuple(range(320, 380, 10)), 300, elapsed, accepted)
    policy.direct_result(380, False)
    assert policy.segment_size(380, 300, 25, 6) == 6


@pytest.mark.parametrize("accepted,elapsed", [(0, 72), (2, 72), (6, None),
                                            (6, 0), (6, float("nan")), (6, "72")])
def test_failed_segment_prevents_learning_incomplete_following_point_recovery(accepted, elapsed):
    policy = RecoveryPolicy(10)
    policy.begin_point()
    policy.record_attempt(observed(10))  # Failed direct seek before the segment.
    policy.observe_segment(TARGETS, 240, elapsed, accepted)
    policy.record_attempt(observed(12))  # Recovery after the failed segment.
    policy.recovered(260, 60, 200, 62, observed(12))
    assert policy.prerolls(270, DEFAULTS) == DEFAULTS
    arm(policy)
    assert policy.segment_size(320, 240, 25, 6) == 6

    # The next point has a complete, independent observation and may learn.
    recover(policy, cost=100, target=330, before=60)
    assert policy.prerolls(340, DEFAULTS) == (10, 60, 30, 120)
    policy.observe_segment(tuple(range(340, 400, 10)), 300, 92, 6)
    policy.direct_result(400, False)
    assert policy.segment_size(400, 300, 25, 6) == 25


def test_less_than_six_verified_samples_cannot_establish_expansion_eligibility():
    policy = RecoveryPolicy(10)
    recover(policy, cost=1000)
    policy.observe_segment(tuple(range(260, 310, 10)), 240, 62, 5)
    policy.direct_result(310, False)
    assert policy.segment_size(310, 240, 25, 6) == 6


def test_less_than_six_verified_samples_clear_previous_expansion_eligibility():
    policy = RecoveryPolicy(10)
    recover(policy, cost=1000)
    arm(policy)
    assert policy.segment_size(320, 240, 25, 6) == 25
    policy.observe_segment(tuple(range(320, 370, 10)), 300, 62, 5)
    policy.direct_result(370, False)
    assert policy.segment_size(370, 300, 25, 6) == 6


def test_new_page_policy_has_no_cost_hint_or_expansion_from_previous_page():
    previous = RecoveryPolicy(10)
    recover(previous, cost=1000, before=60)
    arm(previous)
    current = RecoveryPolicy(10)
    current.direct_result(320, False)
    assert current.segment_size(320, 240, 25, 6) == 6
    assert current.prerolls(320, DEFAULTS) == DEFAULTS
