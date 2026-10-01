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

リポジトリの基準は CLAUDE_PROJECT_DIR（無ければフック入力の cwd）．相対パスは
フック入力の cwd を起点に，コマンド内の cd / pushd / Set-Location 等を追跡して解決する．

禁止リスト: <リポジトリ>/.claude/repo-access.json
    {"deny_read": ["~/.ssh", "/etc", "secrets"]}
  絶対パス・`~` 始まり・リポジトリルート相対を受け付ける．ファイルが無ければ空，
  壊れていれば空として扱い stderr に警告する．

許可ゾーン（リポジトリ外でも削除・上書きを許可する）:
- システム一時ディレクトリ（/tmp・%TEMP% 等．Claude Code のスクラッチパッドを含む）
  （/sync-template・/set-mode がテンプレートを mktemp -d に clone して cp・rm -rf
   するため．一時ディレクトリは使い捨て領域であり，許可しても失うものがない）
- /dev/null・NUL・$null 等の特殊デバイス

限界（重要）:
  本フックはコマンド文字列の静的解析であり，原理的に完全には防げない．変数展開
  （$DIR 等．$HOME・~ 以外は解決しない），スクリプトファイル経由の操作，
  パイプで渡した入力（xargs 等），エイリアス・関数，Base64 エンコードされた
  コマンド，未対応のコマンドによる削除・上書き，インライン実行中の未対応の書き方
  （変数に入れたパス・Perl の open 等）は検出できない．存在判定はフック実行時点の
  ものであり，同じコマンド内で先に作ったファイルを上書きする場合等は許可される．
  逆にインライン実行は，削除系の呼び出しとリポジトリ外のパス（文字列・コメント中の
  ものも含む）が同時に現れると，削除対象でなくても拒否することがある．
  ユーザーが `!` で実行したコマンドには本フックはかからない．確実性が必要な場合は
  OS のフォルダ権限，settings.json の permissions.deny，Bash / PowerShell の実行前に
  確認を挟む権限モード，バックアップを併用すること（docs/01_GUIDE/GUIDE_01 の環境構築）．
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import sys
import tempfile

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


def is_temp_path(path: str) -> bool:
    """path がシステム一時ディレクトリ配下かを判定する.

    OS ネイティブの一時ディレクトリ（tempfile.gettempdir()）に加え，
    Git Bash 等の POSIX 形式 /tmp も文字列正規化で判定する
    （Windows では /tmp が実パスに解決できないため realpath に頼れない）．
    `..` は正規化してから判定するので /tmp/../etc のような脱出は温存されない．
    """
    normalized = posixpath.normpath(path.replace("\\", "/"))
    if normalized == "/tmp" or normalized.startswith("/tmp/"):
        return True
    return is_within_directory(path, tempfile.gettempdir())


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
    token = convert_posix_drive(token)
    if not os.path.isabs(token):
        if cwd is None:
            return UNKNOWN
        token = os.path.join(cwd, token)
    return os.path.normpath(token)


def looks_absolute(text: str) -> bool:
    """コード文字列中のリテラルがリポジトリ外を指しうるパスに見えるかを判定する."""
    return bool(
        re.match(r"^(?:/[^/\s]|~(?:[/\\]|$)|[A-Za-z]:[\\/]|\\\\|\.\.(?:[/\\]|$))", text)
    )


# ---------------------------------------------------------------------------
# 判定コンテキスト
# ---------------------------------------------------------------------------
class Policy:
    def __init__(self, repo_root: str, deny_read: list) -> None:
        self.repo_root = repo_root
        self.deny_read = deny_read

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

    def is_write_allowed(self, path: str) -> bool:
        return self.is_inside_repo(path) or is_temp_path(path)


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
        print(
            f"[restrict_repo_access] {CONFIG_RELPATH} を読み込めないため禁止リストを空として扱います: {e}",
            file=sys.stderr,
        )
        return []
    result = []
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        entry = convert_posix_drive(expand_home(entry))
        if not os.path.isabs(entry):
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
            if text.startswith("/") and not re.match(r"^/[A-Za-z]/", text):
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
        switches = {w.lower() for w in words[1:] if w.startswith("/")}
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
    if not (into_dir or os.path.isdir(dest)):
        return [(name, dest_raw, dest, OVERWRITE)]
    entries = []
    for src in srcs:
        if contents_on_slash and src.endswith(("/", "\\")):
            continue
        base = posixpath.basename(src.replace("\\", "/").rstrip("/").split("?")[0])
        if base and base not in (".", "..") and "$" not in base:
            entries.append((name, dest_raw, os.path.join(dest, base), OVERWRITE))
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
        return [(cmd, r, resolve_path(r, cwd, dialect), kind) for r in raws]

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


def analyze_bash(command: str, cwd: str | None, findings: Findings, depth: int = 0) -> None:
    """Bash コマンドを解析して findings に書き込み・読み取りを追加する."""
    if depth > 3:
        return
    tokens = tokenize_bash(command)
    dir_stack: list = []
    subshell_stack: list = []
    for segment in split_segments(tokens):
        if segment == "open":
            subshell_stack.append(cwd)
            continue
        if segment == "close":
            if subshell_stack:
                cwd = subshell_stack.pop()
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
                resolved = resolve_path(target, cwd)
                if text == "<":
                    findings.reads.append(("<", target, resolved, False))
                else:
                    # >> / &>> は追記，<> は切り詰めずに開くので新規作成扱い．> / >| / &> は上書き
                    op_kind = CREATE if text in (">>", "&>>", "<>") else OVERWRITE
                    findings.writes.append((text, target, resolved, op_kind))
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
            for candidate in (a, a.split("=", 1)[1] if "=" in a else None):
                if candidate:
                    findings.reads.append((cmd, candidate, resolve_path(candidate, cwd), cmd in RECURSIVE_READ_COMMANDS))

        # cd / pushd / popd の追跡
        if cmd in ("cd", "pushd", "popd"):
            if cmd == "popd":
                cwd = dir_stack.pop() if dir_stack else UNKNOWN
                continue
            dest = [a for a in args if not (a.startswith("-") and a != "-")]
            if not dest:
                new_cwd = home_dir()
            elif dest[0] == "-":
                new_cwd = UNKNOWN
            else:
                new_cwd = resolve_path(dest[0], cwd)
                if new_cwd == SPECIAL:
                    new_cwd = UNKNOWN
            if cmd == "pushd":
                dir_stack.append(cwd)
            cwd = new_cwd
            continue

        findings.writes += write_targets_bash(cmd, args, cwd)

        analyze_inline(cmd, args, heredoc_bodies + herestrings, cwd, findings, depth, "bash")


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
        return [(cmd, r, resolve_path(r, cwd, "powershell"), kind) for r in raws]

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
                    findings.writes.append((text, target, resolve_path(target, cwd, "powershell"), op_kind))
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
            if cmd in PS_LOCATION_POP:
                cwd = dir_stack.pop() if dir_stack else UNKNOWN
                continue
            dest = named.get("path", []) + named.get("literalpath", []) + positional
            if not dest:
                new_cwd = home_dir() if cmd in PS_LOCATION_SET else cwd
            elif dest[0] in ("-", "+"):
                new_cwd = UNKNOWN
            else:
                new_cwd = resolve_path(dest[0], cwd, "powershell")
                if new_cwd == SPECIAL:
                    new_cwd = UNKNOWN
            if cmd in PS_LOCATION_PUSH:
                dir_stack.append(cwd)
            cwd = new_cwd
            continue

        findings.writes += write_targets_ps(cmd, args, cwd)
        analyze_inline(cmd, args, [], cwd, findings, depth, "powershell")


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def destructive_reason(kind: str, resolved: str, policy: Policy) -> str | None:
    """リポジトリ外の削除・既存ファイルの上書きなら，その説明（「削除」等）を返す."""
    if kind == CREATE or policy.is_write_allowed(resolved):
        return None
    if kind == DELETE:
        return "ファイルの削除（移動を含む）"
    if os.path.lexists(resolved):
        return "既存ファイルの上書き"
    return None  # 存在しないパスへの書き込みは新規作成なので許可する


def evaluate_findings(findings: Findings, policy: Policy) -> str | None:
    """解析結果を判定し，拒否する場合は理由文字列を返す."""
    for name, raw, resolved, kind in findings.writes:
        if resolved in (UNKNOWN, SPECIAL):
            continue
        entry = policy.denied_entry(resolved)
        if entry:
            return deny_read_message(f"`{name}` が `{raw}` を対象としています", entry)
        action = destructive_reason(kind, resolved, policy)
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
        action = destructive_reason(kind, resolved, policy)
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
    findings = Findings()
    analyzer(command, cwd, findings)
    return evaluate_findings(findings, policy)


def check_bash_command(command: str, cwd: str, repo_root: str | None = None, deny_read: list | None = None) -> str | None:
    """Bash コマンドがリポジトリ外の削除・上書き，禁止パスへのアクセスを含むか判定する."""
    return check_shell_command(analyze_bash, command, cwd, Policy(repo_root or cwd, deny_read or []))


def check_powershell_command(command: str, cwd: str, repo_root: str | None = None, deny_read: list | None = None) -> str | None:
    """PowerShell コマンドがリポジトリ外の削除・上書き，禁止パスへのアクセスを含むか判定する."""
    return check_shell_command(analyze_powershell, command, cwd, Policy(repo_root or cwd, deny_read or []))


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
        resolved = resolve_path(raw, cwd)
        if resolved in (UNKNOWN, SPECIAL):
            continue
        entry = policy.denied_entry(resolved)
        if entry is None and tool_name in ("Glob", "Grep"):
            entry = policy.contained_denied_entry(resolved)
        if entry:
            return deny_read_message(raw, entry)
        if destructive_reason(kind, resolved, policy):
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
    sys.exit(0)


def get_repo_root(cwd: str) -> str:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        return os.path.normpath(convert_posix_drive(project_dir))
    return cwd


def main() -> None:
    data = json.load(sys.stdin)
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    cwd = data.get("cwd") or os.getcwd()
    repo_root = get_repo_root(cwd)
    policy = Policy(repo_root, load_deny_read(repo_root))

    if tool_name in ("Bash", "PowerShell"):
        analyzer = analyze_bash if tool_name == "Bash" else analyze_powershell
        reason = check_shell_command(analyzer, tool_input.get("command", "") or "", cwd, policy)
    else:
        reason = check_file_tool(tool_name, tool_input, cwd, policy)

    if reason:
        deny(reason)
    sys.exit(0)


if __name__ == "__main__":
    main()
