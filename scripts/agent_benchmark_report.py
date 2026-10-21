"""Render recorded protocol-agent journey measurements from pytest's JUnit artifact."""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree


def main() -> None:
    root = ElementTree.parse(Path(sys.argv[1])).getroot()
    print("## RoomCall protocol-agent journeys\n")
    print(
        "Deterministic HA tool clients; mocked devices/providers and scripted listening answers.\n"
    )
    print("| Journey | Result | Measurements |")
    print("|---|---|---|")
    for case in root.iter("testcase"):
        measurements = [
            f"{item.attrib['name']}: {item.attrib.get('value', '')}"
            for item in case.findall("./properties/property")
        ]
        if not measurements:
            continue
        outcome = (
            "passed"
            if not list(case.findall("failure")) + list(case.findall("error"))
            else "failed"
        )
        print(f"| {case.attrib['name']} | {outcome} | {'; '.join(measurements)} |")


if __name__ == "__main__":
    main()
