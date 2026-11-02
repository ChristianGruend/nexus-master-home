from __future__ import annotations
from .job import DownloadJob

class DownloadQueue:
    def __init__(self) -> None:
        self.jobs: dict[str, DownloadJob] = {}

    def add(self, job: DownloadJob) -> None:
        self.jobs[job.id] = job

    def list(self) -> list:
        return [j.to_dict() for j in self.jobs.values()]
