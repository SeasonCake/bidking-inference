// Adapted historical harnesses plus synthetic boundary/runner controls.
// Provenance: docs/research/PROVENANCE.json (entry: match10-harnesses).
using System;
using System.Collections.Generic;
using System.Text;
using System.Threading;
using HistoricalScheduling;

internal sealed class CheckFailure : Exception
{
    internal CheckFailure(string invariant) : base(invariant) { }
}

internal static class Checks
{
    internal static int Passed;
    internal static int Failed;
    internal static int Assertions;
    internal static int KnownLimits;

    internal static void That(bool value, string invariant)
    {
        Assertions++;
        if (!value) throw new CheckFailure(invariant);
    }

    internal static T Throws<T>(Action action) where T : Exception
    {
        try { action(); }
        catch (T expected) { Assertions++; return expected; }
        catch { throw new CheckFailure("unexpected_exception_type"); }
        throw new CheckFailure("expected_exception_missing");
    }

    internal static void Case(string name, Action action)
    {
        try
        {
            action();
            Passed++;
            Console.WriteLine("CASE " + name + " PASS");
        }
        catch (Exception error)
        {
            Failed++;
            string detail = error is CheckFailure ? error.Message : error.GetType().Name;
            Console.WriteLine("CASE " + name + " FAIL invariant=" + detail);
        }
    }
}

internal static class LimiterHarness
{
    private static void AssertGaps(List<long> emitted, long interval)
    {
        for (int index = 1; index < emitted.Count; index++)
            Checks.That(emitted[index] - emitted[index - 1] >= interval, "minimum_gap");
    }

    // Same virtual-clock loops and assertions as the two historical scales.
    // Labels are synthetic, not product readiness or authorization messages.
    private static List<long> Clock(long interval, long end, long step)
    {
        var limiter = new ReadinessTraceLimiter(interval);
        var emitted = new List<long>();
        long attempt = 0;
        for (long elapsed = 0; elapsed <= end; elapsed += step)
        {
            string code = attempt % 2 == 0 ? "pending" : "retry";
            if (limiter.Observe(elapsed, code))
            {
                emitted.Add(elapsed);
                Checks.That(limiter.LastCode == code, "last_code_is_current");
            }
            attempt++;
        }
        Checks.That(limiter.Attempts == attempt, "every_valid_attempt_counted");
        AssertGaps(emitted, interval);
        return emitted;
    }

    internal static void Run()
    {
        Checks.Case("limiter_historical_virtual_clock", delegate
        {
            List<long> emitted = Clock(10000, 120000, 500);
            Checks.That(emitted.Count == 13, "historical_boundary_count");
            Checks.That(emitted[0] == 0, "first_attempt_emits");
            Checks.That(emitted[12] == 120000, "deadline_bucket_emits");
        });
        Checks.Case("limiter_short_virtual_clock", delegate
        {
            Checks.That(Clock(10, 120, 1).Count == 13, "scaled_boundary_count");
        });
        Checks.Case("limiter_invalid_interval", delegate
        {
            var error = Checks.Throws<ArgumentOutOfRangeException>(delegate { new ReadinessTraceLimiter(0); });
            Checks.That(error.ParamName == "intervalMilliseconds", "parameter_name_preserved");
            Checks.Throws<ArgumentOutOfRangeException>(delegate { new ReadinessTraceLimiter(-1); });
        });
        Checks.Case("limiter_invalid_observation_does_not_mutate", delegate
        {
            var limiter = new ReadinessTraceLimiter(10);
            Checks.That(!limiter.Observe(-1, "pending"), "negative_time_rejected");
            Checks.That(!limiter.Observe(0, null), "null_code_rejected");
            Checks.That(!limiter.Observe(0, ""), "empty_code_rejected");
            Checks.That(limiter.Attempts == 0 && limiter.LastCode == null, "invalid_input_no_state_change");
        });
        Checks.Case("limiter_suppressed_code_still_updates_observation", delegate
        {
            var limiter = new ReadinessTraceLimiter(10);
            Checks.That(limiter.Observe(0, "pending"), "first_emitted");
            Checks.That(!limiter.Observe(9, "changed"), "code_flap_not_extra_emit");
            Checks.That(limiter.LastCode == "changed" && limiter.Attempts == 2, "suppressed_observation_retained");
            Checks.That(limiter.Observe(10, "ready"), "exact_boundary_emitted");
        });
        Checks.Case("limiter_backward_clock_is_suppressed", delegate
        {
            var limiter = new ReadinessTraceLimiter(10);
            Checks.That(limiter.Observe(100, "first"), "first_at_offset");
            Checks.That(!limiter.Observe(90, "backward"), "no_reset_on_clock_reversal");
            Checks.That(limiter.Observe(110, "later"), "old_deadline_remains");
        });
        Checks.Case("limiter_terminal_tick_inherited_limit", delegate
        {
            var limiter = new ReadinessTraceLimiter(10);
            Checks.That(limiter.Observe(long.MaxValue - 5, "first"), "large_first_tick");
            Checks.That(!limiter.Observe(long.MaxValue - 1, "before"), "deadline_does_not_wrap");
            Checks.That(limiter.Observe(long.MaxValue, "terminal"), "terminal_tick_emits");
            Checks.That(limiter.Observe(long.MaxValue, "terminal-again"), "historical_saturation_behavior");
            // This is an observed inherited limitation, not a strict-gap PASS.
            Checks.KnownLimits++;
        });
        Checks.Case("limiter_invariant_rejects_deliberate_early_emit", delegate
        {
            Checks.Throws<CheckFailure>(delegate { AssertGaps(new List<long> { 0, 1 }, 10); });
        });
    }
}

internal static class SchedulerHarness
{
    private static void Finish(ManagedInstallScheduler scheduler)
    {
        Checks.That(scheduler.StopAndDrain(3000), "cleanup_worker_drained");
        scheduler.Dispose();
    }

    internal static void Run()
    {
        Checks.Case("scheduler_single_start_serial_steps", delegate
        {
            int steps = 0, active = 0, maximumActive = 0, faults = 0;
            using (var completed = new ManualResetEventSlim(false))
            {
                var scheduler = new ManagedInstallScheduler(delegate
                {
                    int count = Interlocked.Increment(ref active);
                    if (count > maximumActive) maximumActive = count;
                    int current = Interlocked.Increment(ref steps);
                    Thread.Sleep(15);
                    Interlocked.Decrement(ref active);
                    if (current == 3) { completed.Set(); return false; }
                    return true;
                }, delegate { Interlocked.Increment(ref faults); }, 10);
                try
                {
                    Checks.That(scheduler.Start(), "single_start_accepted");
                    Checks.That(!scheduler.Start(), "duplicate_start_rejected");
                    Checks.That(completed.Wait(3000), "three_steps_completed");
                    Checks.That(scheduler.StopAndDrain(3000), "completed_worker_drained");
                    Checks.That(steps == 3 && maximumActive == 1, "exact_serial_steps");
                    Checks.That(faults == 0, "normal_path_no_fault");
                }
                finally { Finish(scheduler); }
            }
        });
        Checks.Case("scheduler_cancel_prevents_late_step", delegate
        {
            int steps = 0;
            using (var entered = new ManualResetEventSlim(false))
            {
                var scheduler = new ManagedInstallScheduler(delegate
                {
                    Interlocked.Increment(ref steps); entered.Set(); return true;
                }, delegate { }, 1000);
                try
                {
                    Checks.That(scheduler.Start(), "cancel_start_accepted");
                    Checks.That(entered.Wait(3000), "cancel_step_entered");
                    Checks.That(scheduler.StopAndDrain(3000), "cancelled_worker_drained");
                    int after = steps;
                    Thread.Sleep(50);
                    Checks.That(steps == after, "no_step_after_drain");
                }
                finally { Finish(scheduler); }
            }
        });
        Checks.Case("scheduler_timeout_does_not_mean_drained", delegate
        {
            using (var entered = new ManualResetEventSlim(false))
            using (var release = new ManualResetEventSlim(false))
            {
                var scheduler = new ManagedInstallScheduler(delegate
                {
                    entered.Set();
                    if (!release.Wait(10000)) throw new TimeoutException();
                    return true;
                }, delegate { }, 10);
                try
                {
                    Checks.That(scheduler.Start(), "blocked_start_accepted");
                    Checks.That(entered.Wait(3000), "blocked_step_entered");
                    Checks.That(!scheduler.StopAndDrain(20), "undrained_worker_rejected");
                    Checks.Throws<InvalidOperationException>(delegate { scheduler.Dispose(); });
                    release.Set();
                    Checks.That(scheduler.StopAndDrain(3000), "released_worker_drained");
                }
                finally { release.Set(); Finish(scheduler); }
            }
        });
        Checks.Case("scheduler_step_exception_reports_once", delegate
        {
            int faults = 0;
            using (var faulted = new ManualResetEventSlim(false))
            {
                var scheduler = new ManagedInstallScheduler(
                    delegate { throw new InvalidOperationException(); },
                    delegate { Interlocked.Increment(ref faults); faulted.Set(); }, 10);
                try
                {
                    Checks.That(scheduler.Start(), "fault_start_accepted");
                    Checks.That(faulted.Wait(3000), "fault_reported");
                    Checks.That(scheduler.StopAndDrain(3000), "faulted_worker_drained");
                    Checks.That(faults == 1, "fault_exactly_once");
                }
                finally { Finish(scheduler); }
            }
        });
        Checks.Case("scheduler_unexpected_cancellation_is_fault", delegate
        {
            int faults = 0;
            using (var faulted = new ManualResetEventSlim(false))
            {
                var scheduler = new ManagedInstallScheduler(
                    delegate { throw new OperationCanceledException(); },
                    delegate { Interlocked.Increment(ref faults); faulted.Set(); }, 10);
                try
                {
                    Checks.That(scheduler.Start(), "unexpected_cancel_start");
                    Checks.That(faulted.Wait(3000), "unexpected_cancel_reported");
                    Checks.That(scheduler.StopAndDrain(3000) && faults == 1, "unexpected_cancel_not_success");
                }
                finally { Finish(scheduler); }
            }
        });
        Checks.Case("scheduler_fault_callback_exception_is_contained", delegate
        {
            using (var faulted = new ManualResetEventSlim(false))
            {
                var scheduler = new ManagedInstallScheduler(
                    delegate { throw new InvalidOperationException(); },
                    delegate { faulted.Set(); throw new ApplicationException(); }, 10);
                try
                {
                    Checks.That(scheduler.Start(), "throwing_fault_callback_start");
                    Checks.That(faulted.Wait(3000), "throwing_fault_callback_observed");
                    Checks.That(scheduler.StopAndDrain(3000), "throwing_fault_callback_drained");
                }
                finally { Finish(scheduler); }
            }
        });
        Checks.Case("scheduler_stop_before_start_closes_admission", delegate
        {
            var scheduler = new ManagedInstallScheduler(delegate { return false; }, delegate { }, 10);
            try
            {
                Checks.That(scheduler.StopAndDrain(0), "stop_before_start_drained");
                Checks.That(!scheduler.Start(), "start_after_stop_rejected");
            }
            finally { Finish(scheduler); }
        });
        Checks.Case("scheduler_close_admission_before_start", delegate
        {
            var scheduler = new ManagedInstallScheduler(delegate { return false; }, delegate { }, 10);
            try
            {
                scheduler.CloseAdmission(); scheduler.CloseAdmission();
                Checks.That(!scheduler.Start(), "closed_admission_rejects_start");
                Checks.That(scheduler.StopAndDrain(0), "never_started_is_drained");
            }
            finally { Finish(scheduler); }
        });
        Checks.Case("scheduler_invalid_arguments", delegate
        {
            Checks.Throws<ArgumentException>(delegate { new ManagedInstallScheduler(null, delegate { }, 1); });
            Checks.Throws<ArgumentException>(delegate { new ManagedInstallScheduler(delegate { return false; }, null, 1); });
            Checks.Throws<ArgumentException>(delegate { new ManagedInstallScheduler(delegate { return false; }, delegate { }, 0); });
            var scheduler = new ManagedInstallScheduler(delegate { return false; }, delegate { }, 10);
            try { Checks.That(!scheduler.StopAndDrain(-1), "negative_timeout_rejected"); }
            finally { Finish(scheduler); }
        });
        Checks.Case("scheduler_disposed_lifecycle_is_not_reusable", delegate
        {
            var scheduler = new ManagedInstallScheduler(delegate { return false; }, delegate { }, 10);
            scheduler.Dispose(); scheduler.Dispose(); scheduler.CloseAdmission();
            Checks.That(!scheduler.Start(), "disposed_start_rejected");
            Checks.That(scheduler.StopAndDrain(0), "disposed_stop_is_idempotent");
        });
    }
}

internal static class Program
{
    public static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        string scenario = args.Length == 0 ? "all" : args[0];
        if (args.Length > 1 || (scenario != "all" && scenario != "limiter" &&
            scenario != "scheduler" && scenario != "failure-control" && scenario != "timeout-control"))
        {
            Console.Error.WriteLine("INPUT_ERROR unknown scenario");
            return 2;
        }
        if (scenario == "timeout-control")
        {
            Console.WriteLine("CONTROL finite_wait_ms=5000");
            Console.Out.Flush();
            Thread.Sleep(5000);
            Console.WriteLine("CONTROL parent_deadline_not_triggered");
            return 3;
        }
        if (scenario == "failure-control")
            Checks.Case("deliberately_failed_invariant", delegate { Checks.That(false, "expected_control_failure"); });
        else
        {
            if (scenario == "all" || scenario == "limiter") LimiterHarness.Run();
            if (scenario == "all" || scenario == "scheduler") SchedulerHarness.Run();
        }
        Console.WriteLine("SUMMARY passed=" + Checks.Passed + " failed=" + Checks.Failed +
            " assertions=" + Checks.Assertions + " known_limits=" + Checks.KnownLimits);
        return Checks.Failed == 0 ? 0 : 1;
    }
}
