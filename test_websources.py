import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import inbox
import websources


class ListingUpdates(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        db = patch.object(inbox, 'DB', Path(folder.name)/'check.sqlite')
        db.start()
        self.addCleanup(db.stop)
        inbox.prepare()
        self.url = 'https://example.org/blog/'
        self.posts = ['one','two','three','four']
        self.broken = set()
        pages = patch.object(websources, 'fetch', side_effect=self.fetch)
        pages.start()
        self.addCleanup(pages.stop)

    def fetch(self, url):
        if url == self.url:
            links = ''.join(f'<article><h2><a href="{p}">Article {p}</a></h2></article>' for p in self.posts)
            return url, '<head><title>My lab</title></head><main>'+links+'<a href="/tags/">All tags</a></main>'
        if url in self.broken:
            raise ValueError('temporary failure')
        return url, '<main><p>'+'The research group describes an experiment and its observations. '*3+'</p></main>'

    def refresh(self):
        return websources.refresh(lambda msg: None)

    def rows(self):
        with inbox.database() as db:
            return {r['id']:dict(r) for r in db.execute('SELECT * FROM articles')}

    def test_baseline_dedup_and_retry(self):
        websources.register(self.url,'')
        websources.register(self.url,'')
        self.assertEqual(len(websources.sources()),1)
        self.assertFalse(self.refresh())
        self.assertEqual(len(self.rows()),3)
        self.assertTrue(all(not row['discovered'] for row in self.rows().values()))
        self.posts = ['four','one?utm_source=test','one','three','two']
        self.assertFalse(self.refresh())
        self.assertEqual(len(self.rows()),3)
        self.posts.insert(0,'new')
        self.broken = {self.url+'new'}
        self.assertTrue(self.refresh())
        self.assertTrue(websources.sources()[0]['failed'])
        self.posts.pop(0)
        self.broken.clear()
        self.assertFalse(self.refresh())
        rows=self.rows()
        self.assertEqual(len(rows),4)
        self.assertEqual([r['discovered'] for r in rows.values() if r['title']=='Article new'],[date.today().isoformat()])
        self.posts.clear()
        self.assertTrue(self.refresh())
        self.assertEqual(self.rows(),rows)
        websources.remove(websources.sources()[0]['id'])
        self.assertFalse(websources.sources())
        self.assertEqual(self.rows(),rows)

    def test_custom_pattern(self):
        markup = '<ul><li><a href="/entry?id=1">First post</a></li><li><a href="/people/">Staff</a></li></ul>'
        result = websources.listing(self.url,'/entry?*',page=(self.url,markup))
        self.assertEqual([e['title'] for e in result['items']],['First post'])
