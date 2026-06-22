# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

import httpx
import pytest

from fastly_sync.blocklist import dump_blocklist, load_blocklist
from fastly_sync.errors import BlocklistError
from fastly_sync.models import BlockEntry

SAMPLE = """
# managed blocklist
203.0.113.0/24   # botnet C2
198.51.100.7
2001:db8::/32

198.51.100.7     # duplicate, last comment wins
"""


def test_load_parses_hosts_cidrs_and_comments(tmp_path):
    path = tmp_path / "blocklist.txt"
    path.write_text(SAMPLE, encoding="utf-8")
    entries = load_blocklist(str(path))

    by_ip = {entry.ip: entry for entry in entries}
    assert by_ip["203.0.113.0"].subnet == 24
    assert by_ip["203.0.113.0"].comment == "botnet C2"
    # A bare host has no subnet and is de-duplicated (last comment kept).
    assert by_ip["198.51.100.7"].subnet is None
    assert by_ip["198.51.100.7"].comment == "duplicate, last comment wins"
    assert by_ip["2001:db8::"].subnet == 32
    # Blank lines and the full-line comment are skipped.
    assert len(entries) == 3


def test_load_is_sorted_and_deterministic(tmp_path):
    path = tmp_path / "blocklist.txt"
    path.write_text("198.51.100.7\n203.0.113.0/24\n", encoding="utf-8")
    ips = [entry.ip for entry in load_blocklist(str(path))]
    assert ips == sorted(ips)


def test_invalid_entry_raises_with_line_number(tmp_path):
    path = tmp_path / "blocklist.txt"
    path.write_text("198.51.100.7\nnot-an-ip\n", encoding="utf-8")
    with pytest.raises(BlocklistError, match=r":2: invalid IP/CIDR 'not-an-ip'"):
        load_blocklist(str(path))


def test_dump_round_trips_through_load(tmp_path):
    entries = (
        BlockEntry("198.51.100.7"),
        BlockEntry("203.0.113.0", 24, "botnet"),
    )
    text = dump_blocklist(entries)
    assert text == "198.51.100.7\n203.0.113.0/24  # botnet\n"

    path = tmp_path / "out.txt"
    path.write_text(text, encoding="utf-8")
    reloaded = load_blocklist(str(path))
    assert reloaded == entries


def test_dump_empty_is_empty_string():
    assert dump_blocklist(()) == ""


def test_negated_entry_round_trips(tmp_path):
    path = tmp_path / "bl.txt"
    path.write_text("!198.51.100.7  # allowlist\n203.0.113.0/24\n", encoding="utf-8")
    entries = load_blocklist(str(path))
    by_ip = {e.ip: e for e in entries}
    assert by_ip["198.51.100.7"].negated is True
    assert by_ip["198.51.100.7"].comment == "allowlist"
    assert by_ip["203.0.113.0"].negated is False
    # Round-trips: the "!" prefix is re-emitted.
    text = dump_blocklist(entries)
    assert "!198.51.100.7  # allowlist" in text
    assert load_blocklist(str(path)) == load_blocklist(str(path))


def test_load_remote_blocklist():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="198.51.100.7\n")
    )
    with httpx.Client(transport=transport) as client:
        entries = load_blocklist("https://feeds.test/blocklist.txt", client=client)
    assert entries[0].ip == "198.51.100.7"
