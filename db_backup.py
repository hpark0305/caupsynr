"""Read-only export of every Supabase table to an age-encrypted archive.

The Supabase project is on the Free plan, which has no downloadable database
backups, so research data (Mind Mate, Wardy, NeuroBreeze, portal projects)
exists only in the live database unless this is run.

Usage (SUPABASE_URL / SUPABASE_KEY = server-only service_role key in the env):
  python db_backup.py backup --recipient age1... --output /safe/db-YYYYMMDD.tar.age
  python db_backup.py verify --identity /safe/key.txt --archive /safe/db-YYYYMMDD.tar.age

Guarantees: GET requests only; no plaintext file is written; an existing
output file is never overwritten. Tables are read one at a time (there is no
cross-table transaction), and a table whose row count changes while it is
being read fails the backup so it can be retried.
"""
import argparse
import hashlib
import io
import json
import os
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import requests

PAGE = 1000
# Ephemeral login state; never needed for a restore.
SKIP_TABLES = {'portal_auth_sessions'}


class BackupError(Exception):
    pass


class RestReader:
    """Minimal PostgREST client that can only issue GET requests."""

    def __init__(self, url, key, session=None):
        if not url.startswith('https://'):
            raise BackupError('HTTPS is required')
        self.base = url.rstrip('/') + '/rest/v1'
        self.headers = {'apikey': key, 'Authorization': 'Bearer ' + key}
        self.http = session or requests.Session()

    def get(self, path, params=None, headers=None):
        try:
            response = self.http.get(self.base + path, params=params, timeout=(5, 60),
                                     headers=dict(self.headers, **(headers or {})),
                                     allow_redirects=False)
        except requests.RequestException as exc:
            raise BackupError('Database request failed') from exc
        if not 200 <= response.status_code < 300:
            raise BackupError(f'Database request failed with HTTP {response.status_code}')
        return response

    def tables(self):
        """Table name -> primary-key columns, from the PostgREST OpenAPI document."""
        spec = self.get('/', headers={'Accept': 'application/openapi+json'}).json()
        found = {}
        for name, definition in (spec.get('definitions') or {}).items():
            if name in SKIP_TABLES:
                continue
            props = definition.get('properties') or {}
            pk = sorted(col for col, meta in props.items()
                        if '<pk/>' in (meta.get('description') or ''))
            found[name] = pk or (['id'] if 'id' in props else [])
        if not found:
            raise BackupError('No tables visible; is SUPABASE_KEY the service_role key?')
        return found

    def count(self, table):
        response = self.get('/' + table, params={'select': '*', 'limit': '0'},
                            headers={'Prefer': 'count=exact'})
        total = response.headers.get('Content-Range', '').rpartition('/')[2]
        if not total.isdigit():
            raise BackupError(f'No row count for {table}')
        return int(total)

    def rows(self, table, order):
        offset = 0
        while True:
            params = {'select': '*', 'limit': str(PAGE), 'offset': str(offset)}
            if order:
                params['order'] = ','.join(f'{col}.asc' for col in order)
            page = self.get('/' + table, params=params).json()
            if not isinstance(page, list):
                raise BackupError(f'Unexpected response for {table}')
            yield from page
            if len(page) < PAGE:
                return
            offset += PAGE


def _add(archive, name, body):
    member = tarfile.TarInfo(name)
    member.size, member.mode = len(body), 0o600
    archive.addfile(member, io.BytesIO(body))


def write_archive(reader, stream):
    tables, manifest_tables = reader.tables(), {}
    with tarfile.open(fileobj=stream, mode='w|') as archive:
        for table in sorted(tables):
            if not tables[table]:
                # Without a key, offset paging can skip or repeat rows.
                raise BackupError(f'{table} has no primary key; cannot page it reliably')
            before = reader.count(table)
            lines = [json.dumps(row, ensure_ascii=False, sort_keys=True)
                     for row in reader.rows(table, tables[table])]
            if len(lines) != before or reader.count(table) != before:
                raise BackupError(f'{table} changed during backup; retry later')
            body = ('\n'.join(lines) + ('\n' if lines else '')).encode()
            _add(archive, f'tables/{table}.jsonl', body)
            manifest_tables[table] = {'rows': len(lines), 'primary_key': tables[table],
                                      'sha256': hashlib.sha256(body).hexdigest()}
        manifest = {'version': 1, 'kind': 'supabase-rest-export',
                    'at': datetime.now(timezone.utc).isoformat(), 'tables': manifest_tables}
        _add(archive, 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=1).encode())
    return manifest_tables


def verify_archive(stream):
    actual, manifest, seen = {}, None, set()
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            if not member.isfile() or member.name in seen:
                raise BackupError('Unexpected archive member')
            seen.add(member.name)
            body = archive.extractfile(member).read()
            if member.name == 'manifest.json':
                manifest = json.loads(body)
            elif member.name.startswith('tables/') and member.name.endswith('.jsonl'):
                table = member.name[len('tables/'):-len('.jsonl')]
                rows = [line for line in body.decode().split('\n') if line]
                for line in rows:
                    json.loads(line)
                actual[table] = {'rows': len(rows), 'sha256': hashlib.sha256(body).hexdigest()}
            else:
                raise BackupError('Unexpected archive member')
    if not manifest or manifest.get('version') != 1:
        raise BackupError('Invalid manifest')
    expected = {t: {'rows': m['rows'], 'sha256': m['sha256']} for t, m in manifest['tables'].items()}
    if actual != expected:
        raise BackupError('Backup content does not match manifest')
    while stream.read(1024 * 1024):  # let age check its final authentication tag
        pass
    return {t: m['rows'] for t, m in manifest['tables'].items()}


def encrypt_backup(reader, recipient, output, age='age'):
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # never overwrite
    process = None
    try:
        with os.fdopen(fd, 'wb') as target:
            process = subprocess.Popen([age, '-r', recipient], stdin=subprocess.PIPE,
                                       stdout=target, stderr=subprocess.DEVNULL)
            summary = write_archive(reader, process.stdin)
            process.stdin.close()
            if process.wait(timeout=120) != 0:
                raise BackupError('Encryption failed')
        return {t: m['rows'] for t, m in summary.items()}
    except BaseException:
        if process and process.poll() is None:
            process.kill()
            process.wait()
        Path(output).unlink(missing_ok=True)  # only the incomplete file this run created
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--age', default='age')
    sub = parser.add_subparsers(dest='command', required=True)
    backup = sub.add_parser('backup')
    backup.add_argument('--recipient', required=True)
    backup.add_argument('--output', required=True, type=Path)
    verify = sub.add_parser('verify')
    verify.add_argument('--identity', required=True)
    verify.add_argument('--archive', required=True)
    args = parser.parse_args()
    if args.command == 'backup':
        reader = RestReader(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])
        rows = encrypt_backup(reader, args.recipient, args.output, args.age)
    else:
        process = subprocess.Popen([args.age, '-d', '-i', args.identity, args.archive],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            rows = verify_archive(process.stdout)
            if process.wait(timeout=120) != 0:
                raise BackupError('Decryption failed')
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    print(json.dumps({'ok': True, 'operation': args.command, 'rows': rows,
                      'remote_writes': 0, 'plaintext_files_written': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
