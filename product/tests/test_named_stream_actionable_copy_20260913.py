"""Actual Rust message formatter with a captured MessageBoxW sink; no product launch."""
from pathlib import Path
import re

from test_startup_failure_cleanup_0_3_5_f01 import (
    test_current_messagebox_sink_keeps_actions_and_four_old_bodies as _run_actual_sink,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "native/bidking_trust_bootstrap/src/main.rs"


def test_actual_named_stream_copy_and_cleanup_states(monkeypatch, tmp_path):
    original_write = Path.write_text
    def write(path, data, *args, **kwargs):
        if path.name == "f01_copy_consumer.rs":
            source = SOURCE.read_text(encoding="utf-8")
            mapping = re.search(r"    pub\(crate\) fn product_failure_from_exit\b.*?\n    }", source, re.S)
            assert mapping
            data += "\n" + mapping.group(0) + r'''
#[test]fn named_stream_actionable_four_states(){
 assert_eq!(product_failure_from_exit(111),Some("integrity_named_stream_rejected"));
 for state in ["unknown","incomplete","complete","game"] {
  select(state,"startup_failed");current::report_failure("integrity_named_stream_rejected");let m=captured();
  assert!(m.contains("附加数据格式不受支持"),"misleading reason: {}",m);
  assert!(!m.contains("程序文件带有 Windows 下载标记"));
  let actions=["关闭本提示","原官方 ZIP","属性","若有","解除锁定","勾选并应用","新的空目录","不要覆盖旧目录","仍失败","保留原目录和启动日志","私发支持人员"];
  let action=m.split("下一步：").nth(1).unwrap();let mut offset=0;
  for word in actions {let found=action[offset..].find(word).expect(word);offset+=found+word.len();}
  assert!(m.contains("错误代码：integrity_named_stream_rejected"));
  if state=="unknown"||state=="incomplete" {assert!(m.contains("保留程序目录"));}
  if state=="complete" {assert!(m.contains("收尾已完成"));}
  if state=="game" {assert!(m.contains("仍保留后台读取"));}
  println!("NAMED_STREAM_COPY:{}:{}",state,m.replace('\n',"|"));
 }
}
'''
        return original_write(path, data, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", write)
    _run_actual_sink(tmp_path)
    output = (tmp_path / "COPY.stdout").read_text(encoding="utf-8")
    assert output.count("NAMED_STREAM_COPY:") == 4
    assert "4 passed" in output
