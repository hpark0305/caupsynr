import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from storage_backup import write_archive, verify_archive, encrypt_backup, inventory
from file_storage import StorageUnavailable


class StorageBackupTests(unittest.TestCase):
    def store(self):
        store = MagicMock()
        store.list.side_effect = lambda p: ([{'id': None, 'name': 'portal'}] if p == '' else
                                            [{'id': 'x', 'name': 'test.csv', 'updated_at': 'fixed'}])
        store.get.return_value = b'SYNTHETIC_ONLY,123\n'
        return store

    def test_roundtrip_no_remote_writes(self):
        store, output = self.store(), io.BytesIO()
        self.assertEqual(write_archive(store, output), 1)
        output.seek(0)
        self.assertEqual(verify_archive(output), 1)
        store.put.assert_not_called()
        store.delete.assert_not_called()

    def test_empty_bucket_roundtrip(self):
        store, output = MagicMock(), io.BytesIO()
        store.list.return_value = []
        self.assertEqual(write_archive(store, output), 0)
        output.seek(0)
        self.assertEqual(verify_archive(output), 0)

    def test_changed_or_missing_object_fails(self):
        store = self.store()
        store.list.side_effect = [[{'id': 'x', 'name': 'a'}], [{'id': 'y', 'name': 'a'}]]
        with self.assertRaises(StorageUnavailable): write_archive(store, io.BytesIO())
        store = self.store()
        store.get.return_value = None
        with self.assertRaises(StorageUnavailable): write_archive(store, io.BytesIO())

    def test_unsafe_inventory_rejected(self):
        for name in ('..', 'a/b', 'a\\b'):
            store = MagicMock()
            store.list.return_value = [{'id': 'x', 'name': name}]
            with self.assertRaises(ValueError): inventory(store)

    def test_tampered_archive_fails(self):
        stream = io.BytesIO()
        write_archive(self.store(), stream)
        with self.assertRaises(ValueError):
            verify_archive(io.BytesIO(stream.getvalue().replace(b'SYNTHETIC_ONLY', b'CORRUPTED_ONLY')))

    def test_existing_backup_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'existing.age'
            target.write_bytes(b'previous')
            with self.assertRaises(FileExistsError):
                encrypt_backup(self.store(), 'unused', target)
            self.assertEqual(target.read_bytes(), b'previous')

    def test_encryption_start_failure_removes_only_own_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'new.age'
            with self.assertRaises(FileNotFoundError):
                encrypt_backup(self.store(), 'unused', target, age='/nonexistent/test-age')
            self.assertFalse(target.exists())
