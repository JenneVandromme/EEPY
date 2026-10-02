"""Tests for the validator.

Each test plants one specific mistake and checks that the validator finds it
and says something useful about it. A validator that passes bad files is worse
than no validator, so the emphasis is on things that MUST fail.
"""

from __future__ import annotations

import pytest


def messages(validator) -> str:
    """All problem text, for substring assertions."""
    return "\n".join(
        f"{p.where} {p.message} {p.hint or ''}" for p in validator.problems
    )


# --- the happy path --------------------------------------------------------

GOOD = """
behaviors:
  - name: fine
    ladder: demo
    level: 0
    description: A perfectly ordinary behaviour.
    keyframes:
      - look_at: user
        duration: 0.8
        hold: 0.3
      - use: nod_small
      - head: { pitch: 5, roll: 5 }
        antennas: { right: -30, left: 30 }
        duration: 0.6
        easing: cartoon
        sound: beep.wav
        say: hello
"""


def test_a_good_file_passes(write_behaviors, run_validator):
    write_behaviors(GOOD)
    validator = run_validator()
    assert not validator.errors, messages(validator)


# --- hard limits -----------------------------------------------------------


@pytest.mark.parametrize(
    "field,value,expect",
    [
        ("pitch", 55, "head pitch"),
        ("pitch", -55, "head pitch"),
        ("roll", 50, "head roll"),
        ("z", 200, "head height"),
        ("z", -200, "head height"),
    ],
)
def test_out_of_range_head_values_are_errors(
    write_behaviors, run_validator, field, value, expect
):
    write_behaviors(
        f"""
behaviors:
  - name: too_far
    description: Out of range.
    keyframes:
      - head: {{ {field}: {value} }}
        duration: 1.0
"""
    )
    validator = run_validator()
    assert validator.errors, "an out-of-range value must be an error"
    assert expect in messages(validator)


def test_out_of_range_is_never_silently_clamped(write_behaviors, run_validator):
    """The brief is explicit: out of range is an error, not a quiet fix."""
    write_behaviors(
        """
behaviors:
  - name: way_off
    description: Far out of range.
    keyframes:
      - head: { pitch: 400 }
        duration: 1.0
"""
    )
    validator = run_validator()
    assert validator.errors
    # and the reported number is the one the designer wrote, not a clamped one
    assert "400" in messages(validator)


def test_body_and_head_yaw_cannot_diverge_too_far(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: twisted
    description: Head turned much further than the body.
    keyframes:
      - head: { yaw: 100 }
        body_yaw: 0
        duration: 1.0
"""
    )
    validator = run_validator()
    assert "away from the body" in messages(validator)


def test_antenna_past_limit_is_an_error(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: antennas_too_far
    description: Antenna past 180.
    keyframes:
      - antennas: { right: -200, left: 200 }
        duration: 1.0
"""
    )
    validator = run_validator()
    assert "antenna" in messages(validator)


# --- reachability, the check ranges cannot do ------------------------------


def test_an_unreachable_combination_is_caught(write_behaviors, run_validator):
    """Each value is legal on its own; together they are impossible.

    This is the whole reason the validator calls the real IK.
    """
    write_behaviors(
        """
behaviors:
  - name: impossible
    description: Legal pitch, legal height, impossible together.
    keyframes:
      - head: { pitch: 35, z: 18 }
        duration: 1.0
"""
    )
    validator = run_validator()
    assert "cannot physically reach" in messages(validator), messages(validator)


def test_each_axis_of_that_pose_passes_a_range_check_alone(
    write_behaviors, run_validator
):
    """Proves the previous test is really about coupling, not about ranges."""
    for body in ("{ pitch: 35 }", "{ z: 18 }"):
        write_behaviors(
            f"""
behaviors:
  - name: single_axis
    description: One axis at a time is fine.
    keyframes:
      - head: {body}
        duration: 1.0
"""
        )
        validator = run_validator()
        assert not validator.errors, f"{body} should be fine on its own"


def test_reachability_can_be_skipped(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: impossible
    description: Unreachable combination.
    keyframes:
      - head: { pitch: 35, z: 18 }
        duration: 1.0
"""
    )
    assert not run_validator(check_reach=False).errors


# --- missing references ----------------------------------------------------


@pytest.mark.parametrize(
    "keyframe,expect",
    [
        ("- use: nod_smal", 'no building block called "nod_smal"'),
        ("- look_at: userr\n        duration: 1.0", 'no gaze target called "userr"'),
        (
            "- head: { pitch: 1 }\n        duration: 1.0\n        say: helo",
            'no line called "helo"',
        ),
        (
            "- head: { pitch: 1 }\n        duration: 1.0\n        sound: missing.wav",
            "no sounds/missing.wav",
        ),
    ],
)
def test_missing_references_are_errors(
    write_behaviors, run_validator, keyframe, expect
):
    write_behaviors(
        f"""
behaviors:
  - name: dangling
    description: Points at something that does not exist.
    keyframes:
      {keyframe}
"""
    )
    validator = run_validator()
    assert expect in messages(validator), messages(validator)


def test_a_typo_gets_a_did_you_mean(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: typo
    description: Misspelled block.
    keyframes:
      - use: nod_smal
"""
    )
    assert 'Did you mean "nod_small"?' in messages(run_validator())


def test_unknown_field_is_an_error_with_a_suggestion(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: fat_finger
    description: Misspelled field.
    keyframes:
      - head: { pitch: 2 }
        antena: { right: -10, left: 10 }
        duration: 1.0
"""
    )
    report = messages(run_validator())
    assert "antena" in report and 'Did you mean "antennas"?' in report


def test_a_bad_sound_extension_is_rejected(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: bad_format
    description: Unsupported audio format.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
        sound: noise.aiff
"""
    )
    assert "not a sound format" in messages(run_validator())


# --- names, ladders, structure --------------------------------------------


def test_duplicate_behaviour_names_are_an_error(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: twin
    description: One.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
  - name: twin
    description: Two.
    keyframes:
      - head: { pitch: 2 }
        duration: 1.0
"""
    )
    assert 'the name "twin" is used' in messages(run_validator())


def test_duplicate_names_across_two_files_are_an_error(
    write_behaviors, run_validator
):
    body = """
behaviors:
  - name: shared
    description: Defined twice, in two files.
    keyframes:
      - head: {{ pitch: {p} }}
        duration: 1.0
"""
    write_behaviors(body.format(p=1), name="a.yaml")
    write_behaviors(body.format(p=2), name="b.yaml")
    assert 'the name "shared" is used' in messages(run_validator())


def test_a_gap_in_a_ladder_is_an_error(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: rung0
    ladder: gappy
    level: 0
    description: Bottom.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
  - name: rung2
    ladder: gappy
    level: 2
    description: Skips level 1.
    keyframes:
      - head: { pitch: 2 }
        duration: 1.0
"""
    )
    report = messages(run_validator())
    assert "level(s) 1 are missing" in report


def test_a_ladder_must_start_at_zero(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: rung1
    ladder: late
    level: 1
    description: Starts at 1.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
"""
    )
    assert "not 0" in messages(run_validator())


def test_two_behaviours_on_one_rung_is_an_error(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: first
    ladder: crowded
    level: 0
    description: One.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
  - name: second
    ladder: crowded
    level: 0
    description: Also level 0.
    keyframes:
      - head: { pitch: 2 }
        duration: 1.0
"""
    )
    assert "claimed by 2 behaviours" in messages(run_validator())


def test_ladder_without_level_is_an_error(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: half_set
    ladder: lonely
    description: Ladder but no level.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
"""
    )
    assert "go together" in messages(run_validator())


def test_keyframe_needs_a_duration(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: timeless
    description: No duration.
    keyframes:
      - head: { pitch: 1 }
"""
    )
    assert "needs a 'duration:'" in messages(run_validator())


def test_an_empty_keyframe_is_an_error(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: blank
    description: A keyframe that does nothing.
    keyframes:
      - duration: 1.0
"""
    )
    assert "does nothing" in messages(run_validator())


def test_a_keyframe_cannot_mix_a_pose_and_a_block(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: mixed
    description: Pose and block at once.
    keyframes:
      - head: { pitch: 1 }
        use: nod_small
        duration: 1.0
"""
    )
    assert "two things at once" in messages(run_validator())


def test_sleep_handover_must_be_last(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: early_sleep
    description: Keyframes after the sleep handover.
    keyframes:
      - action: goto_sleep
        duration: 2.0
      - head: { pitch: 1 }
        duration: 1.0
"""
    )
    assert "must be the last keyframe" in messages(run_validator())


def test_blocks_cannot_nest(write_behaviors, behaviors_dir, run_validator):
    (behaviors_dir / "_blocks.yaml").write_text(
        """
gaze_targets:
  user:
    pitch: -12
blocks:
  outer:
    description: Tries to use another block.
    keyframes:
      - use: inner
  inner:
    description: Inner block.
    keyframes:
      - head: { pitch: 2 }
        duration: 1.0
"""
    )
    write_behaviors(
        """
behaviors:
  - name: anything
    description: Placeholder.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
"""
    )
    assert "cannot use another building block" in messages(run_validator())


# --- the YAML traps --------------------------------------------------------


def test_yaml_bool_in_loop_is_explained(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: loopy
    description: loop written as no.
    loop: no
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
"""
    )
    report = messages(run_validator())
    assert "yes, no, on and off" in report


def test_duplicate_yaml_key_is_reported_with_a_line_number(
    write_behaviors, run_validator
):
    write_behaviors(
        """
behaviors:
  - name: dup
    description: one
    description: two
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
"""
    )
    validator = run_validator()
    assert "appears twice" in messages(validator)
    assert any(p.line is not None for p in validator.problems)


def test_tab_indentation_is_explained(write_behaviors, run_validator):
    write_behaviors("behaviors:\n  - name: tabbed\n\tdescription: x\n")
    assert "tab" in messages(run_validator()).lower()


# --- one mistake must not hide the others ---------------------------------


def test_every_problem_is_reported_in_one_run(write_behaviors, run_validator):
    """A designer should not have to re-run once per mistake."""
    write_behaviors(
        """
behaviors:
  - name: bad_one
    description: Unknown field.
    keyframes:
      - head: { pitch: 2 }
        antena: { right: -10, left: 10 }
        duration: 1.0
  - name: bad_two
    description: Out of range.
    keyframes:
      - head: { pitch: 70 }
        duration: 1.0
  - name: bad_three
    description: Dangling block reference.
    keyframes:
      - use: no_such_block
"""
    )
    report = messages(run_validator())
    assert "antena" in report
    assert "head pitch" in report
    assert "no_such_block" in report


# --- warnings vs errors ---------------------------------------------------


def test_a_brisk_movement_warns_but_does_not_fail(write_behaviors, run_validator):
    """Over the 150 deg/s advisory speed, under the 300 deg/s hard limit.

    Linear easing, so peak speed equals average speed: 40 deg in 0.2 s is
    200 deg/s -- brisk enough to look twitchy, legal enough to play.
    """
    write_behaviors(
        """
behaviors:
  - name: brisk
    description: Fast but legal.
    keyframes:
      - head: { yaw: 0 }
        duration: 1.0
      - head: { yaw: 40 }
        duration: 0.2
        easing: linear
"""
    )
    validator = run_validator()
    assert validator.warnings, "200 deg/s should warn"
    assert not validator.errors, "but it is under the hard limit, so it must not fail"


def test_a_movement_over_the_hard_speed_limit_fails(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: twitchy
    description: Far too fast.
    keyframes:
      - head: { yaw: 0 }
        duration: 1.0
      - head: { yaw: 40 }
        duration: 0.05
"""
    )
    assert "over the 300 deg/s limit" in messages(run_validator())


def test_peak_speed_not_average_is_what_counts(write_behaviors, run_validator):
    """30 deg in 0.4 s averages 75 deg/s -- but cartoon peaks at 5.6x that."""
    write_behaviors(
        """
behaviors:
  - name: snappy
    description: Harmless average, dangerous peak.
    keyframes:
      - head: { yaw: 0 }
        duration: 1.0
      - head: { yaw: 30 }
        duration: 0.4
        easing: cartoon
"""
    )
    assert "peak at" in messages(run_validator())


def test_cartoon_overshoot_past_a_limit_fails(write_behaviors, run_validator):
    """Both keyframes are legal; the curve between them is not."""
    write_behaviors(
        """
behaviors:
  - name: overshoot
    description: Cartoon winds up past the pitch limit.
    keyframes:
      - head: { pitch: 38 }
        duration: 1.0
      - head: { pitch: 0 }
        duration: 1.5
        easing: cartoon
"""
    )
    report = messages(run_validator())
    assert "on the way to this keyframe" in report
    assert "overshoots" in report


# --- spoken lines ---------------------------------------------------------


def test_an_unknown_placeholder_is_an_error(behaviors_dir, write_behaviors, run_validator):
    (behaviors_dir / "lines.yaml").write_text(
        """
lines:
  hello:
    description: Bad placeholder.
    variants:
      direct:
        - "In {minute} minutes."
"""
    )
    write_behaviors(
        """
behaviors:
  - name: anything
    description: Placeholder.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
"""
    )
    report = messages(run_validator())
    assert "{minute}" in report and 'Did you mean "{minutes}"?' in report


def test_a_line_needs_at_least_one_variant(behaviors_dir, write_behaviors, run_validator):
    (behaviors_dir / "lines.yaml").write_text(
        """
lines:
  empty:
    description: No variants at all.
    variants: {}
"""
    )
    write_behaviors(
        """
behaviors:
  - name: anything
    description: Placeholder.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
"""
    )
    assert "at least one variant" in messages(run_validator())


# --- the repo's own files -------------------------------------------------


def test_the_repos_real_files_validate_clean():
    """The behaviours shipped in this repo must pass with no errors."""
    from eepy.behaviors.validate import Validator

    validator = Validator()
    validator.run()
    assert not validator.errors, messages(validator)


# --- regressions found in review ------------------------------------------


def test_a_sound_next_to_use_actually_lands(write_behaviors, behaviors_dir, run_validator):
    """It used to validate clean and then be silently thrown away.

    This is how anyone would attach a sound to a block, so passing the check and
    doing nothing is the one failure mode that destroys trust in the checker.
    """
    from eepy.behaviors.loader import load_library

    write_behaviors(
        """
behaviors:
  - name: with_sound
    description: A sound attached to a block.
    keyframes:
      - use: nod_small
        sound: beep.wav
"""
    )
    assert not run_validator().errors
    library = load_library(behaviors_dir)
    steps = library.resolve(library.behaviors["with_sound"])
    assert steps[0].sound == "beep.wav", "the sound must survive block expansion"


def test_timing_next_to_use_is_rejected(write_behaviors, run_validator):
    """Ambiguous rather than silently ignored: whose duration wins?"""
    write_behaviors(
        """
behaviors:
  - name: clashing
    description: Timing on top of a block.
    keyframes:
      - use: nod_small
        duration: 5.0
"""
    )
    assert "cannot go next to 'use:'" in messages(run_validator())


def test_an_unknown_top_level_key_is_rejected(write_behaviors, run_validator):
    """The validator must be at least as strict as the loader.

    BehaviorFile forbids extra keys, so a stray top-level key makes
    load_library() raise. A green check on a file the player cannot read is
    worse than no check.
    """
    write_behaviors(
        """
description: a stray top-level key
behaviors:
  - name: fine
    description: This behaviour itself is fine.
    keyframes:
      - head: { pitch: 2 }
        duration: 1.0
"""
    )
    assert "does not belong at the top" in messages(run_validator())


def test_anything_the_validator_passes_the_loader_can_read(write_behaviors, behaviors_dir, run_validator):
    """The property the previous test protects, stated directly."""
    from eepy.behaviors.loader import load_library

    write_behaviors(
        """
behaviors:
  - name: ordinary
    description: Nothing unusual.
    keyframes:
      - head: { pitch: 2 }
        duration: 1.0
"""
    )
    assert not run_validator().errors
    load_library(behaviors_dir)  # must not raise


@pytest.mark.parametrize("value", [".nan", ".inf", "-.inf"])
def test_nan_and_infinity_are_rejected(write_behaviors, run_validator, value):
    """abs(nan) > 40 is False, so these slipped through every range check."""
    write_behaviors(
        f"""
behaviors:
  - name: not_a_number
    description: A non-finite angle.
    keyframes:
      - head: {{ pitch: {value} }}
        duration: 1.0
"""
    )
    assert run_validator(check_reach=False).errors, f"{value} must be rejected"


def test_an_out_of_range_pose_gives_one_explanation_not_two(
    write_behaviors, run_validator
):
    """An out-of-range pitch used to also draw a 'cannot reach' error whose
    wording ('each value is inside its own limit') contradicted the first."""
    write_behaviors(
        """
behaviors:
  - name: too_far
    description: Pitch past the limit.
    keyframes:
      - head: { pitch: 70 }
        duration: 1.0
"""
    )
    report = messages(run_validator())
    assert "head pitch" in report
    assert "cannot physically reach" not in report


def test_an_empty_description_reads_plainly(write_behaviors, run_validator):
    write_behaviors(
        """
behaviors:
  - name: blank_description
    description: ""
    keyframes:
      - head: { pitch: 2 }
        duration: 1.0
"""
    )
    report = messages(run_validator())
    assert "is empty" in report
    assert "String should have at least" not in report, "pydantic phrasing leaked"


def test_the_tab_hint_does_not_fire_on_unrelated_messages():
    """'unconstructable' contains 'tab'."""
    from eepy.behaviors.validate import Validator

    hint = Validator._yaml_hint("found unconstructable recursive node")
    assert hint is None or "tab character" not in hint


def test_every_sound_file_is_referenced_by_a_behaviour():
    """sounds/README.md documents which behaviour uses each file, so an unused
    file means that table is lying."""
    from eepy.behaviors.loader import SOUNDS_DIR, iter_sound_references, load_library

    referenced = {sound for _, sound in iter_sound_references(load_library())}
    on_disk = {p.name for p in SOUNDS_DIR.glob("*.wav")}
    assert on_disk - referenced == set(), "unreferenced sound file(s)"
    assert referenced - on_disk == set(), "sound(s) referenced but missing"


def test_a_problem_inside_a_block_names_the_behaviours_keyframe(write_behaviors, run_validator):
    """The keyframe number must be one a designer can find in THEIR file.

    Keyframe 3 here uses a block; a problem in it used to be reported as
    "keyframe 1" -- the block's own first keyframe -- which points nowhere.
    """
    write_behaviors(
        """
behaviors:
  - name: findable
    description: The problem is in keyframe 3, which uses a block.
    keyframes:
      - head: { yaw: 0 }
        duration: 1.0
      - head: { pitch: 33 }
        duration: 1.0
      - use: nod_small
"""
    )
    report = messages(run_validator())
    assert "keyframe 3" in report, report
    assert 'in block "nod_small"' in report
