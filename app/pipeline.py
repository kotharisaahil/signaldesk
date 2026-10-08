"""Bounded background jobs; restarts never retry paid requests automatically."""
import os
import threading
import time

from .engine import validate_assessments, score, validate_profile
from .provider import analyze, DEFAULT_MODEL, ProviderError
from .research import research, timestamp
from .workspace import ident


def inquiry_source(lead):
    return [{"id":"inquiry", "kind":"inquiry", "title":"User-supplied inquiry / notes", "url":"",
             "text":lead["inquiry"], "retrieved_at":timestamp()}] if lead["inquiry"] else []


def qualify_one(store, lead_id, profile, key, model, fetch_website=True, researcher=research, analyzer=analyze):
    started = time.monotonic()
    lead = store.lead(lead_id)
    sources, warnings = inquiry_source(lead), []
    if fetch_website and lead["website"]:
        pages, warnings = researcher(lead["website"])
        sources += pages
    if not sources:
        raise ValueError("No readable website or inquiry evidence. Add inquiry text, or use manual review with supplied evidence.")
    assessments, usage = analyzer(profile, lead, sources, key, model)
    assessments, validation_warnings = validate_assessments(profile, sources, assessments)
    warnings += validation_warnings
    return store.save_run(lead_id, {"profile":profile, "assessments":assessments,
                          "sources":sources, "warnings":warnings, "result":score(profile, assessments),
                          "usage":usage, "method":"ai", "duration_seconds":round(time.monotonic()-started,2)})


def manual_run(store, lead_id, supplied):
    profile = store.setting("profile")
    if not profile:
        raise ValueError("Save your product profile first.")
    lead = store.lead(lead_id)
    sources = inquiry_source(lead)
    cached = store.setting("research:" + lead_id, {})
    sources += cached.get("sources", [])
    assessments, warnings = validate_assessments(profile, sources, supplied)
    return store.save_run(lead_id, {"profile":profile, "assessments":assessments, "sources":sources,
                          "warnings":warnings + cached.get("warnings", []), "result":score(profile, assessments),
                          "usage":{}, "method":"manual", "duration_seconds":0})


class Runtime:
    def __init__(self, store, qualifier=qualify_one):
        self.store, self.qualifier = store, qualifier
        self.lock = threading.Lock()
        self.key = os.environ.get("OPENAI_API_KEY", "")
        self.model = store.setting("model", os.environ.get("OPENAI_MODEL", DEFAULT_MODEL))
        self.active = None
        self.cancel = threading.Event()
        for job in store.jobs():
            if job["status"] in ("running", "queued"):
                job["status"] = "interrupted"
                job["message"] = "The server restarted. No paid calls were retried. Review completed rows before starting again."
                for item in job["items"]:
                    if item["status"] in ("running", "queued"):
                        item["status"] = "interrupted"
                store.save_job(job)

    def connection(self):
        return {"provider":"OpenAI", "model":self.model, "key_available":bool(self.key),
                "key_storage":"Server memory only; environment variables are supported."}

    def configure(self, payload):
        import re
        with self.lock:
            if self.active:
                raise ValueError("Wait for the active job to finish before changing the connection.")
            model = payload.get("model", self.model)
            if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,100}", model):
                raise ValueError("Enter a valid model ID.")
            key = payload.get("api_key", "")
            if not isinstance(key, str) or len(key) > 600 or any(c.isspace() for c in key):
                raise ValueError("Enter an API key without spaces.")
            if payload.get("clear_key") is True:
                self.key = ""
            elif key:
                self.key = key
            self.model = model
            self.store.set_setting("model", model)
        return self.connection()

    def start(self, lead_ids, fetch_website, consent):
        if consent is not True:
            raise ValueError("Confirm that the selected inquiries and website excerpts may be sent to OpenAI.")
        if not isinstance(lead_ids, list) or not 1 <= len(lead_ids) <= 20 or any(not isinstance(i,str) for i in lead_ids):
            raise ValueError("Choose 1–20 leads per run.")
        if len(set(lead_ids)) != len(lead_ids):
            raise ValueError("A lead cannot appear twice in the same run.")
        if type(fetch_website) is not bool:
            raise ValueError("Choose whether to research websites.")
        profile = self.store.setting("profile")
        if not profile:
            raise ValueError("Save your product profile before qualifying leads.")
        for lead_id in lead_ids:
            self.store.lead(lead_id)
        with self.lock:
            if not self.key:
                raise ValueError("Connect your OpenAI API key in Settings first.")
            if self.active:
                raise ValueError("A qualification job is already running. Wait or cancel it before starting another.")
            job = {"id":ident(), "status":"running", "created_at":timestamp(), "profile_version":profile["version"],
                   "model":self.model, "fetch_website":fetch_website, "items":[{"lead_id":i,"status":"queued"} for i in lead_ids]}
            self.active = job["id"]
            self.cancel.clear()
            self.store.save_job(job)
            threading.Thread(target=self._work, args=(job, profile, self.key, self.model), daemon=True).start()
        return job

    def _work(self, job, profile, key, model):
        halted = False
        try:
            for item in job["items"]:
                if halted:
                    item.update(status="skipped", error="Resolve the provider connection or quota error before retrying.")
                    continue
                if self.cancel.is_set():
                    item["status"] = "cancelled"
                    continue
                item["status"] = "running"
                self.store.save_job(job)
                try:
                    run = self.qualifier(self.store,item["lead_id"],profile,key,model,job["fetch_website"])
                    item.update(status="completed",run_id=run["id"])
                except ValueError as error:
                    item.update(status="failed",error=str(error)[:500])
                    if isinstance(error, ProviderError) and error.stop_batch:
                        halted = True
                except Exception:
                    item.update(status="failed",error="An unexpected processing error occurred. No result was saved; inspect the input before retrying.")
                self.store.save_job(job)
            job["status"] = "cancelled" if self.cancel.is_set() else "completed_with_errors" if any(i["status"]=="failed" for i in job["items"]) else "completed"
            job["finished_at"] = timestamp()
        finally:
            self.store.save_job(job)
            with self.lock:
                self.active = None

    def cancel_job(self, job_id):
        with self.lock:
            if self.active != job_id:
                raise ValueError("That job is no longer running.")
            self.cancel.set()
        return {"message":"Cancellation requested. The current network call may finish; remaining leads will be skipped."}
