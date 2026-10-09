import json
from pathlib import Path
import tempfile
import unittest

from codexws_server.history_reader import read_page


class HistoryReaderTests(unittest.TestCase):
    def test_pages_are_readonly_and_do_not_consume_partial_records(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'history.jsonl'
            first = (json.dumps({'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'text':'中文'}]}},ensure_ascii=False)+'\n').encode()
            original = first + b'{broken}\n' + b'{"unfinished":'
            path.write_bytes(original)
            page = read_page(path, limit=1)
            self.assertEqual(page['records'][0]['text'], '中文')
            self.assertEqual(page['nextCursor'], len(first))
            next_page = read_page(path, page['nextCursor'])
            self.assertEqual(next_page['records'][0]['type'], 'invalid')
            self.assertEqual(next_page['nextCursor'], len(first)+9)
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaises(ValueError): read_page(path, 2)
