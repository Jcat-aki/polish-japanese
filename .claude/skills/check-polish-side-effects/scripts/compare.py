#!/usr/bin/env python3
"""変更前（git の参照）と変更後（作業ツリー、または --head の参照）の scripts/polish.py で同じ文章を処理し、結果の差を出す。

  # 診断の差：増えた指摘・消えた指摘をファイル・行・文つきで出す
  python3 compare.py analyze main corpus/*.md --dic DIC --user-dic NEOLOGD

  # 照合の差：書き換え前後の組ごとに verify の判定がどう変わったかを出す
  python3 compare.py verify main --pairs before_dir after_dir --lightweight

  # ブランチを切り替えずに、別のブランチの変更を比べる
  python3 compare.py analyze main corpus/*.md --head feature/x --lightweight
"""
from __future__ import annotations
import argparse
from collections import Counter
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[4]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # dataclass の型注釈の解決に sys.modules への登録が必要
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_ref(ref, folder, name):
    source = subprocess.run(['git', '-C', str(REPO), 'show', f'{ref}:scripts/polish.py'],
                            check=True, capture_output=True, text=True).stdout
    path = Path(folder) / f'{name}.py'
    path.write_text(source, encoding='utf-8')
    return load(path, name)


def analyzer(module, args):
    return module.Analyzer(args.dic, args.lightweight, args.user_dic)


def sentence_at(text, start):
    # 指摘を含む文を、前後の句点・改行で切り出す
    left = max(text.rfind('。', 0, start), text.rfind('\n', 0, start)) + 1
    right = min([i for i in (text.find('。', start), text.find('\n', start)) if i >= 0] or [len(text)])
    return text[left:right + 1].strip()


def compare_analyze(base, head, args):
    a_base, a_head = analyzer(base, args), analyzer(head, args)
    per_rule = {'base': Counter(), 'head': Counter()}
    changes = []
    for name in args.files:
        text = Path(name).read_text(encoding='utf-8')
        found = {}
        for label, module, a in (('base', base, a_base), ('head', head, a_head)):
            items = Counter((f['rule'], f['start'], f['end']) for f in module.inspect(text, a)['findings'])
            per_rule[label].update(rule for rule, _, _ in items.elements())
            found[label] = items
        for sign, diff in (('+', found['head'] - found['base']), ('-', found['base'] - found['head'])):
            for rule, start, end in sorted(diff.elements(), key=lambda x: x[1]):
                line = text.count('\n', 0, start) + 1
                changes.append(f'{sign} {rule}\t{name}:{line}\t「{text[start:end]}」\t{sentence_at(text, start)}')
    rules = sorted(set(per_rule['base']) | set(per_rule['head']))
    print(f'# 診断の差（{args.ref} → {args.head or "作業ツリー"}、{len(args.files)}ファイル）\n')
    print('| ルール | 変更前 | 変更後 |\n| --- | --- | --- |')
    for rule in rules:
        mark = '' if per_rule['base'][rule] == per_rule['head'][rule] else ' ←'
        print(f'| {rule} | {per_rule["base"][rule]} | {per_rule["head"][rule]}{mark} |')
    print('\n## 増えた指摘（+）・消えた指摘（-）\n')
    print('\n'.join(changes) or 'なし')


def compare_verify(base, head, args):
    a_base, a_head = analyzer(base, args), analyzer(head, args)
    before_dir, after_dir = map(Path, args.pairs)
    print(f'# 照合の差（{args.ref} → {args.head or "作業ツリー"}）\n')
    print('| ファイル | 変更前 | 変更後 | 変更後の changes |\n| --- | --- | --- | --- |')
    same = 0
    for before in sorted(before_dir.glob('*')):
        after = after_dir / before.name
        if not after.is_file():
            continue
        b, a = before.read_text(encoding='utf-8'), after.read_text(encoding='utf-8')
        r_base = base.verify(b, a, a_base, condense=args.condense)
        r_head = head.verify(b, a, a_head, condense=args.condense)
        if r_base['status'] == r_head['status'] and r_base['changes'] == r_head['changes']:
            same += 1
            continue
        print(f'| {before.name} | {r_base["status"]} | {r_head["status"]} | {r_head["changes"]} |')
    print(f'\n判定と changes が変わらなかった組: {same}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['analyze', 'verify'])
    parser.add_argument('ref', help='比べる変更前の git 参照（例: main）')
    parser.add_argument('files', nargs='*', help='analyze で診断する文章')
    parser.add_argument('--head', help='変更後として使う git 参照。省略すると作業ツリー')
    parser.add_argument('--pairs', nargs=2, metavar=('BEFORE_DIR', 'AFTER_DIR'), help='verify で比べる、同名ファイルの組')
    parser.add_argument('--condense', action='store_true')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dic')
    mode.add_argument('--lightweight', action='store_true')
    parser.add_argument('--user-dic', action='append')
    args = parser.parse_args(argv)
    if args.command == 'verify' and not args.pairs:
        parser.error('verify には --pairs が必要です')
    with tempfile.TemporaryDirectory() as folder:
        base = load_ref(args.ref, folder, 'polish_base')
        head = load_ref(args.head, folder, 'polish_head') if args.head else load(REPO / 'scripts' / 'polish.py', 'polish_head')
        (compare_analyze if args.command == 'analyze' else compare_verify)(base, head, args)


if __name__ == '__main__':
    sys.exit(main())
