"""
Tests for TokenBucketRateLimiter — concurrency primitive for API rate limiting.

Covers: token acquisition, refill mechanics, timeout behavior, burst capacity,
edge cases (zero rate, zero burst), and thread safety.
"""

import time
import threading
from sage.tools.model_caller import TokenBucketRateLimiter


class TestTokenBucketAcquire:
    def test_acquire_with_available_tokens(self):
        """Immediate acquisition when tokens are available."""
        limiter = TokenBucketRateLimiter(rate=10.0, burst=5)
        assert limiter.acquire(timeout=0.0) is True
        assert limiter.tokens == 4.0

    def test_acquire_all_burst_tokens(self):
        """Can acquire up to burst capacity."""
        limiter = TokenBucketRateLimiter(rate=1.0, burst=3)
        assert limiter.acquire(timeout=0.0) is True
        assert limiter.acquire(timeout=0.0) is True
        assert limiter.acquire(timeout=0.0) is True
        assert limiter.tokens < 1.0  # No tokens left

    def test_acquire_returns_false_on_timeout(self):
        """Returns False when no tokens available and timeout expires."""
        limiter = TokenBucketRateLimiter(rate=0.0, burst=1)
        limiter.acquire(timeout=0.0)  # Drain the single token
        result = limiter.acquire(timeout=0.01)
        assert result is False

    def test_acquire_waits_for_refill(self):
        """Blocks and succeeds once a token refills."""
        limiter = TokenBucketRateLimiter(rate=100.0, burst=1)
        limiter.acquire(timeout=0.0)  # Drain
        # With rate=100/s, one token refills in 10ms
        start = time.monotonic()
        result = limiter.acquire(timeout=1.0)
        elapsed = time.monotonic() - start
        assert result is True
        assert elapsed < 0.5  # Should be fast

    def test_acquire_zero_timeout_fails_immediately(self):
        """Zero timeout returns False immediately if no tokens."""
        limiter = TokenBucketRateLimiter(rate=0.0, burst=1)
        limiter.acquire(timeout=0.0)  # Drain
        assert limiter.acquire(timeout=0.0) is False


class TestTokenBucketRefill:
    def test_refill_does_not_exceed_burst(self):
        """Tokens never exceed burst capacity."""
        limiter = TokenBucketRateLimiter(rate=100.0, burst=5)
        # Simulate time passing (via manual last_refill manipulation)
        limiter.last_refill = time.monotonic() - 10.0  # 10 seconds ago
        limiter._refill()
        assert limiter.tokens <= 5.0

    def test_refill_adds_proportional_tokens(self):
        """Tokens increase proportionally to elapsed time."""
        limiter = TokenBucketRateLimiter(rate=10.0, burst=20)
        limiter.tokens = 5.0
        # Manually advance time by 1 second
        limiter.last_refill = time.monotonic() - 1.0
        limiter._refill()
        # Should have ~15 tokens (5 + 10*1), clamped at burst=20
        assert 14.0 <= limiter.tokens <= 16.0

    def test_initial_tokens_equal_burst(self):
        """Limiter starts with full burst capacity."""
        limiter = TokenBucketRateLimiter(rate=5.0, burst=8)
        assert limiter.tokens == 8.0


class TestTokenBucketEdgeCases:
    def test_zero_rate_no_refill(self):
        """Rate=0 means tokens never refill."""
        limiter = TokenBucketRateLimiter(rate=0.0, burst=5)
        limiter.acquire(timeout=0.0)  # 4 left
        limiter.last_refill = time.monotonic() - 100.0  # Fake 100s elapsed
        limiter._refill()
        # No refill possible with rate=0
        assert limiter.tokens < 5.0

    def test_zero_burst_allows_no_acquisition(self):
        """Burst=0 means no tokens ever available."""
        limiter = TokenBucketRateLimiter(rate=10.0, burst=0)
        assert limiter.acquire(timeout=0.0) is False

    def test_fractional_burst(self):
        """Burst of 0.5 means no acquisition (need >=1.0 token)."""
        limiter = TokenBucketRateLimiter(rate=1.0, burst=0.5)
        # 0.5 tokens < 1.0 required → cannot acquire
        assert limiter.acquire(timeout=0.0) is False


class TestTokenBucketThreadSafety:
    def test_concurrent_acquire_does_not_overdraw(self):
        """Multiple threads acquiring won't produce more tokens than burst."""
        limiter = TokenBucketRateLimiter(rate=0.0, burst=10)
        results = []

        def worker():
            got = limiter.acquire(timeout=0.0)
            results.append(got)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        acquired = sum(1 for r in results if r is True)
        assert acquired == 10  # Exactly burst, no more

    def test_serial_acquire_respects_burst(self):
        """Serial calls don't exceed burst capacity."""
        limiter = TokenBucketRateLimiter(rate=0.0, burst=3)
        acquired = 0
        for _ in range(10):
            if limiter.acquire(timeout=0.0):
                acquired += 1
        assert acquired == 3
