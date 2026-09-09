// Historical source adapted for a standalone, synthetic console lab.
// Provenance: docs/research/PROVENANCE.json (entry: match10-managed-scheduler).
// C# 5 compatibility: make the existing async Task.Run overload explicit.
using System;
using System.Threading;
using System.Threading.Tasks;

namespace HistoricalScheduling
{
    internal sealed class ManagedInstallScheduler : IDisposable
    {
        private readonly object _gate = new object();
        private readonly Func<bool> _step;
        private readonly Action _fault;
        private readonly int _intervalMilliseconds;
        private readonly CancellationTokenSource _cancellation = new CancellationTokenSource();
        private Task _worker;
        private bool _startCalled;
        private bool _stopCalled;
        private bool _disposed;

        internal ManagedInstallScheduler(
            Func<bool> step,
            Action fault,
            int intervalMilliseconds)
        {
            if (step == null || fault == null || intervalMilliseconds <= 0)
            {
                throw new ArgumentException();
            }
            _step = step;
            _fault = fault;
            _intervalMilliseconds = intervalMilliseconds;
        }

        internal bool Start()
        {
            lock (_gate)
            {
                if (_disposed || _startCalled || _stopCalled)
                {
                    return false;
                }
                _startCalled = true;
                _worker = Task.Run((Func<Task>)RunAsync);
                return true;
            }
        }

        internal bool StopAndDrain(int timeoutMilliseconds)
        {
            if (timeoutMilliseconds < 0)
            {
                return false;
            }
            Task worker;
            lock (_gate)
            {
                if (_disposed)
                {
                    return true;
                }
                _stopCalled = true;
                _cancellation.Cancel();
                worker = _worker;
            }
            if (worker == null)
            {
                return true;
            }
            if (Task.CurrentId.HasValue && Task.CurrentId.Value == worker.Id)
            {
                return false;
            }
            try
            {
                return worker.Wait(timeoutMilliseconds);
            }
            catch (AggregateException)
            {
                return worker.IsCompleted;
            }
        }

        internal void CloseAdmission()
        {
            lock (_gate)
            {
                if (_disposed) return;
                _stopCalled = true;
                _cancellation.Cancel();
            }
        }

        public void Dispose()
        {
            lock (_gate)
            {
                if (_disposed)
                {
                    return;
                }
                if (_worker != null && !_worker.IsCompleted)
                {
                    throw new InvalidOperationException("install scheduler must drain before disposal");
                }
                _disposed = true;
                _cancellation.Dispose();
            }
        }

        private async Task RunAsync()
        {
            CancellationToken token = _cancellation.Token;
            try
            {
                while (!token.IsCancellationRequested)
                {
                    if (!_step())
                    {
                        return;
                    }
                    await Task.Delay(_intervalMilliseconds, token).ConfigureAwait(false);
                }
            }
            catch (OperationCanceledException)
            {
                if (!token.IsCancellationRequested)
                {
                    NotifyFault();
                }
            }
            catch
            {
                NotifyFault();
            }
        }

        private void NotifyFault()
        {
            try
            {
                _fault();
            }
            catch
            {
            }
        }
    }
}
