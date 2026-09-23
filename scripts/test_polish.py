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
