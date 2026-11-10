"""The page's color rules (AGENTS.md, "The page"): every color is a token in :root with its reason; text is drawn
only in the colors meant for text, never in the border gray; nothing fades text with opacity; and the page builds
its elements from text, never from HTML strings."""
import re

from yt_playlist_maker.config import ROOT

CSS_WITH_COMMENTS = (ROOT / 'static' / 'style.css').read_text()
CSS = re.sub(r'/\*.*?\*/', '', CSS_WITH_COMMENTS, flags=re.S)
JS = (ROOT / 'static' / 'app.js').read_text()
HTML = (ROOT / 'static' / 'index.html').read_text()
TEXT_COLORS = {'--ink', '--paper', '--accent', '--accent-ink', '--good', '--wait', '--bad'}
LITERAL = re.compile(r'#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla)\(|\b(?:gray|grey|silver|darkgray)\b')


def root_block(css: str) -> str:
    return re.search(r':root\s*\{(.*?)\}', css, re.S).group(1)


def test_colors_are_tokens_with_their_reason():
    for line in root_block(CSS_WITH_COMMENTS).strip().splitlines():
        assert re.match(r'\s*--[\w-]+:\s*#[0-9a-f]{3,6};\s*/\*.+\*/', line), line
    outside = CSS.replace(root_block(CSS), '')
    assert not LITERAL.search(outside)
    assert not LITERAL.search(JS)
    assert 'style=' not in HTML


def test_text_is_never_the_border_gray():
    for value in re.findall(r'(?<![-\w])color:\s*([^;]+);', CSS):
        token = re.fullmatch(r'var\((--[\w-]+)\)', value.strip())
        assert token and token.group(1) in TEXT_COLORS, value


def test_nothing_fades_text():
    assert all(float(v) == 1 for v in re.findall(r'opacity:\s*([\d.]+)', CSS))


def test_the_page_builds_elements_from_text():
    assert 'innerHTML' not in JS and 'insertAdjacentHTML' not in JS


def test_code_is_ascii():
    """Non-ASCII text in code is written as an escape (\\u00b7, &middot;), so no look-alike character hides."""
    files = [f for folder in ('yt_playlist_maker', 'static', 'tests', 'experiments')
             for f in (ROOT / folder).rglob('*') if f.suffix in ('.py', '.js', '.css', '.html')]
    assert files
    for path in files:
        text = path.read_text()
        assert text.isascii(), f'{path}: {next(c for c in text if not c.isascii())!r}'
