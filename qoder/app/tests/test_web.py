from __future__ import annotations

from paperdesk.web import _answer_text


def test_answer_text_strips_model_markdown() -> None:
    text = "## 标题\n\n- **要点**：正文\n* 另一条"

    assert _answer_text(text) == "标题\n\n• 要点：正文\n• 另一条"
