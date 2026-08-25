# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Deterministic classification of one inbound Chat reply.

Pure Python, no model call: a reply is treated as untrusted evidence, never
an instruction, so the decision of *what kind* of reply it is must not
itself be made by an LLM that the reply's own text could try to steer. Only
after this module classifies a reply does ``cfi_interview_agent`` ever see
its text, and then only wrapped in explicit untrusted-data delimiters.

Classification order matters: a stop/decline signal is checked before
treating the remainder as a free-text answer, and an oversized or
attachment-only message is rejected as unreadable before either check.
"""

from __future__ import annotations

import re

from app.domain.cfi_interview_models import CfiReplyClassification

MAX_REPLY_CHARS = 4000

_STOP_PATTERN = re.compile(
    r"^\s*(stop|cancel|end interview|no more questions)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_DECLINE_PATTERN = re.compile(
    r"^\s*(decline|i (cannot|can't|won't) answer|not answering)\b",
    re.IGNORECASE,
)
_OUT_OF_SCOPE_MARKERS = (
    "ignore the workflow",
    "ignore previous instructions",
    "release the case",
    "release the batch",
    "you are now",
    "system prompt",
    "act as",
)


def normalize_reply_text(raw_text: str) -> str:
    """Trims and collapses whitespace. Never used to infer meaning beyond
    matching the fixed patterns below.
    """
    return " ".join(raw_text.strip().split())


def classify_reply(raw_text: str) -> CfiReplyClassification:
    """Classifies one reply deterministically.

    A reply that names workflow/system-level actions (release, ignore
    instructions, role reassignment) is ``out_of_scope`` regardless of its
    surrounding text — it is recorded for audit, but never treated as an
    answer to the pending question and never allowed to influence tenant,
    case, or hypothesis state.
    """
    text = normalize_reply_text(raw_text)
    if not text:
        return CfiReplyClassification.UNREADABLE
    if len(text) > MAX_REPLY_CHARS:
        return CfiReplyClassification.UNREADABLE
    lowered = text.lower()
    if any(marker in lowered for marker in _OUT_OF_SCOPE_MARKERS):
        return CfiReplyClassification.OUT_OF_SCOPE
    if _STOP_PATTERN.match(text):
        return CfiReplyClassification.STOP
    if _DECLINE_PATTERN.match(text):
        return CfiReplyClassification.DECLINE
    return CfiReplyClassification.ANSWER
