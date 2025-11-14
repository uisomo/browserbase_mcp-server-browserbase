"""Utility for generating .lrc lyric files using Whisper-based ASR.

This script is intentionally designed to be copy-paste-ready for local use.
Run it from the command line after installing either ``whisper`` or
``faster-whisper``.  It supports Japanese (or any other language that Whisper
understands) and can align timestamps against a supplied lyrics text file.

Example
-------
>>> python scripts/whisper_lrc_generator.py \
...     --audio song.wav \
...     --lyrics lyrics.txt \
...     --language ja \
...     --model medium \
...     --level line

The output ``song.lrc`` can be imported into most karaoke / lyric applications.

Why another script?
-------------------
The default Whisper transcript contains timestamps that correspond to model
segments.  Those do not necessarily align with the lyric lines that you have in
your text file.  This utility performs a lightweight alignment that tries to
pair each lyric line with the most likely segment start time.  It also offers an
optional "word" (karaoke-style) mode that can either use the model's word
timestamps (when available) or distribute the line duration evenly across every
character of the provided lyric.

Limitations
-----------
* The alignment is heuristic.  For challenging material, you may wish to adjust
  the lyrics or manually edit the resulting `.lrc` file.
* Whisper's word-level timestamps are not equally reliable across languages.
  Japanese often lacks whitespace, so you may prefer the ``spread`` strategy to
  distribute the timing evenly across characters.
"""

from __future__ import annotations

import argparse
import logging
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Literal, Optional, Sequence, Tuple

from difflib import SequenceMatcher


LOGGER = logging.getLogger("whisper_lrc_generator")


@dataclass
class Word:
    """Normalized representation of a word-level timestamp."""

    start: float
    end: float
    text: str


@dataclass
class Segment:
    """Container for an ASR segment."""

    start: float
    end: float
    text: str
    words: Sequence[Word] | None = None


@dataclass
class Alignment:
    """Represents an aligned lyric line."""

    text: str
    start: float
    end: float
    score: float
    segments: Sequence[Segment]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, type=Path, help="Path to the audio file (wav, mp3, etc.).")
    parser.add_argument("--lyrics", required=True, type=Path, help="UTF-8 text file containing the lyrics (one line per row).")
    parser.add_argument("--output", type=Path, help="Destination .lrc file. Defaults to <audio stem>.lrc")
    parser.add_argument("--model", default="medium", help="Model size/name for Whisper or faster-whisper (default: medium).")
    parser.add_argument("--language", default="ja", help="Spoken language in the audio (default: ja).")
    parser.add_argument(
        "--backend",
        choices=("auto", "whisper", "faster-whisper"),
        default="auto",
        help="Choose the ASR backend. 'auto' tries faster-whisper first and falls back to whisper.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Torch device for Whisper (e.g., cuda, cpu). Ignored by faster-whisper unless supported.",
    )
    parser.add_argument(
        "--compute-type",
        default="default",
        help="faster-whisper compute_type (auto, int8, float16, float32, etc.). Only used when the backend is faster-whisper.",
    )
    parser.add_argument(
        "--level",
        choices=("line", "word", "both"),
        default="line",
        help="Whether to emit line-level timestamps, word-level timestamps, or both.",
    )
    parser.add_argument(
        "--word-mode",
        choices=("transcript", "spread"),
        default="spread",
        help=(
            "Word timing strategy: 'transcript' uses Whisper's word timestamps (if available). "
            "'spread' distributes the line duration evenly across the characters of the provided lyric line."
        ),
    )
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=0.55,
        help="Minimum similarity ratio required before moving to the next lyric line (default: 0.55).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print debug information about the alignment decisions.",
    )
    parser.add_argument(
        "--time-offset",
        type=float,
        default=0.0,
        help="Optional offset (in seconds) applied to every timestamp. Negative values shift lyrics earlier.",
    )
    return parser.parse_args(argv)


class BackendLoaderError(RuntimeError):
    """Raised when neither whisper nor faster-whisper are available."""


Backend = Literal["whisper", "faster-whisper"]


@dataclass
class LoadedBackend:
    name: Backend
    model: object


def load_backend(name: str, model_name: str, device: Optional[str], compute_type: str) -> LoadedBackend:
    """Attempt to load the requested ASR backend."""

    def try_faster() -> LoadedBackend:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:  # pragma: no cover - only triggered when dependency missing
            raise BackendLoaderError("faster-whisper is not installed") from exc

        compute = None if compute_type == "default" else compute_type
        model = WhisperModel(model_name, device=device or "auto", compute_type=compute)
        return LoadedBackend(name="faster-whisper", model=model)

    def try_whisper() -> LoadedBackend:
        try:
            import whisper  # type: ignore
        except ImportError as exc:  # pragma: no cover - missing dependency path
            raise BackendLoaderError("whisper is not installed") from exc

        model = whisper.load_model(model_name, device=device)
        return LoadedBackend(name="whisper", model=model)

    if name == "faster-whisper":
        return try_faster()
    if name == "whisper":
        return try_whisper()

    # auto
    for loader in (try_faster, try_whisper):
        try:
            return loader()
        except BackendLoaderError as exc:
            LOGGER.debug("Backend load failed: %s", exc)
            continue
    raise BackendLoaderError("Could not import either faster-whisper or whisper. Install at least one backend.")


def transcribe_audio(backend: LoadedBackend, audio_path: Path, language: str, want_words: bool) -> List[Segment]:
    """Transcribe the audio into normalized segments."""

    if backend.name == "whisper":
        import whisper  # type: ignore

        result = backend.model.transcribe(  # type: ignore[attr-defined]
            str(audio_path),
            language=language,
            word_timestamps=want_words,
            task="transcribe",
            verbose=False,
        )
        segments: List[Segment] = []
        for raw in result["segments"]:
            words: Sequence[Word] | None = None
            if want_words and raw.get("words"):
                words = [Word(start=w["start"], end=w["end"], text=w["word"].strip()) for w in raw["words"]]
            segments.append(
                Segment(
                    start=float(raw["start"]),
                    end=float(raw["end"]),
                    text=str(raw["text"]).strip(),
                    words=words,
                )
            )
        return segments

    # faster-whisper
    segments: List[Segment] = []
    generator, _info = backend.model.transcribe(  # type: ignore[attr-defined]
        str(audio_path),
        language=language,
        word_timestamps=want_words,
    )
    for seg in generator:
        words: Sequence[Word] | None = None
        if want_words and seg.words:
            words = [Word(start=w.start, end=w.end, text=w.word.strip()) for w in seg.words if w.word]
        segments.append(
            Segment(
                start=float(seg.start),
                end=float(seg.end),
                text=str(seg.text).strip(),
                words=words,
            )
        )
    return segments


def load_lyrics(path: Path) -> List[str]:
    lines: List[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.strip()
            if stripped:
                lines.append(stripped)
    if not lines:
        raise ValueError(f"No lyric lines found in {path}")
    return lines


def normalize_for_match(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    cleaned = []
    for ch in normalized:
        category = unicodedata.category(ch)
        if category.startswith("P"):
            continue
        if ch.isspace():
            continue
        cleaned.append(ch.lower())
    return "".join(cleaned)


def best_alignment_range(
    segments: Sequence[Segment],
    lyric_norm: str,
    start_index: int,
    min_ratio: float,
) -> Tuple[int, int, float]:
    """Select segments that best match the lyric line starting at *start_index*."""

    if start_index >= len(segments):
        return start_index, start_index, 0.0

    best_ratio = -1.0
    best_end = start_index + 1
    combined_text = ""
    for end_index in range(start_index, len(segments)):
        combined_text = (combined_text + " " + segments[end_index].text).strip()
        candidate_norm = normalize_for_match(combined_text)
        if not candidate_norm and lyric_norm:
            continue
        ratio = SequenceMatcher(None, candidate_norm, lyric_norm).ratio() if lyric_norm else 0.0
        if ratio > best_ratio:
            best_ratio = ratio
            best_end = end_index + 1
        # Early exit once we are above the threshold and length is reasonable.
        if ratio >= max(min_ratio, 0.88) and len(candidate_norm) >= len(lyric_norm) * 0.6:
            break
    return start_index, best_end, best_ratio


def align_segments_to_lyrics(
    segments: Sequence[Segment],
    lyrics: Sequence[str],
    min_ratio: float,
) -> List[Alignment]:
    alignments: List[Alignment] = []
    seg_index = 0
    last_end = segments[-1].end if segments else 0.0

    for line in lyrics:
        lyric_norm = normalize_for_match(line)
        if seg_index >= len(segments):
            alignments.append(Alignment(text=line, start=last_end, end=last_end, score=0.0, segments=()))
            continue
        start_index, end_index, ratio = best_alignment_range(segments, lyric_norm, seg_index, min_ratio)
        chosen = segments[start_index:end_index] or (segments[seg_index : seg_index + 1])
        start_time = chosen[0].start
        end_time = chosen[-1].end
        alignments.append(
            Alignment(
                text=line,
                start=start_time,
                end=end_time,
                score=ratio,
                segments=chosen,
            )
        )
        seg_index = max(end_index, seg_index + 1)
    return alignments


def sec_to_timestamp(value: float) -> str:
    if value < 0:
        value = 0.0
    minutes = int(value // 60)
    seconds = value % 60
    return f"{minutes:02d}:{seconds:05.2f}"


def apply_offset(value: float, offset: float) -> float:
    return max(0.0, value + offset)


def build_line_level_entries(alignments: Iterable[Alignment], offset: float) -> List[str]:
    lines: List[str] = []
    for alignment in alignments:
        timestamp = sec_to_timestamp(apply_offset(alignment.start, offset))
        lines.append(f"[{timestamp}]{alignment.text}")
    return lines


def build_word_level_entries(
    alignments: Iterable[Alignment],
    offset: float,
    mode: Literal["transcript", "spread"],
) -> List[str]:
    entries: List[str] = []
    for alignment in alignments:
        base_timestamp = sec_to_timestamp(apply_offset(alignment.start, offset))
        if mode == "transcript":
            words = []
            for segment in alignment.segments:
                if not segment.words:
                    continue
                words.extend(segment.words)
            if not words:
                # Fallback to spread if transcripts lack word-level detail.
                entries.append(_spread_line(alignment, base_timestamp, offset))
                continue
            word_tokens = []
            for word in words:
                stamp = sec_to_timestamp(apply_offset(word.start, offset))
                cleaned = word.text.strip()
                if not cleaned:
                    continue
                word_tokens.append(f"<{stamp}>{cleaned}")
            if word_tokens:
                entries.append(f"[{base_timestamp}]{''.join(word_tokens)}")
            else:
                entries.append(_spread_line(alignment, base_timestamp, offset))
        else:
            entries.append(_spread_line(alignment, base_timestamp, offset))
    return entries


def _spread_line(alignment: Alignment, base_timestamp: str, offset: float) -> str:
    text = alignment.text
    characters = [char for char in text if not char.isspace()]
    if not characters:
        return f"[{base_timestamp}]{text}"
    duration = max(alignment.end - alignment.start, 0.4)
    step = duration / len(characters)
    pieces = []
    for index, char in enumerate(characters):
        ts = sec_to_timestamp(apply_offset(alignment.start + step * index, offset))
        pieces.append(f"<{ts}>{char}")
    return f"[{base_timestamp}]{''.join(pieces)}"


def write_lrc(path: Path, lines: Sequence[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    try:
        backend = load_backend(args.backend, args.model, args.device, args.compute_type)
    except BackendLoaderError as exc:
        LOGGER.error("%s", exc)
        return 1

    LOGGER.info("Using backend: %s", backend.name)

    try:
        lyrics = load_lyrics(args.lyrics)
    except Exception as exc:  # pragma: no cover - runtime protection only
        LOGGER.error("Failed to load lyrics: %s", exc)
        return 1

    want_words = args.level in {"word", "both"}
    try:
        segments = transcribe_audio(backend, args.audio, args.language, want_words)
    except Exception as exc:  # pragma: no cover - runtime protection only
        LOGGER.error("Transcription failed: %s", exc)
        return 1

    if not segments:
        LOGGER.error("No segments were produced by the ASR model.")
        return 1

    LOGGER.info("Transcribed %d segments", len(segments))

    alignments = align_segments_to_lyrics(segments, lyrics, args.min_ratio)
    if args.verbose:
        for alignment in alignments:
            LOGGER.debug(
                "Lyric: %s | start=%.2f | end=%.2f | ratio=%.2f | segments=%d",
                alignment.text,
                alignment.start,
                alignment.end,
                alignment.score,
                len(alignment.segments),
            )

    output = args.output
    if not output:
        output = args.audio.with_suffix(".lrc")

    lines: List[str] = []
    if args.level in {"line", "both"}:
        lines.extend(build_line_level_entries(alignments, args.time_offset))
    if args.level in {"word", "both"}:
        lines.extend(build_word_level_entries(alignments, args.time_offset, args.word_mode))

    write_lrc(output, lines)
    LOGGER.info("Wrote %d LRC entries to %s", len(lines), output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
