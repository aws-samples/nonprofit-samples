# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
"""Bound PDF parsing without putting process management in the orchestrator."""

from __future__ import annotations

import io
import json
import logging
import multiprocessing
import os
import sys
import threading
import time
from multiprocessing.connection import Connection
from pathlib import Path

import psutil

MAX_PDF_PAGES = 100
MAX_TEXT_CHARACTERS = 100_000
PDF_TIMEOUT_SECONDS = 25


def _read_pages(data: bytes, output: Connection) -> None:
    try:
        # Parser diagnostics can contain document text; return only bounded status.
        logging.disable(logging.CRITICAL)
        if sys.platform == "linux":
            import resource

            # Allow a bounded amount of memory beyond the worker's loaded libraries.
            used = int(Path("/proc/self/statm").read_text().split()[0])
            ceiling = used * os.sysconf("SC_PAGE_SIZE") + 256 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
            resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=True)
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError("PDF exceeds the 100-page limit")
        pages = []
        characters = 0
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            characters += len(text)
            if characters > MAX_TEXT_CHARACTERS:
                raise ValueError("PDF exceeds the extracted-text limit")
            if text:
                pages.append([number, text])
        output.send_bytes(json.dumps({"pages": pages}, ensure_ascii=False).encode())
    except Exception:
        output.send_bytes(b'{"error":"PDF could not be parsed within the safety limits"}')
    finally:
        output.close()


def read_pdf_pages(
    data: bytes, *, cancel_signal: threading.Event | None = None,
) -> list[tuple[int, str]]:
    context = multiprocessing.get_context("spawn")
    incoming, outgoing = context.Pipe(duplex=False)
    worker = context.Process(target=_read_pages, args=(data, outgoing), daemon=True)
    worker.start()
    outgoing.close()
    try:
        deadline = time.monotonic() + PDF_TIMEOUT_SECONDS
        process = psutil.Process(worker.pid)
        while not incoming.poll(0.05):
            if cancel_signal is not None and cancel_signal.is_set():
                raise ValueError("PDF parsing cancelled")
            if time.monotonic() >= deadline:
                raise ValueError("PDF parsing exceeded the time limit")
            if process.memory_info().rss > 512 * 1024 * 1024:
                raise ValueError("PDF parsing exceeded the memory limit")
        result = json.loads(incoming.recv_bytes(1_000_000))
        if "error" in result:
            raise ValueError(result["error"])
        return [(int(number), str(text)) for number, text in result["pages"]]
    except (EOFError, OSError, psutil.NoSuchProcess):
        raise ValueError("PDF parser stopped before producing a bounded result") from None
    finally:
        incoming.close()
        worker.join(timeout=1)
        if worker.is_alive():
            worker.kill()
            worker.join()
        worker.close()
