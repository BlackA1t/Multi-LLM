from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

from .models import Phase, PromptArtifact


class PromptComposer:
    @staticmethod
    def independent(task_id: UUID, question: str, role: str) -> PromptArtifact:
        content = (
            "Answer the research question independently. Do not assume access to other participants' answers.\n\n"
            f"Assigned role: {role}\n\n"
            f"<research_question>\n{question}\n</research_question>"
        )
        encoded = content.encode("utf-8")
        return PromptArtifact(
            prompt_id=uuid4(),
            task_id=task_id,
            version=1,
            content=content,
            size_bytes=len(encoded),
            sha256=hashlib.sha256(encoded).hexdigest(),
        )

    @staticmethod
    def phase(task_id: UUID, phase: Phase, role: str) -> PromptArtifact:
        content = f"Prepare the {role} artifact for the fixed research phase: {phase.value}."
        encoded = content.encode("utf-8")
        return PromptArtifact(
            prompt_id=uuid4(), task_id=task_id, version=1, content=content,
            size_bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest(),
        )
