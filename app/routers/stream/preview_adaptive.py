"""单页内的恢复成本与尝试顺序；提示不替代实际图片和时间戳验证。"""
import math
from numbers import Real


def _number(value):
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _positive(value):
    result = _number(value)
    return result if result is not None and result > 0 else None


class RecoveryPolicy:
    """Keep bounded hints and valid cost observations within one page attempt."""

    def __init__(self, interval):
        self.interval = _positive(interval)
        if self.interval is None:
            raise ValueError("preview interval must be finite and positive")
        self._recovery_cost = None
        self._guard_rate = None
        self._segment_rate = None
        self._measured_recovery = False
        self._expected_next = None
        self._armed = False
        self._preferred = None
        self._preferred_target = None
        self._preferred_uses = 0
        self._preferred_attempt = None
        self.begin_point()

    def begin_point(self):
        self._point_cost = 0.0
        self._point_attempts = 0
        self._point_cost_valid = True

    def record_attempt(self, log):
        elapsed = _positive(getattr(log, "elapsed", None))
        self._point_attempts += 1
        if elapsed is None:
            self._point_cost_valid = False
        elif self._point_cost_valid:
            total = self._point_cost + elapsed
            if math.isfinite(total):
                self._point_cost = total
            else:
                self._point_cost_valid = False

    def direct_result(self, target, success):
        if success:
            self._armed = False
            self._expected_next = None
            # A directly decodable neighbour ends the recent difficult run.
            self._clear_hint()
            return
        target = _number(target)
        if (target is not None and self._expected_next is not None
                and abs(target - self._expected_next) <= 0.001):
            self._armed = True
        # Only the first uncovered direct probe may qualify this segment.
        self._expected_next = None

    def recovered(self, target, before, start, span, log):
        target, before, start, span = (_number(target), _positive(before),
                                       _number(start), _positive(span))
        elapsed = _positive(getattr(log, "elapsed", None))
        rate = _positive(elapsed / span) if elapsed is not None and span is not None else None
        if (not self._point_cost_valid or not self._point_attempts or rate is None
                or target is None or start is None or not 0 <= start < target or before is None):
            # An old expensive observation cannot expand a newly unmeasured
            # recovery. Existing ordering hints retain only their old budget.
            self._measured_recovery = False
            self._armed = False
            self._expected_next = None
            return
        self._recovery_cost = self._point_cost
        self._guard_rate = rate
        self._measured_recovery = True
        self._preferred = before
        self._preferred_target = target
        self._preferred_uses = 2
        self._preferred_attempt = None

    def prerolls(self, target, defaults):
        defaults = tuple(dict.fromkeys(defaults))
        self._preferred_attempt = None
        target = _number(target)
        if (target is not None and self._preferred_uses > 0 and self._preferred_target is not None
                and 0 < target - self._preferred_target <= 120):
            self._preferred_uses -= 1
            self._preferred_attempt = (target, self._preferred)
            # Keep the cheapest default probe ahead of a longer preference;
            # a successful long preroll does not prove shorter ones will fail.
            return tuple(dict.fromkeys((*defaults[:1], self._preferred, *defaults)))
        return defaults

    def failed_preroll(self, target, before):
        target, before = _number(target), _number(before)
        if (target is not None and before is not None and self._preferred_attempt is not None
                and abs(target - self._preferred_attempt[0]) <= 0.001
                and before == self._preferred_attempt[1]):
            self._clear_hint()

    def _clear_hint(self):
        self._preferred = None
        self._preferred_target = None
        self._preferred_uses = 0
        self._preferred_attempt = None

    def segment_size(self, target, start, capacity, base_limit):
        if (isinstance(capacity, bool) or not isinstance(capacity, int)
                or isinstance(base_limit, bool) or not isinstance(base_limit, int)
                or capacity <= 0 or base_limit <= 0):
            return 0
        base = min(base_limit, capacity, 25)
        if (base_limit != 6 or not self._armed or not self._measured_recovery
                or self._recovery_cost is None or self._guard_rate is None):
            return base
        target, start = _number(target), _number(start)
        if target is None or start is None or not 0 <= start <= target:
            return base
        rate = max(self._guard_rate, self._segment_rate or self._guard_rate)
        for limit, multiplier in ((25, 4), (12, 2)):
            count = min(limit, capacity)
            if count <= 6:
                continue
            span = target + (count - 1) * self.interval + min(self.interval, 2) - start
            cost = rate * span
            if math.isfinite(cost) and self._recovery_cost >= multiplier * cost / count:
                return count
        return base

    def observe_segment(self, targets, start, elapsed, accepted_count):
        targets = tuple(_number(target) for target in targets)
        start, elapsed = _number(start), _positive(elapsed)
        valid = (
            targets and start is not None and elapsed is not None
            and all(target is not None and target >= 0 for target in targets)
            and 0 <= start <= targets[0]
            and all(right > left for left, right in zip(targets, targets[1:]))
            and not isinstance(accepted_count, bool) and isinstance(accepted_count, int)
            and accepted_count == len(targets))
        if not valid:
            self._armed = False
            self._expected_next = None
            # A failed segment may sit between the failed direct probe and
            # point recovery. Its wall time is not in record_attempt's total;
            # do not learn that incomplete recovery chain as a measured point.
            self._point_cost_valid = False
            return
        self._expected_next = None
        if len(targets) < 6:
            self._armed = False
            return
        span = targets[-1] + min(self.interval, 2) - start
        rate = _positive(elapsed / span) if span > 0 else None
        expected = _number(targets[-1] + self.interval)
        if rate is None or expected is None:
            self._armed = False
            self._point_cost_valid = False
            return
        self._segment_rate = rate
        self._expected_next = expected
