import re


ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
HOOK_RE = re.compile(r"\bhook\b|钩子", re.IGNORECASE)
HOOK_DONE_RE = re.compile(
    r"\b(?:done|complete|completed|finish|finished|success|succeed|passed|ok)\b|完成|已完成|成功|结束|跑完",
    re.IGNORECASE,
)
HOOK_FAIL_RE = re.compile(r"fail|failed|error|exception|traceback|失败|错误|异常", re.IGNORECASE)


def strip_terminal_control(text):
    return ANSI_RE.sub("", text or "").replace("\r", "\n")


def hook_status_for_line(line):
    if not line or len(line) > 500 or not HOOK_RE.search(line):
        return None
    if HOOK_FAIL_RE.search(line):
        return "failed"
    if HOOK_DONE_RE.search(line):
        return "done"
    return None
