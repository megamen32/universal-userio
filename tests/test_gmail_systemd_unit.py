from pathlib import Path


UNIT = Path(__file__).parents[1] / "deploy" / "userio-gmail-ingress.service"


def test_gmail_ingress_follows_core_lifecycle_and_restarts_clean_exits() -> None:
    text = UNIT.read_text(encoding="utf-8")

    assert "Requires=universal-userio.service" in text
    assert "PartOf=universal-userio.service" in text
    assert "WantedBy=universal-userio.service" in text
    assert "Restart=always" in text
    assert "ExecStart=/usr/bin/python3 -m universal_userio.gmail_ingress" in text


def test_gmail_ingress_has_a_bounded_runtime_budget() -> None:
    text = UNIT.read_text(encoding="utf-8")

    for directive in (
        "MemoryHigh=64M",
        "MemoryMax=128M",
        "MemorySwapMax=32M",
        "CPUQuota=25%",
        "TasksMax=16",
        "IOWeight=25",
    ):
        assert directive in text
