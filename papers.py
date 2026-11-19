"""Read arXiv papers linked from a public academic publication list."""
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit


COVERAGE = '论文原摘要'
TRANSLATOR = ('你是科研论文译者。把提供的作者摘要逐句忠实译成自然的简体中文，完整传达每句话。'
              '保留方法名、缩写、数据集、数字、单位、比较基线、实验条件与局限，'
              '区分仿真与真机结果，保留“约”“最多”“超过”等限定。原文中的阿拉伯数字照写。'
              '不要添加结论或解释。只输出译文。')
SECTION = re.compile(r'^(?:(?:selected|recent|refereed)\s+)?(?:publications?|papers?|research(?:\s+papers)?|论文|研究成果|发表论文)(?:\s*[(（:].*)?$', re.I)
HEADINGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'heading'}


def numeric_values(text):
    # Accept common equivalent notation, e.g. 2M / 200万 and 3D / 三维.
    text = re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))', '', text.replace('三维','3D').replace('二维','2D'))
    scales = {'K': 1000, 'M': 10**6, 'B': 10**9, 'thousand': 1000, 'million': 10**6,
              'billion': 10**9, '千': 1000, '万': 10**4, '亿': 10**8}
    values = set()
    for match in re.finditer(r'(\d+(?:\.\d+)?)(?:([KMB])(?![a-zA-Z])|\s*(thousand|million|billion|[千万亿]))?', text):
        scale = match[2] or match[3]
        values.add(Decimal(match[1]) * scales.get(scale, 1))
    return values


def translate(abstract, complete):
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z$“"])', abstract.strip())
    if not sentences[0]:
        raise ValueError('缺少论文原摘要')
    groups = []
    for sentence in sentences:
        if re.fullmatch(r'(?:Project website|Website|Videos?|For more details)[^.!?]*this https URL\s*[.!]?', sentence, re.I):
            continue
        if groups and len(groups[-1]) < 2 and sum(map(len, groups[-1])) + len(sentence) < 550:
            groups[-1].append(sentence)
        else:
            groups.append([sentence])
    if not groups:
        raise ValueError('缺少可翻译的论文正文')
    glossary = {'compliance':'柔顺性', 'compliant':'柔顺的', 'wrench':'力和力矩',
                'bimanual':'双臂或双手', 'robot-free':'无需机器人', 'policy':'策略',
                'policies':'策略', 'embodiment':'机器人本体', 'long-horizon':'长时程',
                'replay buffer':'经验回放缓冲区', 'rollout':'执行试验或轨迹'}
    def render(source):
        terms = [f'{term} 译作“{meaning}”' for term, meaning in glossary.items() if term in source.lower()]
        instruction = TRANSLATOR
        if terms:
            instruction += ' 注意词义：' + '，'.join(terms) + '。只翻译用户提供的英文。'
        response = complete(dict(messages=[dict(role='system', content=instruction),
                                           dict(role='user', content=source)],
                                 temperature=0, max_tokens=2048, stream=False))
        choice = response['choices'][0]
        result = (choice['message'].get('content') or '').strip()
        if (choice.get('finish_reason') == 'length' or not re.search(r'[\u4e00-\u9fff]', result)
                or any(marker in result for marker in ('本文术语：','注意词义：','译作“'))
                or not numeric_values(source) <= numeric_values(result)):
            raise ValueError('论文译文不完整，等待重试')
        return result

    paragraphs = []
    for group in groups:
        try:
            passage = render(' '.join(group))
        except ValueError:
            if len(group) == 1:
                raise
            passage = ''.join(render(sentence) for sentence in group)
        if paragraphs and len(paragraphs[-1]) + len(passage) < 340:
            paragraphs[-1] += passage
        else:
            paragraphs.append(passage)
    return '\n\n'.join(paragraphs)


def arxiv_url(url):
    parts = urlsplit(url)
    if parts.hostname not in ('arxiv.org', 'www.arxiv.org', 'export.arxiv.org'):
        return None
    match = re.fullmatch(r'/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5})(?:v[0-9]+)?(?:\.pdf)?/?', parts.path)
    return 'https://arxiv.org/abs/' + match[1] if match else None


class PublicationList(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base = base
        self.heading = ''
        self.heading_text = []
        self.level = None
        self.links = {}
        self.title = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self.in_title = True
        if tag in HEADINGS:
            self.heading = tag
            self.heading_text = []
        if tag == 'a' and self.level is not None and not self.heading:
            href = dict(attrs).get('href') or ''
            url = arxiv_url(urljoin(self.base, href))
            if url:
                self.links[url] = {'url': url, 'title': 'arXiv ' + url.rsplit('/', 1)[1]}

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        if tag == self.heading:
            text = ' '.join(''.join(self.heading_text).split())
            depth = 2 if tag == 'heading' else int(tag[1])
            if SECTION.fullmatch(text):
                self.level = depth
            elif self.level is not None and depth <= self.level:
                self.level = None
            self.heading = ''

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if self.heading:
            self.heading_text.append(data)


def listing(base, markup):
    doc = PublicationList(base)
    doc.feed(markup)
    doc.close()
    if not doc.links:
        return None
    # The date and submission sequence in modern arXiv IDs give a stable order,
    # even if the author's homepage puts featured papers first.
    def sequence(item):
        return tuple(map(int, item['url'].rsplit('/', 1)[1].split('.')))
    items = sorted(doc.links.values(), key=sequence, reverse=True)
    name = ' '.join(''.join(doc.title).split()) or urlsplit(base).hostname
    return dict(kind='papers', name=name, url=base, items=items)


class AbstractPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.abstract = []
        self.inside = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            self.meta[attrs.get('name')] = attrs.get('content') or ''
        if tag == 'blockquote' and 'abstract' in (attrs.get('class') or '').split():
            self.inside = True

    def handle_endtag(self, tag):
        if tag == 'blockquote':
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.abstract.append(data)


def metadata(url, markup):
    doc = AbstractPage()
    doc.feed(markup)
    doc.close()
    title = ' '.join(doc.meta.get('citation_title', '').split())
    abstract = re.sub(r'^Abstract:\s*', '', ' '.join(''.join(doc.abstract).split()), flags=re.I)
    if not title or not abstract or doc.meta.get('citation_arxiv_id', '').split('v')[0] != url.rsplit('/', 1)[1]:
        raise ValueError('未取得该论文的作者摘要')
    date = doc.meta.get('citation_date', '')
    published = int(datetime.strptime(date, '%Y/%m/%d').replace(tzinfo=timezone.utc).timestamp()) if date else 0
    return title, abstract, published
