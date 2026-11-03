"""PreToolUse hook: リポジトリ外のファイルを取り返しのつかない形で壊さないためのガード.

目的は「リポジトリ外のファイルの削除と，既存ファイルの上書きだけを確実に止める」こと．
新規作成・ダウンロード・ディレクトリ作成・追記は止めない（環境構築を自動で進められる
ようにするため）．グレーな操作の判断は権限モード（auto mode の分類器等）に任せ，
本フックは最後の砦として働く．読み取りは原則として制限しない（読ませたくない場所は
プロジェクトごとに禁止リストで指定する）．

判定の概要（対象がリポジトリ外の場合．許可ゾーンは常に許可）:
- 読み取り（Read / Glob / Grep，Bash / PowerShell の読み取り）: 許可する
- 新規作成・ダウンロード・mkdir・touch・追記（>>・Add-Content 等）・chmod 等: 許可する
- 削除（rm・Remove-Item・Clear-Content・truncate・find -delete 等）と移動元
  （mv・Move-Item・Rename-Item）: 拒否する
- 上書き（> リダイレクト・cp 等の書き込み先・sed -i・Set-Content・Out-File・
  tee・dd of=・curl -o 等の保存先・Write ツール）: 書き込み先が既に存在する
  場合だけ拒否する（無ければ新規作成なので許可）
- Edit / NotebookEdit: 既存ファイルの書き換えなので拒否する
- インライン実行（python -c 等）: 削除系の呼び出しがありリポジトリ外のパスを
  含む場合と，既知の上書き呼び出し（open(..., 'w') 等）の対象が既存ファイルの場合に拒否する
- 禁止リスト（deny_read）に該当するパスは，どの操作でも拒否する
- 入力の読み取りや判定で想定外の例外が起きたら拒否する（壊れた入力を含む．対象がリポジトリ外かは問わない）
- Bash / PowerShell のコマンドに NUL 文字があれば拒否する（コマンドの種類・対象を問わない）
- Bash / PowerShell のコマンドと Write / Edit / MultiEdit / NotebookEdit の書き込み先のパスが，
  項目はあるのに文字列でなければ拒否する（対象がリポジトリ外かは問わない）

リポジトリの基準は CLAUDE_PROJECT_DIR（無ければフック入力の cwd）．相対パスは
フック入力の cwd を起点に，コマンド内の cd / pushd / Set-Location 等を追跡して解決する．

禁止リスト: <リポジトリ>/.claude/repo-access.json
    {"deny_read": ["~/.ssh", "/etc", "secrets"]}
  絶対パス・`~` 始まり・リポジトリルート相対を受け付ける．ファイルが無ければ空，
  壊れていれば空として扱い stderr に警告する（警告を書けなければ想定外の例外として拒否する）．

許可ゾーン（リポジトリ外でも削除・上書きを許可する）:
- システム一時ディレクトリ（/tmp・%TEMP% 等．Claude Code のスクラッチパッドを含む）
  （/sync-template・/set-mode がテンプレートを mktemp -d に clone して cp・rm -rf
   するため．一時ディレクトリは使い捨て領域であり，許可しても失うものがない）．
  Linux / macOS ではリンクを解決した実パスで判定するので，一時ディレクトリの外へのリンク越しの
  操作は許可しない（rm 等がリンク自体を消すだけの場合は許可する）
- /dev/null・NUL・$null 等の特殊デバイス
- 今のプロジェクトの Claude メモのフォルダの配下（フック入力の transcript_path の親
  フォルダ直下の memory の中．メモの更新・修正・削除は Claude の通常の作業のため）．
  memory フォルダ自体の削除・移動，memory がシンボリックリンクの場合，他のプロジェクトの
  メモ，transcript_path が無い・絶対パスでない場合は許可しない．
  実際の対象を文字列から確定できないパス（波括弧・`..` を含む，途中の階層にワイルドカードを
  含む，コマンド内の cd 等の移動先がそれらやワイルドカードを含む）も許可しない．最後の要素
  だけのワイルドカード（memory/*・memory/*.md）は許可する（rm 等が一致したリンク自体を消すだけの
  場合はリンク先を判定しない．Bash で末尾に / があるとリンク先の中身が消えるので，その場合は判定する）．
  transcript_path の親の階層に [ や { があると，メモのフォルダの許可は効かない（安全側の制限）

パスの展開と解決（削除・上書き・作成の対象について）:
- 波括弧は Bash 系の方言でだけ展開して，元の文字列のままの判定に加えて展開後の各パスも判定する
  （PowerShell・cmd.exe は波括弧を展開しない）．展開数が上限を超えた語が削除・上書きの対象なら拒否する
- ワイルドカードはフック実行時に実際に展開して，一致した各パスも判定する（Bash・PowerShell．
  一致が無ければ文字列のままの判定だけ）
- `..` は文字列の正規化に加え，OS と同じ順に要素をたどる解決（リンク・ジャンクションを解決してから
  親に上がる）でも判定する．どちらかでリポジトリ外や既存ファイルに当たれば拒否・上書き扱いにする
- cd - / pushd - / Set-Location - / + / Push-Location - / + は，同じコマンド内で追跡できる場合だけ移動先を解決する

限界（重要）:
  本フックはコマンド文字列の静的解析であり，原理的に完全には防げない．変数展開
  （$DIR 等．$HOME・~ 以外は解決しない），スクリプトファイル経由の操作，
  パイプで渡した入力（xargs 等），エイリアス・関数，Base64 エンコードされた
  コマンド，未対応のコマンドによる削除・上書き，インライン実行中の未対応の書き方
  （変数に入れたパス・Perl の open 等）は検出できない．存在判定はフック実行時点の
  ものであり，同じコマンド内で先に作ったファイルを上書きする場合等は許可される．
  トークナイザーがクォートの情報を落とすため，クォート内の波括弧・ワイルドカードも展開して
  判定する（安全側）．`**` は 1 階層のワイルドカードとして扱う（globstar の再帰は追わない）．
  ワイルドカードの一致が多すぎると残りは調べず，基点がリポジトリ内・一時ディレクトリならそのまま
  許可，それ以外は拒否する．読み取り（禁止リスト）の判定はワイルドカードを展開しない．
  cmd.exe のコマンド・インライン実行のコードは波括弧・ワイルドカード・リンクの展開の対象外．
  Windows PowerShell 5.1 は Set-Location - / Push-Location - を受け付けず元の場所に留まるが，追跡は 7 系の挙動に合わせる．
  pushd -n <dir>（実際には移動しないが移動として扱う），サブシェル内の pushd / popd
  （スタックを復元しない）は，実際の対象とずれることがある．
  逆にインライン実行は，削除系の呼び出しとリポジトリ外のパス（文字列・コメント中の
  ものも含む）が同時に現れると，削除対象でなくても拒否することがある．
  cmd.exe の引数のうち /opt のように / の後に要素が 1 つだけのパスは，スイッチ
  （/mir 等）と区別できないためスイッチとして読み飛ばす．
  ユーザーが `!` で実行したコマンドには本フックはかからない．確実性が必要な場合は
  OS のフォルダ権限，settings.json の permissions.deny，Bash / PowerShell の実行前に
  確認を挟む権限モード，バックアップを併用すること（docs/01_GUIDE/GUIDE_01 の環境構築）．
"""

from __future__ import annotations

import fnmatch
import json
import os
import posixpath
import re
import stat
import sys
import tempfile
import traceback

CONFIG_RELPATH = ".claude/repo-access.json"

# 拒否理由に添える案内．リポジトリ外の削除・上書きの最終判断は人間が持つため，
# Claude に迂回させず，必要ならユーザーに `!` で実行してもらう
WRITE_DENY_GUIDANCE = (
    "リポジトリ外のファイルの削除・上書きは Claude が自分で実行しないでください．"
    "別の書き方（変数・スクリプトファイル・インライン実行等）で迂回しないでください．"
    "必要な操作であれば，実行するコマンドをそのままユーザーに提示し，"
    "プロンプトで `! <コマンド>` として実行するよう依頼してください"
)
DENY_READ_GUIDANCE = (
    "ユーザーが読ませたくない場所として指定しているパスです．"
    "別の書き方で迂回して読まないでください"
)
NUL_COMMAND_DENY_REASON = (
    "コマンドに NUL 文字が含まれていて対象を判定できないため，ブロックしました．"
    "別の書き方（変数・スクリプトファイル・インライン実行等）で迂回しないでください．"
    "NUL が意図しないものならコマンドを見直し，必要な操作であればユーザーに報告してください"
)
INVALID_VALUE_TYPE_DENY_REASON = (
    "ツールの入力のコマンドまたは書き込み先のパスが文字列ではなく，型が不正で対象を判定できないため，ブロックしました．"
    "別の書き方（変数・スクリプトファイル・インライン実行等）で迂回しないでください．"
    "入力を見直し，解決しなければユーザーに報告してください"
)
# 書き込みとして判定するファイル操作ツールと，そのパスの項目
WRITE_TOOL_PATH_KEYS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}

# 解決できないパスを表す（変数展開を含む等）．判定をスキップする
UNKNOWN = None
# 特殊デバイス（/dev/null 等）を表す
SPECIAL = "<special>"

POSIX_SPECIAL_PATHS = {
    "/dev/null",
    "/dev/zero",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/stdin",
    "/dev/tty",
}
WINDOWS_SPECIAL_NAMES = {"nul", "con", "$null", "nul:", "con:"}

# 操作の種別．DELETE は常に拒否，OVERWRITE は対象が既に存在すれば拒否，
# CREATE は禁止リストの判定だけ行う（新規作成・追記はリポジトリ外でも許可）
DELETE = "delete"
OVERWRITE = "overwrite"
CREATE = "create"
# ワイルドカードの一致が多すぎて全部は調べられなかった．基点が許可ゾーンでなければ拒否する
UNDECIDABLE = "undecidable"

# 波括弧の展開の上限（1 語あたりの展開数・語の長さ・ネストの深さ）．超えた語が削除・上書きの対象なら拒否する
MAX_BRACE_EXPANSIONS = 256
MAX_BRACE_WORD_LENGTH = 4096
MAX_BRACE_DEPTH = 32
# ワイルドカードの展開の上限（1 パスあたりの一致数・1 コマンドあたりの調べるディレクトリエントリ数）．
# フックの実行時間を抑えるため（タイムアウトすると検査が効かない）
MAX_WILDCARD_MATCHES = 1000
MAX_WILDCARD_SCANS = 10000
_wildcard_scans_left = [MAX_WILDCARD_SCANS]

# ---------------------------------------------------------------------------
# Bash: コマンドの分類
# ---------------------------------------------------------------------------
# 非オプション引数をすべて削除（中身を失う操作）の対象とみなすコマンド
BASH_DELETE = {"rm", "rmdir", "unlink", "shred", "truncate", "trash", "trash-put", "srm"}
# 最後の非オプション引数（または -t の値）を書き込み先とみなすコマンド．
# mv は移動元も削除扱い（元の場所から消える）
BASH_COPY = {"mv", "cp", "ln", "install", "rsync", "scp"}
# 値を取るオプション（値はパスとして扱わない．ただし -t は書き込み先）
BASH_OPTS_WITH_VALUE = {
    "cp": {"-t", "-S", "--target-directory", "--suffix"},
    "mv": {"-t", "-S", "--target-directory", "--suffix"},
    "ln": {"-t", "-S", "--target-directory", "--suffix"},
    "install": {"-t", "-m", "-o", "-g", "-S", "--target-directory", "--mode", "--owner", "--group", "--suffix"},
    "truncate": {"-s", "-r", "--size", "--reference"},
    "rsync": {"-e", "--rsh", "--exclude", "--include", "--filter", "-f"},
    "scp": {"-P", "-i", "-o", "-F", "-c", "-l", "-S", "-J"},
    "shred": {"-n", "-s", "--iterations", "--size"},
}
BASH_TARGET_DIR_OPTS = {"-t", "--target-directory"}
# 先頭に付いても実体のコマンドが後ろに続くもの
BASH_PREFIX_COMMANDS = {
    "sudo", "doas", "env", "command", "builtin", "exec", "nohup", "time",
    "nice", "ionice", "stdbuf", "xargs", "timeout", "unbuffer",
}
# VAR=値 形式の環境変数の指定
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# インライン実行を受け付けるインタプリタと，コード文字列を取るオプション
INTERPRETER_CODE_OPTS = {
    "python": {"-c"},
    "python3": {"-c"},
    "py": {"-c"},
    "node": {"-e", "--eval", "-p", "--print"},
    "deno": {"eval"},
    "bun": {"-e", "--eval", "-p", "--print"},
    "perl": {"-e", "-E"},
    "ruby": {"-e"},
    "php": {"-r"},
}
SHELL_COMMANDS = {"bash", "sh", "zsh", "dash", "ksh"}
POWERSHELL_COMMANDS = {"pwsh", "powershell"}
CMD_COMMANDS = {"cmd"}
# 再帰的に読むコマンド（引数の配下に禁止パスが含まれる場合も拒否する）
RECURSIVE_READ_COMMANDS = {
    "grep", "egrep", "fgrep", "rg", "ag", "find", "tar", "zip", "7z",
    "rsync", "cp", "scp", "du", "tree",
}
# インライン実行のコード中の削除系の呼び出し（Python / Node.js / Perl / Ruby / PowerShell）．
# 移動・名前変更も元の場所から消えるので含める
INLINE_DELETE_CALL = re.compile(
    r"rmtree|remove_tree|removedirs|\bos\.(?:remove|replace)\b|shutil\.move|"
    r"\b(?:unlink|rmdir|rename)(?:Sync)?\b|\brm(?:Sync)?\s*\(|\bfs\.(?:promises\.)?rm\b|"
    r"FileUtils\.(?:rm|remove|mv)\w*|\bFile\.delete\b|send2trash|(?i:Remove-Item)"
)
# インライン実行のコード中の上書き呼び出しと，その対象のリテラル（既存ファイルなら拒否）
_LIT = r"(?:'([^'\n]*)'|\"([^\"\n]*)\")"
INLINE_OVERWRITE_CALLS = (
    # Python: open('x', 'w') / open('x', mode='wb') 等
    re.compile(r"\bopen\s*\(\s*" + _LIT + r"\s*,\s*(?:mode\s*=\s*)?['\"][^'\"\n]*w"),
    # Python: Path('x').write_text(...) / write_bytes(...)
    re.compile(r"\bPath\s*\(\s*" + _LIT + r"\s*\)\s*\.write_(?:text|bytes)\b"),
    # Node.js: fs.writeFile('x') / writeFileSync / createWriteStream，Ruby: File.write('x')
    re.compile(r"(?:\bwriteFile(?:Sync)?|\bcreateWriteStream|\bFile\.write)\s*\(\s*" + _LIT),
)

# ---------------------------------------------------------------------------
# PowerShell: コマンドの分類（小文字で比較する）
# ---------------------------------------------------------------------------
# すべての位置引数とパス系パラメーターを削除の対象とみなす
PS_DELETE = {
    "remove-item", "rm", "del", "erase", "rd", "ri", "rmdir",
    "clear-content", "clc", "clear-item", "cli",
}
# 移動元（1 番目の位置引数・-Path）を削除，移動先（2 番目・-Destination）を書き込み先とみなす
PS_MOVE = {"move-item", "mv", "move", "mi"}
# 名前変更（1 番目の位置引数・-Path）は元の名前から消えるので削除扱い
PS_RENAME = {"rename-item", "ren", "rni"}
# 最初の位置引数とパス系パラメーターを上書きの対象とみなす
PS_OVERWRITE_FIRST = {"set-content", "sc", "set-item", "si"}
# -Append / -NoClobber が無ければ上書き，あれば新規作成・追記とみなす
PS_OVERWRITE_UNLESS_APPEND = {"out-file", "tee-object", "tee", "export-csv", "epcsv", "export-clixml"}
PS_NEW_ITEM = {"new-item", "ni"}
# 2 番目の位置引数と -Destination だけを書き込み先とみなす（1 番目は読み取り元）
PS_WRITE_DEST = {"copy-item", "cp", "copy", "cpi"}
# 1 番目の位置引数（-Path / -LiteralPath / -Source）は読み取り元，-DestinationPath
# （-Destination）または 2 番目の位置引数だけを書き込み先とみなす
# （Expand-Archive / Compress-Archive: -Path が位置 0，-DestinationPath が位置 1．
#   Start-BitsTransfer: -Source が位置 0，-Destination が位置 1．いずれも公式リファレンスの定義）
PS_WRITE_ARCHIVE_DEST = {"expand-archive", "compress-archive", "start-bitstransfer"}
# -OutFile の値を書き込み先とみなす（Bash 側の curl -o と揃える）．
# curl / wget は Windows PowerShell 5.1 では Invoke-WebRequest の別名，
# PowerShell 7 では curl.exe 等の実体なので，両方の書き方を見る
PS_WEB_REQUEST = {"invoke-webrequest", "iwr", "invoke-restmethod", "irm", "curl", "wget"}
PS_LOCATION_SET = {"set-location", "cd", "chdir", "sl"}
PS_LOCATION_PUSH = {"push-location", "pushd"}
PS_LOCATION_POP = {"pop-location", "popd"}
# 値がパスであるパラメーター
PS_PATH_PARAMS = {"path", "literalpath", "lp", "pspath", "filepath", "destination", "destinationpath"}
# 操作の対象（書き込み先ではなく対象そのもの）を表すパラメーター
PS_TARGET_PARAMS = {"path", "literalpath", "lp", "pspath", "filepath"}
# Copy-Item 等で読み取り元を表すパラメーター（書き込み対象にはしない）
PS_SOURCE_PARAMS = {"path", "literalpath", "lp", "pspath", "source"}
# 省略形を正式名に揃えるパラメーター（正式名, 省略できる最短の長さ）．
# PowerShell はパラメーター名を一意な前方一致で省略できる（-OutF / -Dest 等）
PS_PARAM_ABBREVIATIONS = (("outfile", 4), ("destination", 4), ("destinationpath", 12))
# 値を取るパラメーター（それ以外は switch とみなす）
PS_VALUE_PARAMS = PS_PATH_PARAMS | {
    "value", "encoding", "filter", "include", "exclude", "name", "newname",
    "itemtype", "type", "credential", "stream", "width", "inputobject",
    "delimiter", "depth", "aclobject", "compressionlevel", "variable",
    "stackname", "command", "c", "file", "f", "executionpolicy", "ep",
    "outfile", "infile", "uri", "method", "body", "headers", "contenttype",
    "useragent", "proxy", "timeoutsec", "source", "transfertype", "priority",
    "displayname", "description",
}
PS_RECURSIVE_READ = {
    "get-childitem", "gci", "ls", "dir", "select-string", "sls",
    "copy-item", "cp", "copy", "cpi", "compress-archive",
}
# [System.IO.File]::Method(引数) の呼び出し
PS_DOTNET_CALL = re.compile(r"\[(?:System\.)?IO\.(File|Directory)\]::(\w+)\s*\(([^)]*)\)", re.IGNORECASE)
# メソッド名（小文字）→ 引数位置ごとの操作種別
PS_DOTNET_KINDS = {
    "delete": (DELETE,),
    "move": (DELETE, OVERWRITE),
    "replace": (DELETE, DELETE),
    "copy": (CREATE, OVERWRITE),
    "openwrite": (OVERWRITE,),
    "encrypt": (OVERWRITE,),
    "decrypt": (OVERWRITE,),
}


# ---------------------------------------------------------------------------
# パスの解決と判定
# ---------------------------------------------------------------------------
def norm(path: str) -> str:
    """比較用に正規化する（realpath + 大文字小文字の正規化）."""
    return os.path.normcase(os.path.realpath(path))


def is_within_directory(target: str, base: str) -> bool:
    """target が base ディレクトリ配下にあるかを判定する（base 自体も含む）."""
    real_target = norm(target)
    real_base = norm(base)
    if real_target == real_base:
        return True
    if not real_base.endswith(os.sep):
        real_base += os.sep
    return real_target.startswith(real_base)


def is_real_path_in_temp(path: str, keep_final_link: bool) -> bool:
    """POSIX で，リンクを解決した path の実パスが /tmp か tempfile.gettempdir() の中にあるかを判定する.

    基準も実パスに解決して比べる（macOS の /tmp → /private/tmp，/var/folders/... → /private/var/folders/...）．
    存在しない要素は realpath が文字どおりに残すので，新規作成のパスは存在する部分までの解決で判定される．
    keep_final_link なら最後の要素のリンクを解決しない（リンク自体を消す操作の判定用）．
    """
    head, tail = os.path.split(path)
    if keep_final_link and tail not in ("", ".", ".."):
        real = os.path.join(os.path.realpath(head), tail)
    else:
        real = os.path.realpath(path)
    for base in ("/tmp", tempfile.gettempdir()):
        real_base = os.path.realpath(base)
        if real == real_base or real.startswith(real_base.rstrip("/") + "/"):
            return True
    return False


def is_temp_path(path: str, keep_final_link: bool = False) -> bool:
    """path がシステム一時ディレクトリ配下かを判定する.

    POSIX では文字列で /tmp 配下に見えても，リンクを解決した実パスが一時ディレクトリの中になければ
    配下と見なさない（/tmp/x/escape/file の escape が外へのリンクのとき，実際に壊れるのは外のファイル）．
    Windows では /tmp（Git Bash）が実パスに解決できないので文字列正規化で判定し，
    OS ネイティブの一時ディレクトリ（tempfile.gettempdir()）は実パスで判定する．
    どちらも `..` を畳んでから判定するので /tmp/../etc のような脱出は温存されない．
    keep_final_link は POSIX でだけ効く（Windows は従来どおりの判定のまま）．
    想定外の失敗は例外を外へ漏らさず，一時ディレクトリの外として扱う（安全側）．
    """
    try:
        if os.name != "nt":
            return is_real_path_in_temp(path, keep_final_link)
        normalized = posixpath.normpath(path.replace("\\", "/"))
        if normalized == "/tmp" or normalized.startswith("/tmp/"):
            return True
        return is_within_directory(path, tempfile.gettempdir())
    except Exception:
        return False


def home_dir() -> str:
    return os.path.expanduser("~")


def expand_home(path: str) -> str:
    """先頭の `~`（`~/`・`~\\` 始まりを含む）をホームディレクトリに展開する（`~user` は展開しない）."""
    if path == "~" or path.startswith("~/") or path.startswith("~\\"):
        return home_dir() + path[1:]
    return path


def convert_posix_drive(path: str) -> str:
    """Windows 上の Git Bash 形式 /c/Users/... を C:/Users/... に変換する."""
    if os.name != "nt":
        return path
    m = re.match(r"^/(?:cygdrive/)?([A-Za-z])(/|$)(.*)$", path, re.DOTALL)
    if m:
        return m.group(1).upper() + ":/" + m.group(3)
    return path


def is_absolute(path: str) -> bool:
    """path を絶対パスとして扱うか（cwd と結合しないか）を判定する.

    Python 3.13 以降の ntpath.isabs はドライブ無しの root-relative パス（`/tmp/x`・`\\tmp\\x`）を
    False と判定するようになり，cwd と結合されて `D:\\tmp\\x` 等に化ける（/tmp の判定を外れる）．
    Windows では Python のバージョンによらず `/`・`\\` 始まりを絶対パスとして扱い，3.12 以前の挙動に揃える．
    POSIX ホストでは os.path.isabs（posixpath）の判定をそのまま使う．
    """
    if os.name == "nt" and path[:1] in ("/", "\\"):
        return True
    return os.path.isabs(path)


def expand_variables(token: str, dialect: str) -> str | None:
    """`~`・$HOME 等の既知の変数を展開する．未知の変数が残れば None を返す."""
    token = expand_home(token)
    if token.startswith("~"):
        return UNKNOWN  # ~user 等は解決しない
    home = home_dir()
    temp = tempfile.gettempdir()
    if dialect == "powershell":
        replacements = [
            (r"\$\{?env:(?:USERPROFILE|HOME)\}?", home),
            (r"\$\{?env:(?:TEMP|TMP)\}?", temp),
            (r"\$\{?HOME\}?(?![\w:])", home),
        ]
    else:
        replacements = [
            (r"\$\{HOME\}|\$HOME(?!\w)", home),
            (r"\$\{(?:TMPDIR|TEMP|TMP)\}|\$(?:TMPDIR|TEMP|TMP)(?!\w)", temp),
        ]
    for pattern, value in replacements:
        token = re.sub(pattern, lambda _m, v=value: v.replace("\\", "/"), token, flags=re.IGNORECASE)
    if "$" in token or "`" in token:
        return UNKNOWN
    return token


def resolve_path(token: str, cwd: str | None, dialect: str = "bash") -> str | None:
    """コマンド中のパス文字列を絶対パスに解決する.

    特殊デバイスは SPECIAL，解決できない（未知の変数を含む・cwd 不明で相対）ものは
    UNKNOWN（None）を返す．
    """
    joined = absolute_unnormalized(token, cwd, dialect)
    if joined in (UNKNOWN, SPECIAL):
        return joined
    return os.path.normpath(joined)


def absolute_unnormalized(token: str, cwd: str | None, dialect: str = "bash") -> str | None:
    """resolve_path の手前まで（`..` を畳まない絶対パス）を返す．戻り値の扱いは resolve_path と同じ."""
    if not token:
        return UNKNOWN
    if dialect == "powershell":
        # プロバイダー修飾（Microsoft.PowerShell.Core\FileSystem::C:\x）を外す
        token = re.sub(r"^(?:Microsoft\.PowerShell\.Core\\)?FileSystem::", "", token, flags=re.IGNORECASE)
    if token.lower() in WINDOWS_SPECIAL_NAMES:
        return SPECIAL
    expanded = expand_variables(token, dialect)
    if expanded is None:
        return UNKNOWN
    token = expanded
    posix_form = posixpath.normpath(token.replace("\\", "/")) if token.startswith("/") else ""
    if posix_form in POSIX_SPECIAL_PATHS or posix_form.startswith("/dev/fd/"):
        return SPECIAL
    if dialect == "powershell" and re.match(r"^[A-Za-z][A-Za-z0-9]+:", token):
        return UNKNOWN  # Env: / HKLM: 等のファイルシステム以外のドライブ
    if dialect == "powershell" and os.name != "nt":
        # Linux / macOS の pwsh は `\` も区切りとして受け付けるが，os.path（posixpath）は `\` を
        # 区切りと見なさず `..\..` を畳めない（リポジトリ内と誤判定する）ため，判定前に `/` へ揃える
        token = token.replace("\\", "/")
    token = convert_posix_drive(token)
    if not is_absolute(token):
        if cwd is None:
            return UNKNOWN
        token = os.path.join(cwd, token)
    return token


def is_ambiguous_for_memory(path: str) -> bool:
    """メモのフォルダの配下と文字列から確定できないパスかを判定する.

    波括弧はシェルが展開すると `..` で外に出ることがあり，`..` は途中のリンクの先の親として
    OS が解決し（memory/link/../x），途中の階層のワイルドカードはリンクに一致しうる．
    いずれも normpath の結果からは分からない．最後の要素だけのワイルドカードは対象にしない．
    """
    if "{" in path or "}" in path:
        return True
    parts = re.split(r"[/\\]", path)
    return ".." in parts or any(re.search(r"[*?\[]", part) for part in parts[:-1])


# ---------------------------------------------------------------------------
# 波括弧の展開（Bash 系の方言のみ）
# ---------------------------------------------------------------------------
class BraceLimitExceeded(Exception):
    """波括弧の展開が上限を超えた."""


BRACE_INT_SEQUENCE = re.compile(r"^(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?$")
BRACE_CHAR_SEQUENCE = re.compile(r"^([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?$")
BRACE_ZERO_PADDED = re.compile(r"^-?0\d")


def expand_brace_sequence(body: str) -> list | None:
    """{1..3}・{a..e}・{01..10..2} の中身を展開する．順序式でなければ None を返す."""
    m = BRACE_INT_SEQUENCE.match(body)
    if m:
        start, end = int(m.group(1)), int(m.group(2))
        padded = BRACE_ZERO_PADDED.match(m.group(1)) or BRACE_ZERO_PADDED.match(m.group(2))
        width = max(len(m.group(1)), len(m.group(2))) if padded else 0
    else:
        m = BRACE_CHAR_SEQUENCE.match(body)
        if not m:
            return None
        start, end = ord(m.group(1)), ord(m.group(2))
        width = None
    step = abs(int(m.group(3))) if m.group(3) else 1
    step = step or 1
    if abs(end - start) // step + 1 > MAX_BRACE_EXPANSIONS:
        raise BraceLimitExceeded()
    numbers = range(start, end + 1, step) if start <= end else range(start, end - 1, -step)
    if width is None:
        return [chr(n) for n in numbers]
    return ["%0*d" % (width, n) for n in numbers]


def split_brace_body(word: str, start: int) -> tuple | None:
    """word[start] の `{` に対応する `}` を探し，(閉じ位置, トップレベルのコンマの位置のリスト) を返す."""
    depth = 0
    commas: list = []
    for i in range(start, len(word)):
        c = word[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i, commas
        elif c == "," and depth == 1:
            commas.append(i)
    return None


def expand_brace_recursive(word: str, depth: int) -> list:
    if depth > MAX_BRACE_DEPTH:
        raise BraceLimitExceeded()
    i = 0
    while i < len(word):
        if word[i] != "{":
            i += 1
            continue
        found = split_brace_body(word, i)
        if i > 0 and word[i - 1] == "$":  # ${VAR} は波括弧の展開ではない
            if found is None:
                return [word]
            i = found[0] + 1
            continue
        if found is None:
            i += 1  # 閉じていない `{` は文字どおり（後ろに別の組があれば Bash はそれを展開する）
            continue
        end, commas = found
        if commas:
            bounds = [i] + commas + [end]
            alternatives: list = []
            for left, right in zip(bounds, bounds[1:]):
                alternatives += expand_brace_recursive(word[left + 1:right], depth + 1)
                if len(alternatives) > MAX_BRACE_EXPANSIONS:
                    raise BraceLimitExceeded()
        else:
            alternatives = expand_brace_sequence(word[i + 1:end])
            if alternatives is None:
                i += 1  # {a} や {} は文字どおり（内側の {{a,b}} は続きの走査で展開される）
                continue
        rest = expand_brace_recursive(word[end + 1:], depth + 1)
        if len(alternatives) * len(rest) > MAX_BRACE_EXPANSIONS:
            raise BraceLimitExceeded()
        return [word[:i] + a + r for a in alternatives for r in rest]
    return [word]


def expand_braces(word: str) -> list | None:
    """Bash の波括弧展開の結果を返す．展開しない語は [word]，上限を超えた・解釈に失敗した語は None."""
    if "{" not in word:
        return [word]
    if len(word) > MAX_BRACE_WORD_LENGTH:
        return None
    try:
        return expand_brace_recursive(word, 0)
    except Exception:  # 上限超過・想定外の失敗はどちらも「展開できなかった」として安全側で扱う
        return None


def raw_comes_from_word(raw: str, word: str) -> bool:
    """エントリの文字列 raw が語 word（of=x・--output=x・-ox の形を含む）に由来するかを判定する."""
    if not raw:
        return False
    return raw == word or word.endswith("=" + raw) or (word.startswith("-") and word.endswith(raw))


# ---------------------------------------------------------------------------
# OS と同じ順に要素をたどる解決（リンクの後の `..`・ワイルドカード）
# ---------------------------------------------------------------------------
# 一致したリンク自体を消すだけのコマンド（リンク先の中身には触らない）．
# truncate・shred・Clear-Content はリンク先の中身を壊すので含めない
REMOVES_LINK_ITSELF = (
    (BASH_DELETE - {"truncate", "shred", "srm"}) | {"mv"} | PS_MOVE | PS_RENAME
    | (PS_DELETE - {"clear-content", "clc", "clear-item", "cli"})
)


def has_wildcard(text: str) -> bool:
    return re.search(r"[*?\[]", text) is not None


def is_link_or_junction(path: str) -> bool:
    """path がシンボリックリンク・ジャンクションかを判定する（Python 3.7 でも使える書き方）."""
    try:
        st = os.lstat(path)
    except (OSError, ValueError):
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def split_path_elements(path: str) -> tuple:
    """絶対パスを (起点, 要素のリスト) に分ける."""
    drive, rest = os.path.splitdrive(path)
    separators = r"[/\\]" if os.name == "nt" else "/"
    anchor = drive + os.sep if re.match(separators, rest) else drive
    return anchor, [part for part in re.split(separators, rest) if part not in ("", ".")]


def wildcard_matches(directory: str, pattern: str) -> tuple:
    """directory 直下で pattern に一致する名前を (名前のリスト, 打ち切ったか) で返す.

    ドットで始まる名前も一致させる（PowerShell は一致する．Bash では dotglob の場合だけなので安全側）．
    """
    names: list = []
    try:
        with os.scandir(directory) as it:
            for entry in it:
                if _wildcard_scans_left[0] <= 0 or len(names) >= MAX_WILDCARD_MATCHES:
                    return names, True
                _wildcard_scans_left[0] -= 1
                if fnmatch.fnmatch(entry.name, pattern):
                    names.append(entry.name)
    except OSError:
        pass
    return names, False


def walk_path(path: str, wildcards: bool, skip_final_links: bool) -> tuple:
    """OS と同じ順に要素をたどって実際の対象を求め，(パスのリスト, 打ち切ったか) を返す.

    途中のリンク・ジャンクションは解決してから次の要素へ進むので，`..` はリンク先の親へ上がる．
    最後の要素のリンクは解決しない（判定側で見る）．存在しない要素は文字どおりに進める．
    要素と同じ名前のものが実在するときはワイルドカードとして扱わない（親の階層の `[x]` 等）．
    ワイルドカードを含むパスは，最後まで実在するものだけを一致とする．
    """
    anchor, elements = split_path_elements(path)
    current = [anchor]
    truncated = False
    expanded = False
    for index, element in enumerate(elements):
        is_last = index == len(elements) - 1
        following: list = []
        for base in current:
            if element == "..":
                following.append(os.path.dirname(base) or base)
                continue
            names = [element]
            wild = wildcards and has_wildcard(element) and not os.path.lexists(os.path.join(base, element))
            if wild:
                expanded = True
                names, cut = wildcard_matches(base, element)
                truncated = truncated or cut
            for name in names:
                candidate = os.path.join(base, name)
                if is_link_or_junction(candidate):
                    if is_last and wild and skip_final_links:
                        continue
                    if not is_last:
                        candidate = os.path.realpath(candidate)
                following.append(candidate)
        current = list(dict.fromkeys(following))
        if len(current) > MAX_WILDCARD_MATCHES:
            current = current[:MAX_WILDCARD_MATCHES]
            truncated = True
    if expanded:
        current = [p for p in current if os.path.lexists(p)]
    return current, truncated


def reaches_link_target_by_trailing_separator(raw: str, dialect: str) -> bool:
    """raw の末尾の区切りで，一致したリンクの先までたどられるかを判定する.

    Bash の rm -rf `*/` はリンク自体を残してリンク先の中身を消す（Git Bash・WSL で実測）．
    PowerShell の Remove-Item `*/` は末尾の区切りがあってもリンク自体を消すだけなので対象にしない
    （Windows の 5.1・7.6 で実測）．`\\` が区切りになるのは Windows だけ．
    """
    if dialect == "powershell":
        return False
    return raw.endswith(("/", "\\") if os.name == "nt" else "/")


def walked_targets(raw: str, cwd: str | None, dialect: str, wildcards: bool = True,
                   skip_final_links: bool = False) -> tuple:
    """raw の実際の対象を (パスのリスト, 打ち切ったか) で返す．`..` もワイルドカードも無ければ空.

    OS と同じ順にたどる解決に加え，`..` を文字列で畳んだ形（Windows の PowerShell の解決）のワイルドカードも
    展開する．想定外の失敗は例外を外へ漏らさず「打ち切った」として返す（基点が許可ゾーンでなければ拒否される）．
    """
    try:
        joined = absolute_unnormalized(raw, cwd, dialect)
        if joined in (UNKNOWN, SPECIAL):
            return [], False
        follow_final_links = skip_final_links and reaches_link_target_by_trailing_separator(raw, dialect)
        if follow_final_links:
            skip_final_links = False
        elements = split_path_elements(joined)[1]
        has_parent = ".." in elements
        wild = wildcards and any(has_wildcard(e) for e in elements)
        if not (has_parent or wild or follow_final_links):
            return [], False
        found, truncated = walk_path(joined, wildcards, skip_final_links)
        if has_parent and wild:
            more, cut = walk_path(os.path.normpath(joined), wildcards, skip_final_links)
            found = list(dict.fromkeys(found + more))
            truncated = truncated or cut
        if follow_final_links:
            # 実際に消えるのはリンク先の中身なので，その実パスも判定する．リンク自体のパスのままだと，
            # リンク自体を消す操作として（removes_only_link）一時ディレクトリの中と見なされ素通りする
            found += [os.path.realpath(path) for path in found if is_link_or_junction(path)]
        return found, truncated
    except Exception:
        return [], True


def path_entries(name: str, raw: str, cwd: str | None, dialect: str, kind: str, wildcards: bool = True) -> list:
    """raw の判定エントリを作る．文字列のままの解決に，実際にたどった結果のパスを加える."""
    resolved = resolve_path(raw, cwd, dialect)
    result = [(name, raw, resolved, kind)]
    if resolved in (UNKNOWN, SPECIAL):
        return result
    found, truncated = walked_targets(raw, cwd, dialect, wildcards, name in REMOVES_LINK_ITSELF)
    result += [(name, raw, path, kind) for path in found if path != resolved]
    if truncated and kind != CREATE:
        result.append((name, raw, glob_base(resolved), UNDECIDABLE))
    return result


def looks_absolute(text: str) -> bool:
    """コード文字列中のリテラルがリポジトリ外を指しうるパスに見えるかを判定する."""
    return bool(
        re.match(r"^(?:/[^/\s]|~(?:[/\\]|$)|[A-Za-z]:[\\/]|\\\\|\.\.(?:[/\\]|$))", text)
    )


# ---------------------------------------------------------------------------
# 判定コンテキスト
# ---------------------------------------------------------------------------
class Policy:
    def __init__(self, repo_root: str, deny_read: list, transcript_path: str | None = None) -> None:
        self.repo_root = repo_root
        self.deny_read = deny_read
        self.transcript_path = transcript_path

    def denied_entry(self, path: str) -> str | None:
        """path が禁止リストのいずれかの配下なら，その禁止エントリを返す."""
        for entry in self.deny_read:
            if is_within_directory(path, entry):
                return entry
        return None

    def contained_denied_entry(self, path: str) -> str | None:
        """path（リポジトリ外の検索起点）の配下に禁止パスがあれば，その禁止エントリを返す."""
        if self.is_inside_repo(path) or is_temp_path(path):
            return None
        for entry in self.deny_read:
            if is_within_directory(entry, path):
                return entry
        return None

    def is_inside_repo(self, path: str) -> bool:
        return is_within_directory(path, self.repo_root)

    def is_inside_memory(self, path: str) -> bool:
        """path が今のプロジェクトのメモのフォルダの配下かを判定する（フォルダ自体は含まない）.

        基準は親フォルダだけを実パスに解決し，memory の部分は解決しない．両方を解決して
        比べると，memory がシンボリックリンクのときにリンク先の配下まで許可してしまう．
        この形なら対象の実パスがリンク先に解決されるので基準から外れて拒否になる．
        """
        transcript = self.transcript_path
        if not isinstance(transcript, str) or not transcript or "\0" in transcript or not is_absolute(transcript):
            return False  # NUL を含むと realpath が例外を出し，どのパスの判定でも拒否になってしまう
        base = os.path.join(norm(os.path.dirname(os.path.normpath(transcript))), "memory")
        return norm(path).startswith(base + os.sep)

    def is_write_allowed(self, path: str, raw: str | None = None, keep_final_link: bool = False) -> bool:
        """path（解決済み）への書き込みを許可するかを判定する．raw はコマンド中の元の文字列.

        resolve_path の normpath で `..` が消えるので，元の文字列も見る．コピー先のエントリでは
        コピー元の名前に由来する部分が解決済みのパスにしか現れないので，両方を見る必要がある．
        keep_final_link は一時ディレクトリの判定で最後の要素のリンクを解決しない（removes_only_link 参照）．
        """
        if self.is_inside_repo(path) or is_temp_path(path, keep_final_link):
            return True
        if is_ambiguous_for_memory(path) or (raw is not None and is_ambiguous_for_memory(raw)):
            return False
        return self.is_inside_memory(path)


def deny_read_message(target: str, entry: str) -> str:
    """禁止リストに該当したときの拒否理由を組み立てる."""
    return (
        f"禁止リスト（{CONFIG_RELPATH}）のパスへのアクセスをブロックしました: "
        f"{target}（禁止: {entry}）．{DENY_READ_GUIDANCE}"
    )


def load_deny_read(repo_root: str) -> list:
    """`.claude/repo-access.json` の deny_read を絶対パスのリストとして読み込む."""
    config_path = os.path.join(repo_root, ".claude", "repo-access.json")
    if not os.path.exists(config_path):
        return []
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        entries = config.get("deny_read", []) if isinstance(config, dict) else None
        if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
            raise ValueError("deny_read は文字列の配列で指定してください")
    except (OSError, ValueError) as e:
        # print(file=sys.stderr) は sys.stderr が None だと stdout に出て，拒否の JSON を壊す．
        # write なら例外になり，stderr に書けないときと同じく deny_on_exception で拒否される
        sys.stderr.write(f"[restrict_repo_access] {CONFIG_RELPATH} を読み込めないため禁止リストを空として扱います: {e}\n")
        return []
    result = []
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        entry = convert_posix_drive(expand_home(entry))
        if not is_absolute(entry):
            entry = os.path.join(repo_root, entry)
        result.append(os.path.normpath(entry))
    return result


# ---------------------------------------------------------------------------
# トークナイザー
# ---------------------------------------------------------------------------
# トークンは [種別, 文字列, 付随情報] のリスト．種別:
#   "word"   : 単語（クォート除去済み）
#   "sep"    : コマンドの区切り（; && || | & 改行）
#   "open"   : サブシェル開始 "(" ， "close": サブシェル終了 ")"
#   "redir"  : リダイレクト演算子（> >> < << <<< 等）
#   "heredoc": ヒアドキュメントの本文（直前の redir "<<" の後の word が区切り語）
def tokenize_bash(command: str) -> list:
    tokens: list = []
    buf: list = []
    in_word = False
    pending_heredocs: list = []  # [区切り語, タブ除去するか, 本文を格納するトークン]
    i = 0
    n = len(command)

    def flush() -> None:
        nonlocal in_word, buf
        if in_word:
            tokens.append(["word", "".join(buf), None])
        buf = []
        in_word = False

    while i < n:
        c = command[i]
        if c == "'":
            end = command.find("'", i + 1)
            end = n if end == -1 else end
            buf.append(command[i + 1:end])
            in_word = True
            i = end + 1
            continue
        if c == '"':
            j = i + 1
            while j < n and command[j] != '"':
                if command[j] == "\\" and j + 1 < n and command[j + 1] in '"\\$`\n':
                    buf.append(command[j + 1])
                    j += 2
                    continue
                buf.append(command[j])
                j += 1
            in_word = True
            i = j + 1
            continue
        if c == "\\":
            if i + 1 < n:
                if command[i + 1] != "\n":
                    buf.append(command[i + 1])
                    in_word = True
                i += 2
            else:
                i += 1
            continue
        if c in " \t\r":
            flush()
            i += 1
            continue
        if c == "\n":
            flush()
            tokens.append(["sep", "\n", None])
            i += 1
            if pending_heredocs:
                for delim, strip_tabs, holder in pending_heredocs:
                    body_lines = []
                    while i < n:
                        end = command.find("\n", i)
                        line = command[i:] if end == -1 else command[i:end]
                        i = n if end == -1 else end + 1
                        check = line.lstrip("\t") if strip_tabs else line
                        if check.rstrip("\r") == delim:
                            break
                        body_lines.append(line)
                    holder[1] = "\n".join(body_lines)
                pending_heredocs = []
            continue
        if c == "#" and not in_word:
            end = command.find("\n", i)
            i = n if end == -1 else end
            continue
        if c in ";&|":
            flush()
            two = command[i:i + 2]
            if two in ("&&", "||", ";;", "|&"):
                tokens.append(["sep", two, None])
                i += 2
            elif c == "&" and command[i + 1:i + 2] == ">":
                op = "&>>" if command[i + 1:i + 3] == ">>" else "&>"
                tokens.append(["redir", op, None])
                i += len(op)
            else:
                tokens.append(["sep", c, None])
                i += 1
            continue
        if c in "()`":
            flush()
            if c == "(":
                tokens.append(["open", c, None])
            elif c == ")":
                tokens.append(["close", c, None])
            else:
                tokens.append(["sep", c, None])
            i += 1
            continue
        if c in "<>":
            # 直前の単語が数字だけならファイルディスクリプタ番号（2> 等）
            if in_word and "".join(buf).isdigit():
                buf = []
                in_word = False
            else:
                flush()
            for op in ("<<<", "<<-", ">>", ">|", ">&", "<<", "<&", "<>", ">", "<"):
                if command.startswith(op, i):
                    break
            tokens.append(["redir", op, None])
            i += len(op)
            if op in ("<<", "<<-"):
                # 区切り語を先読みして本文の取り込みを予約する
                m = re.match(r"\s*(['\"]?)([^\s'\";&|<>()]+)\1", command[i:])
                if m:
                    holder = ["heredoc", "", None]
                    tokens.append(["word", m.group(2), None])
                    tokens.append(holder)
                    pending_heredocs.append([m.group(2), op == "<<-", holder])
                    i += m.end()
            continue
        buf.append(c)
        in_word = True
        i += 1
    flush()
    return tokens


def tokenize_powershell(command: str) -> list:
    tokens: list = []
    buf: list = []
    in_word = False
    i = 0
    n = len(command)

    def flush() -> None:
        nonlocal in_word, buf
        if in_word:
            tokens.append(["word", "".join(buf), None])
        buf = []
        in_word = False

    while i < n:
        c = command[i]
        # ヒア文字列 @' ... '@ / @" ... "@
        if c == "@" and not in_word and command[i + 1:i + 2] in ("'", '"'):
            quote = command[i + 1]
            end = command.find("\n" + quote + "@", i + 2)
            end = n if end == -1 else end
            tokens.append(["word", command[i + 2:end].lstrip("\r\n"), None])
            i = end + 3
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if command[j] == "'":
                    if command[j + 1:j + 2] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(command[j])
                j += 1
            in_word = True
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            while j < n:
                if command[j] == "`" and j + 1 < n:
                    buf.append(command[j + 1])
                    j += 2
                    continue
                if command[j] == '"':
                    if command[j + 1:j + 2] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    break
                buf.append(command[j])
                j += 1
            in_word = True
            i = j + 1
            continue
        if c == "`":
            if i + 1 < n and command[i + 1] != "\n":
                buf.append(command[i + 1])
                in_word = True
            i += 2
            continue
        if c == "<" and command[i + 1:i + 2] == "#":
            end = command.find("#>", i + 2)
            i = n if end == -1 else end + 2
            continue
        if c == "#" and not in_word:
            end = command.find("\n", i)
            i = n if end == -1 else end
            continue
        if c in " \t\r,":
            flush()
            i += 1
            continue
        if c in "\n;{}()":
            flush()
            tokens.append(["sep", c, None])
            i += 1
            continue
        if c in "&|":
            flush()
            two = command[i:i + 2]
            if two in ("&&", "||"):
                tokens.append(["sep", two, None])
                i += 2
            else:
                tokens.append(["sep", c, None])
                i += 1
            continue
        if c == ">" or (c == "<" and not in_word):
            # 2> / *> 等のストリーム番号
            if in_word and "".join(buf) in ("*", "1", "2", "3", "4", "5", "6"):
                buf = []
                in_word = False
            else:
                flush()
            if command.startswith(">>", i):
                op = ">>"
            elif command.startswith(">&", i):
                op = ">&"
            else:
                op = c
            tokens.append(["redir", op, None])
            i += len(op)
            continue
        buf.append(c)
        in_word = True
        i += 1
    flush()
    return tokens


def split_segments(tokens: list) -> list:
    """トークン列を単純コマンドごとに分割する．サブシェルの開始・終了も要素として残す."""
    segments: list = []
    current: list = []
    for tok in tokens:
        if tok[0] in ("sep", "open", "close"):
            if current:
                segments.append(current)
                current = []
            if tok[0] in ("open", "close"):
                segments.append(tok[0])
            continue
        current.append(tok)
    if current:
        segments.append(current)
    return segments


# ---------------------------------------------------------------------------
# 解析結果の収集
# ---------------------------------------------------------------------------
class Findings:
    """コマンド解析で見つかった操作を集める."""

    def __init__(self) -> None:
        self.writes: list = []  # (コマンド名, 元の文字列, 解決済みパス, 操作種別)
        self.reads: list = []  # (コマンド名, 元の文字列, 解決済みパス, 再帰的か)
        self.inline: list = []  # (コマンド名, リテラル, 解決済みパス, 操作種別)
        # cd 等の移動先を文字列から確定できなかった（追跡中の cwd からはメモの配下と言えない）
        self.ambiguous_cd = False
        self.unexpandable: list = []  # (コマンド名, 元の文字列) 波括弧を展開できなかった削除・上書きの対象

    def note_location_change(self, dest: str) -> None:
        """cd 等の移動先を記録する．移動先は最後の要素のワイルドカードも確定できない."""
        if is_ambiguous_for_memory(dest + "/"):  # 末尾に区切りを足して最後の要素も途中の階層として見る
            self.ambiguous_cd = True


def normalize_literal(literal: str) -> str:
    """Python の raw 文字列や二重バックスラッシュを素朴に正規化する."""
    return literal.replace("\\\\", "\\")


def collect_inline_literals(code: str, cmd_name: str, cwd: str | None, findings: Findings, dialect: str) -> None:
    """インラインコード中の文字列リテラルからパスらしいものを拾う.

    削除系の呼び出しがあれば，リポジトリ外を指しうるリテラルをすべて削除対象とみなす
    （どのリテラルが削除対象かまでは判別しない）．既知の上書き呼び出しの対象は上書き，
    それ以外は禁止リストの判定だけに使う．
    """
    has_delete = INLINE_DELETE_CALL.search(code) is not None
    overwrite_literals = set()
    for pattern in INLINE_OVERWRITE_CALLS:
        for m in pattern.finditer(code):
            overwrite_literals.add(normalize_literal(m.group(1) or m.group(2) or ""))
    for m in re.finditer(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`", code):
        literal = normalize_literal(m.group(1) or m.group(2) or m.group(3) or "")
        if not (looks_absolute(literal) or literal in overwrite_literals):
            continue
        resolved = resolve_path(literal, cwd, dialect)
        if resolved in (UNKNOWN, SPECIAL):
            continue
        kind = DELETE if has_delete else (OVERWRITE if literal in overwrite_literals else CREATE)
        findings.inline.append((cmd_name, literal, resolved, kind))


def collect_dotnet_calls(command: str, cwd: str | None, findings: Findings) -> None:
    """PowerShell 中の [System.IO.File]::Delete('x') 等を，引数位置ごとの操作種別で拾う."""
    for m in PS_DOTNET_CALL.finditer(command):
        method = m.group(2).lower()
        if m.group(1).lower() == "directory" and method not in ("delete", "move"):
            continue  # CreateDirectory 等は新規作成
        if method.startswith(("write", "create")):
            kinds: tuple = (OVERWRITE,)
        else:
            kinds = PS_DOTNET_KINDS.get(method, ())
        for pos, arg in enumerate(m.group(3).split(",")[:len(kinds)]):
            lit = re.search(r"'([^'\n]*)'|\"([^\"\n]*)\"", arg)
            if not lit:
                continue
            raw = lit.group(1) or lit.group(2) or ""
            resolved = resolve_path(raw, cwd, "powershell")
            if resolved not in (UNKNOWN, SPECIAL):
                findings.inline.append(("[System.IO." + m.group(1) + "]::" + m.group(2), raw, resolved, kinds[pos]))


# cmd.exe の削除・移動系コマンドと，書き込み先（最後のパス）を持つコピー系コマンド
CMD_DELETE = {"del", "erase", "rd", "rmdir", "move", "ren", "rename"}
CMD_COPY = {"copy", "xcopy", "robocopy"}

# cmd.exe のスイッチ（/s・/q・/-y・/?・/mir・/a:h・/log:C:\x 等）．`/` の直後が英字（または - + ?）で，
# 名前部分に区切り（`/`・`\`）を含まないもの．/workspace/x（POSIX の絶対パス）や /c/Users（Git Bash 形式）は
# 名前部分に 2 つ目の区切りを含むのでパスとして扱う（OS によって判定が変わらないようにする）
CMD_SWITCH = re.compile(r"^/[-+]?(?:\?|[A-Za-z][A-Za-z0-9]*)(?::.*)?$", re.DOTALL)


def is_cmd_switch(text: str) -> bool:
    """cmd.exe の引数 text がスイッチ（パスではない）かを判定する."""
    return bool(CMD_SWITCH.match(text))


def collect_cmd_exe(args: list, cwd: str | None, findings: Findings) -> None:
    """cmd /c 以降のコマンドから削除・上書きの対象と読み取りを拾う."""
    for part in re.split(r"&&|\|\||[&|]", " ".join(args)):
        words = [w[0] or w[1] for w in re.findall(r'"([^"]*)"|(\S+)', part)]
        if not words:
            continue
        sub = command_basename(words[0])
        paths: list = []
        redirect_kind = None
        for text in words[1:]:
            if text in (">", ">>"):
                redirect_kind = OVERWRITE if text == ">" else CREATE
                continue
            if text.startswith(">"):
                redirect_kind = CREATE if text.startswith(">>") else OVERWRITE
                text = text.lstrip(">")
            if is_cmd_switch(text):
                continue  # /s /q 等のスイッチ
            resolved = resolve_path(text, cwd, "powershell")
            if resolved in (UNKNOWN, SPECIAL):
                redirect_kind = None
                continue
            if redirect_kind:
                findings.writes.append(("cmd", text, resolved, redirect_kind))
                redirect_kind = None
                continue
            paths.append((text, resolved))
        switches = {w.lower() for w in words[1:] if is_cmd_switch(w)}
        for idx, (text, resolved) in enumerate(paths):
            findings.reads.append(("cmd " + sub, text, resolved, False))
            is_last = idx == len(paths) - 1 and idx > 0
            kind = None
            if sub in ("ren", "rename"):
                kind = DELETE if idx == 0 else None  # 2 番目は新しい名前
            elif sub == "robocopy" and switches & {"/mir", "/purge", "/mov", "/move"}:
                kind = DELETE
            elif sub in CMD_DELETE:
                kind = OVERWRITE if sub == "move" and is_last else DELETE
            elif sub in CMD_COPY and is_last:
                kind = OVERWRITE
            if kind == OVERWRITE and os.path.isdir(resolved):
                kind = None  # ディレクトリへのコピー・移動は書き込み先のファイル名を判断できない
            if kind:
                findings.writes.append(("cmd " + sub, text, resolved, kind))


def strip_bash_prefixes(words: list) -> list:
    """sudo / env / VAR=値 等のプレフィックスを取り除いた argv を返す."""
    idx = 0
    while idx < len(words):
        if ENV_ASSIGNMENT.match(words[idx]):
            idx += 1
            continue
        base = command_basename(words[idx])
        if base in BASH_PREFIX_COMMANDS:
            idx += 1
            # プレフィックスのオプションと，timeout の時間指定を読み飛ばす
            while idx < len(words) and (words[idx].startswith("-") or ENV_ASSIGNMENT.match(words[idx])):
                idx += 1
            if base == "timeout" and idx < len(words):
                idx += 1
            continue
        break
    return words[idx:]


def command_basename(word: str) -> str:
    base = os.path.basename(word.replace("\\", "/")).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if re.match(r"^python3(\.\d+)?$", base):
        return "python3"
    return base


def split_option_args(cmd: str, args: list) -> tuple:
    """args を (非オプション引数のリスト, -t 等の書き込み先ディレクトリのリスト) に分ける."""
    with_value = BASH_OPTS_WITH_VALUE.get(cmd, set())
    positional: list = []
    target_dirs: list = []
    idx = 0
    end_of_opts = False
    while idx < len(args):
        a = args[idx]
        if end_of_opts or not a.startswith("-") or a == "-":
            positional.append(a)
        elif a == "--":
            end_of_opts = True
        elif "=" in a and a.startswith("--"):
            name, value = a.split("=", 1)
            if name in BASH_TARGET_DIR_OPTS:
                target_dirs.append(value)
        elif a in with_value:
            if idx + 1 < len(args):
                if a in BASH_TARGET_DIR_OPTS:
                    target_dirs.append(args[idx + 1])
                idx += 1
        elif a[:2] in with_value and len(a) > 2 and not a.startswith("--"):
            if a[:2] in BASH_TARGET_DIR_OPTS:
                target_dirs.append(a[2:])
        idx += 1
    return positional, target_dirs


def is_remote(path: str) -> bool:
    """scp / rsync のリモート指定（host:path）かを判定する（C: 等のドライブは除く）."""
    return bool(re.match(r"^[^/\\]{2,}:", path))


def has_short_flag(args: list, letter: str) -> bool:
    """-f や -rf のような短いオプションの束に letter が含まれるかを判定する."""
    return any(re.match(r"^-[a-zA-Z]*" + letter, a) for a in args if not a.startswith("--"))


def copy_dest_entries(name: str, dest_raw: str, srcs: list, cwd: str | None, dialect: str,
                      into_dir: bool = False, contents_on_slash: bool = False) -> list:
    """コピー・移動の書き込み先を上書き判定用のエントリにする.

    書き込み先が既存のディレクトリ（または -t 指定）なら，実際に書き込まれる
    `<書き込み先>/<コピー元の名前>` を判定対象にする．コピー元の名前が分からない
    （rsync の `src/` のように中身を展開する等）場合は判断できないので対象にしない．
    """
    dest = resolve_path(dest_raw, cwd, dialect)
    if dest in (UNKNOWN, SPECIAL):
        return []
    walked_dests, truncated = walked_targets(dest_raw, cwd, dialect)
    bases: list = []
    for src in srcs:
        if contents_on_slash and src.endswith(("/", "\\")):
            continue
        base = posixpath.basename(src.replace("\\", "/").rstrip("/").split("?")[0])
        if base and base not in (".", "..") and "$" not in base:
            bases.append(base)
            if has_wildcard(base):  # コピー元のワイルドカードは，一致した各ファイルの名前で書き込まれる
                matched, cut = walked_targets(src, cwd, dialect)
                bases += [os.path.basename(path) for path in matched]
                truncated = truncated or cut
    entries = []
    for target in [dest] + [path for path in walked_dests if path != dest]:
        if into_dir or os.path.isdir(target):
            entries += [(name, dest_raw, os.path.join(target, base), OVERWRITE) for base in bases]
        else:
            entries.append((name, dest_raw, target, OVERWRITE))
    if truncated:
        entries.append((name, dest_raw, glob_base(dest), UNDECIDABLE))
    return entries


def short_option_values(args: list, letter: str) -> list:
    """`-o FILE`・`-sSLo FILE`・`-oFILE` 形式の値を取り出す（curl -o / wget -O 用）."""
    values = []
    for idx, a in enumerate(args):
        m = re.match(r"^-[a-zA-Z]*?" + letter + r"(.*)$", a)
        if not m or a.startswith("--"):
            continue
        value = m.group(1) or (args[idx + 1] if idx + 1 < len(args) else "")
        if value and value != "-":
            values.append(value)
    return values


def write_targets_bash(cmd: str, args: list, cwd: str | None, dialect: str = "bash") -> list:
    """Bash の単純コマンドから削除・上書き・作成の対象を (名前, 文字列, 解決済みパス, 種別) で取り出す."""

    def entries(raws: list, kind: str) -> list:
        return [e for r in raws for e in path_entries(cmd, r, cwd, dialect, kind)]

    if cmd in BASH_DELETE:
        positional, _ = split_option_args(cmd, args)
        return entries(positional, DELETE)
    if cmd in BASH_COPY:
        positional, target_dirs = split_option_args(cmd, args)
        if target_dirs:
            srcs, dest, into_dir = positional, target_dirs[0], True
        elif len(positional) >= 2:
            srcs, dest, into_dir = positional[:-1], positional[-1], False
        else:
            srcs, dest, into_dir = positional, None, False
        local_srcs = [s for s in srcs if not is_remote(s)]
        result = []
        # 移動元は元の場所から消える
        if cmd == "mv" or (cmd == "rsync" and "--remove-source-files" in args):
            result += entries(local_srcs, DELETE)
        if dest is None or is_remote(dest):
            return result
        if cmd == "ln" and not (has_short_flag(args, "f") or "--force" in args):
            return result  # -f が無ければ既存のリンク先を置き換えない
        if cmd in ("cp", "mv") and (has_short_flag(args, "n") or "--no-clobber" in args):
            return result
        if cmd == "install" and (has_short_flag(args, "d") or "--directory" in args):
            return result  # ディレクトリ作成
        if cmd == "rsync" and any(a == "--del" or a.startswith("--delete") for a in args):
            return result + entries([dest], DELETE)  # 書き込み先の余分なファイルを消す
        return result + copy_dest_entries(cmd, dest, local_srcs, cwd, dialect, into_dir, cmd == "rsync")
    if cmd == "tee":
        positional, _ = split_option_args(cmd, args)
        append = has_short_flag(args, "a") or "--append" in args
        return entries(positional, CREATE if append else OVERWRITE)
    if cmd == "sed":
        return entries(sed_inplace_targets(args), OVERWRITE)
    if cmd in ("perl", "ruby") and has_short_flag(args, "i"):
        return entries([a for a in interp_positional(args, {"-e", "-E", "-I", "-M", "-r"}) if a != "-"], OVERWRITE)
    if cmd == "dd":
        return entries([a[3:] for a in args if a.startswith("of=")], OVERWRITE)
    if cmd == "find":
        return entries(find_targets(args), DELETE)
    if cmd == "curl":
        return entries(option_values(args, {"--output"}) + short_option_values(args, "o"), OVERWRITE)
    if cmd == "wget":
        # -P（保存先ディレクトリ）は既存ファイルを上書きせず別名で保存するので対象外
        return entries(option_values(args, {"--output-document"}) + short_option_values(args, "O"), OVERWRITE)
    return []


def option_values(args: list, names: set) -> list:
    values = []
    for idx, a in enumerate(args):
        if a in names and idx + 1 < len(args):
            values.append(args[idx + 1])
        elif "=" in a and a.split("=", 1)[0] in names:
            values.append(a.split("=", 1)[1])
    return values


def interp_positional(args: list, value_opts: set) -> list:
    """-e CODE 等の値を除いた非オプション引数を返す（sed / perl -i 用）."""
    result = []
    idx = 0
    while idx < len(args):
        a = args[idx]
        if a in value_opts:
            idx += 2
            continue
        if not a.startswith("-") or a == "-":
            result.append(a)
        idx += 1
    return result


def sed_inplace_targets(args: list) -> list:
    inplace = False
    has_script_opt = False
    for a in args:
        if a.startswith("--in-place") or (re.match(r"^-[a-zA-Z]*i", a) and not a.startswith("--")):
            inplace = True
        if a in ("-e", "-f", "--expression", "--file") or a.startswith("--expression=") or a.startswith("--file="):
            has_script_opt = True
        elif re.match(r"^-[a-zA-Z]*[ef]$", a):
            has_script_opt = True
    if not inplace:
        return []
    positional = interp_positional(args, {"-e", "-f", "--expression", "--file", "-l", "--line-length"})
    if not has_script_opt and positional:
        positional = positional[1:]  # 先頭はスクリプト
    return positional


def find_targets(args: list) -> list:
    """find の -delete / -exec rm 等があれば検索起点を削除対象とみなす."""
    starts = []
    for a in args:
        if a.startswith("-") or a in ("(", "!", "\\("):
            break
        starts.append(a)
    destructive = "-delete" in args
    for idx, a in enumerate(args):
        if a in ("-exec", "-execdir", "-ok", "-okdir") and idx + 1 < len(args):
            sub = command_basename(args[idx + 1])
            if sub in BASH_DELETE or sub in BASH_COPY or sub in ("sed", "dd", "tee"):
                destructive = True
    if not destructive:
        return []
    return starts or ["."]


def record_bash_arg_reads(cmd: str, arg: str, cwd: str | None, findings: Findings) -> None:
    """Bash の引数 arg（--x=y・of=y の y も）を読み取り（禁止リスト判定用）として記録する."""
    for candidate in (arg, arg.split("=", 1)[1] if "=" in arg else None):
        if candidate:
            findings.reads.append((cmd, candidate, resolve_path(candidate, cwd), cmd in RECURSIVE_READ_COMMANDS))


def analyze_bash(command: str, cwd: str | None, findings: Findings, depth: int = 0) -> None:
    """Bash コマンドを解析して findings に書き込み・読み取りを追加する."""
    if depth > 3:
        return
    tokens = tokenize_bash(command)
    dir_stack: list = []
    subshell_stack: list = []
    previous_cwd = UNKNOWN  # cd - / pushd - の移動先（コマンド開始時点の OLDPWD は分からない）
    for segment in split_segments(tokens):
        if segment == "open":
            subshell_stack.append((cwd, previous_cwd))
            continue
        if segment == "close":
            if subshell_stack:
                cwd, previous_cwd = subshell_stack.pop()
            continue
        words: list = []
        heredoc_bodies: list = []
        herestrings: list = []
        idx = 0
        while idx < len(segment):
            kind, text, _ = segment[idx]
            if kind == "redir":
                target = segment[idx + 1][1] if idx + 1 < len(segment) and segment[idx + 1][0] == "word" else None
                idx += 2 if target is not None else 1
                if target is None:
                    continue
                if text in ("<<", "<<-"):
                    continue  # 区切り語
                if text == "<<<":
                    herestrings.append(target)
                    continue
                if text in (">&", "<&") and (target.isdigit() or target == "-"):
                    continue  # ファイルディスクリプタの複製
                expansions = expand_braces(target)
                variants = [target] + [v for v in (expansions or []) if v != target]
                if text == "<":
                    findings.reads += [("<", target, resolve_path(v, cwd), False) for v in variants]
                else:
                    # >> / &>> は追記，<> は切り詰めずに開くので新規作成扱い．> / >| / &> は上書き
                    op_kind = CREATE if text in (">>", "&>>", "<>") else OVERWRITE
                    if expansions is None and op_kind != CREATE:
                        findings.unexpandable.append((text, target))
                    for variant in variants:
                        # 元の文字列を残す（波括弧を含むパスはメモのフォルダで許可しないため）
                        findings.writes += [(text, target, e[2], e[3])
                                            for e in path_entries(text, variant, cwd, "bash", op_kind)]
                continue
            if kind == "heredoc":
                if text:
                    heredoc_bodies.append(text)
                idx += 1
                continue
            words.append(text)
            idx += 1
        argv = strip_bash_prefixes(words)
        if not argv:
            continue
        cmd = command_basename(argv[0])
        args = argv[1:]

        # 引数はすべて読み取り（禁止リスト判定用）として記録する
        for a in args:
            record_bash_arg_reads(cmd, a, cwd, findings)

        # cd / pushd / popd の追跡
        if cmd in ("cd", "pushd", "popd"):
            if cmd == "popd":
                previous_cwd = cwd
                cwd = dir_stack.pop() if dir_stack else UNKNOWN
                continue
            dest = [a for a in args if not (a.startswith("-") and a != "-")]
            if not dest:
                new_cwd = home_dir()
            elif dest[0] == "-":
                # pushd - は cd - と同じ場所へ移動する．pushd -n はディレクトリを移動しないので追跡しない
                new_cwd = previous_cwd if cmd == "cd" or "-n" not in args else UNKNOWN
            else:
                findings.note_location_change(dest[0])
                new_cwd = resolve_path(dest[0], cwd)
                if new_cwd == SPECIAL:
                    new_cwd = UNKNOWN
            if cmd == "pushd":
                dir_stack.append(cwd)
            previous_cwd = cwd
            cwd = new_cwd
            continue

        literal_entries = write_targets_bash(cmd, args, cwd)
        findings.writes += literal_entries
        collect_brace_expansions(words, cmd, literal_entries, cwd, findings)

        analyze_inline(cmd, args, heredoc_bodies + herestrings, cwd, findings, depth, "bash")


def collect_brace_expansions(words: list, cmd: str, literal_entries: list, cwd: str | None, findings: Findings) -> None:
    """単純コマンドの語を波括弧展開した場合の削除・上書きの対象と読み取りを追加する.

    元の語のままの判定（literal_entries）は残す．クォートされた波括弧は展開されないが，トークナイザーが
    クォートの情報を落とすので両方を見る．展開後のエントリの文字列は元の語に戻す（`{` を含むパスは
    メモのフォルダで許可しないため）．インライン実行のコードは呼び出し側が展開前の語で解析する．
    """
    expanded_words: list = []
    sources: dict = {}  # 展開後の語 → 元の語
    destructive = [e for e in literal_entries if e[3] != CREATE]
    for word in words:
        expansions = expand_braces(word)
        if expansions is None:
            # 判定の対象になる語だけを拒否する（echo '<長い JSON>' > file の JSON 等は対象にしない）．
            # コピー元の語はエントリの文字列に現れないので，コピー系は引数であれば対象とみなす
            if any(raw_comes_from_word(e[1], word) for e in destructive) or (cmd in BASH_COPY and destructive):
                findings.unexpandable.append((cmd, word))
            expanded_words.append(word)
            continue
        expanded_words += expansions
        if expansions != [word]:
            for expanded in expansions:
                sources[expanded] = word
    if not sources:
        return
    argv = strip_bash_prefixes(expanded_words)
    if not argv:
        return
    expanded_cmd = command_basename(argv[0])
    expanded_args = argv[1:]
    for a in expanded_args:
        if a in sources:
            record_bash_arg_reads(expanded_cmd, a, cwd, findings)
    for name, raw, resolved, kind in write_targets_bash(expanded_cmd, expanded_args, cwd):
        origin = next((word for expanded, word in sources.items() if raw_comes_from_word(raw, expanded)), raw)
        findings.writes.append((name, origin, resolved, kind))


def analyze_inline(cmd: str, args: list, stdin_codes: list, cwd: str | None, findings: Findings,
                   depth: int, dialect: str) -> None:
    """インライン実行（bash -c・pwsh -Command・cmd /c・python -c 等）のコードを解析する.

    stdin_codes はヒアドキュメント・ヒア文字列で標準入力に渡したコード（Bash のみ）．
    """
    if cmd in SHELL_COMMANDS:
        for c in option_values(args, {"-c"}) + stdin_codes:
            analyze_bash(c, cwd, findings, depth + 1)
    elif cmd in POWERSHELL_COMMANDS:
        for c in powershell_inline_code(args):
            analyze_powershell(c, cwd, findings, depth + 1)
    elif cmd in CMD_COMMANDS:
        collect_cmd_exe(cmd_exe_args(args), cwd, findings)
    elif cmd in INTERPRETER_CODE_OPTS or re.match(r"^python\d", cmd):
        opts = INTERPRETER_CODE_OPTS.get(cmd, {"-c"})
        for c in option_values(args, opts) + stdin_codes:
            collect_inline_literals(c, cmd, cwd, findings, dialect)


def cmd_exe_args(args: list) -> list:
    """cmd /c（Git Bash では //c）以降の引数を返す."""
    for idx, a in enumerate(args):
        if a.lower() in ("/c", "//c", "/k", "//k"):
            return args[idx + 1:]
    return []


def powershell_inline_code(args: list) -> list:
    codes = []
    for idx, a in enumerate(args):
        low = a.lower()
        if low in ("-c", "-command", "-com", "-comm", "-comma", "-comman") and idx + 1 < len(args):
            codes.append(" ".join(args[idx + 1:]))
            break
    return codes


# ---------------------------------------------------------------------------
# PowerShell の解析
# ---------------------------------------------------------------------------
def parse_ps_args(args: list) -> tuple:
    """PowerShell の引数を (位置引数のリスト, {パラメーター名: [値, ...]}) に分ける.

    switch パラメーター（-Force・-Append 等）は値の無いキー（空リスト）として入れる．
    """
    positional: list = []
    named: dict = {}
    idx = 0
    while idx < len(args):
        a = args[idx]
        if a.startswith("-") and len(a) > 1 and not re.match(r"^-\d", a):
            name = a[1:]
            value = None
            if ":" in name:
                name, value = name.split(":", 1)
            name = canonical_ps_param(name.lower())
            if value is None and name in PS_VALUE_PARAMS and idx + 1 < len(args):
                value = args[idx + 1]
                idx += 1
            values = named.setdefault(name, [])
            if value is not None:
                values.append(value)
            idx += 1
            continue
        positional.append(a)
        idx += 1
    return positional, named


def canonical_ps_param(name: str) -> str:
    """-OutF / -Dest 等の省略形を正式なパラメーター名に揃える."""
    if name in PS_VALUE_PARAMS:
        return name
    for full, min_len in PS_PARAM_ABBREVIATIONS:
        if len(name) >= min_len and full.startswith(name):
            return full
    return name


def write_targets_ps(cmd: str, args: list, cwd: str | None) -> list:
    """PowerShell の単純コマンドから削除・上書き・作成の対象を (名前, 文字列, 解決済みパス, 種別) で取り出す."""
    positional, named = parse_ps_args(args)

    def entries(raws: list, kind: str) -> list:
        return [e for r in raws for e in path_entries(cmd, r, cwd, "powershell", kind)]

    def targets(first_only: bool) -> list:
        result = positional[:1] if first_only else list(positional)
        for name in PS_TARGET_PARAMS:
            result += named.get(name, [])
        return result

    def source_and_dest(dest_params: tuple) -> tuple:
        """(読み取り元・移動元のリスト, 書き込み先のリスト) を返す."""
        srcs = [v for name in PS_SOURCE_PARAMS for v in named.get(name, [])]
        dest = [v for name in dest_params for v in named.get(name, [])]
        rest = list(positional)
        if not srcs:
            srcs, rest = rest[:1], rest[1:]
        if not dest:
            dest = rest[:1]
        return srcs, dest

    if cmd in PS_DELETE:
        return entries(targets(False), DELETE)
    if cmd in PS_RENAME:
        return entries(targets(True), DELETE)
    if cmd in PS_OVERWRITE_FIRST:
        return entries(targets(True), OVERWRITE)
    if cmd in PS_OVERWRITE_UNLESS_APPEND:
        append = "append" in named or "noclobber" in named
        return entries(targets(True), CREATE if append else OVERWRITE)
    if cmd in PS_NEW_ITEM:
        paths = targets(True)
        if "name" in named:
            paths = [os.path.join(b, nm) for b in (paths or ["."]) for nm in named["name"]]
        item_type = (named.get("itemtype", []) + named.get("type", []) + [""])[0].lower()
        # -Force 付きの New-Item（ファイル）は既存ファイルを空にして作り直す
        overwrite = "force" in named and not item_type.startswith("d")
        return entries(paths, OVERWRITE if overwrite else CREATE)
    if cmd in PS_MOVE or cmd in PS_WRITE_DEST:
        srcs, dest = source_and_dest(("destination",))
        result = entries(srcs, DELETE) if cmd in PS_MOVE else []
        for d in dest:
            result += copy_dest_entries(cmd, d, srcs, cwd, "powershell")
        return result
    if cmd in PS_WRITE_ARCHIVE_DEST:
        srcs, dest = source_and_dest(("destinationpath", "destination"))
        if cmd == "start-bitstransfer":
            return [e for d in dest for e in copy_dest_entries(cmd, d, srcs, cwd, "powershell")]
        if not dest and cmd == "expand-archive":
            dest = ["."]  # 展開先の省略時はカレントディレクトリに展開する
        # -Force（Compress-Archive は -Update も）が無ければ既存の書き込み先があるとエラーで止まる
        overwrite = "force" in named or (cmd == "compress-archive" and "update" in named)
        return entries(dest, OVERWRITE if overwrite else CREATE)
    if cmd in PS_WEB_REQUEST:
        # -OutFile にディレクトリを渡すと，その中に URL のファイル名で保存する（判断できないので対象外）
        result = [e for e in entries(named.get("outfile", []), OVERWRITE)
                  if e[2] in (UNKNOWN, SPECIAL) or not os.path.isdir(e[2])]
        if cmd in ("curl", "wget"):
            result += write_targets_bash(cmd, args, cwd, "powershell")
        return result
    return []


def analyze_powershell(command: str, cwd: str | None, findings: Findings, depth: int = 0) -> None:
    """PowerShell コマンドを解析して findings に書き込み・読み取りを追加する."""
    if depth > 3:
        return
    collect_dotnet_calls(command, cwd, findings)
    tokens = tokenize_powershell(command)
    dir_stack: list = []
    # Set-Location - / + と Push-Location - / + の履歴（コマンド開始より前の履歴は分からない）
    location_back: list = []
    location_forward: list = []
    for segment in split_segments(tokens):
        if segment in ("open", "close"):
            continue
        words: list = []
        idx = 0
        while idx < len(segment):
            kind, text, _ = segment[idx]
            if kind == "redir":
                target = segment[idx + 1][1] if idx + 1 < len(segment) and segment[idx + 1][0] == "word" else None
                idx += 2 if target is not None else 1
                if target is None or (text == ">&" and target.isdigit()):
                    continue
                if text == "<":
                    findings.reads.append(("<", target, resolve_path(target, cwd, "powershell"), False))
                else:
                    op_kind = CREATE if text == ">>" else OVERWRITE
                    findings.writes += path_entries(text, target, cwd, "powershell", op_kind)
                continue
            words.append(text)
            idx += 1
        if not words:
            continue
        cmd = command_basename(words[0])
        args = words[1:]

        positional, named = parse_ps_args(args)
        recursive = cmd in PS_RECURSIVE_READ
        for value in positional + [v for vs in named.values() for v in vs]:
            findings.reads.append((cmd, value, resolve_path(value, cwd, "powershell"), recursive))

        if cmd in PS_LOCATION_SET or cmd in PS_LOCATION_PUSH or cmd in PS_LOCATION_POP:
            # Push-Location / Pop-Location も Set-Location と同じ履歴を更新する（PowerShell 7.6 で実測）
            if cmd in PS_LOCATION_POP:
                if dir_stack:
                    location_back.append(cwd)
                    location_forward = []
                    cwd = dir_stack.pop()
                else:  # コマンド開始より前のスタックは分からないので，移動先も以降の - / + も未解決にする
                    location_back, location_forward = [], []
                    cwd = UNKNOWN
                continue
            dest = named.get("path", []) + named.get("literalpath", []) + positional
            if dest and dest[0] in ("-", "+"):
                source, other = (location_back, location_forward) if dest[0] == "-" else (location_forward, location_back)
                new_cwd = source.pop() if source else UNKNOWN
                other.append(cwd)
            else:
                if not dest:
                    new_cwd = home_dir() if cmd in PS_LOCATION_SET else cwd
                else:
                    findings.note_location_change(dest[0])
                    new_cwd = resolve_path(dest[0], cwd, "powershell")
                    if new_cwd == SPECIAL:
                        new_cwd = UNKNOWN
                location_back.append(cwd)
                location_forward = []
            if cmd in PS_LOCATION_PUSH:
                dir_stack.append(cwd)
            cwd = new_cwd
            continue

        findings.writes += write_targets_ps(cmd, args, cwd)
        analyze_inline(cmd, args, [], cwd, findings, depth, "powershell")


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def removes_only_link(name: str, raw: str, kind: str) -> bool:
    """最後の要素がリンクでも，リンク自体を消すだけでリンク先に触らない操作かを判定する.

    末尾に区切りがあるとリンク先の中身に届くことがある（reaches_link_target_by_trailing_separator）ので含めない．
    """
    return kind == DELETE and name in REMOVES_LINK_ITSELF and not raw.endswith(("/", "\\"))


def destructive_reason(kind: str, raw: str, resolved: str, policy: Policy, name: str = "") -> str | None:
    """リポジトリ外の削除・既存ファイルの上書きなら，その説明（「削除」等）を返す."""
    if kind == CREATE or policy.is_write_allowed(resolved, raw, removes_only_link(name, raw, kind)):
        return None
    if kind == DELETE:
        return "ファイルの削除（移動を含む）"
    if kind == UNDECIDABLE:
        return "削除・上書き（ワイルドカードの一致が多すぎて対象を確定できません）"
    if os.path.lexists(resolved):
        return "既存ファイルの上書き"
    return None  # 存在しないパスへの書き込みは新規作成なので許可する


def evaluate_findings(findings: Findings, policy: Policy) -> str | None:
    """解析結果を判定し，拒否する場合は理由文字列を返す."""
    if findings.ambiguous_cd:
        policy = Policy(policy.repo_root, policy.deny_read)  # メモのフォルダの許可だけを外す
    if findings.unexpandable:
        name, raw = findings.unexpandable[0]
        return (
            f"波括弧の展開数が多すぎて対象を確定できないため，リポジトリ外を指しうる削除・上書きとして"
            f"ブロックしました: `{name}` が `{raw[:200]}` を対象としています．対象を分けて指定してください．"
            f"{WRITE_DENY_GUIDANCE}"
        )
    for name, raw, resolved, kind in findings.writes:
        if resolved in (UNKNOWN, SPECIAL):
            continue
        entry = policy.denied_entry(resolved)
        if entry:
            return deny_read_message(f"`{name}` が `{raw}` を対象としています", entry)
        action = destructive_reason(kind, raw, resolved, policy, name)
        if action:
            return (
                f"リポジトリ外の{action}をブロックしました: "
                f"`{name}` が `{raw}` を対象としています．{WRITE_DENY_GUIDANCE}"
            )
    for name, raw, resolved, kind in findings.inline:
        if resolved in (UNKNOWN, SPECIAL):
            continue
        entry = policy.denied_entry(resolved)
        if entry:
            return deny_read_message(f"`{name}` のコード中に `{raw}` が含まれています", entry)
        action = destructive_reason(kind, raw, resolved, policy)
        if action:
            detail = "削除系の呼び出しとリポジトリ外のパス" if kind == DELETE else "リポジトリ外の既存ファイルへの上書き"
            return (
                f"インライン実行のコード中に{detail} `{raw}` が含まれるためブロックしました"
                f"（`{name}`）．{WRITE_DENY_GUIDANCE}"
            )
    for name, raw, resolved, recursive in findings.reads:
        if resolved in (UNKNOWN, SPECIAL):
            continue
        entry = policy.denied_entry(resolved)
        if entry is None and recursive:
            entry = policy.contained_denied_entry(resolved)
        if entry:
            return deny_read_message(f"`{name}` が `{raw}` を対象としています", entry)
    return None


def check_shell_command(analyzer, command: str, cwd: str, policy: Policy) -> str | None:
    """コマンドを analyzer（analyze_bash / analyze_powershell）で解析して判定する."""
    _wildcard_scans_left[0] = MAX_WILDCARD_SCANS
    findings = Findings()
    analyzer(command, cwd, findings)
    return evaluate_findings(findings, policy)


def check_bash_command(command: str, cwd: str, repo_root: str | None = None, deny_read: list | None = None,
                       transcript_path: str | None = None) -> str | None:
    """Bash コマンドがリポジトリ外の削除・上書き，禁止パスへのアクセスを含むか判定する."""
    return check_shell_command(analyze_bash, command, cwd, Policy(repo_root or cwd, deny_read or [], transcript_path))


def check_powershell_command(command: str, cwd: str, repo_root: str | None = None, deny_read: list | None = None,
                             transcript_path: str | None = None) -> str | None:
    """PowerShell コマンドがリポジトリ外の削除・上書き，禁止パスへのアクセスを含むか判定する."""
    return check_shell_command(analyze_powershell, command, cwd, Policy(repo_root or cwd, deny_read or [], transcript_path))


def glob_base(pattern: str) -> str:
    """グロブパターンからワイルドカードを含まない先頭部分を取り出す."""
    m = re.search(r"[*?\[{]", pattern)
    if m is None:
        return pattern
    head = pattern[:m.start()]
    return head[: max(head.rfind("/"), head.rfind("\\")) + 1] or head


def check_file_tool(tool_name: str, tool_input: dict, cwd: str, policy: Policy) -> str | None:
    """Read / Write / Edit / NotebookEdit / Glob / Grep のパスを判定する."""
    paths: list = []
    if tool_name in ("Read", "Write", "Edit", "MultiEdit"):
        paths.append(tool_input.get("file_path"))
    elif tool_name == "NotebookEdit":
        paths.append(tool_input.get("notebook_path"))
    elif tool_name in ("Glob", "Grep"):
        paths.append(tool_input.get("path"))  # 省略時は None → cwd（判定不要）
        pattern = tool_input.get("pattern") if tool_name == "Glob" else None
        if isinstance(pattern, str) and looks_absolute(pattern):
            base_dir = tool_input.get("path") or cwd
            paths.append(os.path.join(base_dir, glob_base(pattern)))
    # Write は既存ファイルの上書きだけを止める．Edit 系は既存ファイルの書き換えなので常に止める
    kind = {"Write": OVERWRITE, "Edit": DELETE, "MultiEdit": DELETE, "NotebookEdit": DELETE}.get(tool_name, CREATE)
    for raw in paths:
        if not isinstance(raw, str) or not raw:
            continue
        # ファイル操作ツールはワイルドカードを展開しない（文字どおりの名前）．`..` だけ要素をたどって解決する
        for _, _, resolved, entry_kind in path_entries(tool_name, raw, cwd, "bash", kind, wildcards=False):
            if resolved in (UNKNOWN, SPECIAL):
                continue
            entry = policy.denied_entry(resolved)
            if entry is None and tool_name in ("Glob", "Grep"):
                entry = policy.contained_denied_entry(resolved)
            if entry:
                return deny_read_message(raw, entry)
            if destructive_reason(entry_kind, raw, resolved, policy):
                action = "既存ファイルの上書き" if kind == OVERWRITE else "既存ファイルの書き換え"
                return f"リポジトリ外の{action}をブロックしました: {raw}．{WRITE_DENY_GUIDANCE}"
    return None


def deny(reason: str) -> None:
    """ブロック用の JSON を出力して終了する."""
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    json.dump(result, sys.stdout)
    # 終了時の flush に任せると，失敗しても例外にならず終了コード 120（非ブロック）になって素通りする
    sys.stdout.flush()
    sys.exit(0)


def get_repo_root(cwd: str) -> str:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        return os.path.normpath(convert_posix_drive(project_dir))
    return cwd


def deny_on_exception(error: Exception) -> None:
    """想定外の例外が起きたときにツール呼び出しを拒否する（異常終了すると非ブロックのエラーとして素通りする）."""
    try:
        # print(file=sys.stderr) は sys.stderr が None だと stdout に出て，拒否の JSON を壊す
        sys.stderr.write(traceback.format_exc())
    except Exception:
        pass  # stderr への出力に失敗しても拒否は続ける
    try:
        deny(
            f"リポジトリ外アクセス制限フックが想定外の例外（{type(error).__name__}）で判定できなかったため，"
            "安全のためツール呼び出しをブロックしました．フックの不具合の可能性があります．"
            "`.claude/hooks/restrict_repo_access.py` を確認してください（詳細は stderr に出力しています）．"
            "別の書き方で迂回せず，解決しなければユーザーに報告してください"
        )
    except SystemExit:
        # 拒否の出力は済んでいる．stderr のバッファに残ったトレースバックの flush が終了時に失敗すると，
        # 終了コードが 120 に変わるので，ここで試してから終了時の flush を通らずに終わる
        try:
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(0)
    except Exception:
        # stdout に書けないので終了コード 2 でブロックする．sys.exit だと終了時の flush の失敗で 120 に変わる
        os._exit(2)


def has_invalid_value_type(tool_name: str, tool_input: dict) -> bool:
    """判定の対象の値（コマンド・書き込み先のパス）が，存在するのに文字列でないかを判定する.

    0・[]・None も文字列ではないので該当する（項目が無い場合と空文字列は該当しない）．
    読み取り系などの他のツールは対象にしない．
    """
    key = "command" if tool_name in ("Bash", "PowerShell") else WRITE_TOOL_PATH_KEYS.get(tool_name)
    return key is not None and key in tool_input and not isinstance(tool_input[key], str)


def run_check() -> None:
    # 入力は UTF-8 の JSON．sys.stdin のテキストモードはロケールの文字コードで読むので，cp932 の Windows では
    # 日本語の直後の \" が壊れて JSONDecodeError になる（例外を拒否にしたので，正常な入力まで止まってしまう）
    data = json.load(sys.stdin.buffer)
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    cwd = data.get("cwd") or os.getcwd()
    repo_root = get_repo_root(cwd)
    policy = Policy(repo_root, load_deny_read(repo_root), data.get("transcript_path"))

    if has_invalid_value_type(tool_name, tool_input):
        deny(INVALID_VALUE_TYPE_DENY_REASON)

    if tool_name in ("Bash", "PowerShell"):
        analyzer = analyze_bash if tool_name == "Bash" else analyze_powershell
        command = tool_input.get("command", "")  # 文字列でない値は has_invalid_value_type で拒否済み
        # 実際の argv に NUL は入らず，シェルが取り除くかどうかで対象が変わる．OS によって例外になったり
        # 一時ディレクトリとして許可されたりするので，解析の前にコマンドの種類を問わず一律に拒否する
        reason = NUL_COMMAND_DENY_REASON if "\0" in command else check_shell_command(analyzer, command, cwd, policy)
    else:
        reason = check_file_tool(tool_name, tool_input, cwd, policy)

    if reason:
        deny(reason)
    sys.exit(0)


def main() -> None:
    try:
        run_check()
    except Exception as error:  # SystemExit（deny の sys.exit）・KeyboardInterrupt は Exception ではないので通す
        deny_on_exception(error)


if __name__ == "__main__":
    main()
