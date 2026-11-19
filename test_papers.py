import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import inbox
import papers
import websources


class AcademicSubscription(unittest.TestCase):
    def test_direct_abstract_initial_baseline_updates_and_deletion(self):
        home = 'https://example.org/'
        papers_base = 'https://arxiv.org/abs/'
        ids = ['2602.00003', '2602.00001', '2602.00004', '2602.00002']
        broken = set()
        def fetch(url):
            if url == home:
                links = ''.join(f'<a href="{papers_base}{key}v2">Paper</a>' for key in ids)
                return home, ('<title>Author</title><h2>Publications</h2>' + links +
                              '<h2>Teaching</h2><a href="https://arxiv.org/abs/2612.00001">Reading</a>')
            key = url.rsplit('/', 1)[1]
            if key in broken:
                return url, '<p>Download the paper</p>'
            return url, (f'<meta name="citation_title" content="Paper {key}">'
                         f'<meta name="citation_arxiv_id" content="{key}">'
                         '<meta name="citation_date" content="2026/02/01">'
                         '<blockquote class="abstract mathjax"><span>Abstract:</span>Original &amp; complete.</blockquote>')
        with tempfile.TemporaryDirectory() as tmp, patch.object(inbox, 'DB', Path(tmp)/'inbox.sqlite'), patch.object(websources, 'fetch', side_effect=fetch):
            inbox.prepare()
            preview = app.inspect_site(home)
            self.assertEqual((preview['kind'],preview['count']),('papers',4))
            self.assertEqual(preview['items'][0]['url'],papers_base+'2602.00004')
            websources.register(home,'','papers')
            calls = []
            def translate(payload):
                calls.append(payload['messages'][-1]['content'])
                return {'choices':[{'message':{'content':'原始且完整。'}}]}
            def sync():
                return inbox.sync(lambda s:None, None, lambda:translate, websources.refresh)
            self.assertFalse(sync())
            with inbox.database() as db:
                rows=db.execute('select * from articles').fetchall()
            self.assertEqual(len(rows),3)
            self.assertTrue(all(r['summary']=='原始且完整。' and r['body']=='Original & complete.' and not r['discovered'] for r in rows))
            self.assertFalse(sync())
            self.assertEqual(calls, ['Original & complete.']*3)
            inbox.delete_article(rows[0]['id'])
            ids.reverse()
            ids.append('2611.12345')
            broken.add(ids[-1])
            self.assertTrue(sync())
            broken.clear()
            self.assertFalse(sync())
            with inbox.database() as db:
                self.assertEqual(db.execute('select count(*) from articles').fetchone()[0],3)
                self.assertTrue(db.execute("select discovered from articles where title='Paper 2611.12345'").fetchone()[0])
                self.assertIsNone(db.execute('select 1 from articles where id=?',(rows[0]['id'],)).fetchone())

    def test_legacy_paper_retries_without_losing_original_or_flags(self):
        abstract = 'On hardware our method succeeds in 80% of trials versus 26.7% for the baseline.'
        with tempfile.TemporaryDirectory() as tmp, patch.object(inbox,'DB',Path(tmp)/'test.sqlite'):
            inbox.prepare()
            with inbox.database() as db:
                db.execute("DELETE FROM preferences WHERE key='paper_translations_v1'")
                db.execute('INSERT INTO articles(id,title,body,summary,coverage,seen,queued,starred) VALUES(?,?,?,?,?,1,1,1)',
                           ('old','Paper',abstract,abstract,papers.COVERAGE))
            inbox.prepare()
            def generate(answer):
                def complete(payload):
                    self.assertEqual(payload['messages'][1]['content'], abstract)
                    return {'choices':[{'message':{'content':answer}}]}
                return inbox.generate_summaries(lambda s:None, lambda:complete, papers_only=True)
            self.assertTrue(generate('该方法成功率为 80%。'))
            self.assertFalse(generate('真机试验中，该方法的成功率为 80%，基线为 26.7%。'))
            inbox.prepare()
            with inbox.database() as db:
                row = db.execute('SELECT * FROM articles').fetchone()
                self.assertEqual((row['body'],row['seen'],row['queued'],row['starred']), (abstract,1,1,1))
                self.assertIn('基线为 26.7%',row['summary'])
                self.assertFalse(row['problem'])

    def test_pdf_and_version_share_identity(self):
        self.assertEqual(papers.arxiv_url('https://arxiv.org/pdf/2301.01234v3.pdf'),
                         'https://arxiv.org/abs/2301.01234')
        self.assertIsNone(papers.listing('https://example.org/', '<h2>News</h2><a href="https://arxiv.org/abs/2301.01234">Paper</a>'))

    def test_translation_units_and_project_link_placeholder(self):
        self.assertEqual(papers.numeric_values('3D, 2M trajectories, 2,500 steps'),
                         papers.numeric_values('三维，200 万条轨迹，共 2500 步'))
        def complete(payload):
            self.assertEqual(payload['messages'][-1]['content'], 'We use 2 million trajectories.')
            return {'choices':[{'message':{'content':'我们使用了 200 万条轨迹。'}}]}
        self.assertEqual(papers.translate('We use 2 million trajectories. Project website: this https URL.',complete),
                         '我们使用了 200 万条轨迹。')

    def test_retries_sentences_if_model_changes_magnitude(self):
        answers = iter(['模型使用了 20 万条轨迹。测试成功。', '模型使用了 200 万条轨迹。', '测试成功。'])
        def complete(payload):
            return {'choices':[{'message':{'content':next(answers)}}]}
        self.assertEqual(papers.translate('We use 2M trajectories. Testing succeeded.', complete),
                         '模型使用了 200 万条轨迹。测试成功。')
