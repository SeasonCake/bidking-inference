"""Real WinPS child via extracted current Rust host; no diagnostics collection."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import time

import psutil
import pytest

ROOT=Path(__file__).resolve().parents[1]


def _rust_function(source,name):
    matches=list(re.finditer(r'^fn '+re.escape(name)+r'\b.*?^}',source,re.M|re.S))
    assert len(matches)==1
    return matches[0].group()


@pytest.fixture(scope='module')
def native_host(tmp_path_factory):
    if os.name!='nt':pytest.skip('Windows host required')
    directory=tmp_path_factory.mktemp('native-host')
    rustc=Path('C:/Users/example/.cargo/bin/rustc.exe')
    if not rustc.is_file():pytest.skip('isolated rustc required')
    source=(ROOT/'native/bidking_kernel/src/client_lifecycle.rs').read_text('utf-8')
    functions='\n'.join(_rust_function(source,name) for name in ('run_powershell','canonical_file','canonical_path','base64_encode'))
    bootstrap=re.search(r'const POWERSHELL_STDIN_BOOTSTRAP: &str = r#".*?"#;',source,re.S).group()
    no_window=re.search(r'const CREATE_NO_WINDOW: u32 = .*?;',source).group()
    prelude=r'''
use std::env;
use std::io::Write;
use std::path::{Path,PathBuf};
use std::process::{Command,Stdio};
use std::os::windows::process::CommandExt;
#[derive(Clone,Copy,Debug,PartialEq,Eq)] enum LifecycleMode {Launch,Diagnostics,Repair}
#[derive(Debug)] struct LifecycleError;
impl LifecycleError {fn start()->Self{Self} fn contract()->Self{Self} fn payload()->Self{Self}}
mod startup_timing {pub fn write_event(_: &str,_: &str,_: Option<&str>) {}}
'''
    main=r'''
fn main(){
 let args:Vec<String>=env::args().collect();
 let mode=match args[1].as_str(){"diagnostics"=>LifecycleMode::Diagnostics,"launch"=>LifecycleMode::Launch,"repair"=>LifecycleMode::Repair,_=>panic!("invalid fixture mode")};
 let root=Path::new(&args[2]);let payload=std::fs::read(&args[3]).unwrap();
 let result=run_powershell(mode,&payload,root,root,None,"portable",false,false,false);
 println!("NATIVE_HOST_END={:?}",result);
 std::process::exit(result.unwrap_or(91));
}
'''
    path=directory/'native_host.rs';path.write_text(prelude+bootstrap+no_window+functions+main,encoding='utf-8')
    executable=directory/'native_host.exe'
    command=[str(rustc),'--edition','2024',str(path),'-o',str(executable)]
    start=time.monotonic()
    result=subprocess.run(command,capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    (directory/'COMPILE.stdout').write_bytes(result.stdout);(directory/'COMPILE.stderr').write_bytes(result.stderr)
    (directory/'COMPILE.json').write_text(json.dumps(dict(command=command,exit=result.returncode,hard_timeout=60,seconds=time.monotonic()-start)),encoding='utf-8')
    assert result.returncode==0,result.stderr.decode('utf-8','replace')
    (directory/'BidKingLive.exe').write_bytes(b'not-executed-canonical-file-fixture')
    return directory,executable


@pytest.mark.parametrize('mode,ambient,explorer',[
    ('diagnostics',None,False),('diagnostics','',False),('diagnostics',None,True),
    ('launch',None,False),('repair',None,False),('launch','ambient-preserved',False)])
def test_actual_native_mode_sets_only_diagnostic_noninteractive(native_host,mode,ambient,explorer):
    directory,executable=native_host
    original=(ROOT/'scripts/collect_diagnostics.ps1').read_text('utf-8-sig')
    tail=original[original.index('if (-not $env:BIDKING_DIAG_NOEXPLORER) {'):]
    label=f'{mode}-{repr(ambient)}-{explorer}'.replace("'",'').replace(' ','_')
    payload=directory/(label+'.ps1')
    prefix=r'''
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$outFile='no-real-report'
function Start-Process {param($FilePath,$ArgumentList);[Console]::Out.WriteLine('EXPLORER_BOUNDARY_ONLY')}
[Console]::Out.WriteLine('ACTUAL_NONINTERACTIVE=['+$env:BIDKING_DIAG_NONINTERACTIVE+']')
'''
    # Launch/repair do not use the diagnostics tail; only env leakage is tested.
    payload.write_text(prefix+(tail if mode=='diagnostics' else '\nexit 0\n'),encoding='utf-8')
    env={k:v for k,v in os.environ.items() if not k.upper().startswith('BIDKING_')}
    if ambient is not None:env['BIDKING_DIAG_NONINTERACTIVE']=ambient
    if not explorer:env['BIDKING_DIAG_NOEXPLORER']='1'
    command=[str(executable),mode,str(directory),str(payload)]
    start=time.monotonic();process=subprocess.Popen(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
    timed_out=False;ended_children=[]
    try:out,err=process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        timed_out=True
        # Only children of this retained fixture process; no name-based search.
        parent=psutil.Process(process.pid)
        for child in parent.children(recursive=True):
            born=child.create_time()
            if child.is_running() and child.create_time()==born:
                child.kill();child.wait(timeout=5);ended_children.append(dict(pid=child.pid,created=born))
        process.kill();out,err=process.communicate(timeout=5)
    (directory/(label+'.stdout')).write_bytes(out);(directory/(label+'.stderr')).write_bytes(err)
    (directory/(label+'.run.json')).write_text(json.dumps(dict(command=command,exit=process.returncode,timeout=timed_out,hard_timeout=5,
        seconds=time.monotonic()-start,owned_children_ended=ended_children,fixture_only=True)),encoding='utf-8')
    assert not timed_out and process.returncode==0, out.decode('utf-8','replace')+err.decode('utf-8','replace')
    text=out.decode('utf-8','replace')
    expected='1' if mode=='diagnostics' else (ambient or '')
    assert f'ACTUAL_NONINTERACTIVE=[{expected}]' in text
    assert ('EXPLORER_BOUNDARY_ONLY' in text)==explorer
    assert 'NATIVE_HOST_END=Ok(0)' in text


def test_real_execute_payload_passes_its_existing_mode_to_host():
    source=(ROOT/'native/bidking_kernel/src/client_lifecycle.rs').read_text('utf-8')
    function=_rust_function(source,'execute_payload')
    assert 'let result = run_powershell(\n        mode,\n        &source,' in function
    assert 'LifecycleMode::Diagnostics | LifecycleMode::Repair => std::mem::take(payload)' in function
    assert source.count('command.env("BIDKING_DIAG_NONINTERACTIVE", "1")')==1
