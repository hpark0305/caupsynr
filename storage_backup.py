"""Read-only Storage export to age encryption. Never restores to production.

Usage: SUPABASE_URL/KEY in the process environment, then:
  python storage_backup.py backup --recipient age1... --output /safe/new.tar.age
  python storage_backup.py verify --identity /safe/key.txt --archive /safe/new.tar.age
No scheduler, retention deletion, plaintext archives, or remote writes are included.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
from datetime import datetime, timezone

from file_storage import BUCKET, PrivateStorage, StorageUnavailable


def inventory(store, prefix=''):
    found = {}
    for row in store.list(prefix):
        name = row['name']
        if not name or '/' in name or name in ('.', '..') or '\\' in name:
            raise ValueError('Unsafe object name')
        key = prefix + '/' + name if prefix else name
        if row.get('id') is None:
            found.update(inventory(store, key))
        else:
            found[key] = {k: row.get(k) for k in ('id', 'updated_at', 'metadata')}
    return found


def write_archive(store, stream):
    before = inventory(store)
    records = []
    with tarfile.open(fileobj=stream, mode='w|') as archive:
        for key in sorted(before):
            body = store.get(key)
            if body is None:
                raise StorageUnavailable('An object disappeared during backup')
            member = tarfile.TarInfo('objects/' + key)
            member.size, member.mode = len(body), 0o600
            archive.addfile(member, io.BytesIO(body))
            records.append({'key': key, 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()})
        if inventory(store) != before:
            raise StorageUnavailable('Storage changed during backup; retry later')
        # shortcut: Storage has no transaction snapshot; retry when its inventory changes.
        manifest = {'version': 1, 'bucket': BUCKET, 'at': datetime.now(timezone.utc).isoformat(), 'objects': records}
        body = json.dumps(manifest, ensure_ascii=False).encode()
        member = tarfile.TarInfo('manifest.json')
        member.size, member.mode = len(body), 0o600
        archive.addfile(member, io.BytesIO(body))
    return len(records)


def verify_archive(stream):
    actual, manifest, seen = {}, None, set()
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            if not member.isfile() or member.name in seen:
                raise ValueError('Unexpected archive member')
            seen.add(member.name)
            source = archive.extractfile(member)
            if member.name == 'manifest.json':
                if member.size > 4 * 1024 * 1024:
                    raise ValueError('Manifest too large')
                manifest = json.load(source)
            elif member.name.startswith('objects/'):
                key = member.name[len('objects/'):]
                PrivateStorage._path(key)
                digest, size = hashlib.sha256(), 0
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                actual[key] = {'key': key, 'size': size, 'sha256': digest.hexdigest()}
            else:
                raise ValueError('Unexpected archive member')
    if not manifest or manifest.get('version') != 1 or manifest.get('bucket') != BUCKET:
        raise ValueError('Invalid manifest')
    expected = {row['key']: row for row in manifest['objects']}
    if len(expected) != len(manifest['objects']) or actual != expected:
        raise ValueError('Backup content does not match manifest')
    # Drain the encrypted stream so age checks its final authentication tag, too.
    while stream.read(1024 * 1024):
        pass
    return len(actual)


def encrypt_backup(store, recipient, output, age='age'):
    # Exclusive creation protects every existing backup, including symbolic links.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    process = None
    try:
        with os.fdopen(fd, 'wb') as target:
            process = subprocess.Popen([age, '-r', recipient], stdin=subprocess.PIPE,
                                       stdout=target, stderr=subprocess.DEVNULL)
            count = write_archive(store, process.stdin)
            process.stdin.close()
            if process.wait(timeout=60) != 0:
                raise ValueError('Encryption failed')
        return count
    except BaseException:
        if process and process.poll() is None:
            process.kill()
            process.wait()
        Path(output).unlink(missing_ok=True)  # Only our newly-created incomplete ciphertext.
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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
        url, key = os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY']
        if not url.startswith('https://'):
            raise ValueError('HTTPS is required')
        count = encrypt_backup(PrivateStorage(url, key), args.recipient, args.output, args.age)
    else:
        process = subprocess.Popen([args.age, '-d', '-i', args.identity, args.archive],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            count = verify_archive(process.stdout)
            if process.wait(timeout=60) != 0:
                raise ValueError('Decryption failed')
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    print(json.dumps({'ok': True, 'objects': count, 'operation': args.command,
                      'remote_writes': 0, 'plaintext_files_written': 0}))


if __name__ == '__main__':
    main()
