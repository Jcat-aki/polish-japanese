# polish-japanese

書き手だけが知る呼び名や、根拠なく補った前提が読み手向けの文章に漏れている状態を「AI臭さ」の中心と捉え、日本語を診断して推敲するエージェント用スキルです。抽象語や名詞が重なって場面が浮かばない、内容のわりに仰々しい、といった読みにくさも直します。元の意味と書き手の声を保ったまま、一度の言い換えで叩き台を返します。主張や例えの方向は、叩き台を見ながら人と調整します。

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
| 読み手に漏れた文脈（定義のない内部の呼び名、根拠なく補った前提）と、読み手を見くびる書き方（重要さの宣言、締めの決まり文句）の指摘 | 書き手の意図の断定（確認事項として返す） |
| 抽象語の重なり・名詞の連なり・大げさな表現・回りくどい言い方の指摘 | AI が書いた文章かどうかの判定 |
| エージェントによる一回の言い換えと、主張についての相談（最大一つ） | AI 検出器の回避の保証 |
| 修正前後の数値・固有名詞・否定・確度などの機械照合 | 意味が変わっていないことの証明 |
| MeCab ＋ NEologd による固有名詞の保護と名詞構造の診断 | 本文の外部送信（スクリプトはネットワークに接続しない） |

診断結果は「確認すべき場所」であり、直すべき誤りの一覧ではありません。

## 構成

```text
SKILL.md                     スキル本体（エージェントが読む手順）
scripts/polish.py            診断・定型修正・照合の CLI
scripts/test_*.py            テスト
scripts/install.sh           一括導入スクリプト
scripts/userdic.py           keep.txt の語を固有名詞として登録するユーザー辞書を作る
references/editing.md        推敲の観点と終了条件
references/runtime.md        MeCab・辞書の準備と CLI の詳細
references/sources-and-scope.md  参照資料と類似ツールとの比較範囲
agents/openai.yaml           ChatGPT / Codex 向けの設定
assets/icon.svg              アイコン
```

## 導入

```bash
git clone git@github.com:Jcat-aki/polish-japanese.git ~/workspace/polish-japanese
~/workspace/polish-japanese/scripts/install.sh
```

これだけです（初回は約 55MB をダウンロードし、1 分ほどかかります）。`install.sh` は次を行います。何度実行しても、済んでいる手順は飛ばします。

1. `~/.claude/skills/polish-japanese` にこのリポジトリへのシンボリックリンクを置く
2. MeCab（mecab-python3）と unidic-lite を入れた venv を作る
3. mecab-unidic-NEologd の辞書データを取得し、ハッシュを照合してユーザー辞書を作る
4. 辞書を自動で指定する `polish-japanese` コマンドを `~/.local/bin` に置く
5. 常に保護する語のリスト `~/.config/polish-japanese/keep.txt` を用意する（既にあれば触らない）。ここに書いた語は1語の固有名詞として解析される

必要なもの: `uv` か Python 3.10 以降、`curl`。導入先は `POLISH_JA_HOME`（既定 `~/.local/share/polish-japanese`）と `POLISH_JA_BIN`（既定 `~/.local/bin`）で変えられます。ディスクは約 1.3GB 使います。手動で導入したい場合や辞書の詳細は [references/runtime.md](references/runtime.md) を参照してください。

辞書を入れなくても、`python3 scripts/polish.py ... --lightweight` なら標準ライブラリだけで動きます（固有名詞の自動保護と名詞構造の診断は省略）。

## 使い方

### Claude Code から

```text
/polish-japanese この文章をさっと自然にして
```

文章を平易にしたい、AI っぽさを消したい、といった依頼では、スキル名を書かなくても自動で使われることがあります。エージェントは診断 → 一回の言い換え → 数値等の照合を行い、叩き台と評価シートを返します。

### CLI として

`report` 以外のコマンドは JSON を標準出力に返します。入力に `-` を渡すと標準入力から読みます。

```bash
# 診断（行・列・文字オフセットと理由を返す）
polish-japanese analyze draft.md

# 定型的な修正候補だけを別ファイルに保存する（出力先が存在すれば中止）
polish-japanese fix draft.md --output candidate.md

# 修正前後の照合。推敲しても変えてはいけない語は --keep で指定する
polish-japanese verify draft.md candidate.md --keep '製品の正式名称'

# 何がどう変わったかの評価シート（Markdown）
polish-japanese report draft.md candidate.md

# 辞書なし（軽量モード）
python3 scripts/polish.py analyze draft.md --lightweight
```

`install.sh` を使わない場合は `python3 scripts/polish.py <コマンド> ... --dic <UniDic> --user-dic <neologd.dic>` で実行します。基本辞書は `NEOLOGD_DIC` 環境変数でも指定できます。

### 常に保護する語

社名・サービス名・製品名など、推敲しても変えてはいけない語は `~/.config/polish-japanese/keep.txt` に1行1語で書いておくと、`polish-japanese` の全コマンドで自動的に保護されます（`--keep` を毎回付けるのと同じ）。

```text
# 空行と # で始まる行は無視されます
ピックゴー
CBcloud
```

ここに書いた語は、形態素解析でも1語の固有名詞として扱われます。`polish-japanese` が `keep.txt` から小さなユーザー辞書（`~/.local/share/polish-japanese/dic/keep.dic`）を作り、NEologd と一緒に読み込むためです。`keep.txt` を編集すると、次の実行時に自動で作り直します（1 秒ほど）。

| 語 | 登録前 | 登録後 |
| --- | --- | --- |
| ピックゴー | ピック／ゴー | ピックゴー［固有名詞］ |
| CBcloud | CB／cloud | CBcloud［固有名詞］ |

空白・カンマ・引用符を含む語は1語として登録できないため、辞書には入れず保護（`--keep`）だけに使います。別のファイルを使うときは `--keep-file <ファイル>` か環境変数 `POLISH_KEEP_FILE` で指定します。

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
| `undefined-term` | 説明なしで使われた内部の呼び名（ファイル名・パス・識別子・オプション・PR 番号・インラインコード）。初出だけを指摘し、`（…）` や「とは」で説明していれば対象外 |
| `unsourced-claim` | 原文に根拠のない前提になりやすい言い方（「一般的に」「多くの企業」「調査によると」「と言われています」「お悩みの方も多い」「あるお客様から」、誰が言っているのかを隠した「として語られます」「とされます」「が求められています」「注目されています」など）。推敲でこうした言い方が増えたり消えたりすると、`verify` が要確認（`unsourced`）にする |
| `self-declared-importance` | 「ここで重要なのは」「大切なことは」「本質は」「ポイントは」のように、重要さを自分で宣言する前置き |
| `closing-suggestion` | 「〜してみてはいかがでしょうか」「いかがでしたか」のような締めの決まり文句（「ご都合はいかがでしょうか」のような本当の問いかけは対象外） |
| `inflated-language` | 圧倒的・革命的・画期的など、根拠のない強い言葉 |
| `abstract-stack` | 一つの文に抽象語が三つ以上 |
| `noun-heavy` | 名詞の割合が高い文 |
| `noun-chain` | 「の」で名詞が三つ以上つながる |
| `long-sentence` | 100 文字以上の文 |
| `redundant-opening` | 「まず最初に」 |
| `roundabout-capability` | 「〜することができます」 |

個別の指摘とは別に、評価シートには文書全体の傾向として、20文字未満の文・一文だけの行・箇条書きの行の割合を修正前後で並べます。文を細かく切る・一文ごとに改行する・箇条書きに崩す書き方の目安で、線引きは未校正のため良し悪しは判定しません。

コード・引用・URL・リンク・表・HTML タグ・`<script>` などは保守的に対象外にします。完全な Markdown パーサーではないため、特殊な構文は目で確認してください。

## テスト

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'   # 辞書なしで動くテスト（MeCab 連携はスキップ）

# 辞書を使う統合テストも含めて実行する
BASE=~/.local/share/polish-japanese
POLISH_TEST_DIC=$BASE/.venv/lib/python3.12/site-packages/unidic_lite/dicdir \
POLISH_TEST_USER_DIC=$BASE/dic/neologd.dic \
  $BASE/.venv/bin/python -m unittest discover -s scripts -p 'test_*.py'
```

## 注意点

- NEologd は「組織」「知見」などの一般語も固有名詞として解析することがあり、その語は診断対象から外れます。辞書の品詞を文脈上の正解とは扱いません。
- 形態素の閾値は初期の目安で、コーパスによる校正はしていません。
- 数値照合は漢数字やすべての単位を網羅しません。同じ語や数字を別の主語に付け替える変化は検出できません。
- 類似ツール（textlint-rule-preset-ai-writing、natural-japanese）との比較範囲は [references/sources-and-scope.md](references/sources-and-scope.md) にまとめています。性能の比較は未測定です。
- 辞書データは再配布していません。mecab-python3・UniDic・NEologd の利用条件は、それぞれの配布元に従ってください。
