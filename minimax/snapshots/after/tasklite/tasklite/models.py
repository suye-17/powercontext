from dataclasses import dataclass


@dataclass
class Task:
    id: str
    title: str
    priority: int
    archived: bool = False
