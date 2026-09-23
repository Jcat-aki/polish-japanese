# 実行環境

Python 3.10以降。軽量モードは標準ライブラリだけで動く。全文の書き直しはこのスキルを使うエージェントが行い、スクリプトは診断・小さな修正・差分照合を担当する。

## MeCabと指定辞書

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install mecab-python3==1.0.12
```

mecab-python3のwheelにはMeCabライブラリが入るが、辞書は含まれない。ビルド済みのmecab-unidic-neologdを別途用意し、`--dic`でディレクトリを渡す。`NEOLOGD_DIC`環境変数でも指定できる。シェルの既定辞書やunidic-liteへ暗黙には切り替えない。

辞書の導入は上流のREADMEに従う：
https://github.com/neologd/mecab-unidic-neologd/blob/master/README.ja.md

上流ではMeCab・ビルド環境等を準備し、次を実行する手順が案内されている。実行前に現在のOSに合う依存関係と導入先を確認する。

```bash
git clone --depth 1 https://github.com/neologd/mecab-unidic-neologd.git
cd mecab-unidic-neologd
./bin/install-mecab-unidic-neologd -n
```

配布元・取得日時・コミットを利用側で記録すると解析結果を再現しやすい。このスキルは辞書データを再配布しない。辞書更新は自動では行わない。大きな辞書の導入で推敲を待たせず、その場は`--lightweight`で案を返す。

## UniDicへNEologdを追加する

システム辞書を作り直す代わりに、NEologdのseedをユーザー辞書として追加する構成も使える。上流の `--create_user_dic` もこのコンパイル方法を採用している。基本辞書には対応するUniDicを指定する。IPADIC用NEologdは混用しない。

```bash
# mecab-dict-indexの場所はOS・導入方法によって異なる。
mecab-dict-index -f UTF8 -t UTF8 -d /path/to/unidic -u /path/to/neologd.dic /path/to/mecab-unidic-user-dict-seed.20200910.csv

python3 SKILL_DIR/scripts/polish.py analyze draft.md --dic /path/to/unidic --user-dic /path/to/neologd.dic
python3 SKILL_DIR/scripts/polish.py verify draft.md candidate.md --dic /path/to/unidic --user-dic /path/to/neologd.dic
```

`--user-dic`は`analyze`・`fix`・`verify`・`report`のすべてで指定でき、複数回指定すると順に読み込む（MeCabにはカンマ区切りで渡す）。各コマンドで同じ辞書を使う。読み込んだ辞書一覧と件数はJSONの`engine.dictionaries`に出す。軽量モードと追加辞書を同時に指定するとエラーになる。

## 辞書を自動で使うラッパー

毎回`--dic`・`--user-dic`を渡さずに済むよう、PATHの通った場所に`polish-japanese`という名前のラッパーを置ける。SKILL.mdはこのコマンドがあれば優先して使う。ラッパーは利用者の環境ごとに作り、スキルには同梱しない。

```bash
#!/bin/bash
set -euo pipefail
PY=/path/to/.venv/bin/python   # mecab-python3を入れたPython
SCRIPT=SKILL_DIR/scripts/polish.py
for arg in "$@"; do
  case "$arg" in
    --lightweight|--dic|--dic=*|--user-dic|--user-dic=*) exec "$PY" "$SCRIPT" "$@" ;;
  esac
done
if [ $# -eq 0 ] || [[ "$1" == -* ]]; then exec "$PY" "$SCRIPT" "$@"; fi
exec "$PY" "$SCRIPT" "$@" --dic /path/to/unidic --user-dic /path/to/neologd.dic
```

`--lightweight`・`--dic`・`--user-dic`を明示したときは、ラッパーは辞書を足さずにその指定へ従う。

## CLIの範囲

- `analyze`: 行・列・原文の文字オフセットと理由をJSONで返す。オフセットはUnicodeコードポイント単位。
- `fix`: 文頭の「まず最初に」と、形態素条件を満たす「サ変名詞＋することができます」のみ短縮候補を保存する。他の表現はエージェントが判断する。原文と既存出力を上書きしない。
- `verify`: 数字と一部の単位、ASCII用語、辞書が固有名詞とした語、指定語、保護領域の出現回数を照合する。否定・可能・義務などの表現差を確認対象として返す。
- `report`: 修正前後を比べた評価シートをMarkdownで返す。概要（文字数・指摘件数・照合結果）、変更した文ごとの前後と対応した指摘、解消・残存・新規の指摘、機械照合の差異を含む。変更の対応づけは文単位で、文の分割・統合は一つの行にまとめて示す。
- `--keep`: 原文のまま残す語句を繰り返し指定できる。
- `scripts/userdic.py <keep.txt> <出力.dic> [--dic <UniDic>]`: keep.txtの語を「名詞-固有名詞-一般」（文脈ID 4786、コスト-5000）としてユーザー辞書にする。空白・カンマ・引用符を含む語は除く。登録語がなければ出力を作らず、既存の出力は消す。コンパイルにはmecab-python3同梱の`mecab_dict_index`を使う。
- `--keep-file`: 常に保護する語のリストを読む。1行1語で、空行と`#`で始まる行は無視する。環境変数`POLISH_KEEP_FILE`でも指定でき、`--keep`と併用できる。ファイルがなければエラーにする。
- `--lightweight`: MeCab未使用と明示する。固有名詞の自動抽出と名詞構造の診断は省略する。

コード、引用、URL、リンク、表、HTML等は保守的に除外する。完全なMarkdownパーサーではなく、入れ子の引用・複雑なHTML・特殊な構文では目視確認が必要。引用自体を編集したい依頼では本文と分けて扱う。

数値照合は漢数字やすべての単位を網羅しない。同じ語や数字を別の主語へ付け替える変化は通り得る。否定等のパターンには誤検知・見逃しがある。終了コード0は内容保持の証明ではない。形態素の閾値は初期の目安で、コーパスによる校正は未実施。

PythonコードはAPI呼び出しやネットワーク送信を行わない。ただしエージェントに渡した本文の扱いは利用しているサービスの設定に従う。

## Claude Codeでの配置

このスキルのフォルダを`~/.claude/skills/polish-japanese/`またはプロジェクトの`.claude/skills/polish-japanese/`に置く。`/polish-japanese この文章をさっと自然にして`のように使う。ChatGPTへの導入とClaude Codeへの配置は別操作。

## 検証済みの構成

2026-09-23に、mecab-unidic-neologdのseed全3,384,963件をユーザー辞書としてコンパイルし、実際に読み込んで14件のテストを通した。基本辞書はunidic-lite 1.0.8（UniDic 2.1.2由来）、バインディングはmecab-python3 1.0.12、Linux / Python 3.12で検証した。

- 上流コミット：`22895c054014393307967eddcd351c69e1fd57af`
- seed：`mecab-unidic-user-dict-seed.20200910.csv.xz`（2020-09-10版）
- seedのGit blob SHA-1：`2cef5d3c09296b199b3a0e384eb4bc70dc190cb5`。ダウンロード後に照合した。
- 追加辞書のサイズ：1,025,938,094 bytes。辞書はスキルに同梱しない。
- コンパイラ：mecab-python3に同梱されたMeCabの`mecab_dict_index`を使用。`pos-id.def`がないため最小設定が使われた。このコードは内部の品詞IDではなく品詞文字列を参照する。

数値・否定・可能表現・引用・コードの確認、固有表現「東京スカイツリー」の一語認識と保持、追加辞書の誤指定を検証した。システム辞書への統合ビルドと、別OSでの導入は未検証。

NEologdでは一般語も固有名詞として解析される例がある。実際に「組織」「知見」で確認した。名前を含む名詞の連なりも診断できるよう、連結の「の」を指摘する。辞書の品詞を文脈上の正解とは扱わない。語彙の新しさや競合より高い精度は保証しない。
