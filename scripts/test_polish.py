"""Run: python3 -m unittest discover -s scripts -p 'test_*.py'."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import polish


class ShiteAsProperNounAnalyzer:
    """NEologdが「としての」の「して」を固有名詞と判定した実例を再現する。
    「して」の後ろが「の」のときだけ固有名詞、それ以外は動詞として返す。"""
    tagger = True
    info = {'mode': 'stub', 'morphology': True}

    def tokens(self, text):
        return [polish.Token(m.start(), m.end(), 'して',
                             ('名詞', '固有名詞', '一般') if text[m.end():m.end() + 1] == 'の' else ('動詞', '非自立可能'))
                for m in polish.re.finditer('して', text)]


class TaggedAsProperNounAnalyzer:
    """指定した語をすべて固有名詞と判定する。NEologdが「企業」「お客様」などの一般語を固有名詞とした実例の再現用。"""
    info = {'mode': 'stub', 'morphology': True}

    def __init__(self, *words):
        self.tagger, self.words = True, words

    def tokens(self, text):
        return sorted((polish.Token(m.start(), m.end(), w, ('名詞', '固有名詞', '一般'))
                       for w in self.words for m in polish.re.finditer(polish.re.escape(w), text)),
                      key=lambda t: t.start)


class CommonWordsTaggedAsProperTests(unittest.TestCase):
    """NEologdが一般語を固有名詞とした判定を、基本辞書（UniDic）だけの解析で見直す。"""
    PROPER = ('名詞', '固有名詞', '一般')
    COMMON = ('名詞', '普通名詞', '一般')
    base = {'観点': [COMMON], '企業': [COMMON], 'ある': [('連体詞', '*', '*')],
            'ピックゴー': [COMMON, COMMON], '東京': [('名詞', '固有名詞', '地名')], 'CBcloud': [COMMON]}

    def refined(self, surface):
        token = polish.Token(0, len(surface), surface, self.PROPER)
        return polish.refine_proper([token], self.base.get)[0].pos

    def test_word_the_base_dictionary_reads_as_one_common_word_is_not_proper(self):
        for surface, expected in [('観点', self.COMMON), ('企業', self.COMMON), ('ある', ('連体詞', '*', '*'))]:
            with self.subTest(surface=surface):
                self.assertEqual(self.refined(surface), expected)

    def test_compound_names_real_proper_nouns_and_ascii_names_stay_proper(self):
        for surface in ['ピックゴー', '東京', 'CBcloud']:
            with self.subTest(surface=surface):
                self.assertEqual(self.refined(surface), self.PROPER)

    def test_tokens_not_tagged_proper_are_left_alone(self):
        token = polish.Token(0, 2, '観点', self.COMMON)
        self.assertEqual(polish.refine_proper([token], self.base.get), [token])


class SplitsAsciiByContextAnalyzer:
    """NEologdが英字の語を文脈によって「st」「op」のような断片に切り、固有名詞とした実例を再現する。"""
    tagger = True
    info = {'mode': 'stub', 'morphology': True}

    def tokens(self, text):
        if '観点' not in text:
            return []
        start = text.find('stop')
        return [polish.Token(start, start + 2, 'st', ('名詞', '固有名詞', '一般')),
                polish.Token(start + 2, start + 4, 'op', ('名詞', '固有名詞', '一般'))]


class AsciiFragmentTests(unittest.TestCase):
    def test_fragments_of_an_ascii_word_are_not_counted_as_names(self):
        result = polish.verify('stop を参考にした。', 'stop の観点を参考にした。', SplitsAsciiByContextAnalyzer())
        self.assertNotIn('named_terms', result['changes'])

    def test_ascii_word_itself_is_still_checked(self):
        result = polish.verify('stop を参考にした。', 'go を参考にした。', SplitsAsciiByContextAnalyzer())
        self.assertEqual(result['changes']['ascii_terms']['removed'], {'stop': 1})


class ProperNounPositionTests(unittest.TestCase):
    def test_finding_that_only_overlaps_a_proper_noun_is_kept(self):
        source = '多くの企業が導入しています。先日、あるお客様から連絡がありました。'
        found = [f['text'] for f in polish.inspect(source, TaggedAsProperNounAnalyzer('企業', 'お客様'))['findings']
                 if f['rule'] == 'unsourced-claim']
        self.assertEqual(found, ['多くの企業', 'あるお客様から'])

    def test_finding_inside_a_proper_noun_is_still_suppressed(self):
        source = '圧倒的プロダクト社に相談します。'
        rules = [f['rule'] for f in polish.inspect(source, TaggedAsProperNounAnalyzer('圧倒的プロダクト社'))['findings']]
        self.assertNotIn('inflated-language', rules)

    def test_proper_noun_elsewhere_does_not_hide_findings_at_other_positions(self):
        source = 'EM目線としての改善です。ぜひ試してみてはいかがでしょうか。'
        found = [f['text'] for f in polish.inspect(source, ShiteAsProperNounAnalyzer())['findings'] if f['rule'] == 'closing-suggestion']
        self.assertEqual(found, ['てみてはいかがでしょうか'])

    def test_changing_the_same_string_at_a_non_proper_position_is_not_a_name_change(self):
        before = 'EM目線としての改善です。中心として扱います。'
        after = 'EM目線としての改善です。中心に扱います。'
        result = polish.verify(before, after, ShiteAsProperNounAnalyzer())
        self.assertNotIn('named_terms', result['changes'])

    def test_removing_the_proper_noun_itself_is_still_a_name_change(self):
        before = 'EM目線としての改善です。'
        after = 'EM目線での改善です。'
        result = polish.verify(before, after, ShiteAsProperNounAnalyzer())
        self.assertEqual(result['changes']['named_terms']['removed'], {'して': 1})


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = polish.Analyzer(lightweight=True)

    def test_does_not_silently_use_another_dictionary(self):
        with self.assertRaises(ValueError):
            polish.Analyzer()

    def test_does_not_silently_ignore_extra_dictionary(self):
        with self.assertRaises(ValueError):
            polish.Analyzer(lightweight=True, user_dic='neologd.dic')

    def test_preserves_code_quotes_links_and_line_endings(self):
        source = ('まず最初に、確認します。\r\n'
                  '「まず最初に」と読みます。\r\n'
                  '`まず最初に`\r\n'
                  '~~~txt\r\nまず最初に\r\n~~~\r\n'
                  '[まず最初に](https://example.test/a)\r\n')
        result, _ = polish.fix(source, self.analyzer)
        self.assertEqual(result, source.replace('まず最初に、', 'まず、', 1))

    def test_unclosed_fence_is_preserved(self):
        source = '````txt\nまず最初に\n```\nまず最初に'
        self.assertEqual(polish.fix(source, self.analyzer)[0], source)

    def test_keep_protects_an_unusual_product_name(self):
        source = 'まず最初に社に相談します。'
        self.assertEqual(polish.fix(source, self.analyzer, ['まず最初に社'])[0], source)

    def test_counts_duplicate_numbers_and_unit_changes(self):
        for before, after in [('30日と30日', '30日'), ('30日以内', '30日未満'), ('30日', '30時間')]:
            with self.subTest(after=after):
                self.assertIn('numbers_and_units', polish.verify(before, after, self.analyzer)['changes'])

    def test_condense_allows_dropping_repeated_mentions(self):
        before = '#11 の上に積んだ。#11 を先にマージする。AIが書き、AIが直した。'
        after = '#11 の上に積んだので、先にマージする。AIが書き、直した。'
        self.assertTrue(polish.verify(before, after, self.analyzer)['changes'])
        self.assertEqual(polish.verify(before, after, self.analyzer, condense=True)['changes'], {})

    def test_condense_reports_dropped_terms_without_stopping(self):
        result = polish.verify('30日以内に返金します。', '期限内に返金します。', self.analyzer, condense=True)
        self.assertEqual(result['changes'], {})
        self.assertEqual(result['dropped']['numbers_and_units'], {'30日以内': 1})

    def test_condense_still_stops_on_added_terms(self):
        result = polish.verify('速く処理します。', '10msで処理します。', self.analyzer, condense=True)
        self.assertEqual(result['changes']['numbers_and_units']['added'], {'10ms': 1})

    def test_cli_verify_accepts_condense(self):
        with tempfile.TemporaryDirectory() as folder:
            before, after = Path(folder)/'a.md', Path(folder)/'b.md'
            before.write_text('30日と30日です。', encoding='utf-8')
            after.write_text('30日です。', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                code = polish.main(['verify', str(before), str(after), '--lightweight', '--condense'])
            self.assertEqual(code, 0)

    def test_flags_new_factual_number(self):
        result = polish.verify('速く処理します。', '10msで処理します。', self.analyzer)
        self.assertTrue(result['changes'])

    def test_capability_must_not_become_actual_execution(self):
        result = polish.verify('返金できます。', '返金します。', self.analyzer)
        self.assertEqual(result['status'], 'needs-review')

    def test_negation_must_not_disappear(self):
        result = polish.verify('自動返信はしません。', '自動返信します。', self.analyzer)
        self.assertTrue(result['review'])

    def test_same_numbers_cannot_prove_same_meaning(self):
        result = polish.verify('Aは10件、Bは20件です。', 'Aは20件、Bは10件です。', self.analyzer)
        self.assertTrue(result['semantic_review_required'])

    def test_lightweight_mode_never_claims_morphology(self):
        report = polish.inspect('組織の知見の共有の促進による能力の向上。', self.analyzer)
        self.assertFalse(report['engine']['morphology'])
        self.assertIsNone(report['metrics']['tokens'])

    def test_cli_never_overwrites_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder)/'in.md', Path(folder)/'out.md'
            source.write_text('まず最初に、確認します。', encoding='utf-8')
            target.write_text('元のファイル', encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                code = polish.main(['fix', str(source), '--lightweight', '--output', str(target)])
            self.assertEqual(code, 2)
            self.assertEqual(target.read_text(encoding='utf-8'), '元のファイル')

    def test_report_shows_changed_sentence_and_resolved_finding(self):
        before = 'まず最初に、確認します。\n履歴を見ます。'
        after = 'まず、確認します。\n履歴を見ます。'
        sheet = polish.report(before, after, self.analyzer)
        self.assertIn('| 1 | まず最初に、確認します。 | まず、確認します。 | redundant-opening |', sheet)
        self.assertNotIn('| 履歴を見ます。 |', sheet)
        self.assertIn('| 解消 | 1 |', sheet)

    def test_report_lists_adjacent_changed_sentences_separately(self):
        before = 'まず最初に、確認します。\n履歴を確認することができます。'
        after = 'まず、確認します。\n履歴を確認できます。'
        sheet = polish.report(before, after, self.analyzer)
        self.assertIn('| 1 | まず最初に、確認します。 | まず、確認します。 | redundant-opening |', sheet)
        self.assertIn('| 2 | 履歴を確認することができます。 | 履歴を確認できます。 | roundabout-capability |', sheet)
        self.assertIn('| 変更した文 | 2 |', sheet)

    def test_report_separates_remaining_and_new_findings(self):
        before = '圧倒的な効率化です。'
        after = '圧倒的で画期的な効率化です。'
        sheet = polish.report(before, after, self.analyzer)
        self.assertIn('| 残存 | 1 |', sheet)
        self.assertIn('| 新規 | 1 |', sheet)
        self.assertIn('画期的', sheet.split('## 新たに出た指摘')[1])

    def test_report_surfaces_protected_changes_and_review_items(self):
        sheet = polish.report('2012年に開業しました。返金できます。', '2013年に開業しました。返金します。', self.analyzer)
        self.assertIn('2012年', sheet.split('## 機械照合')[1])
        self.assertIn('possibility', sheet.split('## 機械照合')[1])

    def test_report_strips_html_tags_from_sentences(self):
        sheet = polish.report('<p>まず最初に、確認します。</p>', '<p>まず、確認します。</p>', self.analyzer)
        self.assertIn('| まず最初に、確認します。 | まず、確認します。 |', sheet)

    def test_cli_report_prints_markdown_sheet(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder)/'in.md', Path(folder)/'out.md'
            source.write_text('まず最初に、確認します。', encoding='utf-8')
            target.write_text('まず、確認します。', encoding='utf-8')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = polish.main(['report', str(source), str(target), '--lightweight'])
            self.assertEqual(code, 0)
            self.assertTrue(out.getvalue().startswith('# 推敲の評価シート'))

    def test_keep_file_protects_listed_terms_and_ignores_comments(self):
        with tempfile.TemporaryDirectory() as folder:
            before, after, keep = Path(folder)/'a.md', Path(folder)/'b.md', Path(folder)/'keep.txt'
            before.write_text('ピックゴーで運びます。', encoding='utf-8')
            after.write_text('ピックアップで運びます。', encoding='utf-8')
            keep.write_text('# 社名・サービス名\n\nピックゴー\n', encoding='utf-8')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = polish.main(['verify', str(before), str(after), '--lightweight', '--keep-file', str(keep)])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(out.getvalue())['changes']['named_terms']['removed'], {'ピックゴー': 1})

    def test_keep_file_can_be_given_by_environment_variable(self):
        with tempfile.TemporaryDirectory() as folder:
            before, after, keep = Path(folder)/'a.md', Path(folder)/'b.md', Path(folder)/'keep.txt'
            before.write_text('ピックゴーで運びます。', encoding='utf-8')
            after.write_text('ピックアップで運びます。', encoding='utf-8')
            keep.write_text('ピックゴー\n', encoding='utf-8')
            with mock.patch.dict(os.environ, {'POLISH_KEEP_FILE': str(keep)}), contextlib.redirect_stdout(io.StringIO()):
                code = polish.main(['verify', str(before), str(after), '--lightweight'])
            self.assertEqual(code, 1)

    def test_missing_keep_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'a.md'
            source.write_text('確認します。', encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                code = polish.main(['analyze', str(source), '--lightweight', '--keep-file', str(Path(folder)/'none.txt')])
            self.assertEqual(code, 2)

    def test_undefined_internal_name_is_flagged_at_first_use_only(self):
        source = 'keep.dic を作り直します。keep.dic は小さいです。'
        found = [f for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == 'undefined-term']
        self.assertEqual([(f['text'], f['start']) for f in found], [('keep.dic', 0)])

    def test_internal_name_kinds_are_flagged(self):
        for name in ['artifact_gate', 'verifyAndStamp', '~/.claude/settings.json', 'PR #4', '--keep-file', '`stamp`']:
            with self.subTest(name=name):
                found = [f['text'] for f in polish.inspect(f'{name}で確認します。', self.analyzer)['findings']
                         if f['rule'] == 'undefined-term']
                self.assertEqual(found, [name.strip('`')])

    def test_term_explained_at_first_use_is_not_flagged(self):
        for source in ['小さな辞書（keep.dic）を作ります。', 'keep.dic（小さな辞書）を作ります。',
                       'keep.dicとは小さな辞書です。', 'keep.dic という小さな辞書を作ります。']:
            with self.subTest(source=source):
                rules = [f['rule'] for f in polish.inspect(source, self.analyzer)['findings']]
                self.assertNotIn('undefined-term', rules)

    def test_terms_the_reader_knows_are_not_flagged(self):
        source = 'AIとAPIとURLを使い、CBcloudの画面を開きます。'
        rules = [f['rule'] for f in polish.inspect(source, self.analyzer, ['CBcloud'])['findings']]
        self.assertNotIn('undefined-term', rules)

    def test_names_quoted_as_examples_are_not_flagged(self):
        source = '「keep.dic を作り直します」のような書き方は避けます。'
        rules = [f['rule'] for f in polish.inspect(source, self.analyzer)['findings']]
        self.assertNotIn('undefined-term', rules)

    def test_names_in_code_blocks_and_links_are_not_flagged(self):
        source = '```\nartifact_gate\n```\n[設定](https://example.test/a_b.json)を見ます。'
        rules = [f['rule'] for f in polish.inspect(source, self.analyzer)['findings']]
        self.assertNotIn('undefined-term', rules)

    def test_unsourced_generalization_hearsay_and_mind_reading_are_flagged(self):
        for source, expected in [('多くの企業が導入しています。', '多くの企業'),
                                 ('調査によると満足度は高いです。', '調査によると'),
                                 ('一般的に、配送は遅れがちです。', '一般的に'),
                                 ('配送でお悩みの方も多いのではないでしょうか。', 'お悩みの方も多い'),
                                 ('先日、あるお客様から感謝されました。', 'あるお客様から')]:
            with self.subTest(source=source):
                found = [f['text'] for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == 'unsourced-claim']
                self.assertIn(expected, found)

    def test_statement_hiding_who_says_it_is_flagged(self):
        for source, expected in [('バックログは開発の中心的な存在として語られます。', 'として語られます'),
                                 ('朝型の生活が良いと言われます。', 'と言われます'),
                                 ('この手法が標準とされます。', 'とされます'),
                                 ('原因は人手不足だと考えられています。', 'と考えられています'),
                                 ('エンジニアにも顧客理解が求められています。', 'が求められています'),
                                 ('生成AIの活用が注目されています。', '注目されています')]:
            with self.subTest(source=source):
                found = [f['text'] for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == 'unsourced-claim']
                self.assertEqual(found, [expected])

    def test_hearsay_with_a_stated_speaker_or_a_direct_quote_is_not_hidden(self):
        for source in ['最近、ユーザーから「AI臭いよ」と言われることが増えました。',
                       '「AIについて書いて」と言われると、平均的な記事が出てきます。',
                       '上司から遅いと言われます。']:
            with self.subTest(source=source):
                rules = [f['rule'] for f in polish.inspect(source, self.analyzer)['findings']]
                self.assertNotIn('unsourced-claim', rules)

    def test_writers_own_inference_and_plain_verbs_are_not_hidden_subjects(self):
        for source in ['原因は人手不足だと考えられます。', '当日は事例について語ります。', '顧客から期限の短縮を求められました。']:
            with self.subTest(source=source):
                rules = [f['rule'] for f in polish.inspect(source, self.analyzer)['findings']]
                self.assertNotIn('unsourced-claim', rules)

    def test_degree_without_number_or_example_is_flagged(self):
        for source, expected in [('要件定義の手戻りが非常に多い', '非常に'),
                                 ('リードタイムを大幅に短縮しました。', '大幅に'),
                                 ('問い合わせがかなり減りました。', 'かなり')]:
            with self.subTest(source=source):
                found = [f['text'] for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == 'vague-degree']
                self.assertEqual(found, [expected])

    def test_degree_backed_by_a_number_in_the_same_sentence_is_not_flagged(self):
        for source in ['手戻りが非常に多く、月に10件ありました。', 'リードタイムを3日から1日へ大幅に短縮しました。']:
            with self.subTest(source=source):
                rules = [f['rule'] for f in polish.inspect(source, self.analyzer)['findings']]
                self.assertNotIn('vague-degree', rules)

    def test_plain_statement_is_not_flagged_as_unsourced(self):
        for source in ['当社は2018年から配送を手がけています。', '「一般的に」という言葉は避けます。']:
            with self.subTest(source=source):
                rules = [f['rule'] for f in polish.inspect(source, self.analyzer)['findings']]
                self.assertNotIn('unsourced-claim', rules)

    def test_premise_added_in_revision_needs_review(self):
        result = polish.verify('配送を自動化します。', '多くの企業が悩む配送を自動化します。', self.analyzer)
        self.assertEqual(result['status'], 'needs-review')
        self.assertIn('unsourced', result['review'][0]['categories'])

    def test_self_declared_importance_is_flagged(self):
        for source, expected in [('ここで重要なのは、配送時間です。', 'ここで重要なのは'),
                                 ('大切なことは、続けることです。', '大切なことは'),
                                 ('本質は顧客体験にあります。', '本質は'),
                                 ('ポイントは3つあります。', 'ポイントは')]:
            with self.subTest(source=source):
                found = [f['text'] for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == 'self-declared-importance']
                self.assertEqual(found, [expected])

    def test_other_forms_of_declaring_importance_are_flagged(self):
        for source, expected in [('原因3は文脈の不足です。これが最も重要です。', 'これが最も重要です'),
                                 ('ここからが本題です。', 'ここからが本題'),
                                 ('ここに、スロップの核心があります。', 'ここに、スロップの核心があります'),
                                 ('ここに突破口があります。', 'ここに突破口があります')]:
            with self.subTest(source=source):
                found = [f['text'] for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == 'self-declared-importance']
                self.assertEqual(found, [expected])

    def test_rhetorical_contrast_is_flagged(self):
        for source, expected in [('これは単なる文体の癖の話ではなく、根本的な問いです。', '単なる文体の癖の話ではなく、'),
                                 ('これらは症状であって病気ではない。', '症状であって病気ではない'),
                                 ('速さではなく、正確さが求められる。', 'ではなく、')]:
            with self.subTest(source=source):
                found = [f['text'] for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == 'binary-contrast']
                self.assertEqual(found, [expected])

    def test_plain_choice_between_nouns_is_not_a_rhetorical_contrast(self):
        for source in ['今日は雨ではなく晴れです。', 'ピザではなくパスタを頼んだ。',
                       # 「だけではなく」は追加（〜だけでなく〜も）、「容易ではなく」は形容動詞の否定で、どちらも対比ではない
                       'これは個人の努力だけではなく、組織の環境が支えていました。',
                       '職能の壁を越えた協働は容易ではなく、失敗も多くありました。']:
            with self.subTest(source=source):
                rules = [f['rule'] for f in polish.inspect(source, self.analyzer)['findings']]
                self.assertNotIn('binary-contrast', rules)

    def test_negative_listing_is_flagged(self):
        found = [f['text'] for f in polish.inspect('速さでもない、安さでもない、信頼だ。', self.analyzer)['findings']
                 if f['rule'] == 'negative-listing']
        self.assertEqual(found, ['でもない、安さでもない'])

    def test_things_doing_human_actions_are_flagged(self):
        for source, expected in [('データが示しているのは需要の変化だ。', 'データが示して'),
                                 ('歴史が物語っている。', '歴史が物語って'),
                                 ('課題が浮き彫りになった。', '浮き彫りにな'),
                                 ('挑戦する文化が醸成される。', '醸成され')]:
            with self.subTest(source=source):
                found = [f['text'] for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == 'false-agency']
                self.assertEqual(found, [expected])

    def test_person_presenting_data_is_not_false_agency(self):
        rules = [f['rule'] for f in polish.inspect('担当者がデータを示した。', self.analyzer)['findings']]
        self.assertNotIn('false-agency', rules)

    def test_symbol_artifacts_are_flagged(self):
        for source, expected in [('結論——それは信頼だ。', ['——']),
                                 ('これは**重要。', ['**']),
                                 ('導入しました🚀', ['🚀'])]:
            with self.subTest(source=source):
                found = [f['text'] for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == 'symbol-artifact']
                self.assertEqual(found, expected)

    def test_paired_bold_markup_is_not_a_leftover(self):
        rules = [f['rule'] for f in polish.inspect('**太字**は残す。', self.analyzer)['findings']]
        self.assertNotIn('symbol-artifact', rules)

    def test_katakana_metaphors_and_pet_words_are_flagged(self):
        for source, rule, expected in [('思考のOSをアップデートしよう。', 'katakana-metaphor', '思考のOS'),
                                       ('習慣をインストールする。', 'katakana-metaphor', '習慣をインストール'),
                                       ('解像度を上げて考える。', 'pet-word', '解像度を上げ'),
                                       ('現場の熱量を感じた。', 'pet-word', '熱量')]:
            with self.subTest(source=source):
                found = [f['text'] for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == rule]
                self.assertEqual(found, [expected])

    def test_literal_uses_are_not_metaphors_or_pet_words(self):
        for source in ['アプリをインストールする。', '画面の解像度は1920です。']:
            with self.subTest(source=source):
                rules = [f['rule'] for f in polish.inspect(source, self.analyzer)['findings']]
                self.assertNotIn('katakana-metaphor', rules)
                self.assertNotIn('pet-word', rules)

    def test_academic_self_reference_is_flagged(self):
        for source, expected in [('本記事では手順を紹介します。', '本記事'), ('筆者はこう考える。', '筆者'), ('本稿の目的を述べる。', '本稿')]:
            with self.subTest(source=source):
                found = [f['text'] for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == 'academic-self']
                self.assertEqual(found, [expected])

    def test_findings_are_categorized_so_emphasis_is_not_mixed_with_ai_patterns(self):
        for source, rule, category in [('圧倒的な効率化です。', 'inflated-language', 'emphasis'),
                                       ('本質は顧客体験にあります。', 'self-declared-importance', 'emphasis'),
                                       ('結論——それは信頼だ。', 'symbol-artifact', 'ai-pattern'),
                                       ('一般的に、配送は遅れがちです。', 'unsourced-claim', 'context')]:
            with self.subTest(source=source):
                found = [f['category'] for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == rule]
                self.assertEqual(found, [category])

    def test_every_finding_has_a_category(self):
        source = ('まず最初に、ここで重要なのは、圧倒的な効率化ではなく、データが示している事実です。'
                  '一般的にkeep.dicは非常に大きいと言われています。思考のOSをアップデートしてみてはいかがでしょうか——')
        for finding in polish.inspect(source, self.analyzer)['findings']:
            with self.subTest(rule=finding['rule']):
                self.assertIn(finding['category'], polish.CATEGORY_LABELS)

    def test_report_counts_findings_by_category(self):
        sheet = polish.report('圧倒的な効率化です。', '効率化です。', self.analyzer)
        self.assertIn('| 強調・誇張（意図的なら残してよい） | 1 | 0 |', sheet.split('## 指摘の分類')[1])

    def test_ordinary_use_of_important_words_is_not_flagged(self):
        for source in ['重要な書類を送ります。', '品質が重要です。', '鍵は玄関の棚にあります。']:
            with self.subTest(source=source):
                rules = [f['rule'] for f in polish.inspect(source, self.analyzer)['findings']]
                self.assertNotIn('self-declared-importance', rules)

    def test_stock_closing_suggestion_is_flagged(self):
        for source, expected in [('ぜひ使ってみてはいかがでしょうか。', 'てみてはいかがでしょうか'),
                                 ('いかがでしたか？', 'いかがでしたか')]:
            with self.subTest(source=source):
                found = [f['text'] for f in polish.inspect(source, self.analyzer)['findings'] if f['rule'] == 'closing-suggestion']
                self.assertEqual(found, [expected])

    def test_genuine_question_is_not_a_closing_suggestion(self):
        rules = [f['rule'] for f in polish.inspect('来週のご都合はいかがでしょうか。', self.analyzer)['findings']]
        self.assertNotIn('closing-suggestion', rules)

    def test_style_tendency_measures_short_sentences_line_breaks_and_bullets(self):
        text = ('# 見出し\n'
                '一つ目です。\n'
                '短い。\n'
                '- 項目\n'
                '- 項目\n'
                '\n'
                '長い説明の文で、理由と条件をつなげて書いています。次の文です。\n'
                '```\nコードの行\n```\n')
        style = polish.style_tendency(text)
        self.assertEqual(style['sentences'], 4)
        self.assertEqual(style['short_sentence_ratio'], 0.75)
        self.assertEqual(style['one_sentence_line_ratio'], round(2 / 3, 2))
        self.assertEqual(style['bullet_line_ratio'], 0.4)

    def test_style_tendency_of_empty_text_has_no_ratios(self):
        style = polish.style_tendency('')
        self.assertEqual(style['sentences'], 0)
        self.assertIsNone(style['short_sentence_ratio'])

    def test_report_shows_style_tendency_before_and_after(self):
        before = '短い。\n短い文。\n短いです。\n'
        after = '短く切らずに、理由と条件をつなげて一続きの文で書き直しました。\n'
        sheet = polish.report(before, after, self.analyzer)
        tendency = sheet.split('## 文書全体の傾向')[1]
        self.assertIn('| 20文字未満の文の割合 | 100% | 0% |', tendency)
        self.assertIn('| 一文だけの行の割合 | 100% | 100% |', tendency)

    @unittest.skipUnless(os.environ.get('POLISH_TEST_DIC'), 'set POLISH_TEST_DIC for MeCab integration')
    def test_real_dictionary_integration_and_source_offsets(self):
        analyzer = polish.Analyzer(os.environ['POLISH_TEST_DIC'], user_dic=os.environ.get('POLISH_TEST_USER_DIC'))
        source = 'まず最初に、履歴を確認することができます。\n組織の知見の共有の促進による能力の向上。'
        report = polish.inspect(source, analyzer)
        self.assertTrue(report['engine']['morphology'])
        for item in report['findings']:
            self.assertEqual(source[item['start']:item['end']], item['text'])
        result, _ = polish.fix(source, analyzer)
        self.assertIn('履歴を確認できます。', result)
        self.assertIn('noun-chain', [x['rule'] for x in report['findings']])

    @unittest.skipUnless(os.environ.get('POLISH_TEST_USER_DIC'), 'set POLISH_TEST_USER_DIC for full seed integration')
    def test_neologd_named_expression_is_preserved(self):
        analyzer = polish.Analyzer(os.environ['POLISH_TEST_DIC'], user_dic=os.environ['POLISH_TEST_USER_DIC'])
        source = 'まず最初に、東京スカイツリーを見ます。'
        report = polish.inspect(source, analyzer)
        self.assertEqual(len(report['engine']['dictionaries']), 2)
        self.assertIn('東京スカイツリー', report['proper_noun_candidates'])
        result, _ = polish.fix(source, analyzer)
        self.assertEqual(result, 'まず、東京スカイツリーを見ます。')


if __name__ == '__main__':
    unittest.main()
