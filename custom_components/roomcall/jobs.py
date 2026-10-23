"""Observable bounded communication jobs with retries, expiry, replies and ownership."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.core import Context, HomeAssistant, callback

from .calls import CallError
from .core import CallRecord
from .errors import Problem
from .messages import send
from .policy import person_area

if TYPE_CHECKING:
    from . import RoomCallData

TERMINAL = {"completed", "partial", "failed", "expired", "cancelled", "acknowledged", "answered"}


@dataclass
class Job:
    id: str
    owner: str | None
    values: dict[str, Any]
    context: Context
    deadline: float
    assistant: str | None = None
    state: str = "queued"
    created: float = field(default_factory=time.time)
    record: CallRecord | None = None
    error: dict[str, Any] | None = None
    responses: list[dict[str, Any]] = field(default_factory=list)
    task: asyncio.Task | None = None
    acknowledged: asyncio.Event = field(default_factory=asyncio.Event)

    started_mono: float = field(default_factory=time.monotonic)
    progress: list[dict[str, Any]] = field(default_factory=list)

    def phase(self, stage: str) -> None:
        self.progress.append(
            {
                "stage": stage,
                "elapsed_ms": max(0, round((time.monotonic() - self.started_mono) * 1000)),
            }
        )
        self.progress[:] = self.progress[-16:]

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "progress": list(self.progress),
            "created": self.created,
            "result": self.record.summary() if self.record else None,
            "error": self.error,
            "responses": self.responses,
            "audible": None,
        }


class Jobs:
    def __init__(self, hass: HomeAssistant, data: RoomCallData) -> None:
        self.hass, self.data = hass, data
        self.items: dict[str, Job] = {}
        self.keys: dict[tuple[str | None, str], tuple[str, str]] = {}

    def submit(self, values: dict[str, Any], context: Context, assistant: str | None = None) -> Job:
        fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
        key = (context.user_id, values.get("idempotency_key", ""))
        if key[1] and key in self.keys:
            job_id, existing = self.keys[key]
            if existing != fingerprint:
                raise Problem("idempotency_conflict")
            self.data.metrics.counters["duplicates_suppressed"] += 1
            return self.items[job_id]
        if len(self.items) >= 128:
            oldest = next((j for j in self.items.values() if j.state in TERMINAL), None)
            if oldest is None:
                raise Problem("queue_full", retryable=True)
            self.items.pop(oldest.id)
            self.keys = {k: v for k, v in self.keys.items() if v[0] != oldest.id}
        job = Job(
            secrets.token_urlsafe(16),
            context.user_id,
            dict(values),
            context,
            asyncio.get_running_loop().time() + values.get("expires_in", 300),
            assistant=assistant,
        )
        job.phase("queued")
        self.items[job.id] = job
        if key[1]:
            self.keys[key] = (job.id, fingerprint)
        job.task = self.hass.async_create_background_task(self._run(job), f"roomcall job {job.id}")
        self.data.metrics.counters["jobs_submitted"] += 1
        return job

    async def get(self, job_id: str, context: Context, *, recipient: bool = False) -> Job:
        job = self.items.get(job_id)
        if job is None:
            raise Problem("not_found")
        user = await self.hass.auth.async_get_user(context.user_id) if context.user_id else None
        if job.owner != context.user_id and not (user and user.is_admin) and not recipient:
            raise Problem("forbidden")
        if recipient and job.owner != context.user_id:
            from .communication import permitted

            if not user or not job.record:
                raise Problem("forbidden")
            for delivery in job.record.results:
                if not await permitted(self.hass, delivery.entity_id, context, control=False):
                    raise Problem("forbidden")
        return job

    async def cancel(self, job_id: str, context: Context) -> Job:
        job = await self.get(job_id, context)
        if job.state != "queued":
            raise Problem("already_started")
        job.state = "cancelled"
        if job.task:
            job.task.cancel()
        self.data.metrics.counters["jobs_cancelled"] += 1
        return job

    async def acknowledge(self, job_id: str, context: Context, response: str) -> Job:
        job = await self.get(job_id, context, recipient=True)
        if job.state != "waiting_ack":
            raise Problem("not_waiting")
        if not context.user_id:
            raise Problem("forbidden")
        # A signed-in household member explicitly acknowledges; identity is never inferred.
        if not any(r.get("user_id") == context.user_id for r in job.responses):
            job.responses.append(
                {
                    "user_id": context.user_id,
                    "text": response,
                    "at": time.time(),
                    "method": "explicit",
                }
            )
            self.data.metrics.counters["acknowledgments"] += 1
        job.state = "acknowledged"
        job.acknowledged.set()
        if job.record:
            job.record.requires_ack = False
            job.record.evidence.setdefault("human", {})["acknowledged"] = True
        return job

    async def _run(self, job: Job) -> None:
        def dispatched() -> None:
            job.state = "running"
            if not any(p["stage"] == "dispatching" for p in job.progress):
                job.phase("dispatching")

        try:
            async with asyncio.timeout_at(job.deadline):
                if job.values.get("defer_until_present"):
                    job.phase("waiting_presence")
                    await self._wait_for_presence(job)
                if job.values.get("satellites"):
                    dispatched()
                    await self._ask(job)
                    return
                from .communication import validate_targets

                await validate_targets(self.hass, job.values, job.context, job.assistant)
                job.record = await send(
                    self.hass,
                    self.data,
                    job.values,
                    job.context,
                    call_id=job.id,
                    deadline=job.deadline,
                    on_dispatch=dispatched,
                    on_progress=job.phase,
                )
                if not job.record.ok:
                    accepted = sum(r.ok for r in job.record.results)
                    job.state = "partial" if accepted else "failed"
                    if all(r.error_code == "expired" for r in job.record.results):
                        job.state = "expired"
                    code = (
                        next(
                            (r.error_code for r in job.record.results if not r.ok),
                            "playback_failed",
                        )
                        or "playback_failed"
                    )
                    job.error = Problem(code, retryable=code != "expired").as_dict(self.hass)[
                        "error"
                    ]
                    await self._retain(job)
                    return
                if job.values.get("require_ack"):
                    job.record.requires_ack = True
                    job.state = "waiting_ack"
                    job.phase("waiting_ack")
                    try:
                        async with asyncio.timeout(job.values.get("ack_timeout", 60)):
                            await job.acknowledged.wait()
                    except TimeoutError:
                        job.state = "expired"
                        job.error = Problem("ack_timeout").as_dict(self.hass)["error"]
                        await self._retain(job)
                        await self._fallback(job)
                else:
                    job.state = "completed"
        except TimeoutError:
            job.state = "expired"
            job.error = Problem("expired").as_dict(self.hass)["error"]
            await self._retain(job)
        except (Problem, CallError) as err:
            problem = (
                err
                if isinstance(err, Problem)
                else Problem(
                    err.code,
                    retryable=err.code
                    in {"dnd_blocked", "quiet_hours_blocked", "presence_unknown"},
                )
            )
            job.state, job.error = "failed", problem.as_dict(self.hass)["error"]
            await self._retain(job)
        except asyncio.CancelledError:
            job.state = "cancelled"
            raise
        except Exception:
            logging.getLogger(__name__).exception("RoomCall job %s failed", job.id)
            job.state, job.error = "failed", Problem("provider_failed").as_dict(self.hass)["error"]
        finally:
            if job.record and job.state != "waiting_ack":
                job.record.requires_ack = False
            job.phase(job.state)
            self.data.metrics.counters[f"jobs_{job.state}"] += 1

    async def _ask(self, job: Job) -> None:
        async def ask(entity_id: str) -> None:
            lock = await self.data.scheduler.acquire(entity_id, job.deadline)
            try:
                response = await self.hass.services.async_call(
                    "assist_satellite",
                    "ask_question",
                    {
                        "entity_id": entity_id,
                        "question": job.values["message"],
                        "answers": job.values.get("answers", []),
                    },
                    blocking=True,
                    return_response=True,
                    context=job.context,
                )
                job.responses.append({"entity_id": entity_id, **(response or {})})
            except Exception:
                job.responses.append({"entity_id": entity_id, "error": "provider_failed"})
            finally:
                lock.release()

        await asyncio.gather(*(ask(e) for e in job.values["satellites"]))
        job.state = "answered" if all(r.get("sentence") for r in job.responses) else "expired"

    async def _retain(self, job: Job) -> None:
        if job.values.get("voicemail") and self.data.mailbox and self.data.voicemail_enabled:
            await self.data.mailbox.add(
                job.owner,
                job.values.get("message", ""),
                ", ".join(job.values.get("areas", [])),
                request={
                    key: job.values[key]
                    for key in ("areas", "people", "speakers", "language")
                    if key in job.values
                },
            )

    async def _wait_for_presence(self, job: Job) -> None:
        changed = asyncio.Event()

        @callback
        def on_change(_event) -> None:
            changed.set()

        unsubscribe = self.hass.bus.async_listen("state_changed", on_change)
        try:
            while any(
                person_area(
                    self.hass,
                    person,
                    max_age=self.data.presence_max_age,
                    min_confidence=self.data.presence_min_confidence,
                )
                is None
                for person in job.values.get("people", [])
            ):
                changed.clear()
                await changed.wait()
        finally:
            unsubscribe()

    async def _fallback(self, job: Job) -> None:
        if not job.values.get("fallback_notify"):
            return
        for entity_id in self.data.notify:
            service = entity_id.removeprefix("notify.")
            if self.hass.services.has_service("notify", service):
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {
                        "title": "RoomCall",
                        "message": job.values.get("message", ""),
                        "data": {"tag": job.id},
                    },
                    blocking=True,
                    context=job.context,
                )

    async def close(self) -> None:
        tasks = [job.task for job in self.items.values() if job.task and not job.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
