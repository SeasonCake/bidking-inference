"""Run a synthetic half-stream example without network access."""
import json
from . import TcpSeqReassembler


def main():
    flow = ("sender", 1, "receiver", 2)
    gate = TcpSeqReassembler(event_limit=0)
    arrivals = [(100, b"AAAA"), (108, b"CCCC"), (104, b"BBBB"), (108, b"CCCC")]
    chunks = [gate.feed(flow, seq, data, now=i) for i, (seq, data) in enumerate(arrivals)]
    print(json.dumps({"synthetic": True, "output": b"".join(chunks).decode("ascii"),
                      "emitted_lengths": list(map(len, chunks)), "status": gate.as_dict()}, indent=2))


if __name__ == "__main__":
    main()
