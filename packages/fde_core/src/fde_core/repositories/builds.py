from __future__ import annotations

from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.database import Database

from fde_core.domain.builds import (
    ActivityEntry,
    ActivityKind,
    Artifact,
    ArtifactCard,
    Build,
    BuildMessage,
    Code,
    Delivery,
    Design,
    InputRequest,
    PendingInput,
    SmokeTest,
    Spec,
    Stage,
    Turn,
)
from fde_core.repositories.bson import to_document
from fde_core.repositories.indexes import BUILD_ACTIVITY, BUILD_ARTIFACTS, BUILD_MESSAGES, BUILDS


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MongoBuildRepository:
    """Builds, their artifacts, messages and activity. The review app creates builds and user messages; the
    project_builder flow writes the rest. The flow addresses a build by its id or its chat session alone."""

    def __init__(self, db: Database) -> None:
        self._builds = db[BUILDS]
        self._artifacts = db[BUILD_ARTIFACTS]
        self._messages = db[BUILD_MESSAGES]
        self._activity = db[BUILD_ACTIVITY]

    # Builds

    def create(self, build: Build) -> Build:
        self._builds.insert_one(to_document(build))
        return build

    def get(self, fde_id: str, build_id: str) -> Build | None:
        document = self._builds.find_one({"fde_id": fde_id, "id": build_id}, {"_id": 0})
        return Build.model_validate(document) if document else None

    def find(self, build_id: str) -> Build | None:
        document = self._builds.find_one({"id": build_id}, {"_id": 0})
        return Build.model_validate(document) if document else None

    def by_session(self, session_id: str) -> Build | None:
        """The flow's way in: a chat turn knows only its session."""
        document = self._builds.find_one({"session_id": session_id}, {"_id": 0})
        return Build.model_validate(document) if document else None

    def list(self, fde_id: str, limit: int = 100) -> list[Build]:
        found = self._builds.find({"fde_id": fde_id}, {"_id": 0}).sort("updated_at", DESCENDING).limit(limit)
        return [Build.model_validate(document) for document in found]

    def in_turn(self, fde_id: str) -> list[Build]:
        """Builds with a turn in progress (a local worker that restarts finds the ones it left behind)."""
        found = self._builds.find({"fde_id": fde_id, "turn": {"$ne": None}}, {"_id": 0})
        return [Build.model_validate(document) for document in found]

    def save_stages(self, build: Build) -> None:
        stages = {stage: state.model_dump() for stage, state in build.stages.items()}
        self._set(build, stages=stages, current=build.current)

    def start_turn(self, build: Build, turn: Turn) -> bool:
        """Claim the build for one turn; False when another turn is in progress."""
        result = self._builds.update_one(
            {"fde_id": build.fde_id, "id": build.id, "turn": None},
            {"$set": {"turn": turn.model_dump(), "updated_at": _now()}},
        )
        return result.modified_count == 1

    def finish_turn(self, build: Build, turn_id: str) -> None:
        self._builds.update_one(
            {"fde_id": build.fde_id, "id": build.id, "turn.id": turn_id},
            {"$set": {"turn": None, "updated_at": _now()}},
        )

    def set_sandbox(self, build: Build, sandbox_id: str | None) -> None:
        self._set(build, sandbox_id=sandbox_id)

    def _set(self, build: Build, **fields: object) -> None:
        self._builds.update_one({"fde_id": build.fde_id, "id": build.id}, {"$set": {**fields, "updated_at": _now()}})

    def _next_seq(self, build: Build) -> int:
        document = self._builds.find_one_and_update(
            {"fde_id": build.fde_id, "id": build.id},
            {"$inc": {"seq": 1}},
            projection={"seq": 1},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise LookupError(f"Build {build.id} not found")
        return document["seq"]

    # Artifacts

    def add_artifact(
        self,
        build: Build,
        stage: Stage,
        content: Spec | Design | Code,
        summary: str,
        author: str,
        based_on: dict[Stage, int] | None = None,
    ) -> Artifact:
        """The stage's next version. One turn at a time per build, so versions never collide."""
        latest = self.latest_artifact(build, stage)
        artifact = Artifact(
            fde_id=build.fde_id,
            build_id=build.id,
            stage=stage,
            version=(latest.version if latest else 0) + 1,
            content=content,
            based_on=based_on or {},
            summary=summary,
            author=author,
            created_at=_now(),
        )
        self._artifacts.insert_one(to_document(artifact))
        return artifact

    def set_smoke(self, build: Build, version: int, smoke: SmokeTest) -> None:
        """The smoke test of a code version: it runs after the version is saved, and can run again."""
        self._artifacts.update_one(
            {"fde_id": build.fde_id, "build_id": build.id, "stage": "code", "version": version},
            {"$set": {"content.smoke": smoke.model_dump()}},
        )

    def artifact(self, build: Build, stage: Stage, version: int) -> Artifact | None:
        document = self._artifacts.find_one(
            {"fde_id": build.fde_id, "build_id": build.id, "stage": stage, "version": version}, {"_id": 0}
        )
        return Artifact.model_validate(document) if document else None

    def latest_artifact(self, build: Build, stage: Stage) -> Artifact | None:
        document = self._artifacts.find_one(
            {"fde_id": build.fde_id, "build_id": build.id, "stage": stage},
            {"_id": 0},
            sort=[("version", DESCENDING)],
        )
        return Artifact.model_validate(document) if document else None

    # The timeline: messages and activity, in one sequence per build

    def add_message(
        self,
        build: Build,
        role: str,
        author: str,
        text: str,
        card: ArtifactCard | None = None,
        turn_id: str | None = None,
        delivery: Delivery | None = None,
        envelope: str | None = None,
        input_request: InputRequest | None = None,
    ) -> BuildMessage:
        message = BuildMessage.model_validate(
            {
                "fde_id": build.fde_id,
                "build_id": build.id,
                "seq": self._next_seq(build),
                "role": role,
                "author": author,
                "text": text,
                "card": card,
                "turn_id": turn_id,
                "delivery": delivery,
                "envelope": envelope,
                "input_request": input_request,
                "created_at": _now(),
            }
        )
        if input_request:  # the build waits for these answers, and nothing else: set before the form shows
            pending = {"message_id": message.id, "request_id": input_request.id, "kind": input_request.kind}
            self._set(build, pending_input=pending)
            build.pending_input = PendingInput.model_validate(pending)
        self._messages.insert_one(to_document(message))
        return message

    def resolve_input(self, build: Build, message_id: str, request: InputRequest) -> None:
        """The form is answered: store the answers on its message, and the build takes messages again."""
        self._messages.update_one(
            {"fde_id": build.fde_id, "build_id": build.id, "id": message_id},
            {"$set": {"input_request": to_document(request)}},
        )
        self._set(build, pending_input=None)
        build.pending_input = None

    def dismiss_input(self, build: Build, message_id: str) -> None:
        """A smoke test's form the user moved past (another message): closed unanswered."""
        self._messages.update_one(
            {"fde_id": build.fde_id, "build_id": build.id, "id": message_id},
            {"$set": {"input_request.status": "dismissed"}},
        )
        self._set(build, pending_input=None)
        build.pending_input = None

    def last_smoke_form(self, build: Build) -> InputRequest | None:
        """The latest smoke test form, to ask it again."""
        document = self._messages.find_one(
            {"fde_id": build.fde_id, "build_id": build.id, "input_request.kind": "smoke"},
            {"_id": 0, "input_request": 1},
            sort=[("seq", DESCENDING)],
        )
        return InputRequest.model_validate(document["input_request"]) if document else None

    def message(self, build: Build, message_id: str) -> BuildMessage | None:
        document = self._messages.find_one({"fde_id": build.fde_id, "build_id": build.id, "id": message_id}, {"_id": 0})
        return BuildMessage.model_validate(document) if document else None

    def messages(self, build: Build, after: int = 0) -> list[BuildMessage]:
        found = self._messages.find(
            {"fde_id": build.fde_id, "build_id": build.id, "seq": {"$gt": after}}, {"_id": 0}
        ).sort("seq", ASCENDING)
        return [BuildMessage.model_validate(document) for document in found]

    def set_delivery(self, build: Build, message_id: str, delivery: Delivery) -> None:
        self._messages.update_one(
            {"fde_id": build.fde_id, "build_id": build.id, "id": message_id}, {"$set": {"delivery": delivery}}
        )

    def queued(self, fde_id: str) -> list[BuildMessage]:
        """User messages waiting for a local worker (when the review app does not send turns to AMP)."""
        found = self._messages.find({"fde_id": fde_id, "role": "user", "delivery": "queued"}, {"_id": 0})
        return [BuildMessage.model_validate(document) for document in found.sort("seq", ASCENDING)]

    def add_activity(
        self,
        build: Build,
        kind: ActivityKind,
        text: str,
        turn_id: str | None = None,
        detail: dict[str, object] | None = None,
    ) -> ActivityEntry:
        entry = ActivityEntry(
            fde_id=build.fde_id,
            build_id=build.id,
            seq=self._next_seq(build),
            turn_id=turn_id,
            kind=kind,
            text=text,
            detail=detail or {},
            at=_now(),
        )
        self._activity.insert_one(to_document(entry))
        return entry

    def activity(self, build: Build, after: int = 0) -> list[ActivityEntry]:
        found = self._activity.find(
            {"fde_id": build.fde_id, "build_id": build.id, "seq": {"$gt": after}}, {"_id": 0}
        ).sort("seq", ASCENDING)
        return [ActivityEntry.model_validate(document) for document in found]
