#!/usr/bin/env python3
"""Copy-only regeneration of exact 76b7ca lookup and qualified model inputs."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import stat
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from bidking_lab.extract.official_20261008_model_profile import (
    BASELINE_IDENTITIES, TEXT_ASSET_IDENTITIES, OfficialModelInputError, build_inputs, identity,
)


def stable_read(path: Path, expected: dict) -> bytes:
    before=path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or path.is_symlink() or before.st_size!=expected['bytes']:
        raise OfficialModelInputError(f"missing/unsafe/wrong-size input: {path}")
    raw=path.read_bytes()
    after=path.stat(follow_symlinks=False)
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
        raise OfficialModelInputError(f"input changed while reading: {path}")
    if identity(raw)!=expected:
        raise OfficialModelInputError(f"wrong input SHA: {path}")
    return raw


def main(argv: list[str]|None=None) -> int:
    args_parser=argparse.ArgumentParser(description=__doc__)
    args_parser.add_argument('--textassets-root',type=Path,required=True)
    args_parser.add_argument('--baseline-root',type=Path,required=True)
    args_parser.add_argument('--output-root',type=Path,required=True)
    args_parser.add_argument('--receipt-out',type=Path,required=True)
    args_parser.add_argument('--check',action='store_true',help='Read-only verify existing outputs against exact regeneration.')
    args=args_parser.parse_args(argv)
    started=time.perf_counter()
    try:
        text_root=args.textassets_root.resolve(strict=True)
        baseline_root=args.baseline_root.resolve(strict=True)
        output_root=args.output_root.absolute().resolve()
        receipt_path=args.receipt_out.absolute().resolve()
        for source_root in (text_root,baseline_root):
            if output_root==source_root or output_root.is_relative_to(source_root) or receipt_path.is_relative_to(source_root):
                raise OfficialModelInputError('candidate output/receipt must be outside both input roots')
        texts={name:stable_read(text_root/f'{name}.txt',{k:pin[k] for k in ('bytes','sha256')}) for name,pin in TEXT_ASSET_IDENTITIES.items()}
        baseline={path:stable_read(baseline_root/path,pin) for path,pin in BASELINE_IDENTITIES.items()}
        outputs,receipt=build_inputs(text_assets=texts,baseline_inputs=baseline)
        # Check all extant candidate leaves before writing any new member.
        for relative,raw in outputs.items():
            target=output_root/relative
            if args.check or target.exists():
                if target.is_symlink() or target.resolve()!=target.absolute() or target.read_bytes()!=raw:
                    raise OfficialModelInputError(f'candidate differs from exact regeneration: {relative}')
        receipt['locators']={'textassets_root':str(text_root),'baseline_root':str(baseline_root),'output_root':str(output_root)}
        rendered=(json.dumps(receipt,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
        if args.check:
            if receipt_path.read_bytes()!=rendered:
                raise OfficialModelInputError('receipt differs from exact regeneration')
        else:
            if receipt_path.exists() and receipt_path.read_bytes()!=rendered:
                raise OfficialModelInputError('refuse to replace a different existing receipt')
            for relative,raw in outputs.items():
                target=output_root/relative
                if not target.exists():
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with target.open('xb') as stream: stream.write(raw)
            if not receipt_path.exists():
                receipt_path.parent.mkdir(parents=True,exist_ok=True)
                with receipt_path.open('xb') as stream: stream.write(rendered)
    except (OSError,ValueError) as error:
        print(f'input-assembly-not-ready: {error}',file=sys.stderr)
        return 2
    print(json.dumps({'action':'checked' if args.check else 'wrote','generation':receipt['generation'],'leaves':len(outputs),
                      'output_root':str(output_root),'receipt':str(receipt_path),'seconds':time.perf_counter()-started}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
