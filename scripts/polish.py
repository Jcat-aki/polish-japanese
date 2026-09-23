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


# 原文に根拠がなくても書けてしまう前提：出典のない一般化・伝聞、読み手の気持ちの推測、ぼかした体験談
UNSOURCED = re.compile(
    r'一般的に|一般に|多くの(?:人|方|企業|会社|ユーザー|お客様)|ほとんどの(?:人|方|企業|会社)|誰もが|よく知られ(?:て|た)'
    r'|周知の(?:通り|とおり)|ご存じの(?:通り|とおり)|言うまでもなく'
    r'|(?:調査|研究|データ|統計)(?:によると|によれば|では)|と言われて(?:いる|います)|とされて(?:いる|います)|(?:という|との)声'
    r'|(?:お悩み|お困り)の方(?:も|は)(?:多い|少なくない)|(?:悩んで|困って|感じて)いる方(?:も|は)(?:多い|少なくない)'
    r'|ある(?:お客様|企業|会社|ユーザー|方)(?:から|が|は|に)')
# 読み手を見くびる書き方：重要さを自分で宣言する前置きと、決まり文句の締め。
# 「鍵」は実物の鍵と区別できないので含めない。「いかがでしょうか」単独は本当の問いかけがあるので含めない
SELF_IMPORTANCE = re.compile(r'(?:ここで|最も|特に|一番)?(?:重要|大切|大事|肝心)な(?:の|こと|点)は|(?:本質|核心|ポイント|キモ)は')
CLOSING_SUGGESTION = re.compile(r'てみては(?:いかが|どう)でしょうか|いかがでしたか')
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
    # 推敲で根拠のない前提が増えたり消えたりしていないかを見る
    'unsourced': UNSOURCED,
}


# 書き手の作業文脈でしか通じない内部の呼び名（ファイル名・パス・識別子・オプション・PR番号・インラインコード）。
# 日本語を語の一部と見なさないよう ASCII モードで照合する
INTERNAL_NAME = re.compile(r'''
    `(?P<code>[^`\n]+)`                                   # インラインコード
  | ~/[\w./-]+                                            # ホーム以下のパス
  | (?:\.{1,2}/)?[\w-]+(?:/[\w.-]+)+\.\w+                  # 相対パス
  | [\w-]+(?:\.[\w-]+)*\.(?:md|txt|py|sh|json|dic|html?|csv|ya?ml|js|ts|rb|toml|lock|log)\b  # ファイル名
  | (?:\b(?:PR|Issue|issue)\s?)?\#\d+\b                    # PR・Issue番号
  | (?<![\w-])--[a-z][a-z0-9-]*                          # コマンドのオプション
  | \b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b                     # snake_case
  | \b[a-z]+(?:[A-Z][a-z0-9]*)+\b                         # camelCase
''', re.ASCII | re.VERBOSE)
# リンク・URLと、「」『』で引いた例文は、書き手自身の呼び名ではないので対象外にする
LINKS_AND_QUOTES = re.compile(r'!?\[[^\]\n]*\]\([^)\n]*\)|https?://[^\s<>「」『』。]+|「[^「」]*」|『[^『』]*』')
# 初出の直前・直後がこれなら、読み手向けの説明があると見なす
EXPLAINED_BEFORE = ('（', '(')
EXPLAINED_AFTER = ('（', '(', 'とは', 'という', 'って', '：', ':')


def fenced_spans(text):
    return [(a, b) for a, b in protected_spans(text)
            if re.match(r' {0,3}(`{3,}|~{3,})', text[a:b])]


def undefined_terms(text, keep=()):
    """説明なしで使われている内部の呼び名の初出を返す。読み手が知っている語（keep）は除く。"""
    skip = fenced_spans(text) + [m.span() for m in LINKS_AND_QUOTES.finditer(text)]
    seen, result, taken = set(), [], []
    for m in INTERNAL_NAME.finditer(text):
        start, end = m.span('code') if m.group('code') else m.span()
        term = text[start:end]
        if overlaps(*m.span(), skip) or overlaps(start, end, taken) or term in keep:
            continue
        taken.append((start, end))
        if term in seen:
            continue
        seen.add(term)
        before = text[:m.start()].rstrip(' `')
        after = text[m.end():].lstrip(' `')
        if before.endswith(EXPLAINED_BEFORE) or after.startswith(EXPLAINED_AFTER):
            continue
        result.append((start, end))
    return result


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

    def add(rule, start, end, reason, replacement=None, guard=True):
        if guard and overlaps(start, end, guarded):
            return
        findings.append({'rule': rule, 'start': start, 'end': end,
                         'line': text.count('\n', 0, start) + 1,
                         'column': start - text.rfind('\n', 0, start),
                         'text': text[start:end], 'reason': reason, 'replacement': replacement})

    # 内部の呼び名はインラインコードの中も見るため、保護領域による除外（guard）をしない
    for start, end in undefined_terms(text, keep):
        add('undefined-term', start, end,
            '読み手が知らない内部の呼び名かもしれない。初出で何を指すか説明するか、一般的な言葉に置き換える。', guard=False)
    for m in UNSOURCED.finditer(prose):
        add('unsourced-claim', *m.span(),
            '原文に根拠のない一般化・伝聞・読み手の気持ちの推測・ぼかした体験談かもしれない。'
            '根拠や出典が本文にあれば残す。なければ削るか書き手に確認し、もっともらしく補わない。')
    for m in SELF_IMPORTANCE.finditer(prose):
        add('self-declared-importance', *m.span(),
            '重要さを自分で宣言している。前置きを外し、何がなぜ大事かをそのまま書けば読み手は自分で判断できる。')
    for m in CLOSING_SUGGESTION.finditer(prose):
        add('closing-suggestion', *m.span(),
            '読み手に行動を促す決まり文句。勧めたいなら何をなぜ勧めるかを言い切り、不要なら削る。')
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
            'metrics': {'characters': len(text), 'tokens': len(tokens) if analyzer.tagger else None,
                        'style': style_tendency(text)}}


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


SENTENCE = re.compile(r'[^。！？!?\r\n]+[。！？!?]?')
STATUS_LABEL = {'no-mechanical-difference': '機械的な差異なし', 'needs-review': '表現差あり（目で確認が必要）',
                'changed-protected-content': '保護対象が変化（要修正）'}


SHORT_SENTENCE = 20
BULLET = re.compile(r'(?:[-*+]|\d+[.)])\s')


def style_tendency(text):
    """文書全体の書き方の傾向（短い文・一文ごとの改行・箇条書きの多さ）を数える。
    良し悪しの線引きは未校正なので、判定はせず割合だけを返す。"""
    fences = fenced_spans(text)
    counted = bullets = prose_lines = one_sentence_lines = 0
    sentences, offset = [], 0
    for raw in text.splitlines(keepends=True):
        start, offset = offset, offset + len(raw)
        # コードブロック・見出し・表・空行は数えない。HTMLはタグを除いて本文だけを見る
        line = re.sub(r'<[^>]*>', '', raw).strip()
        if overlaps(start, offset, fences) or not line or line.startswith(('#', '|')):
            continue
        counted += 1
        if BULLET.match(line):
            bullets += 1
            continue
        found = [s.strip() for s in SENTENCE.findall(line) if s.strip()]
        prose_lines += 1
        one_sentence_lines += len(found) == 1
        sentences += found
    ratio = lambda part, whole: round(part / whole, 2) if whole else None
    return {'sentences': len(sentences),
            'short_sentence_ratio': ratio(sum(len(s) < SHORT_SENTENCE for s in sentences), len(sentences)),
            'one_sentence_line_ratio': ratio(one_sentence_lines, prose_lines),
            'bullet_line_ratio': ratio(bullets, counted)}


def cell(text):
    # 表のセルに入れるため、HTMLタグ・改行・区切り文字を除く
    text = re.sub(r'<[^>]*>', '', text)
    return re.sub(r'\s+', ' ', text).strip().replace('|', '\\|')


def report(before, after, analyzer, keep=()):
    """修正前後を比べ、何がどう変わったかをMarkdownの評価シートにまとめる。"""
    b_report, a_report = inspect(before, analyzer, keep), inspect(after, analyzer, keep)
    check = verify(before, after, analyzer, keep)
    key = lambda f: (f['rule'], f['text'])
    b_count, a_count = Counter(map(key, b_report['findings'])), Counter(map(key, a_report['findings']))
    resolved, remaining, new = b_count - a_count, b_count & a_count, a_count - b_count

    b_spans = [m.span() for m in SENTENCE.finditer(before)]
    a_spans = [m.span() for m in SENTENCE.finditer(after)]
    rows = []
    matcher = difflib.SequenceMatcher(None, [before[a:b] for a, b in b_spans], [after[a:b] for a, b in a_spans], autojunk=False)
    groups = []
    for op, i, j, k, l in matcher.get_opcodes():
        if op == 'equal':
            continue
        # 隣り合う文の置換は difflib が一塊にするため、文の数が同じなら1文ずつ対応させる
        if op == 'replace' and j - i == l - k:
            groups += [(i + n, i + n + 1, k + n, k + n + 1) for n in range(j - i)]
        else:
            groups.append((i, j, k, l))
    for i, j, k, l in groups:
        old = ''.join(before[a:b] for a, b in b_spans[i:j])
        new_text = ''.join(after[a:b] for a, b in a_spans[k:l])
        if cell(old) == cell(new_text):
            continue
        # この箇所にあった指摘のうち、修正後に消えたものを「対応した指摘」とする
        start = b_spans[i][0] if i < j else None
        end = b_spans[j - 1][1] if i < j else None
        rules = sorted({f['rule'] for f in b_report['findings']
                        if start is not None and start <= f['start'] < end and resolved[key(f)]})
        rows.append((cell(old) or '（なし）', cell(new_text) or '（削除）', ', '.join(rules) or '—'))

    engine = '形態素解析あり（MeCab）' if b_report['engine']['morphology'] else '軽量モード（形態素解析なし）'
    lines = ['# 推敲の評価シート', '', '## 概要', '', '| 項目 | 値 |', '| --- | --- |',
             f'| 文字数 | {len(before)} → {len(after)} |', f'| 変更した文 | {len(rows)} |',
             f'| 指摘（修正前 → 修正後） | {sum(b_count.values())} → {sum(a_count.values())} |',
             f'| 解消 | {sum(resolved.values())} |', f'| 残存 | {sum(remaining.values())} |',
             f'| 新規 | {sum(new.values())} |', f'| 機械照合 | {STATUS_LABEL[check["status"]]} |',
             f'| 診断エンジン | {engine} |', '', '## 文書全体の傾向', '']
    b_style, a_style = style_tendency(before), style_tendency(after)
    pct = lambda v: '—' if v is None else f'{round(v * 100)}%'
    lines += ['| 項目 | 修正前 | 修正後 |', '| --- | --- | --- |',
              f'| 文の数 | {b_style["sentences"]} | {a_style["sentences"]} |']
    for key, label in (('short_sentence_ratio', f'{SHORT_SENTENCE}文字未満の文の割合'),
                       ('one_sentence_line_ratio', '一文だけの行の割合'), ('bullet_line_ratio', '箇条書きの行の割合')):
        lines.append(f'| {label} | {pct(b_style[key])} | {pct(a_style[key])} |')
    lines += ['', '文を細かく切る・一文ごとに改行する・箇条書きに崩す傾向の目安。線引きは未校正のため、良し悪しは判定しない。',
              '', '## 変更箇所', '']
    if rows:
        lines += ['| # | 修正前 | 修正後 | 対応した指摘 |', '| --- | --- | --- | --- |']
        lines += [f'| {n} | {old} | {new_text} | {rules} |' for n, (old, new_text, rules) in enumerate(rows, 1)]
    else:
        lines.append('変更はありません。')
    for title, counter in (('解消した指摘', resolved), ('残った指摘', remaining), ('新たに出た指摘', new)):
        lines += ['', f'## {title}', '']
        lines += [f'- `{rule}` {cell(text)}' + (f'（{n}件）' if n > 1 else '')
                  for (rule, text), n in sorted(counter.items())] or ['なし']
    lines += ['', '## 機械照合', '']
    labels = {'numbers_and_units': '数値・単位', 'protected_regions': '保護領域（コード・引用等）',
              'ascii_terms': '英字の用語', 'named_terms': '固有名詞・指定語'}
    for name, change in check['changes'].items():
        removed = '、'.join(cell(w) for w in change['removed']) or 'なし'
        added = '、'.join(cell(w) for w in change['added']) or 'なし'
        lines.append(f'- {labels[name]}: 消えた {removed} ／ 増えた {added}')
    for item in check['review']:
        lines.append(f'- 要確認（{", ".join(item["categories"])}）: {cell(item["before"])} → {cell(item["after"])}')
    if not check['changes'] and not check['review']:
        lines.append('- 数値・固有名詞・否定や確度の表現に機械的な差異はありません。')
    lines += ['', '機械照合は意味が同じであることを保証しません。主語と数値の対応や因果の変化は目で確認してください。']
    return '\n'.join(lines) + '\n'


def read(path):
    data = sys.stdin.buffer.read() if path == '-' else Path(path).read_bytes()
    text = data.decode('utf-8')
    if '\x00' in text or len(data) > 2_000_000:
        raise ValueError('NUL文字を含まない2MB以下のUTF-8テキストを指定してください。')
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('analyze', 'fix', 'verify', 'report'):
        p = commands.add_parser(command)
        p.add_argument('input')
        if command in ('verify', 'report'):
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
        elif args.command == 'report':
            # 評価シートは人が読むものなので、JSONではなくMarkdownで出力する
            if args.input == '-' and args.candidate == '-':
                raise ValueError('標準入力は片方の文書だけに使えます。')
            print(report(text, read(args.candidate), analyzer, args.keep), end='')
            return 0
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
