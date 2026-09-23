#!/usr/bin/env python3
"""常に保護する語のリスト（keep.txt）から、語を1語の固有名詞として解析させるMeCabユーザー辞書を作る。"""
from __future__ import annotations
import argparse
import ctypes
import glob
import os
from pathlib import Path
import sys
import tempfile

from polish import read_keep_file

# UniDicの「名詞-固有名詞-一般」の文脈ID。NEologdのseedと同じ値を使う
PROPER_NOUN_ID = 4786
# NEologdの固有名詞（-3000前後）より優先されるよう低めのコストにする
COST = -5000


def rows(terms):
    """語をUniDic形式のユーザー辞書CSVの行にする。1語にできない語（空白・カンマ・引用符を含む）は除く。"""
    result = []
    for term in dict.fromkeys(terms):
        if not term or any(c.isspace() or c in ',"' for c in term):
            continue
        fields = [term, PROPER_NOUN_ID, PROPER_NOUN_ID, COST, '名詞', '固有名詞', '一般', '*', '*', '*',
                  *[term] * 6, '固', '*', '*', '*', '*']
        result.append(','.join(map(str, fields)))
    return result


def compile_dictionary(csv_path, out, sys_dic):
    """mecab-python3同梱のlibmecabにあるmecab_dict_indexでユーザー辞書をコンパイルする。"""
    import MeCab
    pkg = os.path.dirname(MeCab.__file__)
    libs = glob.glob(os.path.join(pkg, '.dylibs', 'libmecab*')) + glob.glob(os.path.join(os.path.dirname(pkg), '*.libs', 'libmecab*'))
    if not libs:
        raise RuntimeError('mecab-python3 同梱の libmecab が見つかりません。')
    args = ['mecab-dict-index', '-d', str(sys_dic), '-u', str(out), '-f', 'UTF8', '-t', 'UTF8', str(csv_path)]
    argv = (ctypes.c_char_p * len(args))(*[a.encode() for a in args])
    # コンパイラの進捗表示が標準出力を汚さないよう、一時的に標準エラーへ流す
    sys.stdout.flush()
    saved = os.dup(1)
    os.dup2(2, 1)
    try:
        status = ctypes.CDLL(libs[0]).mecab_dict_index(len(args), argv)
    finally:
        os.dup2(saved, 1)
        os.close(saved)
    if status != 0:
        raise RuntimeError('ユーザー辞書のコンパイルに失敗しました。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('keep_file', help='常に保護する語のリスト（1行1語）')
    parser.add_argument('output', help='作成するユーザー辞書')
    parser.add_argument('--dic', help='基本辞書（UniDic）のディレクトリ。省略時はunidic-lite')
    args = parser.parse_args(argv)
    try:
        sys_dic = args.dic
        if not sys_dic:
            import unidic_lite
            sys_dic = unidic_lite.DICDIR
        out = Path(args.output)
        entries = rows(read_keep_file(args.keep_file))
        if not entries:
            # 登録する語がなければ古い辞書も残さない
            out.unlink(missing_ok=True)
            return 0
        with tempfile.TemporaryDirectory() as folder:
            csv_path, part = Path(folder)/'keep.csv', Path(folder)/'keep.dic'
            csv_path.write_text('\n'.join(entries) + '\n', encoding='utf-8')
            compile_dictionary(csv_path, part, sys_dic)
            out.parent.mkdir(parents=True, exist_ok=True)
            os.replace(part, out)
        return 0
    except (OSError, UnicodeError, RuntimeError, ImportError) as e:
        print(f'エラー: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
