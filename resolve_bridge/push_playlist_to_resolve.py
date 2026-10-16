#!/usr/bin/env python3
"""
push_playlist_to_resolve.py — take a ShotGrid Playlist and build a graded
DaVinci Resolve timeline from it.

    python3 -m resolve_bridge.push_playlist_to_resolve <playlist id or exact code> \\
        [--resolve-project NAME] [--timeline-name NAME] [--dry-run]

(run as a module, from the repository root, so the `resolve_bridge` package
import resolves — see resolve_bridge/README.md)

For each Version in the Playlist, in Playlist order:
  1. Resolve the highest-resolution media on disk (media_resolver.py) —
     camera-original plates before any review encode.
  2. Resolve the color pipeline (color_plan.py): camera ACES IDT, per-shot
     CDL, Show LUT (per-shot with global fallback).
  3. Import into a Resolve Media Pool bin named after the Playlist, append
     to a new timeline in Playlist order, and apply the color plan to the
     resulting timeline clip.

--dry-run resolves and prints everything (media choice, color plan, every
warning) WITHOUT connecting to Resolve or touching ShotGrid write APIs —
use it to sanity-check a Playlist before spending time on a real push. See
resolve_bridge/README.md for one-time setup (ShotGrid credentials, Resolve
scripting environment variables, ACES IDT string verification, optional
PowerGrade template).
"""

from __future__ import annotations

import argparse
import sys

from resolve_bridge import config, media_resolver, resolve_api, sg_playlist
from resolve_bridge.color_plan import ShotContext, resolve_color_plan


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("playlist", help="ShotGrid Playlist id or exact code")
    parser.add_argument(
        "--resolve-project", default=None,
        help="DaVinci Resolve project to open/create (default: same as the Playlist code)",
    )
    parser.add_argument(
        "--timeline-name", default=None,
        help="Timeline name to create (default: same as the Playlist code)",
    )
    parser.add_argument(
        "--powergrade", default=config.POWERGRADE_TEMPLATE_PATH,
        help="Name of a 2-node PowerGrade to apply before CDL/LUT (see config.py)",
    )
    parser.add_argument(
        "--bake-combined-lut-fallback", action="store_true",
        default=config.BAKE_COMBINED_LUT_FALLBACK,
        help="Bake CDL+LUT into one cube when only 1 grading node is available (EXPERIMENTAL, see lut_bake.py)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve media + color plan and print the plan; do not touch Resolve",
    )
    return parser.parse_args(argv)


def run(args) -> int:
    sg = sg_playlist.connect()
    entries = sg_playlist.fetch_playlist(sg, args.playlist)
    if not entries:
        print("Nothing to push: playlist has no versions.")
        return 1

    playlist_label = args.resolve_project or str(args.playlist)
    resolved = []
    had_error = False
    for entry in entries:
        try:
            candidate = media_resolver.find_highest_res_media(entry)
        except media_resolver.NoMediaFoundError as exc:
            print("ERROR: %s" % exc)
            had_error = True
            continue

        shot_context = ShotContext(
            shot_code=entry.shot_code or entry.version_code,
            episode=entry.episode,
            sequence=entry.sequence,
            camera_field=entry.camera_field,
            plate_path=candidate.representative_frame,
        )
        try:
            plan = resolve_color_plan(shot_context)
        except Exception as exc:
            print("ERROR: %s: could not resolve color plan: %s" % (shot_context.shot_code, exc))
            had_error = True
            continue

        resolved.append((entry, candidate, plan))

    if args.dry_run:
        for entry, candidate, plan in resolved:
            print("-" * 72)
            print("Shot: %s (Version %s)" % (shot_code_of(entry), entry.version_code))
            print("Media: %s (%s) %sx%s" % (
                candidate.path, candidate.kind, candidate.width, candidate.height,
            ))
            print("Camera family: %s -> ACES IDT '%s'" % (plan.camera_family, plan.aces_idt))
            print("CDL: %s" % (plan.cdl_path or "none"))
            print("Show LUT: %s" % (plan.lut_path or "none"))
            for warning in plan.warnings:
                print("  warning: %s" % warning)
        return 1 if had_error else 0

    if not resolved:
        print("Nothing resolved successfully; aborting before touching Resolve.")
        return 1

    resolve = resolve_api.connect_resolve()
    project = resolve_api.ensure_project(resolve, args.resolve_project or playlist_label)
    resolve_api.configure_aces_color_management(project)
    media_pool = project.GetMediaPool()
    resolve_api.ensure_bin(media_pool, args.timeline_name or playlist_label)

    media_pool_items = []
    for entry, candidate, plan in resolved:
        item = resolve_api.import_media(media_pool, candidate.path)
        media_pool_items.append((entry, item, plan))

    timeline, appended = resolve_api.build_playlist_timeline(
        project, media_pool,
        args.timeline_name or playlist_label,
        [item for _, item, _ in media_pool_items],
    )

    for (entry, item, plan), timeline_item in zip(media_pool_items, appended):
        result = resolve_api.apply_color_plan(
            timeline_item, item, plan,
            powergrade_name=args.powergrade,
            bake_combined_lut_fallback=args.bake_combined_lut_fallback,
        )
        print(
            "%s: %s (input_color_space_ok=%s cdl_ok=%s lut_ok=%s)"
            % (
                plan.shot_code, result.strategy, result.input_color_space_ok,
                result.cdl_ok, result.lut_ok,
            )
        )

    return 1 if had_error else 0


def shot_code_of(entry) -> str:
    return entry.shot_code or entry.version_code


def main(argv=None) -> int:
    args = parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
