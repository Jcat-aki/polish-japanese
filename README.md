# polish-japanese

抽象語や名詞が重なって場面が浮かばない、内容のわりに仰々しい、といった日本語を診断して推敲するエージェント用スキルです。元の意味と書き手の声を保ったまま、一度の言い換えで叩き台を返します。主張や例えの方向は、叩き台を見ながら人と調整します。

Claude Code のスキルとして使えます。ChatGPT / Codex 向けの設定（`agents/openai.yaml`）も同梱しています。

```text
原文: まず最初に、問い合わせ対応の品質の向上を図ることで圧倒的な効率化を実現します。
診断: 「まず最初に」順序の説明が重複／「圧倒的」内容に見合う強さか確認
叩き台: まず、問い合わせに正確に答えられるようにして、対応の手間を減らします。
       （「品質」が速さや丁寧さの意味なら言い換えを変えるので、ここだけ確認させてください）
```

## できること・できないこと

| できる | できない（しない） |
| --- | --- |
| 抽象語の重なり・名詞の連なり・大げさな表現・回りくどい言い方の指摘 | AI が書いた文章かどうかの判定 |
| エージェントによる一回の言い換えと、主張についての相談（最大一つ） | AI 検出器の回避の保証 |
| 修正前後の数値・固有名詞・否定・確度などの機械照合 | 意味が変わっていないことの証明 |
| MeCab ＋ NEologd による固有名詞の保護と名詞構造の診断 | 本文の外部送信（スクリプトはネットワークに接続しない） |

診断結果は「確認すべき場所」であり、直すべき誤りの一覧ではありません。

## 構成

```text
SKILL.md                     スキル本体（エージェントが読む手順）
scripts/polish.py            診断・定型修正・照合の CLI
scripts/test_polish.py       テスト
references/editing.md        推敲の観点と終了条件
references/runtime.md        MeCab・辞書の準備と CLI の詳細
references/sources-and-scope.md  参照資料と類似ツールとの比較範囲
agents/openai.yaml           ChatGPT / Codex 向けの設定
assets/icon.svg              アイコン
```

## 導入

### 1. スキルを置く（Claude Code）

```bash
git clone git@github.com:Jcat-aki/polish-japanese.git ~/workspace/polish-japanese
ln -s ~/workspace/polish-japanese ~/.claude/skills/polish-japanese
```

プロジェクト単位で使う場合は、そのプロジェクトの `.claude/skills/polish-japanese/` に置きます。`SKILL.md` と `scripts/`・`references/` は同じディレクトリに保ってください。

この段階で、辞書なしの軽量モード（`--lightweight`）で動きます。Python 3.9 以降の標準ライブラリだけで動作します（MeCab を使う場合は 3.10 以降を推奨）。

### 2. MeCab と NEologd 辞書を入れる（任意・推奨）

固有名詞の自動保護と名詞構造の診断には、MeCab と mecab-unidic-NEologd が必要です。辞書はこのリポジトリに含めていません。以下は macOS（arm64）で確認した手順で、基本辞書に unidic-lite、追加辞書に NEologd の seed 全件を使います。ディスクを約 1.3GB 使います。

```bash
BASE=~/.local/share/polish-japanese
mkdir -p "$BASE/src" "$BASE/dic" && cd "$BASE"

# mecab-python3 の wheel には MeCab 本体が入る（辞書は入らない）
uv venv -p 3.12 .venv   # または python3.12 -m venv .venv
uv pip install -p .venv/bin/python mecab-python3==1.0.12 unidic-lite==1.0.8

# NEologd の seed を取得し、Git blob ハッシュを照合する
C=22895c054014393307967eddcd351c69e1fd57af
curl -fL -o src/seed.csv.xz \
  https://github.com/neologd/mecab-unidic-neologd/raw/$C/seed/mecab-unidic-user-dict-seed.20200910.csv.xz
git hash-object src/seed.csv.xz   # 2cef5d3c09296b199b3a0e384eb4bc70dc190cb5 になること
xz -dk src/seed.csv.xz
```

mecab-python3 には `mecab-dict-index` コマンドが同梱されていません。同梱ライブラリの `mecab_dict_index` 関数を呼んでユーザー辞書をコンパイルします。

```bash
.venv/bin/python - src/seed.csv dic/neologd.dic <<'EOF'
import ctypes, glob, os, sys
import MeCab, unidic_lite
lib = glob.glob(os.path.join(os.path.dirname(MeCab.__file__), '.dylibs', 'libmecab*'))[0]  # Linux は MeCab.libs 等
args = ['mecab-dict-index', '-d', unidic_lite.DICDIR, '-u', sys.argv[2], '-f', 'UTF8', '-t', 'UTF8', sys.argv[1]]
sys.exit(ctypes.CDLL(lib).mecab_dict_index(len(args), (ctypes.c_char_p * len(args))(*[a.encode() for a in args])))
EOF
rm src/seed.csv   # 展開した CSV（約 800MB）は不要
```

`dic/neologd.dic` が 1,025,938,094 bytes になれば成功です。`pos-id.def is not found` という警告は出ますが問題ありません（`polish.py` は品詞 ID ではなく品詞の文字列を参照します）。システムに MeCab を入れている場合は、上流 README の手順や `mecab-dict-index` コマンドも使えます。詳細は [references/runtime.md](references/runtime.md) を参照してください。

### 3. ラッパーを置く（任意・推奨）

毎回 `--dic` と `--user-dic` を渡さずに済むよう、PATH の通った場所に `polish-japanese` という名前のラッパーを置きます。`SKILL.md` はこのコマンドがあれば優先して使うので、スキル経由でも自動で辞書が使われます。

```bash
cat > ~/.local/bin/polish-japanese <<'EOF'
#!/bin/bash
set -euo pipefail
BASE="$HOME/.local/share/polish-japanese"
PY="$BASE/.venv/bin/python"
SCRIPT="$HOME/.claude/skills/polish-japanese/scripts/polish.py"
for arg in "$@"; do
  case "$arg" in
    --lightweight|--dic|--dic=*|--user-dic|--user-dic=*) exec "$PY" "$SCRIPT" "$@" ;;
  esac
done
if [ $# -eq 0 ] || [[ "$1" == -* ]]; then exec "$PY" "$SCRIPT" "$@"; fi
exec "$PY" "$SCRIPT" "$@" \
  --dic "$BASE/.venv/lib/python3.12/site-packages/unidic_lite/dicdir" \
  --user-dic "$BASE/dic/neologd.dic"
EOF
chmod +x ~/.local/bin/polish-japanese
```

`--lightweight`・`--dic`・`--user-dic` を明示したときは、ラッパーは辞書を足さずにその指定に従います。

## 使い方

### Claude Code から

```text
/polish-japanese この文章をさっと自然にして
```

文章を平易にしたい、AI っぽさを消したい、といった依頼では、スキル名を書かなくても自動で使われることがあります。エージェントは診断 → 一回の言い換え → 数値等の照合を行い、叩き台を返します。

### CLI として

すべてのコマンドは JSON を標準出力に返します。入力に `-` を渡すと標準入力から読みます。

```bash
# 診断（行・列・文字オフセットと理由を返す）
polish-japanese analyze draft.md

# 定型的な修正候補だけを別ファイルに保存する（出力先が存在すれば中止）
polish-japanese fix draft.md --output candidate.md

# 修正前後の照合。推敲しても変えてはいけない語は --keep で指定する
polish-japanese verify draft.md candidate.md --keep '製品の正式名称'

# 辞書なし（軽量モード）
python3 scripts/polish.py analyze draft.md --lightweight
```

ラッパーを置いていない場合は `python3 scripts/polish.py <コマンド> ... --dic <UniDic> --user-dic <neologd.dic>` で実行します。基本辞書は `NEOLOGD_DIC` 環境変数でも指定できます。

`verify` の終了コード:

| コード | 意味 |
| --- | --- |
| 0 | 機械的な差異なし（意味が同じという証明ではない） |
| 1 | 数値・固有名詞・指定語などの保護対象が変わった |
| 2 | 実行エラー |
| 3 | 否定・可能・確度などの表現差があり、目で確認が必要 |

## 診断の対象

| ルール | 例 |
| --- | --- |
| `inflated-language` | 圧倒的・革命的・画期的など、根拠のない強い言葉 |
| `abstract-stack` | 一つの文に抽象語が三つ以上 |
| `noun-heavy` | 名詞の割合が高い文 |
| `noun-chain` | 「の」で名詞が三つ以上つながる |
| `long-sentence` | 100 文字以上の文 |
| `redundant-opening` | 「まず最初に」 |
| `roundabout-capability` | 「〜することができます」 |

コード・引用・URL・リンク・表・HTML タグ・`<script>` などは保守的に対象外にします。完全な Markdown パーサーではないため、特殊な構文は目で確認してください。

## テスト

```bash
python3 scripts/test_polish.py            # 辞書なしで動くテスト（MeCab 連携の2件はスキップ）

# 辞書を使う統合テストも含めて実行する
BASE=~/.local/share/polish-japanese
POLISH_TEST_DIC=$BASE/.venv/lib/python3.12/site-packages/unidic_lite/dicdir \
POLISH_TEST_USER_DIC=$BASE/dic/neologd.dic \
  $BASE/.venv/bin/python scripts/test_polish.py
```

## 注意点

- NEologd は「組織」「知見」などの一般語も固有名詞として解析することがあり、その語は診断対象から外れます。辞書の品詞を文脈上の正解とは扱いません。
- 形態素の閾値は初期の目安で、コーパスによる校正はしていません。
- 数値照合は漢数字やすべての単位を網羅しません。同じ語や数字を別の主語に付け替える変化は検出できません。
- 類似ツール（textlint-rule-preset-ai-writing、natural-japanese）との比較範囲は [references/sources-and-scope.md](references/sources-and-scope.md) にまとめています。性能の比較は未測定です。
- 辞書データは再配布していません。mecab-python3・UniDic・NEologd の利用条件は、それぞれの配布元に従ってください。
