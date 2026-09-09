// Historical source adapted for a standalone, synthetic console lab.
// Provenance: docs/research/PROVENANCE.json (entry: match10-readiness-limiter).
// C# 5 compatibility: nameof(intervalMilliseconds) uses the equivalent string literal.
using System;

namespace HistoricalScheduling
{
    internal sealed class ReadinessTraceLimiter
    {
        private readonly long _intervalMilliseconds;
        private bool _emitted;
        private long _nextEmitMilliseconds;

        internal ReadinessTraceLimiter(long intervalMilliseconds)
        {
            if (intervalMilliseconds <= 0)
            {
                throw new ArgumentOutOfRangeException("intervalMilliseconds");
            }
            _intervalMilliseconds = intervalMilliseconds;
        }

        internal long Attempts { get; private set; }
        internal string LastCode { get; private set; }

        internal bool Observe(long elapsedMilliseconds, string code)
        {
            if (elapsedMilliseconds < 0 || string.IsNullOrEmpty(code))
            {
                return false;
            }
            Attempts++;
            LastCode = code;
            if (_emitted && elapsedMilliseconds < _nextEmitMilliseconds)
            {
                return false;
            }
            _emitted = true;
            _nextEmitMilliseconds = elapsedMilliseconds > long.MaxValue - _intervalMilliseconds
                ? long.MaxValue
                : elapsedMilliseconds + _intervalMilliseconds;
            return true;
        }
    }
}
