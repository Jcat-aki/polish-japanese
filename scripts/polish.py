#!/usr/bin/env python3
"""Japanese prose diagnostics and revision checks; not an AI detector."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from dataclasses import dataclass
import difflib
import json
import os
from pathlib import Path
import re
import sys


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    surface: str
    pos: tuple[str, ...]


def overlaps(start, end, spans):
    return any(start < b and a < end for a, b in spans)


def protected_spans(text):
    """Conservative Markdown subset; not a complete CommonMark parser."""
    spans, offset, fence, fence_start = [], 0, None, 0
    front = re.match(r'\A\ufeff?---\r?\n.*?\r?\n(?:---|\.\.\.)[^\S\n]*(?:\n|$)', text, re.S)
    if front:
        spans.append(front.span())
    for line in text.splitlines(keepends=True):
        m = re.match(r' {0,3}(`{3,}|~{3,})(.*)', line)
        if fence:
            if m and m[1][0] == fence[0] and len(m[1]) >= len(fence) and not m[2].strip():
                spans.append((fence_start, offset + len(line)))
                fence = None
        elif m:
            fence, fence_start = m[1], offset
        elif re.match(r'(?: {0,3}>| {4}|\t)', line) or '|' in line:
            spans.append((offset, offset + len(line)))
        offset += len(line)
    if fence:
        spans.append((fence_start, len(text)))
    patterns = [
        r'(`+)(?!`)[\s\S]*?(?<!`)\1(?!`)',
        r'「[^「」]*」|『[^『』]*』|“[^“”]*”|"[^"\r\n]*"',
        r'!?\[[^\]\n]*\]\([^\n]*?\)|!?\[[^\]\n]*\]\[[^\]\n]*\]',
        r'(?m)^ {0,3}\[[^\]\n]+\]:[^\n]*',
        r'<!--[\s\S]*?-->|<([A-Za-z][\w:-]*)\b[^>]*>[\s\S]*?</\1\s*>|<[^>\n]+>',
        r'https?://[^\s<>「」『』。]+',
        r'\$\$[\s\S]*?\$\$|\$[^$\n]+\$',
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            if not overlaps(*m.span(), spans):
                spans.append(m.span())
    merged = []
    for a, b in sorted(spans):
        if merged and a < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def mask(text, spans):
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            if chars[i] not in '\r\n':
                chars[i] = ' '
    return ''.join(chars)


class Analyzer:
    def __init__(self, dic=None, lightweight=False, user_dic=None):
        self.tagger = None
        self.info = {'mode': 'lightweight', 'morphology': False}
        if lightweight and user_dic:
            raise ValueError('--lightweight と --user-dic は同時に使えません。')
        if lightweight:
            return
        if not dic:
            raise ValueError('辞書を --dic または NEOLOGD_DIC で指定してください。辞書なしなら --lightweight を明示してください。')
        path = str(Path(dic).expanduser().resolve())
        if not (Path(path) / 'sys.dic').is_file():
            raise ValueError('指定ディレクトリにビルド済み sys.dic がありません。')
        if any(c in path for c in ['"', "'", '\n', '\r', '\\']):
            raise ValueError('辞書パスには引用符・改行・バックスラッシュを含めないでください。')
        try:
            import MeCab
        except ImportError as e:
            raise ValueError('mecab-python3 をインストールしてください。') from e
        options = f'-r "{os.devnull}" -d "{path}"'
        if user_dic:
            user_path = str(Path(user_dic).expanduser().resolve())
            if not Path(user_path).is_file():
                raise ValueError('指定した追加辞書ファイルがありません。')
            if any(c in user_path for c in ['"', "'", '\n', '\r', '\\', ',']):
                raise ValueError('追加辞書パスに引用符・改行・バックスラッシュ・カンマは使えません。')
            options += f' -u "{user_path}"'
        self.tagger = MeCab.Tagger(options)
        info = self.tagger.dictionary_info()
        if info is None or info.charset.lower().replace('-', '') != 'utf8':
            raise ValueError('UTF-8の辞書が必要です。')
        self.info = {'mode': 'mecab', 'morphology': True, 'dictionary': info.filename,
                     'dictionary_entries': info.size, 'dictionary_version': info.version,
                     'provenance': 'user-supplied; 辞書の配布元・版は利用者が確認'}
        dictionaries = []
        while info:
            if info.charset.lower().replace('-', '') != 'utf8':
                raise ValueError('追加辞書もUTF-8である必要があります。')
            dictionaries.append({'filename': info.filename, 'entries': info.size,
                                 'type': info.type, 'version': info.version})
            info = info.next
        self.info['dictionaries'] = dictionaries

    def tokens(self, text):
        if not self.tagger:
            return []
        result, cursor = [], 0
        node = self.tagger.parseToNode(text)
        while node:
            surface = node.surface
            if surface:
                start = text.find(surface, cursor)
                if start < 0 or text[cursor:start].strip():
                    raise ValueError('形態素の位置と原文が一致しません。')
                pos = tuple(next(csv.reader([node.feature])))[:4]
                result.append(Token(start, start + len(surface), surface, pos))
                cursor = start + len(surface)
            node = node.next
        if text[cursor:].strip():
            raise ValueError('形態素解析が本文の途中で終了しました。')
        return result


ABSTRACT = re.compile(r'最適化|効率化|高度化|知見|共有|活用|推進|強化|向上|実現|確保|促進|価値創出|課題解決|相乗効果|多角的|包括的')
NUMBER = re.compile(r'[+\-−]?[0-9０-９]+(?:[,.．，][0-9０-９]+)*(?:[ \t]*(?:億円|万円|千円|円|ms|秒|分|時間|日|年|月|%|％|kg|GB|MB|人|件|回|倍|個|台))?(?:以上|以下|未満|以内|超|程度|前後)?')
ASCII_TERM = re.compile(r'[A-Za-z][A-Za-z0-9]*(?:[_.+/#-][A-Za-z0-9]+)*')
MODALITY = {
    'negation': re.compile(r'できない|できません|しない|しません|ではない|ではありません|なかった|ませんでした|禁止|不要|ない|なく|ません|ずに|ぬ'),
    'possibility': re.compile(r'できます|できる|可能性|可能|かもしれ(?:ない|ません)'),
    'uncertainty': re.compile(r'見込|推測|推定|おそらく|恐らく|と考え|と思|だろう|でしょう'),
    'obligation': re.compile(r'必須|義務|必要|なければ|べき|しなくては'),
    'recommendation': re.compile(r'推奨|おすすめ|お勧め'),
    'scope': re.compile(r'すべて|全て|必ず|一部|場合|限り|のみ|だけ|原則|ただし'),
}


def proper_terms(tokens):
    return {t.surface for t in tokens if t.pos[:2] == ('名詞', '固有名詞')}


def term_spans(text, names):
    return [m.span() for word in sorted(set(names)) if word for m in re.finditer(re.escape(word), text)]


def inspect(text, analyzer, keep=()):
    protected = protected_spans(text)
    prose = mask(text, protected)
    tokens = analyzer.tokens(prose)
    names = proper_terms(tokens) | set(keep)
    guarded = protected + term_spans(text, names)
    findings = []

    def add(rule, start, end, reason, replacement=None):
        if overlaps(start, end, guarded):
            return
        findings.append({'rule': rule, 'start': start, 'end': end,
                         'line': text.count('\n', 0, start) + 1,
                         'column': start - text.rfind('\n', 0, start),
                         'text': text[start:end], 'reason': reason, 'replacement': replacement})

    for m in re.finditer(r'革命的|圧倒的|驚異的|画期的|究極|無限の可能性|新たな地平', prose):
        add('inflated-language', *m.span(), '内容に見合う強さか確認する。裏づけがなければ普通の言葉へ戻す。')
    for m in re.finditer(r'まず最初に', prose):
        prefix = prose[:m.start()].rstrip()
        opening = not prefix or prefix[-1] in '。！？!?' or prose[m.start()-1:m.start()] == '\n'
        add('redundant-opening', *m.span(), '順序の説明が重複している。', 'まず' if opening else None)
    for m in re.finditer(r'することができます(?=[。！？!?\r\n]|$)', prose):
        prior = next((t for t in reversed(tokens) if t.end == m.start()), None)
        safe = prior and prior.pos[0] == '名詞' and any('サ変' in p for p in prior.pos)
        add('roundabout-capability', *m.span(), '可能という意味を保ったまま短くできるか確認する。', 'できます' if safe else None)
    for m in re.finditer(r'[^。！？!?\r\n]+[。！？!?]?', prose):
        relevant = [t for t in tokens if m.start() <= t.start and t.end <= m.end() and not overlaps(t.start, t.end, guarded)]
        words = [t for t in relevant if t.pos and t.pos[0] not in ('補助記号', '記号')]
        nouns = [t for t in words if t.pos[0] == '名詞']
        abstract = [a for a in ABSTRACT.finditer(m[0]) if not overlaps(m.start()+a.start(), m.start()+a.end(), guarded)]
        if len(abstract) >= 3 and (not analyzer.tagger or len(nouns) >= 4):
            a = abstract[0]
            add('abstract-stack', m.start()+a.start(), m.start()+a.end(), '同じ文に抽象語が複数ある。誰が何をするのか、原文の情報で言い直せるか確認する。')
        if len(words) >= 12 and len(nouns) / len(words) >= .60:
            add('noun-heavy', nouns[0].start, nouns[0].end, '名詞の割合が高い。専門用語は残し、主体と動作が埋もれていないか確認する。')
        if len(m[0].strip()) >= 100:
            pos = m.start() + len(m[0]) - len(m[0].lstrip())
            add('long-sentence', pos, min(pos + 12, m.end()), '条件や主張が詰まっていないか確認する。長さだけで分割しない。')
    chain = []
    for t in tokens + [Token(len(text), len(text), '', ())]:
        eligible = t.pos and (t.pos[0] == '名詞' or (t.surface == 'の' and t.pos[0] == '助詞'))
        if eligible:
            if chain and (prose[chain[-1].end:t.start].strip() or '\n' in prose[chain[-1].end:t.start]):
                chain = []
            chain.append(t)
        else:
            if sum(x.surface == 'の' for x in chain) >= 3:
                # Names can participate in a chain; flag the connector, never rewrite a name.
                connector = next(x for x in chain if x.surface == 'の')
                add('noun-chain', connector.start, connector.end, '「の」で名詞がつながっている。固有名詞は残し、動作や文の主役を明確にできるか確認する。')
            chain = []
    return {'schema_version': 1, 'engine': analyzer.info,
            'limits': ['指摘は推敲の手掛かりでありAI生成確率ではない。', '意見や比喩の妥当性・文脈の意味はエージェントが確認する。'],
            'proper_noun_candidates': sorted(names), 'findings': sorted(findings, key=lambda f: (f['start'], f['rule'])),
            'protected_regions': protected,
            'metrics': {'characters': len(text), 'tokens': len(tokens) if analyzer.tagger else None}}


def delta(before, after):
    return {'removed': dict(before - after), 'added': dict(after - before)}


def verify(before, after, analyzer, keep=()):
    b_spans, a_spans = protected_spans(before), protected_spans(after)
    b_plain, a_plain = mask(before, b_spans), mask(after, a_spans)
    names = proper_terms(analyzer.tokens(b_plain)) | proper_terms(analyzer.tokens(a_plain)) | set(keep)
    checks = {
        'numbers_and_units': delta(Counter(NUMBER.findall(before)), Counter(NUMBER.findall(after))),
        'protected_regions': delta(Counter(before[a:b] for a, b in b_spans), Counter(after[a:b] for a, b in a_spans)),
        'ascii_terms': delta(Counter(ASCII_TERM.findall(b_plain)), Counter(ASCII_TERM.findall(a_plain))),
        'named_terms': delta(Counter({w: before.count(w) for w in names}), Counter({w: after.count(w) for w in names})),
    }
    checks = {k: v for k, v in checks.items() if v['removed'] or v['added']}
    review = []
    b_sent = re.findall(r'[^。！？!?\r\n]+[。！？!?]?|\r?\n', b_plain)
    a_sent = re.findall(r'[^。！？!?\r\n]+[。！？!?]?|\r?\n', a_plain)
    for op, i, j, k, l in difflib.SequenceMatcher(None, b_sent, a_sent, autojunk=False).get_opcodes():
        if op == 'equal':
            continue
        old, new = ''.join(b_sent[i:j]), ''.join(a_sent[k:l])
        changed = [c for c, p in MODALITY.items() if Counter(p.findall(old)) != Counter(p.findall(new))]
        if changed:
            review.append({'categories': changed, 'before': old, 'after': new})
    return {'schema_version': 1, 'engine': analyzer.info,
            'status': 'changed-protected-content' if checks else 'needs-review' if review else 'no-mechanical-difference',
            'changes': checks, 'review': review, 'semantic_review_required': True,
            'limits': '語の出現回数が同じでも主語・数値の対応や因果が変わることがある。意味同一性は保証しない。',
            'diff': ''.join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile='before', tofile='after'))}


def fix(text, analyzer, keep=()):
    report = inspect(text, analyzer, keep)
    edits = [f for f in report['findings'] if f['replacement'] is not None]
    candidate = text
    for f in reversed(edits):
        candidate = candidate[:f['start']] + f['replacement'] + candidate[f['end']:]
    check = verify(text, candidate, analyzer, keep)
    if check['changes'] or check['review']:
        raise ValueError('定型修正で保護対象または確度表現の変化を検出したため、候補を保存しません。')
    return candidate, {'applied': edits, 'verification': check, 'remaining': inspect(candidate, analyzer, keep)['findings']}


def read(path):
    data = sys.stdin.buffer.read() if path == '-' else Path(path).read_bytes()
    text = data.decode('utf-8')
    if '\x00' in text or len(data) > 2_000_000:
        raise ValueError('NUL文字を含まない2MB以下のUTF-8テキストを指定してください。')
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('analyze', 'fix', 'verify'):
        p = commands.add_parser(command)
        p.add_argument('input')
        if command == 'verify':
            p.add_argument('candidate')
        if command == 'fix':
            p.add_argument('--output', required=True, help='新規ファイル。既存ファイルは上書きしない')
        mode = p.add_mutually_exclusive_group()
        mode.add_argument('--dic')
        mode.add_argument('--lightweight', action='store_true')
        p.add_argument('--user-dic', help='UniDicに追加するビルド済みNEologdユーザー辞書')
        p.add_argument('--keep', action='append', default=[])
    args = parser.parse_args(argv)
    try:
        analyzer = Analyzer(args.dic or os.environ.get('NEOLOGD_DIC'), args.lightweight, args.user_dic)
        text, code = read(args.input), 0
        if args.command == 'analyze':
            result = inspect(text, analyzer, args.keep)
        elif args.command == 'verify':
            if args.input == '-' and args.candidate == '-':
                raise ValueError('標準入力は片方の文書だけに使えます。')
            result = verify(text, read(args.candidate), analyzer, args.keep)
            code = 1 if result['changes'] else 3 if result['review'] else 0
        else:
            candidate, result = fix(text, analyzer, args.keep)
            with open(args.output, 'xb') as output:
                output.write(candidate.encode('utf-8'))
            result['output'] = str(Path(args.output).resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    except (OSError, UnicodeError, ValueError, RuntimeError) as e:
        print(json.dumps({'error': str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
