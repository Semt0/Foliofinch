import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import inbox
import websources


class ArticleActions(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for obj, key, value in [(inbox, 'DB', root/'inbox.sqlite'), (app, 'DATA', root),
                                (app, 'CONFIG', root/'settings.json'), (app.services, 'FRESH', root/'rss')]:
            override = patch.object(obj, key, value)
            override.start()
            self.addCleanup(override.stop)
        self.url = 'https://example.org/blog/post'
        self.aid = inbox.digest(self.url)[:20]
        self.item = dict(alternate=[{'href': self.url}], summary={'content': 'research ' * 100},
                         published=1, id='first', title='Research')
        inbox.prepare()

    def command(self, name):
        with patch.object(sys, 'argv', ['app.py', name, self.aid]):
            return app.main()

    def ingest(self, feed):
        inbox.ingest(dict(id=feed, title='Test Feed'), lambda path: {'items': [self.item]})

    def test_flags_persist_and_deleted_url_is_skipped_across_feeds(self):
        self.ingest('feed/1')
        self.command('read')
        self.command('star')
        self.item['summary']['content'] += 'more'
        self.ingest('feed/1')
        row, = self.command('snapshot')['articles']
        self.assertEqual((row['seen'], row['starred']), (1, 1))
        self.command('unread')
        self.command('unstar')
        row, = self.command('snapshot')['articles']
        self.assertEqual((row['seen'], row['starred']), (0, 0))
        self.command('delete')
        self.command('delete')
        self.item.update(id='new-guid', alternate=[{'href': self.url + '/?utm_source=rss#top'}])
        with patch.object(inbox, 'article_text', side_effect=AssertionError('unexpected fetch')):
            self.ingest('feed/2')
        web = dict(id='feed/web-example', name='web')
        link = dict(key=self.url, url=self.url, title='Research', day='')
        with inbox.database() as db:
            websources.enqueue(db, web['id'], [link], initial=True)
        with patch.object(websources, 'fetch', side_effect=AssertionError('unexpected fetch')):
            websources.collect(web, link)
        self.assertEqual(self.command('snapshot')['articles'], [])
        with inbox.database() as db:
            self.assertEqual(db.execute('SELECT pending FROM web_links').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM article_feeds').fetchone()[0], 0)

    def test_queue_and_folders_survive_refresh(self):
        self.ingest('feed/1')
        self.command('queue')
        self.command('read')
        self.ingest('feed/1')
        row, = self.command('snapshot')['articles']
        self.assertEqual((row['queued'], row['seen'], row['starred']), (1, 1, 0))
        self.command('unqueue')
        self.assertEqual(self.command('snapshot')['articles'][0]['queued'], 0)
        feed = {'id':'feed/1', 'name':'Test', 'url':self.url}
        with patch.object(app,'feeds',side_effect=lambda:[feed.copy()]):
            app.folder_action('folder-create',['Robotics'])
            folder, = self.command('snapshot')['folders']
            key = str(folder['id'])
            app.folder_action('folder-move',[feed['id'],key])
            app.folder_action('folder-rename',[key,'Research'])
            other = self.command('snapshot')
            self.assertEqual(other['feeds'][0]['folder'], folder['id'])
            self.assertEqual(other['folders'][0]['name'], 'Research')
            app.folder_action('folder-delete',[key])
            final = self.command('snapshot')
            self.assertIsNone(final['feeds'][0]['folder'])
            self.assertEqual(len(final['articles']), 1)

    def test_delete_during_rss_download_cannot_resurrect_article(self):
        self.ingest('feed/1')
        self.item['summary']['content'] += 'edited'
        def concurrent_delete(url, html):
            self.command('delete')
            return 'download finished after deletion', '原文'
        with patch.object(inbox, 'article_text', side_effect=concurrent_delete):
            self.ingest('feed/2')
        self.assertEqual(self.command('snapshot')['articles'], [])
        with inbox.database() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM article_feeds').fetchone()[0], 0)
