#!/bin/bash
# polish-japanese を一括で導入する。何度実行しても、済んでいる手順は飛ばす。
#   1. ~/.claude/skills/polish-japanese にこのリポジトリへのシンボリックリンクを置く
#   2. MeCab（mecab-python3）と unidic-lite を入れた venv を作る
#   3. mecab-unidic-NEologd の seed を取得・照合し、ユーザー辞書をコンパイルする
#   4. 辞書を自動で指定する polish-japanese コマンドを置く
# 環境変数 POLISH_JA_HOME（既定 ~/.local/share/polish-japanese）と POLISH_JA_BIN（既定 ~/.local/bin）で導入先を変えられる。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${POLISH_JA_HOME:-$HOME/.local/share/polish-japanese}"
BIN="${POLISH_JA_BIN:-$HOME/.local/bin}"
SKILLS="$HOME/.claude/skills"
NEOLOGD_COMMIT=22895c054014393307967eddcd351c69e1fd57af
SEED_NAME=mecab-unidic-user-dict-seed.20200910.csv.xz
SEED_SHA1=2cef5d3c09296b199b3a0e384eb4bc70dc190cb5  # seed の Git blob SHA-1
PY="$BASE/.venv/bin/python"

step() { printf '\n==> %s\n' "$1"; }

step "スキルを配置"
mkdir -p "$SKILLS"
if [ -e "$SKILLS/polish-japanese" ] || [ -L "$SKILLS/polish-japanese" ]; then
  echo "既にあります: $SKILLS/polish-japanese"
else
  ln -s "$REPO" "$SKILLS/polish-japanese"
  echo "$SKILLS/polish-japanese -> $REPO"
fi

step "MeCab の venv を作成"
mkdir -p "$BASE/src" "$BASE/dic"
if [ ! -x "$PY" ]; then
  if command -v uv >/dev/null; then
    uv venv -q -p 3.12 "$BASE/.venv"
  else
    # uv がなければ Python 3.10 以降を探して venv を作る
    for cand in python3.13 python3.12 python3.11 python3.10 python3; do
      if command -v "$cand" >/dev/null && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
        "$cand" -m venv "$BASE/.venv"
        break
      fi
    done
    [ -x "$PY" ] || { echo "Python 3.10 以降か uv が必要です。" >&2; exit 1; }
  fi
fi
if ! "$PY" -c 'import MeCab, unidic_lite' 2>/dev/null; then
  if command -v uv >/dev/null; then
    uv pip install -q -p "$PY" mecab-python3==1.0.12 unidic-lite==1.0.8
  else
    "$PY" -m pip install -q mecab-python3==1.0.12 unidic-lite==1.0.8
  fi
fi
echo "OK: $PY"

step "NEologd 辞書を準備（初回は約 55MB をダウンロード）"
if [ -f "$BASE/dic/neologd.dic" ]; then
  echo "既にあります: $BASE/dic/neologd.dic"
else
  if [ ! -f "$BASE/src/$SEED_NAME" ]; then
    curl -fL --progress-bar -o "$BASE/src/$SEED_NAME.part" \
      "https://github.com/neologd/mecab-unidic-neologd/raw/$NEOLOGD_COMMIT/seed/$SEED_NAME"
    mv "$BASE/src/$SEED_NAME.part" "$BASE/src/$SEED_NAME"
  fi
  "$PY" - "$BASE/src/$SEED_NAME" "$BASE/dic/neologd.dic" "$SEED_SHA1" <<'EOF'
# seed を照合・展開し、mecab-python3 同梱の libmecab にある mecab_dict_index でユーザー辞書を作る。
# （mecab-python3 には mecab-dict-index コマンドが同梱されないため、関数を直接呼ぶ）
import ctypes, glob, hashlib, lzma, os, shutil, sys, tempfile
import MeCab, unidic_lite

seed, out, expected = sys.argv[1:]
data = open(seed, 'rb').read()
actual = hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()
if actual != expected:
    sys.exit(f'seed のハッシュが一致しません: {actual}')
pkg = os.path.dirname(MeCab.__file__)
libs = glob.glob(os.path.join(pkg, '.dylibs', 'libmecab*')) + glob.glob(os.path.join(os.path.dirname(pkg), '*.libs', 'libmecab*'))
if not libs:
    sys.exit('mecab-python3 同梱の libmecab が見つかりません。')
with tempfile.TemporaryDirectory() as tmp:
    csv = os.path.join(tmp, 'seed.csv')
    with lzma.open(seed) as src, open(csv, 'wb') as dst:
        shutil.copyfileobj(src, dst)
    args = ['mecab-dict-index', '-d', unidic_lite.DICDIR, '-u', out + '.part', '-f', 'UTF8', '-t', 'UTF8', csv]
    argv = (ctypes.c_char_p * len(args))(*[a.encode() for a in args])
    if ctypes.CDLL(libs[0]).mecab_dict_index(len(args), argv) != 0:
        sys.exit('辞書のコンパイルに失敗しました。')
os.replace(out + '.part', out)
EOF
  echo "OK: $BASE/dic/neologd.dic"
fi

step "polish-japanese コマンドを配置"
SYS_DIC="$("$PY" -c 'import unidic_lite; print(unidic_lite.DICDIR)')"
mkdir -p "$BIN"
# 自分で書き換えたラッパーは上書きしない（このスクリプトが生成したものだけ更新する）
if [ -e "$BIN/polish-japanese" ] && ! grep -q 'scripts/install.sh が生成' "$BIN/polish-japanese"; then
  echo "既存の $BIN/polish-japanese は手動で作られたものなので残します。"
else
cat > "$BIN/polish-japanese" <<EOF
#!/bin/bash
# polish-japanese のラッパー（scripts/install.sh が生成）。辞書を自動で指定して polish.py を実行する。
# --lightweight / --dic / --user-dic が明示されたときは、その指定を優先して辞書を足さない。
set -euo pipefail
PY="$PY"
SCRIPT="$REPO/scripts/polish.py"
for arg in "\$@"; do
  case "\$arg" in
    --lightweight|--dic|--dic=*|--user-dic|--user-dic=*) exec "\$PY" "\$SCRIPT" "\$@" ;;
  esac
done
if [ \$# -eq 0 ] || [[ "\$1" == -* ]]; then exec "\$PY" "\$SCRIPT" "\$@"; fi
exec "\$PY" "\$SCRIPT" "\$@" --dic "$SYS_DIC" --user-dic "$BASE/dic/neologd.dic"
EOF
chmod +x "$BIN/polish-japanese"
echo "OK: $BIN/polish-japanese"
fi

step "動作確認"
echo 'まず最初に、履歴を確認することができます。' | "$BIN/polish-japanese" analyze - \
  | "$PY" -c 'import json, sys; d = json.load(sys.stdin); print("mode:", d["engine"]["mode"], "/ 指摘:", len(d["findings"]), "件")'
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "注意: $BIN が PATH に入っていません。シェルの設定に追加してください。" ;;
esac
echo "完了しました。"
