"""Runner factory used by the launcher test (imported by the detached child)."""
from task_frame_host import RoleResult


class _Runner:
    def run(self, role, *, attempt, feedback):
        return RoleResult("COMPLETED", f"{role} ok", f"task-frame-result://f/{role.lower()}/{attempt}", "b" * 64)


def make(spec):
    return _Runner()
