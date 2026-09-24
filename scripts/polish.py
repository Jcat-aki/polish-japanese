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


def refine_proper(tokens, base_pos_of):
    """追加辞書（NEologd等）が固有名詞とした語を、基本辞書だけの解析で見直す。
    基本辞書がその語を1語の固有名詞以外（普通名詞・連体詞など）と読むなら、一般語として扱う。
    基本辞書が複数の語に分ける語（ピックゴー、東京スカイツリー）と英字の語は、追加辞書の判定を残す。"""
    result = []
    for token in tokens:
        base = base_pos_of(token.surface) if token.pos[:2] == ('名詞', '固有名詞') and not token.surface.isascii() else None
        if base and len(base) == 1 and base[0][:2] != ('名詞', '固有名詞'):
            token = Token(token.start, token.end, token.surface, base[0])
        result.append(token)
    return result


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
        # 追加辞書は複数指定できる（MeCabにはカンマ区切りで渡す）
        user_paths = []
        for user in ([user_dic] if isinstance(user_dic, (str, os.PathLike)) else user_dic or []):
            user_path = str(Path(user).expanduser().resolve())
            if not Path(user_path).is_file():
                raise ValueError('指定した追加辞書ファイルがありません。')
            if any(c in user_path for c in ['"', "'", '\n', '\r', '\\', ',']):
                raise ValueError('追加辞書パスに引用符・改行・バックスラッシュ・カンマは使えません。')
            user_paths.append(user_path)
        if user_paths:
            options += f' -u "{",".join(user_paths)}"'
        self.tagger = MeCab.Tagger(options)
        # 追加辞書があるときは、固有名詞の判定を見直すために基本辞書だけの解析器も持つ
        self.base_tagger = MeCab.Tagger(f'-r "{os.devnull}" -d "{path}"') if user_paths else None
        self._base_cache = {}
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
        return refine_proper(result, self._base_pos) if self.base_tagger else result

    def _base_pos(self, surface):
        if surface not in self._base_cache:
            poses, node = [], self.base_tagger.parseToNode(surface)
            while node:
                if node.surface:
                    poses.append(tuple(next(csv.reader([node.feature])))[:4])
                node = node.next
            self._base_cache[surface] = poses
        return self._base_cache[surface]


# 原文に根拠がなくても書けてしまう前提：出典のない一般化・伝聞、読み手の気持ちの推測、ぼかした体験談
UNSOURCED = re.compile(
    r'一般的に|一般に|多くの(?:人|方|企業|会社|ユーザー|お客様)|ほとんどの(?:人|方|企業|会社)|誰もが|よく知られ(?:て|た)'
    r'|周知の(?:通り|とおり)|ご存じの(?:通り|とおり)|言うまでもなく'
    r'|(?:調査|研究|データ|統計)(?:によると|によれば|では)|(?:という|との)声'
    # 誰が言っているのかを隠した言い方。「と考えられます」（書き手自身の推測）や「求められました」（実際の出来事）は含めない
    r'|(?:として|と)語られ(?:る|ます|て(?:いる|います))|と言われ(?:る|ます|て(?:いる|います))|とされ(?:る|ます|て(?:いる|います))'
    r'|と考えられて(?:いる|います)|が求められて(?:いる|います)|(?:注目|期待)されて(?:いる|います)'
    r'|(?:お悩み|お困り)の方(?:も|は)(?:多い|少なくない)|(?:悩んで|困って|感じて)いる方(?:も|は)(?:多い|少なくない)'
    r'|ある(?:お客様|企業|会社|ユーザー|方)(?:から|が|は|に)')
# 読み手を見くびる書き方：重要さを自分で宣言する前置きと、決まり文句の締め。
# 「鍵」は実物の鍵と区別できないので含めない。「いかがでしょうか」単独は本当の問いかけがあるので含めない
SELF_IMPORTANCE = re.compile(
    r'(?:ここで|最も|特に|一番)?(?:重要|大切|大事|肝心)な(?:の|こと|点)は|(?:本質|核心|ポイント|キモ)は'
    # 直前の内容を指して重要さを言い切る・本題や核心の在りかを宣言する形
    r'|(?:これ|それ)が(?:最も|一番|特に)?(?:重要|大切|大事)(?:です|だ)|ここからが本題'
    r'|ここに、?[^。、]{0,15}?(?:核心|本質|突破口)があ(?:る|ります)'
    # 「〜が」で受ける形・過去や変化の形（「重要だったのが」「大切になるのは」）と「鍵を握るのは」
    r'|(?:重要|大切|大事|肝心)(?:だった|になる|になった|となる|となった)の(?:が|は)|(?:重要|大切|大事|肝心)なのが'
    r'|(?:鍵|カギ)(?:を握る|となる)のは')
CLOSING_SUGGESTION = re.compile(r'てみては(?:いかが|どう)でしょうか|いかがでしたか')
# 程度を強める語。同じ文に数字がなければ、どのくらいかは書き手しか知らない
VAGUE_DEGREE = re.compile(r'非常に|とても|大幅に|劇的に|格段に|著しく|かなり|大きく')
# 文の型（stop-ai-slop-jp の観点を参考に独自に定義）。
# 「AではなくB」は名詞を選ぶだけの普通の用法（「雨ではなく晴れ」）が多いので、節をつなぐ形と「単なる／であって」の形だけを見る
# 「だけではなく」「のみではなく」は追加（〜だけでなく〜も）、「容易ではなく」などは形容動詞の否定なので除く
NOT_CONTRAST_BEFORE = ''.join(f'(?<!{w})' for w in ('だけ', 'のみ', '容易', '簡単', '単純', '平坦', '一様', '十分', '得意', '自明', '一筋縄', '楽'))
BINARY_CONTRAST = re.compile(r'(?:単なる|ただの)[^。、]{0,20}?ではなく、?|[^。、「」は]{1,20}?であって[^。、]{1,10}?ではない|'
                             + NOT_CONTRAST_BEFORE + r'ではなく、')
NEGATIVE_LISTING = re.compile(r'でもない、[^。]{1,20}?でもない')
# モノや抽象が人の動作をする言い方
FALSE_AGENCY = re.compile(r'(?:データ|数字|数値|結果|歴史|事実|経験|現実)(?:が|は)(?:示して|物語って|語って|教えてくれ)'
                          r'|浮き彫りに(?:な|し)|醸成され|結実し')
# 記号と語彙（stop-ai-slop-jp の観点を参考に独自に定義）
DASH = re.compile(r'——|──|―{2,}|—')
DECORATIVE_EMOJI = re.compile('[\U0001F680\U0001F3AF\u2728\U0001F4A1\U0001F525\U0001F449\u2705\U0001F4CC\U0001F64C\U0001F4AA\U0001F389]')
KATAKANA_METAPHOR = re.compile(r'(?:思考|考え方|マインド|脳|人生|習慣|キャリア|仕事|働き方)(?:の|を)(?:OS|アップデート|ハック|インストール|リファクタリング)')
PET_WORD = re.compile(r'解像度(?:が|を)(?:高|上げ|低)|解像度の高い|手触り感?|泥臭さ|熱量|営み|腹落ち')
ACADEMIC_SELF = re.compile(r'本稿|本記事|本論文|筆者')
# 時代を持ち出す決まった書き出し。「江戸時代において」「学生時代に」のような具体的な時代は含めない
ERA_OPENER = re.compile(r'(?:進化|発展|変化|普及|進歩)(?:が|の)(?:目覚ましい|著しい|激しい|進む|速い)(?:この|今の|現在の)?(?:時代|昨今|現代|世の中)(?:において)?'
                        r'|(?:生成AI|AI|DX)時代(?:において|の今|だからこそ)|現代社会において')
# 伝聞の形。言った人や発言の引用が同じ文にあれば、根拠のない前提として扱わない
HEARSAY = re.compile(r'と言われ|とされ|(?:として|と)語られ')
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


# 指摘の分類。強調・誇張は説得のために意図して選ぶこともあるので、AIに多い型と混ぜずに数える
CATEGORY_LABELS = {'context': '文脈の漏れ・根拠のない前提', 'ai-pattern': 'AIに多い型',
                   'emphasis': '強調・誇張（意図的なら残してよい）', 'readability': '読みやすさ'}
RULE_CATEGORY = {
    'undefined-term': 'context', 'unsourced-claim': 'context',
    'binary-contrast': 'ai-pattern', 'negative-listing': 'ai-pattern', 'false-agency': 'ai-pattern',
    'symbol-artifact': 'ai-pattern', 'katakana-metaphor': 'ai-pattern', 'pet-word': 'ai-pattern',
    'academic-self': 'ai-pattern', 'closing-suggestion': 'ai-pattern', 'era-opener': 'ai-pattern',
    'inflated-language': 'emphasis', 'self-declared-importance': 'emphasis', 'vague-degree': 'emphasis',
    'noun-chain': 'readability', 'noun-heavy': 'readability', 'long-sentence': 'readability',
    'abstract-stack': 'readability', 'redundant-opening': 'readability', 'roundabout-capability': 'readability',
}


def is_proper(token):
    return token.pos[:2] == ('名詞', '固有名詞')


def proper_terms(tokens):
    return {t.surface for t in tokens if is_proper(t)}


def named_counts(text, tokens, keep):
    """固有名詞は辞書が固有名詞と判定した位置だけを数え、keepの語は文字列として数える。
    （「としての」の「して」のように、別の位置で固有名詞と判定された文字列を全出現で数えないため）"""
    keep = set(keep)
    # 英字の語は ascii_terms で語ごとに照合するので、辞書が切った断片（「st」「op」など）はここで数えない
    counts = Counter(t.surface for t in tokens if is_proper(t) and t.surface not in keep and not t.surface.isascii())
    counts.update({w: text.count(w) for w in keep if w})
    return counts


def term_spans(text, names):
    return [m.span() for word in sorted(set(names)) if word for m in re.finditer(re.escape(word), text)]


def inspect(text, analyzer, keep=()):
    protected = protected_spans(text)
    prose = mask(text, protected)
    tokens = analyzer.tokens(prose)
    names = proper_terms(tokens) | set(keep)
    # 固有名詞は判定された位置だけを守る。keepの語はどこに出ても守る
    name_spans = [(t.start, t.end) for t in tokens if is_proper(t)] + term_spans(text, keep)
    guarded = protected + name_spans
    findings = []

    def add(rule, start, end, reason, replacement=None, guard=True):
        # コード・引用などは少しでも重なれば除外する。固有名詞とkeepの語は、指摘がその中に収まるときだけ除外する
        # （NEologdは「企業」「お客様」のような一般語も固有名詞とするため、重なりだけで消すと「多くの企業」が消える）
        if guard and (overlaps(start, end, protected) or any(a <= start and end <= b for a, b in name_spans)):
            return
        findings.append({'rule': rule, 'category': RULE_CATEGORY[rule], 'start': start, 'end': end,
                         'line': text.count('\n', 0, start) + 1,
                         'column': start - text.rfind('\n', 0, start),
                         'text': text[start:end], 'reason': reason, 'replacement': replacement})

    # 内部の呼び名はインラインコードの中も見るため、保護領域による除外（guard）をしない
    for start, end in undefined_terms(text, keep):
        add('undefined-term', start, end,
            '読み手が知らない内部の呼び名かもしれない。初出で何を指すか説明するか、一般的な言葉に置き換える。', guard=False)
    for m in UNSOURCED.finditer(prose):
        if HEARSAY.match(m[0]):
            # 言った人が書かれている（「上司から」）、または発言そのものを引いている（「…」と言われる）なら、隠した言い方ではない
            before = text[max(text.rfind('。', 0, m.start()), text.rfind('\n', 0, m.start())) + 1:m.start()]
            if before.rstrip().endswith('」') or 'から' in before:
                continue
        add('unsourced-claim', *m.span(),
            '原文に根拠のない一般化・伝聞・読み手の気持ちの推測・ぼかした体験談かもしれない。'
            '根拠や出典が本文にあれば残す。なければ削るか書き手に確認し、もっともらしく補わない。')
    for sentence in SENTENCE.finditer(prose):
        if NUMBER.search(sentence[0]):
            continue
        for m in VAGUE_DEGREE.finditer(sentence[0]):
            add('vague-degree', sentence.start() + m.start(), sentence.start() + m.end(),
                'どのくらいかが数字や具体例で書かれていない。中身は書き手しか知らないので補わず、書き手に尋ねる。')
    for m in BINARY_CONTRAST.finditer(prose):
        add('binary-contrast', *m.span(), '「AではなくB」の対比で主張を立てている。Aが誰も言っていない想定なら、Bを直接書く。')
    for m in NEGATIVE_LISTING.finditer(prose):
        add('negative-listing', *m.span(), '「Aでもない、Bでもない」と否定を重ねて答えを引き延ばしている。答えを先に書く。')
    for m in FALSE_AGENCY.finditer(prose):
        add('false-agency', *m.span(), 'モノや抽象が人の動作をしている。誰が何を見て、何をしたのかに書き換える。')
    for m in DASH.finditer(prose):
        add('symbol-artifact', *m.span(), 'ダッシュでつないでいる。読点・コロン・改行で区切るか、文を分ける。')
    for m in DECORATIVE_EMOJI.finditer(prose):
        add('symbol-artifact', *m.span(), '装飾の絵文字。内容を運んでいなければ削る。')
    offset = 0
    for line in prose.splitlines(keepends=True):
        # 閉じていない ** は、Markdown の太字ではなく装飾の消し忘れ
        marks = [offset + m.start() for m in re.finditer(r'\*\*', line)]
        if len(marks) % 2:
            for start in marks:
                add('symbol-artifact', start, start + 2, '閉じていない ** がある。装飾の消し忘れなら削る。')
        offset += len(line)
    for m in KATAKANA_METAPHOR.finditer(prose):
        add('katakana-metaphor', *m.span(), '横文字の比喩。「考え方を変える」「習慣をつける」のような普通の言葉に戻す。')
    for m in PET_WORD.finditer(prose):
        add('pet-word', *m.span(), 'AIが好んで使う語。何を指すのかを具体的な言葉で書く。1つの文章に何度も撒かない。')
    for m in ACADEMIC_SELF.finditer(prose):
        add('academic-self', *m.span(), '論文風の自称。「この記事」「私」のように普通に書く。')
    for m in ERA_OPENER.finditer(prose):
        add('era-opener', *m.span(), '時代を持ち出す決まった書き出し。どの文章にも当てはまるので、自分の現場で起きたことから書き始める。')
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


def verify(before, after, analyzer, keep=(), condense=False):
    """condense=True は要約・圧縮向け。言及を減らす・削るのは要約では当然なので止めず、消えた語は dropped として知らせる。
    原文にない語や数値が新しく現れる変化（補った前提になりうる）だけを changes として止める。"""
    b_spans, a_spans = protected_spans(before), protected_spans(after)
    b_plain, a_plain = mask(before, b_spans), mask(after, a_spans)
    b_tokens, a_tokens = analyzer.tokens(b_plain), analyzer.tokens(a_plain)
    counted = (lambda c: Counter(dict.fromkeys(+c, 1))) if condense else (lambda c: c)
    compare = lambda b, a: delta(counted(Counter(b)), counted(Counter(a)))
    checks = {
        'numbers_and_units': compare(NUMBER.findall(before), NUMBER.findall(after)),
        'protected_regions': compare((before[a:b] for a, b in b_spans), (after[a:b] for a, b in a_spans)),
        'ascii_terms': compare(ASCII_TERM.findall(b_plain), ASCII_TERM.findall(a_plain)),
        'named_terms': compare(named_counts(before, b_tokens, keep), named_counts(after, a_tokens, keep)),
    }
    dropped = {}
    if condense:
        dropped = {k: v['removed'] for k, v in checks.items() if v['removed']}
        checks = {k: {'removed': {}, 'added': v['added']} for k, v in checks.items()}
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
            'changes': checks, **({'dropped': dropped} if condense else {}), 'review': review, 'semantic_review_required': True,
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
             f'| 診断エンジン | {engine} |', '', '## 指摘の分類', '',
             '| 分類 | 修正前 | 修正後 |', '| --- | --- | --- |']
    b_cat = Counter(f['category'] for f in b_report['findings'])
    a_cat = Counter(f['category'] for f in a_report['findings'])
    lines += [f'| {label} | {b_cat[cat]} | {a_cat[cat]} |' for cat, label in CATEGORY_LABELS.items()]
    lines += ['', '## 文書全体の傾向', '']
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


def read_keep_file(path):
    """常に保護する語のリストを読む。1行1語、空行と#で始まる行は無視する。"""
    lines = Path(path).expanduser().read_text(encoding='utf-8').splitlines()
    return [s for s in (line.strip() for line in lines) if s and not s.startswith('#')]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('analyze', 'fix', 'verify', 'report'):
        p = commands.add_parser(command)
        p.add_argument('input')
        if command in ('verify', 'report'):
            p.add_argument('candidate')
        if command == 'verify':
            p.add_argument('--condense', action='store_true',
                           help='要約・圧縮向け。重複した言及の削減は許し、語が消える・現れる変化だけを見る')
        if command == 'fix':
            p.add_argument('--output', required=True, help='新規ファイル。既存ファイルは上書きしない')
        mode = p.add_mutually_exclusive_group()
        mode.add_argument('--dic')
        mode.add_argument('--lightweight', action='store_true')
        p.add_argument('--user-dic', action='append', help='UniDicに追加するビルド済みユーザー辞書（NEologd等）。複数指定できる')
        p.add_argument('--keep', action='append', default=[])
        p.add_argument('--keep-file', help='常に保護する語のリスト（1行1語）。環境変数 POLISH_KEEP_FILE でも指定できる')
    args = parser.parse_args(argv)
    try:
        keep_file = args.keep_file or os.environ.get('POLISH_KEEP_FILE')
        if keep_file:
            args.keep += read_keep_file(keep_file)
        analyzer = Analyzer(args.dic or os.environ.get('NEOLOGD_DIC'), args.lightweight, args.user_dic)
        text, code = read(args.input), 0
        if args.command == 'analyze':
            result = inspect(text, analyzer, args.keep)
        elif args.command == 'verify':
            if args.input == '-' and args.candidate == '-':
                raise ValueError('標準入力は片方の文書だけに使えます。')
            result = verify(text, read(args.candidate), analyzer, args.keep, args.condense)
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
