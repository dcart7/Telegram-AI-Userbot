import re

from main import (
    build_prompt,
    clamp_short_message,
    infer_tone,
    pick_contextual_emojis,
    split_response,
)


def test_infer_tone_variants() -> None:
    assert infer_tone("Ты меня бесишь") == "angry"
    assert infer_tone("I feel so sad today") == "sad"
    assert infer_tone("Это правда, согласен") == "agree"
    assert infer_tone("Wow, ничего себе") == "surprise"
    assert infer_tone("Are you coming?") == "question"
    assert infer_tone("Класс, супер") == "happy"
    assert infer_tone("ахахаха 😂") == "laugh"
    assert infer_tone("Просто текст") == "neutral"


def test_infer_tone_emoji_and_punctuation() -> None:
    assert infer_tone("Ну давай! 😂") == "laugh"
    assert infer_tone("Что это?!") == "question"
    assert infer_tone("Вот это да!! 😮") == "surprise"
    assert infer_tone("Бесит!!! 😡") == "angry"


def test_pick_contextual_emojis_prefers_tone() -> None:
    context_map = {"angry": ["😡"], "neutral": ["🙂"]}
    assert pick_contextual_emojis("angry", context_map, ["👍"]) == ["😡"]
    assert pick_contextual_emojis("unknown", context_map, ["👍"]) == ["🙂"]
    assert pick_contextual_emojis("unknown", {}, ["👍"]) == ["👍"]


def test_split_response_behaviour() -> None:
    token = "<SPLIT>"
    assert split_response("one", token) == ["one"]
    assert split_response(f"one {token} two", token) == ["one", "two"]
    assert split_response(f"one {token} two {token} three", token) == [
        "one",
        "two three",
    ]


def test_clamp_short_message_limits_length() -> None:
    long_text = " ".join(["word"] * 200) + ". Another sentence here."
    clamped = clamp_short_message(long_text, max_words=20, max_chars=120)
    assert len(clamped.split()) <= 20
    assert len(clamped) <= 121
    assert clamped.endswith("…")


def test_build_prompt_includes_rules() -> None:
    prompt = build_prompt(
        base_prompt="Friendly casual chat.",
        topic_prompt="Tech and startups",
        context=["Alice: hi", "Bob: hello"],
        use_emoji=True,
        short_reply=False,
        persona="Playful",
        focus_message="What do you think?",
        reply_context="Earlier point about funding",
        allow_split=True,
        split_token="<SPLIT>",
        emoji_hint="😡 😠",
        prompt_mode="compact",
    )
    assert "Reply in the same language as the latest human message." in prompt
    assert "If you decide to split, output exactly two parts" in prompt
    assert "<SPLIT>" in prompt
    assert re.search(r"Prefer emojis that match tone: 😡 😠", prompt)
