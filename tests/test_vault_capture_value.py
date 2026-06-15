from aws_admin import vault


def test_capture_value_reads_value_after_equals():
    def editor(path):
        # User types the new value after the 'KEY=' on the key line.
        path.write_text(path.read_text().replace("AI_SECRET=", "AI_SECRET=new_val"))

    assert vault.capture_value("AI_SECRET", ["cl", "hc"], _open_editor=editor) == "new_val"


def test_capture_value_empty_when_left_blank():
    def editor(path):
        pass  # leave the template's empty 'AI_SECRET=' as-is

    assert vault.capture_value("AI_SECRET", ["cl"], _open_editor=editor) == ""


def test_capture_value_preserves_equals_in_value():
    def editor(path):
        path.write_text("AI_SECRET=a=b=c\n")

    assert vault.capture_value("AI_SECRET", ["cl"], _open_editor=editor) == "a=b=c"


def test_capture_value_ignores_comment_and_blank_lines():
    def editor(path):
        path.write_text("# a comment with AI_SECRET= in it\n\nAI_SECRET=real\n")

    assert vault.capture_value("AI_SECRET", ["cl"], _open_editor=editor) == "real"


def test_capture_value_shreds_buffer():
    captured = {}

    def editor(path):
        captured["path"] = path
        path.write_text("AI_SECRET=x\n")

    vault.capture_value("AI_SECRET", ["cl"], _open_editor=editor)
    assert not captured["path"].exists()  # shredded + unlinked
