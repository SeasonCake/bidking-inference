import json
from .ownership import Coordinator


def main() -> None:
    log = []
    coordinator = Coordinator(lambda: log.append("closed callback"))
    lease = coordinator.acquire("synthetic-worker")
    coordinator.shutdown()
    before = coordinator.wait_closed(0)
    wrong_owner = coordinator.complete(lease, "different-worker")
    completed = coordinator.complete(lease, "synthetic-worker")
    print(
        json.dumps(
            {
                "synthetic": True,
                "closed_before_drain": before,
                "wrong_owner_completed": wrong_owner,
                "owner_completed": completed,
                "closed_after_drain": coordinator.wait_closed(0),
                "callback_log": log,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
