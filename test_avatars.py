import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import avatars

# One solid pixel; real ImageIO conversion exercises the PNG cache format.
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6Zd8AAAAASUVORK5CYII=')


class AvatarTests(unittest.TestCase):
    def test_redirect_base_and_link_rel(self):
        page = b'<base href="/theme/"><link rel="shortcut ICON" href="mark.png"><link rel="canonical" href="/"><img src="/site-logo.png">'
        with patch.object(avatars,'get',return_value=('https://site.test/posts/',page)):
            links = avatars.candidates('https://example.test/feed')
        self.assertEqual(links[:3],['https://site.test/theme/mark.png','https://site.test/favicon.ico','https://site.test/site-logo.png'])

    def test_cache_and_offline_retention(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(avatars.services,'DATA',Path(folder)):
            feed = {'url':'https://feeds.test/feed','website':'https://site.test/'}
            with patch.object(avatars,'candidates',return_value=['https://site.test/mark.png']), patch.object(avatars,'get',return_value=('',PNG)) as get:
                path = avatars.resolve(feed)
                self.assertIsNotNone(path)
                self.assertEqual(avatars.resolve(feed), path)
                self.assertEqual(get.call_count,1)
            avatars.os.utime(path,(0,0))
            avatars.os.utime(avatars.cached_file(feed).with_suffix('.checked'),(0,0))
            with patch.object(avatars,'candidates',return_value=[]), patch.object(avatars,'freshrss_copy',return_value=None):
                self.assertEqual(avatars.resolve(feed),path)
                self.assertIsNone(avatars.resolve({'url':'https://unknown.test/'}))
