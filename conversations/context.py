"""Per-turn context and original-message provenance, separate from persistence."""

from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

from pydantic_ai.messages import CompactionPart, ModelMessage


@dataclass
class ConversationContext:
    messages: list[ModelMessage]
    compact: bool = True
    originals: dict[str, ModelMessage] = field(default_factory=dict)
    summaries: list[ModelMessage] = field(default_factory=list)

    def capture(self, messages: list[ModelMessage]) -> None:
        for message in messages:
            if any(isinstance(part, CompactionPart) for part in message.parts):
                continue
            metadata = message.metadata = dict(message.metadata or {})
            key = metadata.setdefault('archive_id', uuid4().hex)
            self.originals[key] = deepcopy(message)

    def record_summary(self, before: list[ModelMessage], after: list[ModelMessage]) -> None:
        retained = {(message.metadata or {}).get('archive_id') for message in after}
        sources: set[str] = set()
        for message in before:
            metadata = message.metadata or {}
            if metadata.get('archive_id') not in retained:
                if metadata.get('archive_id'):
                    sources.add(metadata['archive_id'])
            for part in message.parts:
                if isinstance(part, CompactionPart):
                    sources.update((part.metadata or {}).get('summary_sources', []))
        for message in after:
            if any(isinstance(part, CompactionPart) for part in message.parts):
                # Core may merge adjacent requests and discard message-level metadata.
                # Part metadata survives that normalization and carries cumulative lineage.
                for part in message.parts:
                    if isinstance(part, CompactionPart):
                        part.metadata = {**(part.metadata or {}), 'summary_sources': sorted(sources)}
                message.metadata = {**(message.metadata or {}), 'summary_sources': sorted(sources)}
                self.summaries.append(deepcopy(message))
