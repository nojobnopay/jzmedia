"""Whole-utterance stage cues for 小映. Free-form conversations go to the model."""
import re
import unicodedata
from typing import Literal


Reaction = Literal["wave", "smile", "cry", "dance"]

# A cue must consume the entire utterance. A greeting followed by a question
# about fonts or a request to turn off dancing belongs to settings guidance.
_CUES = {
    "name": r"你(?:的名字(?:是(?:什么|啥))?|叫(?:什么|啥)(?:名字)?|是谁)|(?:怎么|如何)称呼你|介绍一下你自己",
    "hello": r"你好|您好|嗨+|哈[喽啰]|早(?:上好|安)?|晚上好|在吗|hello|hi",
    "smile": r"(?:再)?笑(?:一笑|一?个|一下)|微笑(?:一下)?|露个笑脸",
    "cry": r"(?:再)?哭(?:一哭|一?个|一下)|表演(?:一下|个)?哭(?:戏)?",
    "dance": r"(?:再)?跳(?:一?[个支段])?舞(?:蹈)?|来(?:个|一段)舞(?:蹈)?",
    "wave": r"挥(?:挥手|个手|手)|打个招呼",
    "thanks": r"谢谢(?:你|啦)?|谢啦|多谢|辛苦了",
    "bye": r"再见|拜拜|回头见|晚安",
    "sad": r"我(?:今天)?(?:心情不(?:太)?好|(?:有(?:一)?点|好|很)难过|有点伤心)",
    "again": r"再来(?:一[次遍个段])?|再表演一[次段]",
    "encore_dance": r"再跳一[次遍段]",
}
_CUE = re.compile(
    r"(?:请(?:问|你)?|麻烦(?:你)?|(?:你)?(?:能不能|可以|能))?(?:给我|帮我)?(?:"
    + "|".join(f"(?P<{key}>{pattern})" for key, pattern in _CUES.items())
    + r")(?:给我看(?:看)?)?(?:好吗|好不好|可以吗|行吗|[吗吧嘛呗呀啊哇呢啦哦哈])?", re.I,
)
_PUNCT = " \t\r\n,，。.!！?？~～、：:"
_CALL_NAME = re.compile(r"^(?:小映[\s,、:!?.~]*)+")

_LINES = {
    "name": ("我叫小映，是 jzmedia 的小放映机。“映”是放映的映。", "小映，电影的“映”。看到那两个片盘了吗？那就是我。"),
    "hello": ("你好呀，我是小映！今天想聊点什么？", "嗨，又见面啦！我在这儿。"),
    "smile": ("嘿嘿，笑一个！连镜头都亮起来了。", "给你一个大大的笑脸。咔嚓，这一帧留给你！"),
    "cry": ("呜……借我一小截片尾字幕擦擦眼泪。", "哭戏开拍！一滴眼泪，两滴眼泪……好，收工。"),
    "dance": ("向左摇，向右摇，再轻轻跳一下！这是我的片头小舞步。", "再来一段！左摇右摆，片盘也踩上拍子啦。"),
    "wave": ("嗨，我在这里！给你挥挥手。", "又碰面啦，给你打个招呼！"),
    "thanks": ("不客气！", "很高兴帮上忙。"),
    "bye": ("回头见，我在设置页等你。", "下次见！"),
    "sad": ("听起来今天不太好过。我在，你想说说发生了什么吗？", "我听着呢。你想聊聊，还是让我陪你安静一会儿？"),
}
_ACTIONS = {"smile", "cry", "dance", "wave"}
ENCORES = {"again", "encore_dance"}
ENCORE_QUESTION = "你想再看哪个动作，还是继续刚才的设置？"


def social_intent(question):
    text = unicodedata.normalize("NFKC", question).strip(_PUNCT)
    text = _CALL_NAME.sub("", text).removesuffix("小映").strip(_PUNCT)
    cue = _CUE.fullmatch(text)
    return cue.lastgroup if cue else None


def stage_cue(intent, previous_answer=""):
    """Return an authored reply and its optional visual cue, or defer to the model."""
    if not intent:
        return None
    if intent in ENCORES:
        if not previous_answer:
            return ENCORE_QUESTION, None
        requested_dance = intent == "encore_dance"
        intent = next((act for act in _ACTIONS if previous_answer in _LINES[act]), None)
        if not intent or (requested_dance and intent != "dance"):
            return None
    lines = _LINES[intent]
    answer = lines[1] if previous_answer == lines[0] else lines[0]
    reaction = intent if intent in _ACTIONS else {"hello": "wave", "name": "wave", "thanks": "smile", "bye": "wave"}.get(intent)
    return answer, reaction
