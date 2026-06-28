from enum import Enum
from pydantic import BaseModel, computed_field


class Severity(str, Enum):
    ERROR = "ERROR"
    WARN = "WARN"


class Violation(BaseModel):
    rule_id: str
    severity: Severity
    message: str
    why: str
    fix: str
    path: str | None = None


class GuardResult(BaseModel):
    passed: bool
    violations: list[Violation] = []

    @computed_field
    @property
    def has_errors(self) -> bool:
        return any(v.severity == Severity.ERROR for v in self.violations)
