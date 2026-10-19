import unittest
from unittest.mock import patch, Mock
import requests
from file_storage import PrivateStorage, StorageUnavailable


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.store = PrivateStorage('https://example.test', 'synthetic-key')
        self.http = patch('file_storage.requests.request')
        self.request = self.http.start()
        self.addCleanup(self.http.stop)

    def reply(self, status=200, body=None, content=b'test'):
        self.request.return_value = Mock(status_code=status, content=content)
        self.request.return_value.json.return_value = body

    def test_upload_never_overwrites_existing_object(self):
        self.reply()
        self.store.put('portal/example.csv', b'code,score')
        args, kwargs = self.request.call_args
        self.assertEqual(args, ('POST', 'https://example.test/storage/v1/object/caupsynr-files/portal/example.csv'))
        self.assertEqual(kwargs['headers']['x-upsert'], 'false')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['timeout'], (5, 20))

    def test_only_explicit_missing_object_allows_fallback(self):
        for status, body in [(404, {'code': 'NoSuchKey'}), (400, {'statusCode': '404', 'message': 'Object not found'})]:
            self.reply(status, body)
            self.assertIsNone(self.store.get('site/gallery_data.json'))
        for status, body in [(400, {'message': 'Bucket not found'}), (401, {}), (403, {}), (500, {}), (302, {})]:
            self.reply(status, body)
            with self.assertRaises(StorageUnavailable): self.store.get('site/gallery_data.json')

    def test_capacity_error_and_timeout_fail_closed(self):
        self.reply(413, {})
        with self.assertRaises(StorageUnavailable): self.store.put('portal/a.csv', b'x')
        self.request.side_effect = requests.Timeout('synthetic timeout')
        with self.assertRaises(StorageUnavailable): self.store.get('portal/a.csv')

    def test_no_path_traversal(self):
        for key in ('../secret', '/secret', 'portal/../site/a', 'portal\\a', 'a//b'):
            with self.assertRaises(ValueError): self.store.get(key)
        self.request.assert_not_called()

    def test_listing_paginates(self):
        self.request.side_effect = [Mock(status_code=200, json=lambda: [{'name': str(i)} for i in range(100)]),
                                    Mock(status_code=200, json=lambda: [{'name': 'last'}])]
        self.assertEqual(len(self.store.list('gallery/test')), 101)
        self.assertEqual(self.request.call_args.kwargs['json']['offset'], 100)

    def test_delete_scoped_to_one_object(self):
        self.reply()
        self.store.delete('portal/one.csv')
        self.assertEqual(self.request.call_args.kwargs['json'], {'prefixes': ['portal/one.csv']})
