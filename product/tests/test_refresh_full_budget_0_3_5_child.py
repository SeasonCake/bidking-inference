"""Test-only numeric seam inside the actual Windows owned child protocol."""
import os
from pathlib import Path
import time


def run(channel, behavior, marker):
    from bidking_lab.live import monitor as module

    def build(payload, **kwargs):
        from bidking_lab.live.live_refresh_signatures import inference_semantic_signature_from_payload
        Path(marker).write_text(str(os.getpid()), encoding='ascii')
        if behavior == 'crash':
            os._exit(19)
        if behavior == 'partial':
            raise ValueError('incomplete length')
        if behavior == 'error':
            raise RuntimeError('synthetic numeric failure')
        if behavior == 'slow':
            while True:
                time.sleep(.01)
        signature = inference_semantic_signature_from_payload(payload)
        if behavior == 'large-result':
            return {'session_id':signature[0], 'blob':'x' * (17 * 1024 * 1024)}
        return {'session_id': 'wrong' if behavior == 'wrong-session' else signature[0],
                'phase': 'bidding', 'round': 1, 'created_at': time.time(),
                'processor': 'synthetic-numeric-seam', 'latest_bids': {},
                'received_trials': kwargs['n_trials']}

    module.build_monitor_artifact_from_payload = build
    module._full_artifact_child(channel)


def run_f01_caller(directory):
    """Actual returning parent process; no capture/UI/network authority."""
    import json
    import subprocess
    import sys
    import run_fatbeans_webhook_monitor as module
    from bidking_lab.live.monitor import MonitorTables
    from test_refresh_semantic_deadline_hotfix2 import _state,_action
    root=Path(directory)
    monitor=module.FatbeansWebhookMonitor(config=module.WebhookMonitorConfig(
        log_dir=root/'logs',raw_dir=root/'raw',process_name='BidKing.exe',server_ports=(10000,),
        n_trials=500,roi_trials=0,shadow_trials=20,full_shadow_trials=20,run_debug_shadows=False,
        seed=1,archive_round_snapshots=False),tables=MonitorTables({}, {}, {}))
    module.build_monitor_artifact_from_payload=lambda payload,**kwargs:{
        'session_id':module._semantic_signature_from_payload(payload)[0],'phase':'bidding','round':1}
    def start(job):
        code='from test_refresh_full_budget_0_3_5_child import run;run('+repr(job['channel_name'])+",'slow',"+repr(str(root/'compute-entered'))+')'
        return subprocess.Popen([sys.executable,'-B','-c',code],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    monitor._start_full_process=start
    monitor.accept_row(_state());monitor.accept_row(_action());monitor._process_snapshot(force=False)
    monitor._last_packet_at=time.monotonic()-2;monitor._maybe_run_idle_full_upgrade()
    deadline=time.monotonic()+4
    while not (root/'compute-entered').exists() and time.monotonic()<deadline:
        monitor._supervise_full_job();time.sleep(.01)
    assert (root/'compute-entered').exists()
    monitor.accept_row(_action(43))
    original_reap=monitor._reap_full_process
    monitor._reap_full_process=lambda job: original_reap(job) if (root/'release-reap').exists() else False
    order=[]
    module.build_monitor_artifact_from_payload=lambda payload,**kwargs:{
        'session_id':module._semantic_signature_from_payload(payload)[0],'phase':'settled','round':2}
    def write(artifact,**kwargs):
        assert (root/'caller-returned.json').exists() # capture-resource stub already closed
        assert artifact['snapshot_mode']=='fast' and kwargs['append_logs'] is True
        order.append('settlement')
    module.write_monitor_logs=write
    close=monitor._fact_receipts.close
    def writer_close():
        order.append('writer_close');close()
    monitor._fact_receipts.close=writer_close
    finish=monitor._finish_compute_trace
    def finished(event,**kwargs):
        if event=='stopped':
            order.append('stopped')
            (root/'shutdown-order.json').write_text(json.dumps(order))
        finish(event,**kwargs)
    monitor._finish_compute_trace=finished
    monitor._stop.set();monitor._worker.start()
    result=monitor.stop()
    (root/'caller-returned.json').write_text(json.dumps({'stop_result':result,'worker_daemon':monitor._worker.daemon,
        'compute_pid':int((root/'compute-entered').read_text()),'capture_resource_stub':'closed'}))
    # Deliberately return like the real finally callers. Non-daemon monitor
    # remains responsible until the external test releases the injected fault.
