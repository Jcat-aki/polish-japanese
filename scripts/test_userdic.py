"""Run: python3 -m unittest discover -s scripts -p 'test_*.py'."""
import os
from pathlib import Path
import tempfile
import unittest
import polish
import userdic


class UserDictionaryTests(unittest.TestCase):
    def test_term_becomes_proper_noun_entry(self):
        self.assertEqual(userdic.rows(['ピックゴー']),
                         ['ピックゴー,4786,4786,-5000,名詞,固有名詞,一般,*,*,*,'
                          'ピックゴー,ピックゴー,ピックゴー,ピックゴー,ピックゴー,ピックゴー,固,*,*,*,*'])

    def test_terms_that_cannot_be_one_word_are_skipped(self):
        self.assertEqual(userdic.rows(['製品 名', 'A,B', '"引用"', '', 'CBcloud']),
                         [userdic.rows(['CBcloud'])[0]])

    def test_duplicate_terms_are_registered_once(self):
        self.assertEqual(len(userdic.rows(['CBcloud', 'CBcloud'])), 1)

    @unittest.skipUnless(os.environ.get('POLISH_TEST_DIC'), 'set POLISH_TEST_DIC for MeCab integration')
    def test_registered_term_is_analyzed_as_one_proper_noun(self):
        with tempfile.TemporaryDirectory() as folder:
            keep, out = Path(folder)/'keep.txt', Path(folder)/'keep.dic'
            keep.write_text('# サービス名\nピックゴー\nCBcloud\n', encoding='utf-8')
            self.assertEqual(userdic.main([str(keep), str(out), '--dic', os.environ['POLISH_TEST_DIC']]), 0)
            user_dics = [d for d in (os.environ.get('POLISH_TEST_USER_DIC'), str(out)) if d]
            analyzer = polish.Analyzer(os.environ['POLISH_TEST_DIC'], user_dic=user_dics)
            report = polish.inspect('CBcloudがピックゴーを運営する。', analyzer)
            self.assertIn('ピックゴー', report['proper_noun_candidates'])
            self.assertIn('CBcloud', report['proper_noun_candidates'])
            self.assertEqual(len(report['engine']['dictionaries']), len(user_dics) + 1)

    @unittest.skipUnless(os.environ.get('POLISH_TEST_DIC'), 'set POLISH_TEST_DIC for MeCab integration')
    def test_keep_file_without_terms_leaves_no_dictionary(self):
        with tempfile.TemporaryDirectory() as folder:
            keep, out = Path(folder)/'keep.txt', Path(folder)/'keep.dic'
            keep.write_text('# まだ何も登録していない\n', encoding='utf-8')
            out.write_text('古い辞書', encoding='utf-8')
            self.assertEqual(userdic.main([str(keep), str(out), '--dic', os.environ['POLISH_TEST_DIC']]), 0)
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
