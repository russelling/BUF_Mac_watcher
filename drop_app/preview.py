"""
preview.py — viewer window for the media loaded into Review Drop.

Opens the dropped files so the artist can check visibility and color BEFORE
sending them down the pipe: is the element actually there, is the plate
blown out, did a display-referred PNG get delivered as if it were linear.

Frames are decoded through the same chain the QT Watcher bakes with
(ACEScg → LogC4 → CDL → show LUT → Rec.709 for scene-linear sources, plain
passthrough for display-referred stills and movies), so what the window
shows is what the delivered QT will look like. Every frame is labelled with
the pipeline and tool that produced it, because the fallbacks — a missing
show LUT, no oiiotool on this Mac — do NOT match the delivery and the
artist has to be able to tell.

Decoding runs on a worker thread; the UI stays live while scrubbing.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

import theme

# ---------------------------------------------------------------------------
# Color pipeline — mirrors qt_bake_oiio.py
# ---------------------------------------------------------------------------
# The values below are only defaults. When the watcher scripts ship next to
# the app (they do in the repo layout) the bake module is imported and its
# own values win, so the preview cannot drift from what actually renders.

SHOW_LUT_PATH = (
    "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots/_globals/LUT/"
    "260629/s3LUT/ARRILogC4_SEV_S3_V3_digital_p1s_R709.cube"
)
SHOTS_ROOT = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots"
OIIOTOOL = "/opt/homebrew/bin/oiiotool"
FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
OCIO_CONFIG = "ocio://studio-config-latest"
OCIO_ACESCG = "ACEScg"
OCIO_LOGC4 = "arri_logc4"
OCIO_REC709_DISPLAY = "Gamma 2.2 Rec.709 - Display"
OCIO_REC709_VIEW = "ACES 2.0 - SDR 100 nits (Rec.709)"

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if _SCRIPTS_DIR.is_dir():
    sys.path.insert(0, str(_SCRIPTS_DIR))
    try:
        import qt_bake_oiio as _bake

        SHOW_LUT_PATH = _bake.SHOW_LUT_PATH
        OIIOTOOL = _bake.OIIOTOOL
        FFMPEG = _bake.FFMPEG
        OCIO_CONFIG = _bake.OCIO_CONFIG
        OCIO_ACESCG = _bake.OCIO_ACESCG
        OCIO_LOGC4 = _bake.OCIO_LOGC4
        OCIO_REC709_DISPLAY = _bake.OCIO_REC709_DISPLAY
        OCIO_REC709_VIEW = _bake.OCIO_REC709_VIEW
    except Exception:
        pass
    finally:
        sys.path.remove(str(_SCRIPTS_DIR))

# Formats Qt decodes itself — no external tool, no temp file.
QT_READABLE_EXTS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".tga",
}
# Scene-linear stills need the show pipeline; everything else is passthrough.
LINEAR_EXTS = {".exr", ".hdr", ".sxr"}

_TOOL_CACHE: dict = {}


@dataclass
class PipeOptions:
    """Which stages of the show color pipe the preview applies."""

    log_convert: bool = True   # ACEScg → LogC4
    cdl: bool = True           # per-shot .cc
    show_lut: bool = True      # show cube → Rec.709

    def cache_key(self):
        return (self.log_convert, self.cdl, self.show_lut)


def _homebrew_prefixes() -> list:
    """Homebrew roots — Apple Silicon, Intel, and brew shellenv if present."""
    prefixes = []
    for key in ("HOMEBREW_PREFIX", "HOMEBREW_REPOSITORY"):
        value = os.environ.get(key)
        if value:
            prefixes.append(value)
    prefixes.extend(["/opt/homebrew", "/usr/local"])
    # De-dupe, keep order.
    seen = set()
    ordered = []
    for prefix in prefixes:
        if prefix and prefix not in seen:
            seen.add(prefix)
            ordered.append(prefix)
    return ordered


def _login_shell_which(name: str) -> Optional[str]:
    """
    Ask the user's login shell where a tool lives.

    Finder / Shotgun Desktop launches strip Homebrew from PATH, so
    shutil.which misses tools that `zsh -lc` would find.
    """
    if sys.platform != "darwin":
        return None
    for shell in ("/bin/zsh", "/bin/bash"):
        if not os.path.isfile(shell):
            continue
        try:
            result = subprocess.run(
                [shell, "-lc", "command -v %s" % name],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            continue
        path = (result.stdout or "").strip().splitlines()
        if result.returncode == 0 and path:
            candidate = path[0].strip()
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def find_tool(configured: str, name: str) -> Optional[str]:
    """Locate a CLI tool across bake paths, PATH, Homebrew, and login shell."""
    if name in _TOOL_CACHE:
        return _TOOL_CACHE[name]

    candidates = []
    if configured:
        candidates.append(configured)
    which = shutil.which(name)
    if which:
        candidates.append(which)

    for prefix in _homebrew_prefixes():
        candidates.append(os.path.join(prefix, "bin", name))
        # Formula-specific kegs used on the Mac Studio bake machine.
        if name == "ffmpeg":
            candidates.extend([
                os.path.join(prefix, "opt", "ffmpeg-full", "bin", "ffmpeg"),
                os.path.join(prefix, "opt", "ffmpeg", "bin", "ffmpeg"),
            ])
        if name == "oiiotool":
            candidates.append(
                os.path.join(prefix, "opt", "openimageio", "bin", "oiiotool")
            )
        if name == "ffprobe":
            candidates.extend([
                os.path.join(prefix, "opt", "ffmpeg-full", "bin", "ffprobe"),
                os.path.join(prefix, "opt", "ffmpeg", "bin", "ffprobe"),
            ])

    candidates.extend([
        "/usr/bin/%s" % name,
        "/bin/%s" % name,
    ])

    found = None
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            found = candidate
            break
    if not found:
        found = _login_shell_which(name)

    _TOOL_CACHE[name] = found
    return found


def tools_search_report() -> str:
    """Short diagnostic for the status line when EXR decode can't start."""
    oiio = find_tool(OIIOTOOL, "oiiotool")
    ffmpeg = find_tool(FFMPEG, "ffmpeg")
    parts = [
        "oiiotool=%s" % (oiio or "NOT FOUND"),
        "ffmpeg=%s" % (ffmpeg or "NOT FOUND"),
    ]
    return "  |  ".join(parts)


def shot_cdl_path(episode: str, sequence: str, shot: str) -> Optional[str]:
    """The per-shot .cc the bake would apply, if it exists on disk."""
    if not (episode and sequence and shot):
        return None
    candidate = os.path.join(
        SHOTS_ROOT, str(episode), str(sequence), str(shot), "plates", "%s.cc" % shot
    )
    return candidate if os.path.exists(candidate) else None


def _run(cmd) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(_first_error_line(result.stderr or result.stdout, cmd[0]))


def _first_error_line(output: str, tool: str) -> str:
    """
    The useful line out of a decoder's noise.

    oiiotool echoes the whole command back after its error, and ffmpeg opens
    with a banner; neither belongs in a message the artist reads.
    """
    for line in (output or "").splitlines():
        line = line.strip()
        if not line or line.startswith(">") or line.startswith("Full command line"):
            continue
        return "%s: %s" % (os.path.basename(tool), line)
    return "%s failed" % os.path.basename(tool)


# ---------------------------------------------------------------------------
# Frame decoding
# ---------------------------------------------------------------------------

class DecodedFrame:
    """
    A decoded preview frame.

    The worker only writes a PNG (or points at a display-referred original).
    QImage / QPixmap are loaded on the GUI thread from display_path — crossing
    threads with QImage/QPixmap is a known black-frame failure on macOS Qt.
    """

    def __init__(
        self,
        pipeline: str,
        tool: str,
        source: Path,
        display_path: Optional[Path] = None,
        image: Optional[QImage] = None,
    ):
        self.pipeline = pipeline
        self.tool = tool
        self.source = Path(source)
        self.display_path = Path(display_path) if display_path else None
        self._image = (
            image.copy() if image is not None and not image.isNull() else None
        )

    @property
    def matches_delivery(self) -> bool:
        return "approx" not in self.pipeline.lower()

    @property
    def image(self) -> QImage:
        if self._image is not None and not self._image.isNull():
            return self._image
        if self.display_path and self.display_path.is_file():
            loaded = QImage(str(self.display_path))
            if not loaded.isNull():
                self._image = loaded
                return self._image
        return QImage()

    def pixmap(self) -> QPixmap:
        if self.display_path and self.display_path.is_file():
            pix = QPixmap()
            if pix.load(str(self.display_path)) and not pix.isNull():
                return pix
        img = self.image
        if img.isNull():
            return QPixmap()
        return QPixmap.fromImage(img)


class FrameSource:
    """A scrubbable series of frames from one dropped delivery."""

    def __init__(self, tmpdir: Path):
        self.tmpdir = tmpdir

    def count(self) -> int:
        return 0

    def frame_label(self, index: int) -> str:
        return ""

    def path(self, index: int) -> Optional[Path]:
        return None

    def decode(self, index: int) -> DecodedFrame:
        raise NotImplementedError

    def unavailable_reason(self) -> str:
        """Empty when the source can be previewed."""
        return ""


class StillSource(FrameSource):
    """Single images and image sequences, linear or display-referred."""

    def __init__(self, files, frame_first, linear, tmpdir, cdl_path=None, pipe=None):
        super().__init__(tmpdir)
        self.files = sorted(files, key=lambda p: p.name)
        self.frame_first = frame_first
        self.linear = linear
        self.cdl_path = cdl_path
        self.pipe = pipe or PipeOptions()

    def count(self):
        return len(self.files)

    def frame_label(self, index):
        frame_no = self.frame_first + index
        if len(self.files) == 1:
            return self.files[0].name
        return "frame %d   (%d of %d)" % (frame_no, index + 1, len(self.files))

    def path(self, index):
        return self.files[index]

    def decode(self, index):
        src = self.files[index]
        ext = src.suffix.lower()
        pipe = self.pipe

        # Display-referred: point at the original; GUI thread loads the pixmap.
        if not self.linear and ext in QT_READABLE_EXTS:
            if not src.is_file() or src.stat().st_size <= 0:
                raise RuntimeError("image file is missing or empty: %s" % src.name)
            return DecodedFrame(
                "as delivered (display-referred)", "Qt", src, src
            )

        # Include pipe state in the temp name so toggling stages can't
        # accidentally reuse a previous bake sitting on disk.
        tag = "%d%d%d" % (
            1 if pipe.log_convert else 0,
            1 if pipe.cdl else 0,
            1 if pipe.show_lut else 0,
        )
        dst = self.tmpdir / ("frame_%s_%04d.png" % (tag, index))
        pipeline, tool = _convert_still(src, dst, self.linear, self.cdl_path, pipe)
        if not dst.is_file() or dst.stat().st_size <= 0:
            raise RuntimeError("decoded frame could not be read back")
        return DecodedFrame(pipeline, tool, src, dst)

    def unavailable_reason(self):
        ext = self.files[0].suffix.lower()
        if not self.linear and ext in QT_READABLE_EXTS:
            return ""
        if find_tool(OIIOTOOL, "oiiotool") or find_tool(FFMPEG, "ffmpeg"):
            return ""
        if sys.platform == "darwin" and os.path.isfile("/usr/bin/qlmanage"):
            # Quick Look can still produce a thumbnail of many EXRs.
            return ""
        return (
            "%s needs oiiotool or ffmpeg to decode, and neither was found on "
            "this Mac (brew install openimageio ffmpeg).  %s"
            % (ext.upper().lstrip("."), tools_search_report())
        )


class MovieSource(FrameSource):
    """Movies, sampled a frame at a time with ffmpeg."""

    def __init__(self, movie_path: Path, tmpdir: Path):
        super().__init__(tmpdir)
        self.movie_path = movie_path
        self.fps = 24.0
        self.duration = 0.0
        self._frames = 1
        self._probe()

    def _probe(self):
        ffprobe = find_tool(
            os.path.join(os.path.dirname(FFMPEG), "ffprobe"), "ffprobe"
        )
        if not ffprobe:
            return
        try:
            result = subprocess.run(
                [
                    ffprobe, "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries",
                    "stream=avg_frame_rate,duration,nb_frames:format=duration",
                    "-of", "default=noprint_wrappers=1",
                    str(self.movie_path),
                ],
                capture_output=True, text=True,
            )
        except Exception:
            return
        values = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                if value not in ("N/A", ""):
                    values.setdefault(key, value)
        rate = values.get("avg_frame_rate", "")
        if "/" in rate:
            num, _, den = rate.partition("/")
            try:
                if float(den):
                    self.fps = float(num) / float(den)
            except ValueError:
                pass
        try:
            self.duration = float(values.get("duration", 0)) or 0.0
        except ValueError:
            self.duration = 0.0
        if values.get("nb_frames", "").isdigit():
            self._frames = max(1, int(values["nb_frames"]))
        elif self.duration and self.fps:
            self._frames = max(1, int(round(self.duration * self.fps)))

    def count(self):
        return self._frames

    def frame_label(self, index):
        if self._frames <= 1:
            return self.movie_path.name
        seconds = index / self.fps if self.fps else 0
        return "frame %d of %d   (%02d:%05.2f)" % (
            index + 1, self._frames, int(seconds // 60), seconds % 60,
        )

    def path(self, index):
        return self.movie_path

    def decode(self, index):
        ffmpeg = find_tool(FFMPEG, "ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg not found")
        dst = self.tmpdir / ("movie_%06d.png" % index)
        seconds = index / self.fps if self.fps else 0
        _run([
            ffmpeg, "-y", "-loglevel", "error",
            "-ss", "%.4f" % seconds, "-i", str(self.movie_path),
            "-frames:v", "1", str(dst),
        ])
        if not dst.is_file() or dst.stat().st_size <= 0:
            raise RuntimeError("ffmpeg produced no frame at %.2fs" % seconds)
        return DecodedFrame(
            "as delivered (no color pipe)", "ffmpeg", self.movie_path, dst
        )

    def unavailable_reason(self):
        if find_tool(FFMPEG, "ffmpeg"):
            return ""
        return (
            "Movies need ffmpeg to preview and it was not found on this Mac "
            "(brew install ffmpeg). Use Open Externally to check it in "
            "QuickTime instead."
        )


class NoPreviewSource(FrameSource):
    """3D geometry and anything else with nothing to show."""

    def __init__(self, files, reason, tmpdir):
        super().__init__(tmpdir)
        self.files = list(files)
        self.reason = reason

    def count(self):
        return 0

    def path(self, index):
        return self.files[0] if self.files else None

    def unavailable_reason(self):
        return self.reason


def source_for_media(
    media: dict,
    tmpdir: Path,
    cdl_path=None,
    pipe: Optional[PipeOptions] = None,
) -> FrameSource:
    """Build the right FrameSource for a classify_paths() result."""
    media_type = (media or {}).get("media_type")
    files = [Path(f) for f in (media or {}).get("files", [])]
    pipe = pipe or PipeOptions()

    if media_type == "movie":
        return MovieSource(Path(media["movie_path"]), tmpdir)
    if media_type in ("exr_single", "exr_sequence", "image_single", "image_sequence"):
        linear = files[0].suffix.lower() in LINEAR_EXTS
        return StillSource(
            files,
            int(media.get("frame_first") or 1),
            linear,
            tmpdir,
            cdl_path if linear else None,
            pipe,
        )
    if media_type == "model_3d":
        return NoPreviewSource(
            files,
            "3D geometry has no image to preview. The ingest watcher builds "
            "the turntable once the files land.",
            tmpdir,
        )
    return NoPreviewSource(files, "Nothing loaded to preview.", tmpdir)


def _convert_still(
    src: Path,
    dst: Path,
    linear: bool,
    cdl_path,
    pipe: Optional[PipeOptions] = None,
) -> tuple:
    """
    Decode any still to an 8-bit PNG; returns (pipeline label, tool).

    oiiotool first because it is the only decoder that reproduces the show
    look, then ffmpeg — which is worth trying even when oiiotool is present,
    since an OCIO config or LUT problem shouldn't leave the artist with no
    picture at all.
    """
    pipe = pipe or PipeOptions()
    errors = []
    for decoder in (_oiiotool_still, _ffmpeg_still, _qlmanage_still):
        try:
            result = decoder(src, dst, linear, cdl_path, pipe)
        except Exception as exc:
            errors.append(str(exc))
            continue
        if result:
            return result
    detail = "; ".join(errors) if errors else tools_search_report()
    raise RuntimeError(
        "no decoder available for %s — brew install openimageio ffmpeg "
        "(%s)" % (src.suffix.upper().lstrip("."), detail)
    )


def _oiiotool_still(src: Path, dst: Path, linear: bool, cdl_path, pipe: PipeOptions):
    oiiotool = find_tool(OIIOTOOL, "oiiotool")
    if not oiiotool:
        return None
    if not linear:
        _run([oiiotool, str(src), "-d", "uint8", "-o", str(dst)])
        return "as delivered (display-referred)", "oiiotool"

    apply_log = pipe.log_convert
    apply_cdl = bool(pipe.cdl and cdl_path and os.path.exists(cdl_path))
    apply_lut = pipe.show_lut and os.path.exists(SHOW_LUT_PATH)
    use_display = pipe.show_lut and not apply_lut

    # Nothing from the show pipe: clamp scene-linear to 8-bit so the frame
    # is at least visible (usually crushed — that's the point of turning it
    # all off to inspect).
    if not apply_log and not apply_cdl and not apply_lut and not use_display:
        _run([
            oiiotool, str(src),
            "--clamp:min=0:max=1", "--ch", "R,G,B", "-d", "uint8", "-o", str(dst),
        ])
        return "raw ACEScg clamp (approx — color pipe off)", "oiiotool"

    cmd = [oiiotool, "--colorconfig", OCIO_CONFIG, str(src)]
    steps = []
    approx = False

    if apply_log:
        cmd += ["--colorconvert", OCIO_ACESCG, OCIO_LOGC4]
        steps.extend(["ACEScg", "LogC4"])
    else:
        steps.append("ACEScg")
        approx = True

    if apply_cdl:
        cmd += ["--ociofiletransform", cdl_path]
        steps.append("CDL")
        if not apply_log:
            approx = True

    if apply_lut:
        cmd += ["--ociofiletransform", SHOW_LUT_PATH]
        steps.append("show LUT")
        if not apply_log:
            approx = True
    elif use_display:
        if apply_log:
            cmd += [
                "--ociodisplay:from=%s" % OCIO_LOGC4,
                OCIO_REC709_DISPLAY,
                OCIO_REC709_VIEW,
            ]
            steps.append("Rec.709 display")
            approx = True
        else:
            # Display transform expects LogC4; without the convert, just clamp.
            steps.append("clamp")
            approx = True
    else:
        # Pipe stages before the LUT only — inspect LogC4 / ACEScg directly.
        steps.append("no LUT")
        approx = True

    cmd += ["--clamp:min=0:max=1", "--ch", "R,G,B", "-d", "uint8", "-o", str(dst)]
    _run(cmd)
    label = " → ".join(steps)
    if apply_lut or (use_display and apply_log):
        if "Rec.709" not in label and apply_lut:
            label = "%s → Rec.709" % label
    if approx:
        label = "%s (approx — not the delivered look)" % label
    return label, "oiiotool"


def _ffmpeg_still(src: Path, dst: Path, linear: bool, cdl_path, pipe: PipeOptions):
    ffmpeg = find_tool(FFMPEG, "ffmpeg")
    if not ffmpeg:
        return None
    cmd = [ffmpeg, "-y", "-loglevel", "error"]
    if linear:
        # The EXR decoder's own transfer curve — nothing like the show look,
        # but it beats showing the artist a black frame.
        cmd += ["-apply_trc", "iec61966_2_1"]
    cmd += ["-i", str(src), "-frames:v", "1", str(dst)]
    _run(cmd)
    return (
        "sRGB approximation (approx — no show pipe, delivery will differ)"
        if linear
        else "as delivered (display-referred)"
    ), "ffmpeg"


def _qlmanage_still(src: Path, dst: Path, linear: bool, cdl_path, pipe: PipeOptions):
    """
    Last-resort macOS thumbnail via Quick Look.

    Artist workstations sometimes lack Homebrew oiiotool/ffmpeg (those live on
    the Mac Studio bake machine). qlmanage can still render many EXRs to PNG.
    """
    del cdl_path, pipe  # Quick Look cannot apply the show color pipe.
    if sys.platform != "darwin":
        return None
    qlmanage = "/usr/bin/qlmanage"
    if not os.path.isfile(qlmanage):
        return None

    out_dir = dst.parent / ("ql_" + dst.stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    _run([qlmanage, "-t", "-s", "2048", "-o", str(out_dir), str(src)])

    # qlmanage writes <filename>.png next to the original basename.
    produced = None
    for candidate in (
        out_dir / (src.name + ".png"),
        out_dir / (src.stem + ".png"),
    ):
        if candidate.is_file() and candidate.stat().st_size > 0:
            produced = candidate
            break
    if produced is None:
        pngs = sorted(out_dir.glob("*.png"))
        produced = pngs[0] if pngs else None
    if produced is None:
        raise RuntimeError("qlmanage produced no thumbnail for %s" % src.name)

    shutil.copy2(produced, dst)
    label = (
        "Quick Look thumbnail (approx — not the delivered look)"
        if linear
        else "Quick Look thumbnail (approx)"
    )
    return label, "qlmanage"


# ---------------------------------------------------------------------------
# Worker plumbing
# ---------------------------------------------------------------------------

class _DecodeSignals(QObject):
    done = Signal(int, int, object, str)


class _DecodeTask(QRunnable):
    def __init__(self, source, index, request_id, signals):
        super().__init__()
        self.source = source
        self.index = index
        self.request_id = request_id
        self.signals = signals

    def run(self):
        try:
            frame = self.source.decode(self.index)
            self.signals.done.emit(self.request_id, self.index, frame, "")
        except Exception as exc:
            self.signals.done.emit(self.request_id, self.index, None, str(exc))


# ---------------------------------------------------------------------------
# Viewer widgets
# ---------------------------------------------------------------------------

CHANNELS = ["RGB", "Red", "Green", "Blue", "Alpha", "Luma"]


def _tone_table(exposure_ev: float, gamma: float) -> bytes:
    """
    256-byte map applying viewer exposure then gamma to display-space bytes.

    Exposure is applied in approximate linear light (undo 2.2, gain, redo)
    so stops behave like stops rather than like a brightness slider.
    """
    gain = 2.0 ** exposure_ev
    inv_gamma = 1.0 / max(gamma, 0.01)
    values = bytearray(256)
    for i in range(256):
        linear = ((i / 255.0) ** 2.2) * gain
        display = linear ** (1.0 / 2.2)
        values[i] = max(0, min(255, int(round(255.0 * (display ** inv_gamma)))))
    return bytes(values)


def _checkerboard(size: QSize) -> QPixmap:
    """Backdrop so transparent areas read as transparent, not as black."""
    tile = QPixmap(32, 32)
    tile.fill(QColor("#3a3a3a"))
    painter = QPainter(tile)
    painter.fillRect(0, 0, 16, 16, QColor("#2c2c2c"))
    painter.fillRect(16, 16, 16, 16, QColor("#2c2c2c"))
    painter.end()
    board = QPixmap(size)
    painter = QPainter(board)
    painter.drawTiledPixmap(0, 0, size.width(), size.height(), tile)
    painter.end()
    return board


def _isolate_channel(image: QImage, channel: str) -> QImage:
    """Grayscale view of one channel, so mattes and alpha can be checked."""
    if channel == "Luma":
        return image.convertToFormat(QImage.Format_Grayscale8)
    has_alpha = image.hasAlphaChannel()
    fmt = QImage.Format_RGBA8888 if has_alpha else QImage.Format_RGB888
    src = image.convertToFormat(fmt)
    step = 4 if has_alpha else 3
    offset = {"Red": 0, "Green": 1, "Blue": 2, "Alpha": 3}[channel]
    if offset >= step:
        # No alpha in the source: a fully opaque frame is the honest answer.
        opaque = QImage(src.size(), QImage.Format_Grayscale8)
        opaque.fill(255)
        return opaque

    width, height = src.width(), src.height()
    stride = src.bytesPerLine()
    data = bytes(src.constBits())
    rows = bytearray()
    for y in range(height):
        row = data[y * stride: y * stride + width * step]
        rows += row[offset::step]
    gray = QImage(bytes(rows), width, height, width, QImage.Format_Grayscale8)
    return gray.copy()


class ImageCanvas(QGraphicsView):
    """
    Frame viewer via QGraphicsView — avoids QLabel/stylesheet black frames.

    PreviewWindow must not apply APP_CSS (it styles every QLabel/QWidget and
    blanks pixmaps on Shotgun Desktop's macOS Qt). This view uses a brush
    background and a scene pixmap item only.
    """

    probed = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PreviewCanvas")
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFrameShape(QFrame.NoFrame)
        self.setBackgroundBrush(QBrush(QColor(theme.MIDNIGHT)))
        self.setStyleSheet(
            "QGraphicsView#PreviewCanvas { border: none; background: %s; }"
            % theme.MIDNIGHT
        )
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = None  # type: Optional[QGraphicsPixmapItem]
        self._pixmap = None  # type: Optional[QPixmap]
        self._fit = True
        self._zoom = 1.0

    def set_frame(self, pixmap: Optional[QPixmap]):
        self._pixmap = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._scene.clear()
        self._item = None
        if self._pixmap is None:
            self.resetTransform()
            return
        self._item = self._scene.addPixmap(self._pixmap)
        self._item.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)
        self._scene.setSceneRect(self._item.boundingRect())
        self._apply_view()

    def set_fit(self, fit: bool):
        self._fit = fit
        if fit:
            self._zoom = 1.0
        self._apply_view()

    def set_zoom(self, zoom: float):
        self._zoom = max(0.05, min(8.0, zoom))
        self._fit = False
        self._apply_view()

    def zoom(self) -> float:
        return self._zoom

    def is_fit(self) -> bool:
        return self._fit

    def has_frame(self) -> bool:
        return self._pixmap is not None and not self._pixmap.isNull()

    def display_image(self) -> Optional[QImage]:
        if not self.has_frame():
            return None
        # Tests sample viewer-adjusted pixels; return the source pixmap as an
        # image (tone/channel already baked into what set_frame received).
        return self._pixmap.toImage()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self._apply_view()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_view()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.probed.emit(-1, -1)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self._item is None or self._pixmap is None:
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        x = int(scene_pos.x())
        y = int(scene_pos.y())
        if not (0 <= x < self._pixmap.width() and 0 <= y < self._pixmap.height()):
            self.probed.emit(-1, -1)
            return
        self.probed.emit(x, y)

    def _apply_view(self):
        if self._item is None:
            return
        if self._fit:
            self.resetTransform()
            self.fitInView(self._item, Qt.KeepAspectRatio)
        else:
            self.resetTransform()
            self.scale(self._zoom, self._zoom)


# ---------------------------------------------------------------------------
# Preview window
# ---------------------------------------------------------------------------

LABEL_CSS = theme.LABEL_CSS
VALUE_CSS = theme.VALUE_CSS


class PreviewWindow(QDialog):
    """Non-modal viewer for whatever is currently loaded in the drop app."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.Window, True)
        self.setWindowTitle("Buffalo Review Drop — Preview")
        self.resize(QSize(1040, 760))
        # Do NOT apply theme.APP_CSS here. It styles every QLabel/QWidget and
        # blanks image painting on Shotgun Desktop's macOS Qt build. Palette +
        # per-control stylesheets only.
        self.setStyleSheet(theme.PREVIEW_DIALOG_CSS)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(theme.WHITE))
        pal.setColor(self.foregroundRole(), QColor(theme.INK))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        self._tmpdir = Path(tempfile.mkdtemp(prefix="review_drop_preview_"))
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(1)
        self._signals = _DecodeSignals()
        self._signals.done.connect(self._on_decoded)
        self._request_id = 0
        self._cache = {}
        self._source = None
        self._frame = None
        self._adjusted_buffer = None
        self._media = None
        self._cdl_path = None
        self._pipe = PipeOptions()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self.lbl_source = QLabel("Nothing loaded.")
        self.lbl_source.setWordWrap(True)
        self.lbl_source.setStyleSheet(VALUE_CSS)
        layout.addWidget(self.lbl_source)

        self.lbl_pipeline = QLabel("")
        self.lbl_pipeline.setWordWrap(True)
        self.lbl_pipeline.setStyleSheet(LABEL_CSS)
        layout.addWidget(self.lbl_pipeline)

        self.canvas = ImageCanvas()
        self.canvas.probed.connect(self._on_probe)
        layout.addWidget(self.canvas, 1)

        layout.addLayout(self._build_pipe_row())
        layout.addLayout(self._build_frame_row())
        layout.addLayout(self._build_view_row())

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet(LABEL_CSS)
        layout.addWidget(self.lbl_status)

    # -- construction helpers ------------------------------------------------

    def _build_pipe_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        title = QLabel("Color pipe")
        title.setStyleSheet(LABEL_CSS)
        row.addWidget(title)

        self.chk_log = QCheckBox("ACEScg → LogC4")
        self.chk_log.setChecked(True)
        self.chk_log.setToolTip("Scene-linear to LogC4 convert (first stage of the bake).")
        self.chk_cdl = QCheckBox("CDL")
        self.chk_cdl.setChecked(True)
        self.chk_cdl.setToolTip("Per-shot .cc from plates/, when one exists.")
        self.chk_lut = QCheckBox("Show LUT")
        self.chk_lut.setChecked(True)
        self.chk_lut.setToolTip("Show cube → Rec.709. Off leaves LogC4 / ACEScg for inspection.")

        for box in (self.chk_log, self.chk_cdl, self.chk_lut):
            box.toggled.connect(self._on_pipe_toggled)
            row.addWidget(box)

        row.addStretch()
        self.lbl_pipe_hint = QLabel("")
        self.lbl_pipe_hint.setStyleSheet(LABEL_CSS)
        row.addWidget(self.lbl_pipe_hint)
        return row

    def _build_frame_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedWidth(36)
        self.btn_prev.clicked.connect(lambda: self._step(-1))
        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedWidth(36)
        self.btn_next.clicked.connect(lambda: self._step(1))
        self.sld_frame = QSlider(Qt.Horizontal)
        self.sld_frame.setMinimum(0)
        self.sld_frame.setMaximum(0)
        self.sld_frame.valueChanged.connect(self._show_frame)
        self.lbl_frame = QLabel("")
        self.lbl_frame.setMinimumWidth(210)
        self.lbl_frame.setStyleSheet(LABEL_CSS)
        row.addWidget(self.btn_prev)
        row.addWidget(self.btn_next)
        row.addWidget(self.sld_frame, 1)
        row.addWidget(self.lbl_frame)
        for button in (self.btn_prev, self.btn_next):
            button.setStyleSheet(theme.GHOST_BUTTON_CSS)
        return row

    def _build_view_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        self.cmb_channel = QComboBox()
        self.cmb_channel.addItems(CHANNELS)
        self.cmb_channel.currentIndexChanged.connect(self._redraw)
        row.addWidget(QLabel("Channel"))
        row.addWidget(self.cmb_channel)

        self.sld_exposure = QSlider(Qt.Horizontal)
        self.sld_exposure.setRange(-50, 50)
        self.sld_exposure.setValue(0)
        self.sld_exposure.setFixedWidth(120)
        self.sld_exposure.valueChanged.connect(self._redraw)
        self.lbl_exposure = QLabel("0.0 EV")
        self.lbl_exposure.setStyleSheet(LABEL_CSS)
        self.lbl_exposure.setFixedWidth(56)
        row.addWidget(QLabel("Exposure"))
        row.addWidget(self.sld_exposure)
        row.addWidget(self.lbl_exposure)

        self.sld_gamma = QSlider(Qt.Horizontal)
        self.sld_gamma.setRange(20, 300)
        self.sld_gamma.setValue(100)
        self.sld_gamma.setFixedWidth(120)
        self.sld_gamma.valueChanged.connect(self._redraw)
        self.lbl_gamma = QLabel("1.00")
        self.lbl_gamma.setStyleSheet(LABEL_CSS)
        self.lbl_gamma.setFixedWidth(40)
        row.addWidget(QLabel("Gamma"))
        row.addWidget(self.sld_gamma)
        row.addWidget(self.lbl_gamma)

        btn_reset = QPushButton("Reset")
        btn_reset.clicked.connect(self._reset_view)
        row.addWidget(btn_reset)

        self.btn_fit = QPushButton("Fit")
        self.btn_fit.setCheckable(True)
        self.btn_fit.setChecked(True)
        self.btn_fit.toggled.connect(self._on_fit_toggled)
        row.addWidget(self.btn_fit)

        btn_zoom_out = QPushButton("−")
        btn_zoom_out.setFixedWidth(32)
        btn_zoom_out.clicked.connect(lambda: self._zoom_by(1 / 1.25))
        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setFixedWidth(32)
        btn_zoom_in.clicked.connect(lambda: self._zoom_by(1.25))
        row.addWidget(btn_zoom_out)
        row.addWidget(btn_zoom_in)

        row.addStretch()
        self.btn_external = QPushButton("Open Externally")
        self.btn_external.clicked.connect(self._open_externally)
        row.addWidget(self.btn_external)

        for button in (btn_reset, self.btn_fit, btn_zoom_out, btn_zoom_in):
            button.setStyleSheet(theme.GHOST_BUTTON_CSS)
        self.btn_external.setStyleSheet(theme.SECONDARY_BUTTON_CSS)
        return row

    # -- public API ----------------------------------------------------------

    def set_media(self, media: dict, cdl_path: Optional[str] = None):
        """Point the viewer at a fresh classify_paths() result."""
        self._media = media
        self._cdl_path = cdl_path
        self._cache.clear()
        self._frame = None
        self.canvas.set_frame(None)
        self._request_id += 1
        self._pipe = self._pipe_from_ui()
        self._source = source_for_media(media, self._tmpdir, cdl_path, self._pipe)
        self._sync_pipe_controls()

        count = self._source.count()
        self.sld_frame.blockSignals(True)
        self.sld_frame.setMaximum(max(0, count - 1))
        self.sld_frame.setValue(0)
        self.sld_frame.blockSignals(False)
        has_frames = count > 1
        self.sld_frame.setEnabled(has_frames)
        self.btn_prev.setEnabled(has_frames)
        self.btn_next.setEnabled(has_frames)

        path = self._source.path(0)
        self.lbl_source.setText(str(path) if path else "Nothing loaded.")
        self.btn_external.setEnabled(bool(path))

        reason = self._source.unavailable_reason()
        if reason or not count:
            self.lbl_pipeline.setText("")
            self.lbl_frame.setText("")
            self.lbl_status.setText(reason or "Nothing to preview.")
            return
        self._show_frame(0)

    def _pipe_from_ui(self) -> PipeOptions:
        return PipeOptions(
            log_convert=self.chk_log.isChecked(),
            cdl=self.chk_cdl.isChecked(),
            show_lut=self.chk_lut.isChecked(),
        )

    def _sync_pipe_controls(self):
        """Enable pipe toggles only for scene-linear stills."""
        linear = isinstance(self._source, StillSource) and self._source.linear
        has_cdl = bool(
            linear and self._cdl_path and os.path.exists(self._cdl_path)
        )
        for box in (self.chk_log, self.chk_lut):
            box.setEnabled(linear)
        self.chk_cdl.setEnabled(linear and has_cdl)
        if not linear:
            self.lbl_pipe_hint.setText("Pipe applies to EXR / HDR only.")
        elif not has_cdl:
            self.lbl_pipe_hint.setText("No shot CDL on disk.")
        else:
            self.lbl_pipe_hint.setText(os.path.basename(self._cdl_path))

    def _on_pipe_toggled(self, *_args):
        if not isinstance(self._source, StillSource) or not self._source.linear:
            return
        self._pipe = self._pipe_from_ui()
        self._source.pipe = self._pipe
        self._cache.clear()
        self._frame = None
        index = self.sld_frame.value()
        self._show_frame(index)

    # -- frame handling ------------------------------------------------------

    def _step(self, delta: int):
        self.sld_frame.setValue(
            max(0, min(self.sld_frame.maximum(), self.sld_frame.value() + delta))
        )

    def _show_frame(self, index: int):
        if not self._source or index >= self._source.count():
            return
        self.lbl_frame.setText(self._source.frame_label(index))
        cached = self._cache.get(index)
        if cached is not None:
            self._frame = cached
            self._apply_frame()
            return
        self._request_id += 1
        self.lbl_status.setText("Decoding %s…" % self._source.frame_label(index))
        self._pool.start(
            _DecodeTask(self._source, index, self._request_id, self._signals)
        )

    def _on_decoded(self, request_id, index, frame, error):
        if request_id != self._request_id:
            return
        if frame is None:
            self.lbl_status.setText("Could not decode this frame: %s" % error)
            self.canvas.set_frame(None)
            return
        # Load pixels on the GUI thread only.
        if frame.pixmap().isNull():
            self.lbl_status.setText(
                "Decoded, but the image could not be displayed "
                "(path: %s)." % (frame.display_path or frame.source)
            )
            self.canvas.set_frame(None)
            return
        # Sequences can be thousands of frames; keep the scrub-back window
        # warm without holding every decoded frame in memory.
        if len(self._cache) > 48:
            self._cache.clear()
        self._cache[index] = frame
        self._frame = frame
        self._apply_frame()

    def _apply_frame(self):
        frame = self._frame
        if frame is None:
            return
        image = frame.image
        self.lbl_pipeline.setText(
            "%s   ·   %d × %d   ·   %s   ·   decoded by %s%s"
            % (
                frame.pipeline,
                image.width(),
                image.height(),
                frame.source.suffix.upper().lstrip("."),
                frame.tool,
                "" if frame.matches_delivery else "   ·   NOT the delivered look",
            )
        )
        self.lbl_status.setText("")
        self._redraw()

    # -- display adjustments -------------------------------------------------

    def _exposure(self) -> float:
        return self.sld_exposure.value() / 10.0

    def _gamma(self) -> float:
        return self.sld_gamma.value() / 100.0

    def _reset_view(self):
        self.sld_exposure.setValue(0)
        self.sld_gamma.setValue(100)
        self.cmb_channel.setCurrentIndex(0)
        self.btn_fit.setChecked(True)

    def _on_fit_toggled(self, checked: bool):
        self.canvas.set_fit(checked)

    def _zoom_by(self, factor: float):
        self.btn_fit.setChecked(False)
        self.canvas.set_zoom(self.canvas.zoom() * factor)

    def _redraw(self):
        self.lbl_exposure.setText("%+.1f EV" % self._exposure())
        self.lbl_gamma.setText("%.2f" % self._gamma())
        if self._frame is None:
            return

        channel = self.cmb_channel.currentText()
        exposure, gamma = self._exposure(), self._gamma()
        # Always load the source pixmap on this (GUI) thread first.
        pix = self._frame.pixmap()
        if pix.isNull():
            self.canvas.set_frame(None)
            self.lbl_status.setText("Could not load decoded frame for display.")
            return

        identity = (
            channel == "RGB"
            and abs(exposure) < 1e-6
            and abs(gamma - 1.0) < 1e-6
            and not pix.hasAlpha()
        )
        if identity:
            self.canvas.set_frame(pix)
            return

        image = pix.toImage()
        if channel != "RGB":
            image = _isolate_channel(image, channel)
        elif image.hasAlphaChannel():
            board = _checkerboard(image.size())
            painter = QPainter(board)
            painter.drawImage(0, 0, image)
            painter.end()
            image = board.toImage()

        image = self._apply_tone(image)
        self.canvas.set_frame(QPixmap.fromImage(image))

    def _apply_tone(self, image: QImage) -> QImage:
        """Exposure/gamma via a byte table — full-res, but at C speed."""
        exposure, gamma = self._exposure(), self._gamma()
        if abs(exposure) < 1e-6 and abs(gamma - 1.0) < 1e-6:
            return image
        gray = image.format() == QImage.Format_Grayscale8
        flat = image if gray else image.convertToFormat(QImage.Format_RGB888)
        # Padding bytes get mapped too; they are never drawn.
        self._adjusted_buffer = bytes(flat.constBits()).translate(
            _tone_table(exposure, gamma)
        )
        return QImage(
            self._adjusted_buffer,
            flat.width(),
            flat.height(),
            flat.bytesPerLine(),
            flat.format(),
        )

    def _on_probe(self, x: int, y: int):
        if self._frame is None or x < 0:
            self.lbl_status.setText("")
            return
        color = self._frame.image.pixelColor(x, y)
        self.lbl_status.setText(
            "x %d  y %d      R %d  G %d  B %d  A %d      %s"
            % (x, y, color.red(), color.green(), color.blue(), color.alpha(),
               color.name().upper())
        )

    def _open_externally(self):
        path = self._source.path(self.sld_frame.value()) if self._source else None
        if not path:
            return
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            subprocess.Popen([opener, str(path)])
        except Exception as exc:
            self.lbl_status.setText("Could not open externally: %s" % exc)

    # -- teardown ------------------------------------------------------------

    def closeEvent(self, event):
        self._request_id += 1
        self._pool.waitForDone(2000)
        self._cache.clear()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._tmpdir.mkdir(parents=True, exist_ok=True)
        super().closeEvent(event)
