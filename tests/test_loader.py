"""Tests for loading and resolving behaviour files."""

from __future__ import annotations

import pytest

from eepy.behaviors.loader import (
    BehaviorFileError,
    load_behaviors,
    load_library,
    read_yaml,
)

ONE = """
behaviors:
  - name: simple
    description: A single keyframe.
    keyframes:
      - head: { pitch: 10 }
        duration: 1.0
"""


def test_loads_a_behaviour(write_behaviors, behaviors_dir):
    write_behaviors(ONE)
    library = load_library(behaviors_dir)
    assert set(library.behaviors) == {"simple"}
    assert library.behaviors["simple"].keyframes[0].head.pitch == 10


def test_duplicate_yaml_key_is_an_error(write_behaviors, behaviors_dir):
    path = write_behaviors(
        """
behaviors:
  - name: twice
    description: first
    description: second
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
"""
    )
    with pytest.raises(BehaviorFileError) as excinfo:
        read_yaml(path)
    assert "appears twice" in excinfo.value.message


def test_empty_file_is_an_error(write_behaviors, behaviors_dir):
    path = write_behaviors("")
    with pytest.raises(BehaviorFileError):
        read_yaml(path)


def test_blocks_are_expanded_into_steps(write_behaviors, behaviors_dir):
    write_behaviors(
        """
behaviors:
  - name: uses_block
    description: Reuses a two-keyframe block.
    keyframes:
      - use: nod_small
"""
    )
    library = load_library(behaviors_dir)
    steps = library.resolve(library.behaviors["uses_block"])
    # nod_small has two keyframes, so one 'use:' becomes two steps.
    assert len(steps) == 2
    assert "nod_small" in steps[0].source


def test_omitted_values_inherit_from_the_previous_keyframe(
    write_behaviors, behaviors_dir
):
    write_behaviors(
        """
behaviors:
  - name: inherits
    description: Second keyframe only sets pitch.
    keyframes:
      - head: { yaw: 20, pitch: 0 }
        antennas: { right: -40, left: 40 }
        duration: 1.0
      - head: { pitch: 10 }
        duration: 1.0
"""
    )
    library = load_library(behaviors_dir)
    steps = library.resolve(library.behaviors["inherits"])
    assert steps[1].head_pitch == 10
    assert steps[1].head_yaw == 20          # carried over
    assert steps[1].antenna_right == -40    # carried over


def test_relative_offsets_add_to_the_previous_pose(write_behaviors, behaviors_dir):
    """A nod must come back to where it started.

    nod_small is relative, "+8 then -8". Read as offsets from the PREVIOUS
    pose, that goes down 8 and returns -- which is what a nod is. Read as
    offsets from a fixed start pose it would end 8 degrees above where it
    began, which is what this used to do.
    """
    write_behaviors(
        """
behaviors:
  - name: rel
    description: A relative nod from a tilted start.
    keyframes:
      - head: { pitch: 10 }
        duration: 1.0
      - use: nod_small
"""
    )
    library = load_library(behaviors_dir)
    steps = library.resolve(library.behaviors["rel"])
    assert steps[1].head_pitch == pytest.approx(18)   # 10 + 8
    assert steps[2].head_pitch == pytest.approx(10)   # back to the start


def test_relative_offsets_accumulate(write_behaviors, behaviors_dir):
    """Three offsets in a row compose, rather than all measuring from 0."""
    write_behaviors(
        """
behaviors:
  - name: stacked
    description: Three relative offsets in a row.
    keyframes:
      - head: { pitch: 10 }
        relative: true
        duration: 1.0
      - head: { pitch: 10 }
        relative: true
        duration: 1.0
      - head: { pitch: -20 }
        relative: true
        duration: 1.0
"""
    )
    library = load_library(behaviors_dir)
    steps = library.resolve(library.behaviors["stacked"])
    assert [s.head_pitch for s in steps] == pytest.approx([10, 20, 0])


def test_breathing_returns_to_where_it_started(behaviors_dir):
    """The real breathe_in/breathe_out pair must form a closed cycle."""
    library = load_library()
    steps = library.resolve(library.behaviors["study_buddy_idle"])
    # Breathe in, breathe out -- and nothing else: no glance down at the notes,
    # which took the face out of the robot camera's picture.
    assert len(steps) == 2
    assert steps[1].head_z == pytest.approx(0.0, abs=1e-9)
    assert steps[1].head_pitch == pytest.approx(0.0, abs=1e-9)


def test_intensity_scales_angles_toward_neutral(write_behaviors, behaviors_dir):
    write_behaviors(
        """
behaviors:
  - name: half
    description: Intensity 0.5.
    intensity: 0.5
    keyframes:
      - head: { pitch: 20, yaw: 30 }
        duration: 1.0
"""
    )
    library = load_library(behaviors_dir)
    step = library.resolve(library.behaviors["half"])[0]
    assert step.head_pitch == pytest.approx(10)
    assert step.head_yaw == pytest.approx(15)


def test_intensity_scales_antennas_toward_their_neutral_offset(
    write_behaviors, behaviors_dir
):
    """Antennas rest at -10/+10, not 0, so they must scale toward that."""
    write_behaviors(
        """
behaviors:
  - name: ant
    description: Antennas at half intensity.
    intensity: 0.5
    keyframes:
      - antennas: { right: -110, left: 110 }
        duration: 1.0
"""
    )
    library = load_library(behaviors_dir)
    step = library.resolve(library.behaviors["ant"])[0]
    # halfway between -10 (neutral) and -110
    assert step.antenna_right == pytest.approx(-60)
    assert step.antenna_left == pytest.approx(60)


def test_look_at_resolves_to_the_named_target(write_behaviors, behaviors_dir):
    write_behaviors(
        """
behaviors:
  - name: gazing
    description: Looks at the notes.
    keyframes:
      - look_at: notes
        duration: 1.0
"""
    )
    library = load_library(behaviors_dir)
    step = library.resolve(library.behaviors["gazing"])[0]
    assert step.head_pitch == 20


def test_timeline_clock_accumulates_duration_and_hold(write_behaviors, behaviors_dir):
    write_behaviors(
        """
behaviors:
  - name: timed
    description: Two keyframes with holds.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
        hold: 0.5
      - head: { pitch: 2 }
        duration: 2.0
"""
    )
    library = load_library(behaviors_dir)
    steps = library.resolve(library.behaviors["timed"])
    assert steps[0].start_s == 0.0
    assert steps[0].end_s == pytest.approx(1.5)
    assert steps[1].start_s == pytest.approx(1.5)
    assert steps[1].end_s == pytest.approx(3.5)


def test_get_behavior_finds_a_ladder_rung(write_behaviors, behaviors_dir):
    write_behaviors(
        """
behaviors:
  - name: rung_zero
    ladder: test
    level: 0
    description: Bottom rung.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
  - name: rung_one
    ladder: test
    level: 1
    description: Next rung.
    keyframes:
      - head: { pitch: 2 }
        duration: 1.0
"""
    )
    library = load_library(behaviors_dir)
    assert library.get_behavior("test", 1).name == "rung_one"
    assert library.ladders() == {"test": [0, 1]}


def test_get_behavior_explains_a_missing_rung(write_behaviors, behaviors_dir):
    write_behaviors(
        """
behaviors:
  - name: only_zero
    ladder: test
    level: 0
    description: Bottom rung.
    keyframes:
      - head: { pitch: 1 }
        duration: 1.0
"""
    )
    library = load_library(behaviors_dir)
    with pytest.raises(KeyError, match="has no level 4"):
        library.get_behavior("test", 4)
    with pytest.raises(KeyError, match="no ladder called"):
        library.get_behavior("nope", 0)


def test_load_behaviors_shorthand(write_behaviors, behaviors_dir):
    write_behaviors(ONE)
    assert set(load_behaviors(behaviors_dir)) == {"simple"}


# --- the repo's own files -------------------------------------------------


def test_the_repos_real_files_load():
    """The behaviours actually shipped in this repo must load and resolve."""
    library = load_library()
    assert library.behaviors, "behaviors/ should not be empty"
    for name, behavior in library.behaviors.items():
        steps = library.resolve(behavior)
        assert steps, f"{name} resolved to nothing"


def test_the_repos_ladders_are_complete():
    ladders = load_library().ladders()
    assert ladders["refocus"] == [0, 1, 2, 3, 4]
    assert ladders["bedtime"] == [0, 1, 2, 3, 4, 5]
