"""Unit tests for the build stamp and the What's new git-log parser."""

import json
import re

import server


# ---- version string format ----

def test_version_matches_documented_format():
    # "1.1.YYMMDDHH", or the documented sentinel when neither git nor file
    # dates are readable. Any third shape means the badge lies about the build.
    assert re.fullmatch(r"1\.1\.\d{8}", server.VERSION) or server.VERSION == "1.1.unknown"


def test_version_source_is_a_known_provenance():
    assert server.VERSION_SOURCE in ("git", "mtime", "unknown")


def test_unknown_source_implies_unknown_stamp():
    if server.VERSION_SOURCE == "unknown":
        assert server.VERSION == "1.1.unknown"
    else:
        assert re.fullmatch(r"1\.1\.\d{8}", server.VERSION)


def test_version_endpoint_is_json_safe():
    payload = server.version_info()
    assert set(payload) == {"version", "source"}
    json.dumps(payload, allow_nan=False)


def test_whats_new_endpoint_is_json_safe():
    payload = server.whats_new()
    assert set(payload) == {"entries", "since", "source"}
    assert payload["since"] == server.WHATS_NEW_SINCE
    assert payload["source"] in ("git", "unavailable")
    json.dumps(payload, allow_nan=False)


# ---- git log parsing ----
#
# Synthetic records only. Asserting against this repo's real history would make
# the test fail on every new commit.

FS = server._FIELD_SEP
RS = server._RECORD_SEP


def _record(commit_hash, date, subject, body):
    return FS.join([commit_hash, date, subject, body]) + RS


MULTILINE_BODY = (
    "First paragraph of the body.\n"
    "\n"
    "- a bullet after a blank line\n"
    "- another bullet\n"
    "\n"
    "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>\n"
)


def test_parses_multiline_body_with_blank_lines_and_trailer():
    raw = _record("abc1234", "2026-07-28", "Add a version badge", MULTILINE_BODY)
    entries = server.parse_git_log(raw)
    assert len(entries) == 1
    e = entries[0]
    assert e == {
        "date": "2026-07-28",
        "hash": "abc1234",
        "subject": "Add a version badge",
        "body": ("First paragraph of the body.\n"
                 "\n"
                 "- a bullet after a blank line\n"
                 "- another bullet"),
    }
    # The trailer is plumbing and must not reach the UI.
    assert "Co-Authored-By" not in e["body"]
    # Internal blank line survives: the body is rendered pre-wrap.
    assert "\n\n" in e["body"]


def test_blank_line_in_body_does_not_split_a_record():
    raw = (_record("aaa1111", "2026-07-28", "Newest", MULTILINE_BODY)
           + _record("bbb2222", "2026-07-28", "Older", "Body with\n\na gap.\n"))
    entries = server.parse_git_log(raw)
    assert [e["hash"] for e in entries] == ["aaa1111", "bbb2222"]
    assert entries[1]["body"] == "Body with\n\na gap."


def test_order_is_preserved_newest_first():
    raw = (_record("aaa1111", "2026-07-28", "Newest", "")
           + _record("bbb2222", "2026-07-27", "Middle", "")
           + _record("ccc3333", "2026-07-26", "Oldest", ""))
    entries = server.parse_git_log(raw)
    assert [e["subject"] for e in entries] == ["Newest", "Middle", "Oldest"]
    assert [e["date"] for e in entries] == ["2026-07-28", "2026-07-27", "2026-07-26"]


def test_empty_body_becomes_empty_string_not_none():
    entries = server.parse_git_log(_record("abc1234", "2026-07-28", "Subject only", ""))
    assert entries[0]["body"] == ""


def test_body_of_only_trailers_collapses_to_empty():
    body = "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>\n"
    entries = server.parse_git_log(_record("abc1234", "2026-07-28", "Terse", body))
    assert entries[0]["body"] == ""


def test_max_entries_caps_the_feed():
    raw = "".join(
        _record("h%05d" % i, "2026-07-28", "Commit %d" % i, "") for i in range(20)
    )
    assert len(server.parse_git_log(raw, max_entries=5)) == 5


def test_empty_and_malformed_input_yields_no_entries():
    assert server.parse_git_log("") == []
    assert server.parse_git_log("\n" + RS + "  " + RS) == []
    # Too few fields (e.g. a truncated pipe) is dropped, not half-parsed.
    assert server.parse_git_log("abc1234" + FS + "2026-07-28" + RS) == []


def test_strip_coauthor_trailers_is_case_insensitive_and_indent_tolerant():
    body = "Real content.\n\n  co-authored-by: Someone <a@b.c>\nCO-AUTHORED-BY: Other <d@e.f>\n"
    assert server.strip_coauthor_trailers(body) == "Real content."


def test_separators_cannot_be_confused_with_message_text():
    # The separators are ASCII unit/record separators precisely because commit
    # messages can contain any printable text, including these words.
    body = "Mentions \\x1f and RS literally, plus a --- ruler.\n"
    entries = server.parse_git_log(_record("abc1234", "2026-07-28", "Tricky", body))
    assert len(entries) == 1
    assert entries[0]["body"] == "Mentions \\x1f and RS literally, plus a --- ruler."
