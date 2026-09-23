"""Run: python3 -m unittest discover -s scripts -p 'test_*.py'."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
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
