"""Shared conversation limits and content safeguards for 小映."""
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Answer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1600)]


class Turn(StrictModel):
    question: Question
    answer: Answer


# Pasted credentials, service URLs and local filesystem locations belong in the
# ordinary settings form. Keep the whole turn on-device/server when detected,
# including a sensitive value in a previous question or answer.
_PRIVATE = re.compile(
    r"(?:https?|smb|nfs|socks5h?)://|(?<![A-Za-z0-9])(?:~?/[^\s/]|[a-z]:[\\/])|\\\\"
    r"|(?:api[ _-]?key|token|secret|passw(?:or)?d|authorization|密钥|密码|令牌|凭据|口令)"
    r"\s*(?:[：:=]|(?:是|为|用|换成|改成|设置成|设置为|is\b)\s*\S|\s+[a-z0-9_/-]{5,})"
    r"|(?<![a-z0-9])(?:bearer|basic)\s+[a-z0-9+/._=-]+|(?<![a-z0-9])(?:sk|ghp)[-_][a-z0-9_-]{5,}"
    r"|[a-z0-9_+/=-]{28,}|(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])"
    r"|[a-z][a-z0-9_]{2,}\s*=\s*\S|-----BEGIN [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE,
)
UNVERIFIED_LINK = re.compile(r"(?:https?|javascript|data|file|smb):|\]\s*\(|</?[a-z][^>]*>", re.IGNORECASE)


def contains_private_text(question, history):
    return bool(_PRIVATE.search(question) or any(
        _PRIVATE.search(turn.question) or _PRIVATE.search(turn.answer) for turn in history))
