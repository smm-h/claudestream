"""Tests for NDJSON protocol I/O."""

import asyncio
import json
import logging

from claudestream._protocol import parse_event, read_events, write_message
from claudestream.events import ControlResponse, SystemInit, UnknownEvent
from claudestream.messages import UserMessage


class TestReadEvents:
    def test_parses_ndjson_lines(self):
        lines = [
            json.dumps({"type": "system", "subtype": "init", "cwd": "/tmp", "tools": [], "model": "test"}) + "\n",
            json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done"}) + "\n",
        ]
        data = "".join(lines).encode("utf-8")

        async def run():
            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            events = []
            async for event in read_events(reader):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 2
        assert isinstance(events[0], SystemInit)

    def test_skips_empty_lines(self):
        data = b"\n\n" + json.dumps({"type": "result", "is_error": False}).encode() + b"\n\n"

        async def run():
            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            events = []
            async for event in read_events(reader):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 1

    def test_skips_non_json_lines(self):
        data = b"[SandboxDebug] some debug output\n" + json.dumps({"type": "result", "is_error": False}).encode() + b"\n"

        async def run():
            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            events = []
            async for event in read_events(reader):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 1

    def test_eof_terminates(self):
        async def run():
            reader = asyncio.StreamReader()
            reader.feed_eof()
            events = []
            async for event in read_events(reader):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert events == []


class TestWriteMessage:
    def test_writes_json_line(self):
        async def run():
            reader = asyncio.StreamReader()

            # Create a simple write target
            output = bytearray()

            class FakeTransport:
                def get_extra_info(self, *a, **kw):
                    return None
                def is_closing(self):
                    return False
                def write(self, data):
                    output.extend(data)
                def close(self):
                    pass

            protocol = asyncio.StreamReaderProtocol(reader)
            fake_transport = FakeTransport()
            writer = asyncio.StreamWriter(fake_transport, protocol, reader, asyncio.get_event_loop())

            msg = UserMessage(content="hello", session_id="s1")
            await write_message(writer, msg)

            line = output.decode("utf-8")
            assert line.endswith("\n")
            parsed = json.loads(line)
            assert parsed["type"] == "user"
            assert parsed["message"]["content"] == "hello"

        asyncio.run(run())

    def test_write_message_logs_at_info(self, caplog):
        """write_message should log the message type at INFO level."""
        async def run():
            reader = asyncio.StreamReader()

            output = bytearray()

            class FakeTransport:
                def get_extra_info(self, *a, **kw):
                    return None
                def is_closing(self):
                    return False
                def write(self, data):
                    output.extend(data)
                def close(self):
                    pass

            protocol = asyncio.StreamReaderProtocol(reader)
            fake_transport = FakeTransport()
            writer = asyncio.StreamWriter(fake_transport, protocol, reader, asyncio.get_event_loop())

            msg = UserMessage(content="test", session_id="s1")
            with caplog.at_level(logging.INFO, logger="claudestream"):
                await write_message(writer, msg)

            assert any(
                "protocol: Sending UserMessage" in record.message
                and record.levelno == logging.INFO
                for record in caplog.records
            )

        asyncio.run(run())


class TestControlResponseParse:
    def test_success_response(self):
        raw = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": "ctrl_1",
                "response": {"still_queued": ["u1"]},
            },
        }
        event = parse_event(raw)
        assert isinstance(event, ControlResponse)
        assert event.request_id == "ctrl_1"
        assert event.subtype == "success"
        assert event.response == {"still_queued": ["u1"]}
        assert event.error == ""

    def test_error_response(self):
        raw = {
            "type": "control_response",
            "response": {
                "subtype": "error",
                "request_id": "ctrl_2",
                "error": "unsupported mode",
            },
        }
        event = parse_event(raw)
        assert isinstance(event, ControlResponse)
        assert event.request_id == "ctrl_2"
        assert event.subtype == "error"
        assert event.error == "unsupported mode"
        assert event.response == {}
