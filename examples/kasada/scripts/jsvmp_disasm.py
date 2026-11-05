"""Disassemble the Kasada JSVMP program and verify the boundaries it infers.

The capture comes from ``otto_vm_ledger.py``: ``program`` is the decoded array the
interpreter runs, ``ops`` are the executed ``(pc, opcode)`` pairs.

Encoding facts this file relies on (all measured, not assumed):

- the opcode of the instruction at ``program[pc]`` is ``program[pc] >> 5``;
- every operand is tagged the same way: ``value >> 5`` is the payload, ``value & 31``
  the tag;
- an instruction's width is ``1 + operands``, and the width per opcode is the mode of
  the program-counter deltas observed between consecutive executions.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

TAG_NAMES = {0: "", 1: "s", 2: "i", 3: "o", 4: "f", 5: "b", 6: "n", 7: "a"}
WIDTH_LIMIT = 40


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("program") or [], data.get("ops") or []


def widths_from(ops):
    """The most frequent gap between consecutive executions, per opcode."""
    counts = defaultdict(Counter)
    for index in range(len(ops) - 1):
        pc, opcode = ops[index] >> 8, ops[index] & 255
        delta = (ops[index + 1] >> 8) - pc
        if 0 < delta < WIDTH_LIMIT:
            counts[opcode][delta] += 1
    return {opcode: counter.most_common(1)[0][0] for opcode, counter in counts.items()}, counts


def operand(value):
    return "%s%d" % (TAG_NAMES.get(value & 31, "?"), value >> 5)


def disassemble(program, widths, start, count):
    lines = []
    pc = start
    while len(lines) < count and pc < len(program):
        opcode = program[pc] >> 5
        width = widths.get(opcode, 1)
        operands = [operand(program[pc + offset])
                    for offset in range(1, width) if pc + offset < len(program)]
        lines.append("%6d  %3d  %s" % (pc, opcode, " ".join(operands)))
        pc += width
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", default=r"C:/Users/huangconghua/AppData/Local/Temp/otto_vm_ledger.json")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=40)
    args = parser.parse_args()

    program, ops = load(args.capture)
    print("PROGRAM", len(program), "OPS", len(ops))

    widths, raw = widths_from(ops)
    table = sorted(widths.items(), key=lambda item: -sum(raw[item[0]].values()))
    print("%-6s %-6s %-6s %s" % ("op", "n", "width", "deltas"))
    for opcode, width in table[:12]:
        total = sum(raw[opcode].values())
        print("%-6d %-6d %-6d %s" % (opcode, total, width, raw[opcode].most_common(3)))

    # Every executed pc should land on an instruction boundary the widths predict.
    executed = sorted({value >> 8 for value in ops})
    boundaries, pc = set(), 0
    first = executed[0] if executed else 0
    pc = first
    while pc < len(program):
        boundaries.add(pc)
        pc += widths.get(program[pc] >> 5, 1)
    inside = [addr for addr in executed if addr < len(program)]
    hit = sum(1 for addr in inside if addr in boundaries)
    print("BOUNDARY_HIT %d/%d (%.1f%%)" % (hit, len(inside), 100.0 * hit / max(1, len(inside))))

    print("\n--- disassembly from %d ---" % args.start)
    for line in disassemble(program, widths, args.start, args.count):
        print(line)


main()
