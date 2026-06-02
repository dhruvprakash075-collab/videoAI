"""isolated_tests.py — exercise parts skipped by --dry-run (fixed APIs)."""
import os

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
os.environ["CREWAI_TELEMETRY_OPTOUT"] = "true"
os.environ["TORCHDYNAMO_SUPPRESS_ERRORS"] = "1"

import json
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(r"C:\Video.AI")
sys.path.insert(0, str(ROOT))

from utils.compatibility import apply_all_patches

apply_all_patches()

from config import load_config

cfg = load_config()
print("=" * 60)
print("ISOLATED PIPELINE TEST (no --dry-run paths)")
print("=" * 60)
print(f"tts.engine = {cfg['tts']['engine']}")
print(f"tts.lang   = {cfg['tts']['lang']}")
print(f"sd model   = {cfg['image_gen']['sd_model']}")
print()

OUT = ROOT / "studio_outputs" / "isolated_tests"
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

results = []
def record(name, status, detail=""):
    results.append((name, status, detail))
    print(f"[{status:6s}] {name}: {detail}\n")


# ── TEST 1: TTS dispatcher ─────────────────────────────────
print("─" * 60)
print("TEST 1: TTS dispatcher (real TTS, f5/omnivoice/edge cascade)")
print("─" * 60)
try:
    seg1 = json.loads((ROOT / "studio_checkpoints" / "smoke_test_190722_seg01.json").read_text(encoding="utf-8"))
    script_en = seg1["script"]["data"]
    print(f"  script: {len(script_en)} chars")

    from audio.audio_proxy import tts_generate
    t1_out = OUT / "tts"
    t1_out.mkdir()
    t1_start = time.time()
    result_path = tts_generate(
        text=script_en,
        lang=cfg["tts"]["lang"],
        slow=cfg["tts"].get("slow", False),
        output_dir=t1_out,
        speed=1.0,
    )
    t1_elapsed = time.time() - t1_start
    print(f"  tts_generate returned: {result_path}")
    print(f"  elapsed: {t1_elapsed:.1f}s")
    if result_path and Path(result_path).exists():
        size = Path(result_path).stat().st_size
        record("TTS dispatcher", "PASS", f"{Path(result_path).name} ({size:,} bytes, {t1_elapsed:.0f}s)")
    else:
        record("TTS dispatcher", "FAIL", f"returned {result_path!r}")
except Exception as e:
    record("TTS dispatcher", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ── TEST 2: MP4 segment assembly ───────────────────────────
print("\n" + "─" * 60)
print("TEST 2: MP4 segment assembly (SD images + audio → MP4)")
print("─" * 60)
try:
    refs = sorted((ROOT / "studio_outputs" / "smoke_test_190722" / "studio_refs" / "zara").glob("*.png"))
    print(f"  {len(refs)} SD images available")
    if not refs:
        record("MP4 assembly", "SKIP", "no images")
    else:
        from video.renderer.assembler import create_segment_mp4
        tts_path = next(iter((OUT / "tts").glob("*.wav")), None) if (OUT / "tts").exists() else None
        # If no TTS, use silent audio
        if tts_path is None:
            silent_wav = OUT / "silent_seg1.wav"
            subprocess_ok = False
            try:
                import subprocess
                subprocess.run([
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", "12", str(silent_wav)
                ], capture_output=True, check=True, timeout=30)
                tts_path = silent_wav
                subprocess_ok = True
            except Exception as e:
                print(f"  ffmpeg silent audio failed: {e}")

        t2_out_dir = OUT / "seg_mp4"
        t2_out_dir.mkdir()
        # Build images list as Paths in order
        images_list: list[Path] = refs[:5]
        t2_start = time.time()
        result_mp4 = create_segment_mp4(
            seg_num=1,
            audio=tts_path if tts_path else Path("character_voices" / "narration_voice.wav"),
            script=script_en[:500],
            out_dir=t2_out_dir,
            config=cfg,
            images=images_list,
        )
        t2_elapsed = time.time() - t2_start
        print(f"  create_segment_mp4 returned: {result_mp4}")
        print(f"  elapsed: {t2_elapsed:.1f}s")
        if result_mp4 and Path(result_mp4).exists():
            size = Path(result_mp4).stat().st_size
            record("MP4 assembly", "PASS", f"{Path(result_mp4).name} ({size:,} bytes, {t2_elapsed:.0f}s)")
        else:
            # check directory for any mp4
            produced = list(t2_out_dir.glob("*.mp4"))
            if produced:
                size = produced[0].stat().st_size
                record("MP4 assembly", "PASS", f"{produced[0].name} ({size:,} bytes, {t2_elapsed:.0f}s)")
            else:
                record("MP4 assembly", "FAIL", f"no mp4 produced (returned {result_mp4!r})")
except Exception as e:
    record("MP4 assembly", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ── TEST 3: Real LoRA training ─────────────────────────────
print("\n" + "─" * 60)
print("TEST 3: Real LoRA training (mock=False, real weights)")
print("─" * 60)
try:
    from train_lora import train_protagonist_lora
    refs = sorted((ROOT / "studio_outputs" / "smoke_test_190722" / "studio_refs" / "zara").glob("*.png"))
    if len(refs) < 3:
        record("LoRA real training", "SKIP", f"need 3+ images, have {len(refs)}")
    else:
        image_paths = [Path(p) for p in refs[:5]]
        lora_target = ROOT / "studio_checkpoints" / "isolated_test_char_xxxx_lora.safetensors"
        if lora_target.exists():
            lora_target.unlink()
        t3_start = time.time()
        out_path = train_protagonist_lora(
            image_paths=image_paths,
            char_name="isolated_test_char",
            output_dir=ROOT / "studio_checkpoints",
            char_description="young woman, purple hair, green eyes, leather jacket, determined",
            mock=False,
        )
        t3_elapsed = time.time() - t3_start
        print(f"  result: {out_path}")
        print(f"  elapsed: {t3_elapsed:.0f}s")
        if out_path and out_path.exists() and out_path.stat().st_size > 5_000_000:
            size = out_path.stat().st_size
            record("LoRA real training", "PASS", f"{out_path.name} ({size/1e6:.1f} MB, {t3_elapsed:.0f}s)")
        else:
            sz = out_path.stat().st_size if (out_path and out_path.exists()) else 0
            record("LoRA real training", "FAIL", f"output too small: {sz} bytes (expected >5MB for real)")
except Exception as e:
    record("LoRA real training", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ── TEST 4: SD multi-load cycle ────────────────────────────
print("\n" + "─" * 60)
print("TEST 4: SD multi-load cycle (2 batches, simulate per-segment)")
print("─" * 60)
try:
    from video.image_gen.image_gen import generate_images, get_oom_report
    prompts_a = [
        "a lone lighthouse on a cliff at midnight, volumetric fog, cinematic",
        "an old wooden door glowing with blue light, mossy stone arch, dark forest",
    ]
    prompts_b = [
        "an ancient weathered book on a stone table, candlelight, mysterious",
        "a hooded figure standing in rain, neon reflections, cyberpunk city",
    ]
    sd_a = OUT / "sd_a"; sd_a.mkdir()
    sd_b = OUT / "sd_b"; sd_b.mkdir()
    t4a_start = time.time()
    paths_a = generate_images(prompts_a, output_dir=sd_a, config=cfg, lora_paths=None, char_presence=None)
    t4a = time.time() - t4a_start
    print(f"  batch A: {len(paths_a)} images in {t4a:.1f}s")
    t4b_start = time.time()
    paths_b = generate_images(prompts_b, output_dir=sd_b, config=cfg, lora_paths=None, char_presence=None)
    t4b = time.time() - t4b_start
    print(f"  batch B: {len(paths_b)} images in {t4b:.1f}s")
    oom = get_oom_report()
    print(f"  OOM events: {len(oom)}")
    if len(paths_a) == 2 and len(paths_b) == 2:
        record("SD multi-load", "PASS", f"A:{len(paths_a)}@{t4a:.0f}s, B:{len(paths_b)}@{t4b:.0f}s, OOM={len(oom)}")
    else:
        record("SD multi-load", "PARTIAL", f"A:{len(paths_a)}/2, B:{len(paths_b)}/2, OOM={len(oom)}")
except Exception as e:
    record("SD multi-load", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ── TEST 5: Final concat ───────────────────────────────────
print("\n" + "─" * 60)
print("TEST 5: Concat segments (multi MP4 → final)")
print("─" * 60)
try:
    from video.renderer.assembler import concatenate_segments
    seg_dir = OUT / "seg_mp4"
    segs = sorted(seg_dir.glob("*.mp4"))
    if not segs:
        record("Final concat", "SKIP", "no seg mp4 from test 2")
    else:
        # duplicate to have 3
        while len(segs) < 3:
            shutil.copy(segs[0], seg_dir / f"dup_{len(segs)}.mp4")
            segs = sorted(seg_dir.glob("*.mp4"))
        segs_paths = [Path(s) for s in segs[:3]]
        final_out = OUT / "final_concat.mp4"
        t5_start = time.time()
        result = concatenate_segments(
            segments=segs_paths,
            output=final_out,
        )
        t5_elapsed = time.time() - t5_start
        print(f"  elapsed: {t5_elapsed:.1f}s")
        if final_out.exists() and final_out.stat().st_size > 0:
            size = final_out.stat().st_size
            record("Final concat", "PASS", f"{final_out.name} ({size:,} bytes, {t5_elapsed:.0f}s)")
        else:
            record("Final concat", "FAIL", "no output")
except Exception as e:
    record("Final concat", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ── TEST 6: master_audio (light mastering) ─────────────────
print("\n" + "─" * 60)
print("TEST 6: master_audio (premium voice processing / ffmpeg fallback)")
print("─" * 60)
try:
    tts_audio = next(iter((OUT / "tts").glob("*.wav")), None) if (OUT / "tts").exists() else None
    if tts_audio is None:
        record("master_audio", "SKIP", "no TTS audio from test 1")
    else:
        from audio.audio_fx import master_audio
        t6_start = time.time()
        out_audio = master_audio(
            audio_path=tts_audio,
            output_dir=OUT / "mastered",
            segment_idx=1,
        )
        t6_elapsed = time.time() - t6_start
        if out_audio.exists():
            size = out_audio.stat().st_size
            record("master_audio", "PASS", f"{out_audio.name} ({size:,} bytes, {t6_elapsed:.0f}s)")
        else:
            record("master_audio", "FAIL", "no output")
except Exception as e:
    record("master_audio", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ── TEST 7: Whisper word-timestamp ─────────────────────────
print("\n" + "─" * 60)
print("TEST 7: Whisper word-timestamp transcription")
print("─" * 60)
try:
    tts_audio = next(iter((OUT / "tts").glob("*.wav")), None) if (OUT / "tts").exists() else None
    if tts_audio is None:
        record("Whisper word-TS", "SKIP", "no TTS audio from test 1")
    else:
        from video.renderer.assembler import _get_whisper_model
        t7_start = time.time()
        model_obj = _get_whisper_model(cfg["performance"].get("whisper_model", "tiny"))
        segments_iter, info = model_obj.transcribe(str(tts_audio), word_timestamps=True)
        words = []
        for seg in segments_iter:
            if seg.words:
                for w in seg.words:
                    if w.word.strip():
                        words.append((w.word, w.start, w.end))
        t7_elapsed = time.time() - t7_start
        print(f"  {len(words)} words, audio={info.duration:.1f}s, time={t7_elapsed:.0f}s")
        if len(words) > 0:
            record("Whisper word-TS", "PASS", f"{len(words)} words in {t7_elapsed:.0f}s")
        else:
            record("Whisper word-TS", "FAIL", "0 words")
except Exception as e:
    record("Whisper word-TS", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()


# ── SUMMARY ────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
for name, status, detail in results:
    print(f"  [{status:6s}] {name:25s}  {detail}")
print(f"\nTotal: {len(results)} tests")
p = sum(1 for _, s, _ in results if s == "PASS")
f = sum(1 for _, s, _ in results if s == "FAIL")
print(f"  PASS : {p}")
print(f"  FAIL : {f}")
print(f"  SKIP : {sum(1 for _, s, _ in results if s == 'SKIP')}")
print(f"  PART : {sum(1 for _, s, _ in results if s == 'PARTIAL')}")
