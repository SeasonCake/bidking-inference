"""Offline product-export inventory and disclosure checks, not a security certification."""
from __future__ import annotations
import ast
import hashlib
import ipaddress
import json
import re
from pathlib import Path, PurePosixPath

IGNORED = {'__pycache__', '.pytest_cache', '.ruff_cache', '.venv', 'target', 'outputs'}
FORBIDDEN_SUFFIXES = {'.exe','.dll','.pyd','.pdb','.pyc','.so','.dylib','.zip','.7z','.pcap','.pcapng','.db','.sqlite','.sqlite3','.enc','.pem','.key'}
RULES = {
    'private_key_material': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]{40,}?-----END'),
    'provider_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{32,}|(?:AKIA|ASIA)[A-Z0-9]{16}|sk-[A-Za-z0-9]{40,})\b'),
    'production_host': re.compile(r'(?i)\bbidkinglab\.cn\b|[a-z0-9.-]+\.workers\.dev\b'),
    'personal_contact_number': re.compile(r'(?i)\bQQ\s*[:：=]?\s*[0-9]{6,12}\b'),
    'personal_profile': re.compile(r'(?i)[A-Z]:[\\/]+Users[\\/]+(?!example(?:[-_][a-z0-9]+)*[\\/]|Public[\\/]|Default[\\/]|test[\\/]|runneradmin[\\/])[^\\/\s"\']+[\\/]'),
}

def relative_path(value):
    return (isinstance(value,str) and bool(value) and '\\' not in value
            and not PurePosixPath(value).is_absolute()
            and all(p not in ('','.', '..') for p in value.split('/')))

def content_errors(relative, data):
    errors=[]
    if relative.casefold().startswith('data/'):
        return [f'runtime data is not source: {relative}']
    if Path(relative).suffix.lower() in FORBIDDEN_SUFFIXES:
        return [f'non-source/private-capable file: {relative}']
    try:text=data.decode('utf-8-sig')
    except UnicodeError:return [f'non-UTF8 source: {relative}']
    for category, pattern in RULES.items():
        matches = list(pattern.finditer(text))
        if category == 'personal_profile':
            # An interpolated profile/regex is code, not a disclosed concrete profile.
            matches = [m for m in matches if not re.search(r'[{}\[\]()*?^$]', m.group(0))]
        if matches:errors.append(f'{category}: {relative}')
    endpoint_text=text
    if relative == 'scripts/activation_transport_diagnostic.py':
        # Reviewed third-party DoH service, not an operator/production locator.
        for quote in ('\"', "'"):
            endpoint_text=endpoint_text.replace(quote+'https://1.1.1.1/dns-query'+quote,quote+quote)
    for address in re.findall(r'https?://([0-9]{1,3}(?:\.[0-9]{1,3}){3})(?=[:/\s\"\']|$)', endpoint_text):
        try: parsed=ipaddress.ip_address(address)
        except ValueError: continue
        documentation = any(parsed in ipaddress.ip_network(cidr) for cidr in ('192.0.2.0/24','198.51.100.0/24','203.0.113.0/24'))
        if parsed.is_global and not documentation:
            errors.append(f'unreviewed public IP endpoint: {relative}')
            break
    if relative.endswith('.py'):
        try:ast.parse(text, filename=relative)
        except SyntaxError as exc:errors.append(f'Python syntax: {relative}:{exc.lineno}')
    return errors

def product_errors(root: Path):
    product=root/'product'; manifest=product/'SOURCE_MANIFEST.json'
    try:doc=json.loads(manifest.read_bytes())
    except (OSError,ValueError):return ['product source manifest missing or invalid']
    if type(doc.get('schema_version')) is not int or doc.get('schema_version')!=1 or not isinstance(doc.get('files'),list) or not doc['files']:
        return ['product source manifest schema invalid']
    errors=[]; expected=set()
    for row in doc['files']:
        if not isinstance(row,dict) or not relative_path(row.get('path')):
            errors.append('product manifest path invalid');continue
        rel=row['path'];p=product/rel
        if rel.casefold().startswith('data/'):
            errors.append(f'runtime data is not source: {rel}')
            continue
        if rel in expected:errors.append(f'duplicate product path: {rel}')
        expected.add(rel)
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(product.resolve()):
            errors.append(f'product source missing or outside tree: {rel}');continue
        data=p.read_bytes()
        if len(data)!=row.get('bytes') or hashlib.sha256(data).hexdigest()!=row.get('sha256'):
            errors.append(f'product source identity changed: {rel}')
        errors.extend(content_errors(rel,data))
    actual={p.relative_to(product).as_posix() for p in product.rglob('*') if p.is_file()
            and not any(x in IGNORED for x in p.relative_to(product).parts)
            and p!=manifest}
    for rel in sorted(actual-expected):errors.append(f'unlisted product file: {rel}')
    return errors

if __name__=='__main__':
    errors=product_errors(Path(__file__).resolve().parents[1])
    if errors:raise SystemExit('\n'.join(errors))
    print(json.dumps({'product_export':'PASS','scope':'inventory, text patterns and Python syntax'}))
