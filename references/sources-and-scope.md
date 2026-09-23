# 根拠と比較範囲

参照確認日：2026-09-23。上流の内容は変わり得る。

- [mecab-unidic-neologd](https://github.com/neologd/mecab-unidic-neologd)：UniDicに新語・固有表現を加える辞書。文体の自然さや主張の妥当性を判定する辞書ではない。固有名詞分類の不完全さが上流でも説明されている。
- [Claude prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)：出力形式や望む文体を具体的に指示する考え方を参照。「日本語のAI臭さ」の公式な統一規格としては扱わない。
- [MeCab output formats](https://taku910.github.io/mecab/format.html)、[mecab-python3](https://github.com/SamuraiT/mecab-python3)：形態素と品詞の取得、辞書の明示指定を参照。
- [Claude Code skills](https://code.claude.com/docs/en/skills)：SKILL.mdと補助スクリプトを同梱する構造を参照。

## 比較対象

| 対象 | 公開資料で確認した範囲 | この試作の選択 |
| --- | --- | --- |
| [textlint-rule-preset-ai-writing](https://github.com/textlint-ja/textlint-rule-preset-ai-writing) | 誇張・強調・構造等のルール、MCP連携。一部はkuromojinによる形態素解析。 | 既存の静的検査を置き換えることは目的にせず、早い言い換え案までを一つのスキルにする。 |
| [natural-japanese](https://github.com/coji/natural-japanese) | SudachiPyの診断、生成前の構成設計、文体・読みやすさ・文書用途を含む推敲。クイックモードもある。 | 抽象語の重なり・大げささに範囲を絞り、既定は一回の言い換えで人に返す。 |

競合にも形態素解析・内容保持への配慮・クイックモードがある。辞書を変えたこと、速さを志向することだけで優位性を断定しない。この試作ではNEologdを選択できる診断と、数値・名前等の機械照合を組み合わせた。全seedをUniDicへ追加する構成の実機検証を済ませた（構成はruntime.mdに記録）。意味保持の完全保証はしない。

ルール・文章・コードは今回の用途に合わせて新規作成し、競合の実装をコピーしていない。辞書本体も同梱していない。各依存物の条件はそれぞれの配布元を参照する。

## 次に差を測るなら

実際の日本語文章を同じ条件で比較し、「すぐ意味が分かる」「言い過ぎが減る」「元の主張が残る」「最初の案まで待たない」を人に評価してもらう。無意味な修正の数も記録する。現段階で競合比較の実測結果や自然さの保証はない。
