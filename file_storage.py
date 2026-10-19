"""Server-only access to one private Supabase Storage bucket."""
from urllib.parse import quote
import requests

BUCKET = 'caupsynr-files'


class StorageUnavailable(Exception):
    pass


class PrivateStorage:
    def __init__(self, url, key):
        self.url = url.rstrip('/') + '/storage/v1'
        self.headers = {'apikey': key, 'Authorization': 'Bearer ' + key}

    def _request(self, method, path, *, missing_ok=False, **kwargs):
        headers = dict(self.headers, **kwargs.pop('headers', {}))
        try:
            response = requests.request(method, self.url + path, headers=headers,
                                        timeout=(5, 20), allow_redirects=False, **kwargs)
            if missing_ok and response.status_code in (400, 404):
                error = response.json()
                if error.get('code') == 'NoSuchKey' or (
                    str(error.get('statusCode')) == '404'
                    and error.get('message', '').lower() == 'object not found'
                ):
                    return None
            if not 200 <= response.status_code < 300:
                raise StorageUnavailable('File storage request failed')
            return response
        except (requests.RequestException, ValueError) as exc:
            raise StorageUnavailable('File storage unavailable') from exc

    @staticmethod
    def _path(key):
        if not key or any(p in ('', '.', '..') for p in key.split('/')) or '\\' in key:
            raise ValueError('Invalid storage path')
        return '/object/' + BUCKET + '/' + quote(key, safe='/')

    def put(self, key, content, *, content_type='application/octet-stream', replace=False):
        self._request('POST', self._path(key), data=content,
                      headers={'Content-Type': content_type, 'x-upsert': str(replace).lower(),
                               'Cache-Control': 'no-store'})

    def get(self, key):
        response = self._request('GET', self._path(key), missing_ok=True)
        return None if response is None else response.content

    def delete(self, key):
        self._path(key)
        self._request('DELETE', '/object/' + BUCKET, json={'prefixes': [key]})

    def list(self, prefix):
        if prefix:
            self._path(prefix)
        result = []
        while True:
            response = self._request('POST', '/object/list/' + BUCKET, json={
                'prefix': prefix, 'limit': 100, 'offset': len(result),
                'sortBy': {'column': 'name', 'order': 'asc'},
            })
            try:
                page = response.json()
                if not isinstance(page, list):
                    raise ValueError('Invalid file listing')
            except ValueError as exc:
                raise StorageUnavailable('Invalid file listing') from exc
            result.extend(page)
            if len(page) < 100:
                return result
