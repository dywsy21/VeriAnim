from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "benchmark"
OUT_PATH = OUT_DIR / "verianim_animbench.jsonl"
README_PATH = OUT_DIR / "README.md"


EASY_OBJECTS = [
    "a red ball", "a blue cube", "a yellow cylinder", "a green cone", "a silver gear",
    "a wooden crate", "a glass marble", "an orange box", "a purple puck", "a white mug",
]
EASY_ACTIONS = [
    ("translate", "slides from the left side of the table to the right side"),
    ("rotate", "rotates one full turn around its vertical axis"),
    ("scale", "grows smoothly and then returns to its original size"),
    ("appear", "fades into view, stays visible, and then fades out"),
    ("support", "drops straight down and comes to rest on the floor"),
    ("camera", "stays still while the camera orbits halfway around it"),
    ("hinge", "opens like a small hinged lid and then stops"),
    ("path", "follows a curved path across a plain floor"),
    ("visibility", "moves behind a thin screen and reappears on the other side"),
    ("rotor", "rotates continuously on a small turntable while the base stays still"),
]

MEDIUM_SCENES = [
    ("ball", "blue box", "floor"),
    ("toy car", "wooden bridge", "road"),
    ("orange box", "gray conveyor belt", "cart"),
    ("mug", "saucer", "table"),
    ("small drone", "landing pad", "studio floor"),
    ("book", "shelf", "desk"),
    ("coin", "slot", "countertop"),
    ("package", "ramp", "platform"),
    ("robot gripper", "yellow cube", "tray"),
    ("windmill rotor", "tower", "field"),
]
MEDIUM_ACTIONS = [
    ("support+translate", "the {a} moves across the {c} and stops next to the {b} without floating."),
    ("follow_path+camera", "the {a} follows an S-shaped path around the {b} while the camera keeps both visible."),
    ("push", "the {a} pushes the {b} across the {c} while both objects remain in contact."),
    ("drop+support", "the {a} falls into the {b}, lands visibly, and stays inside at the end."),
    ("hinge+visibility", "{b} opens like a hinged door while {a} moves through the opening."),
    ("orbit+static", "The camera orbits around the {a} and {b} while the objects remain still on the {c}."),
    ("pick_place", "A simple gripper picks up the {a}, moves it to the {b}, and releases it cleanly."),
    ("rotor+static", "the {a} spins continuously while the {b} and {c} stay static and visible."),
    ("sequence", "the {a} moves from the {c} onto the {b}, pauses, and moves back again."),
    ("appear+translate", "the {a} appears beside the {b}, crosses the {c}, and disappears after stopping."),
]

HARD_SCENES = [
    ("parallel gripper", "orange box", "gray conveyor belt", "cart", "inspection camera"),
    ("forklift", "wooden pallet", "loading ramp", "warehouse shelf", "warning light"),
    ("robot arm", "metal cylinder", "turntable", "sorting bin", "side camera"),
    ("toy train", "bridge", "tunnel", "station platform", "signal gate"),
    ("two drones", "red package", "landing pad", "charging dock", "tracking camera"),
    ("crane hook", "shipping crate", "truck bed", "dock platform", "overhead camera"),
    ("marble", "spiral ramp", "catch tray", "transparent wall", "orbit camera"),
    ("cloth banner", "two posts", "floor fan", "studio backdrop", "front camera"),
    ("windmill", "rotating blades", "opening service door", "grass field", "orbit camera"),
    ("kitchen robot", "mug", "sink", "drying rack", "counter camera"),
]
HARD_ACTIONS = [
    ("pick_place+carry+camera", "the {a} lifts the {b} from the {c}, carries it to the {d}, releases it, and the {e} keeps the handoff visible."),
    ("support_sequence+hinge", "the {a} moves the {b} up the {c}, places it on the {d}, and opens a hinged guard before the transfer."),
    ("concurrent", "the {a} moves the {b} while the {c} moves in the opposite direction, then both stop near the {d} as the {e} pans across them."),
    ("multi_stage", "{b} starts on the {c}, travels through an intermediate support, enters the {d}, and remains visible to the {e}."),
    ("deformable+rigid", "the {a} carries the {b} past a deforming banner near the {c}, places it on the {d}, and the {e} shows both motions."),
    ("articulated+manipulation", "the {a} opens a hinged panel, moves the {b} from the {c} into the {d}, and closes the panel afterward."),
    ("containment+camera", "{b} rolls along the {c}, enters the {d}, and the {e} changes angle to show the final containment."),
    ("visibility+repair", "the {a} moves the {b} behind an obstacle, emerges near the {d}, and keeps the contact point visible to the {e}."),
    ("periodic+transfer", "the {a} transfers the {b} while a nearby rotor spins and the {c} continues moving under it."),
    ("mixed_family", "the {a} performs a rigid transfer of the {b} while a soft object near the {c} visibly deforms and the {e} orbits once."),
]


def easy_records() -> list[dict[str, object]]:
    records = []
    idx = 0
    for obj in EASY_OBJECTS:
        for family, action in EASY_ACTIONS:
            idx += 1
            records.append({
                "id": f"easy_{idx:03d}",
                "tier": "easy",
                "families": ["rigid"] if family not in {"camera", "visibility"} else [family],
                "prompt": f"Create a simple scene where {obj} {action}.",
                "required_motions": [family],
                "verifier_focus": ["object visibility", "start-middle-end temporal change"],
                "difficulty_rationale": "One primary object and one dominant motion contract.",
            })
    return records


def medium_records() -> list[dict[str, object]]:
    records = []
    idx = 0
    for a, b, c in MEDIUM_SCENES:
        for family, template in MEDIUM_ACTIONS:
            idx += 1
            records.append({
                "id": f"medium_{idx:03d}",
                "tier": "medium",
                "families": sorted(set(["rigid", "camera"] if "camera" in family or "orbit" in family else ["rigid"])),
                "prompt": "Create an animation where " + template.format(a=a, b=b, c=c),
                "required_motions": family.split("+"),
                "verifier_focus": ["object identity", "contact or final relation", "camera coverage"],
                "difficulty_rationale": "Two or three scene objects with coordinated motion or explicit visibility requirements.",
            })
    return records


def hard_records() -> list[dict[str, object]]:
    records = []
    idx = 0
    for a, b, c, d, e in HARD_SCENES:
        for family, template in HARD_ACTIONS:
            idx += 1
            families = ["rigid"]
            if "camera" in family or "orbit" in family:
                families.append("camera")
            if "deformable" in family or "mixed" in family:
                families.append("deformable")
            if "articulated" in family or "hinge" in family:
                families.append("rigid")
            records.append({
                "id": f"hard_{idx:03d}",
                "tier": "hard",
                "families": sorted(set(families)),
                "prompt": "Create a detailed animation where " + template.format(a=a, b=b, c=c, d=d, e=e),
                "required_motions": family.split("+"),
                "verifier_focus": [
                    "multi-object identity",
                    "temporal ownership",
                    "contact and nonpenetration",
                    "camera/video visibility",
                ],
                "difficulty_rationale": "Multiple objects, event composition, concurrent motion, or mixed animation families.",
            })
    return records


def main() -> None:
    records = easy_records() + medium_records() + hard_records()
    assert len(records) == 300
    OUT_DIR.mkdir(exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    README_PATH.write_text(
        "# VeriAnim-AnimBench\n\n"
        "VeriAnim-AnimBench contains 300 natural-language Blender animation prompts.\n"
        "The benchmark has 100 easy, 100 medium, and 100 hard prompts.\n\n"
        "- Easy prompts isolate one object and one dominant motion.\n"
        "- Medium prompts coordinate two or three objects, contact relations, or camera coverage.\n"
        "- Hard prompts combine multi-object composition, temporal ownership, concurrent motion, or mixed animation families.\n\n"
        "Each JSONL record contains `id`, `tier`, `families`, `prompt`, `required_motions`, "
        "`verifier_focus`, and `difficulty_rationale`.\n",
        encoding="utf-8",
    )
    print(f"wrote {len(records)} prompts to {OUT_PATH}")


if __name__ == "__main__":
    main()
