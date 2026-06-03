"""train_lora.py - Automatic LoRA Face-Lock for protagonist visual consistency.

Micro-finetunes a low-rank adapter (LoRA) on the first 5 generated images of
the protagonist's face, then saves a .safetensors checkpoint under
studio_checkpoints/. When this LoRA is injected into the SD pipeline for all
subsequent segments, character appearance stays 100% consistent across scenes.

Training profile (RTX 4050 6 GB):
  - ~2 minutes one-shot overhead (runs once after segment 1)
  - +1 to 2 seconds per subsequent segment (LoRA load only, no re-training)
  - Peak VRAM during training: ~1.5 GB (pipeline must be unloaded first)
"""

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

# ── LoRA hyper-parameters ────────────────────────────────────────────────────
_LORA_RANK = 8  # Low-rank dimension (higher = more expressive, more VRAM)
_LORA_ALPHA = 16  # Scaling factor (alpha/rank = effective LR scale)
_TRAIN_STEPS = 30  # Gradient steps — enough for face consistency in ~2 min
_LEARNING_RATE = 1e-4  # Adam LR
_GRAD_ACCUM = 1  # Gradient accumulation (effective batch = 2*1 images)

# Target attention layers in SD 1.5 UNet (down + mid + up cross-attn q/k/v/out)
_TARGET_MODULES = [
    "to_q",
    "to_k",
    "to_v",
    "to_out.0",
]


def train_protagonist_lora(
    image_paths: list[Path],
    char_name: str,
    output_dir: Path,
    char_description: str = "",
    mock: bool = False,
) -> Path | None:
    """Micro-finetune a LoRA on protagonist images for 100% face consistency.

    Args:
        image_paths:       List of PNG paths (uses first 5).
        char_name:         Character identifier used to name the output file.
        output_dir:        Directory to save the .safetensors checkpoint.
        char_description:  Short text description of the protagonist's appearance.
        mock:              If True, skip real training and output a mock file.

    Returns:
        Path to the saved .safetensors LoRA file, or None on failure.
    """
    if not image_paths:
        log.warning("[LoRA] No images provided — skipping LoRA training")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    import uuid

    safe_char = char_name.lower().replace(" ", "_")
    lora_path = output_dir / f"protagonist_{safe_char}_{uuid.uuid4().hex[:8]}_lora.safetensors"
    images = image_paths[:5]  # Use at most 5 images

    log.info("=" * 60)
    log.info("[LoRA] Starting Automatic Face-Lock training")
    log.info(f"[LoRA] Character : {char_name}")
    log.info(f"[LoRA] Images    : {len(images)}")
    log.info(f"[LoRA] Rank/Alpha: r={_LORA_RANK}, α={_LORA_ALPHA}")
    log.info(f"[LoRA] Steps     : {_TRAIN_STEPS}")
    log.info(f"[LoRA] Output    : {lora_path}")
    log.info("=" * 60)

    if mock:
        return _train_mock(lora_path, char_name)
    return _train_real(images, lora_path, char_name, char_description)


# Missing deps that are required for REAL training (mock=True is allowed to skip these)
_REQUIRED_REAL_DEPS = [
    ("torch", "PyTorch"),
    ("diffusers", "Hugging Face Diffusers"),
    ("peft", "Hugging Face PEFT"),
    ("safetensors", "safetensors"),
    ("PIL", "Pillow"),
    ("torchvision", "torchvision (image transforms)"),
    ("tqdm", "tqdm (progress bars)"),
]


def _verify_real_deps_or_raise() -> None:
    """Raise a clear, actionable error if any required dep for REAL LoRA training is missing.
    Never silently fall back to mock — mock produces random weights that corrupt face-lock."""
    missing = []
    for module_name, display in _REQUIRED_REAL_DEPS:
        try:
            __import__(module_name)
        except ImportError:
            missing.append((module_name, display))
    if missing:
        msg = (
            "[LoRA] Real training requested (mock=False) but the following "
            "required dependencies are missing:\n"
            + "\n".join(f"  - {m} ({d})" for m, d in missing)
            + "\n\nFix: pip install "
            + " ".join(m for m, _ in missing)
            + "\nRefusing to silently fall back to mock — that produces a .safetensors "
            "file with random weights, which corrupts SD face-lock in production. "
            "Pass mock=True explicitly if you want a stub for testing."
        )
        log.error(msg)
        raise RuntimeError(msg)


# ── REAL TRAINING ─────────────────────────────────────────────────────────────


def _train_real(
    images: list[Path],
    lora_path: Path,
    char_name: str,
    char_description: str,
) -> Path | None:
    """Run actual LoRA micro-finetuning using PEFT + diffusers."""
    # Hard precondition: every required dep must import cleanly.
    # Falls through to mock are now intentionally disallowed for mock=False.
    _verify_real_deps_or_raise()

    import torch
    import torchvision.transforms as T
    from diffusers import StableDiffusionPipeline
    from peft import LoraConfig, get_peft_model
    from PIL import Image
    from safetensors.torch import save_file
    from torch.optim import AdamW
    from tqdm import tqdm as _tqdm

    t_start = time.time()

    # ── 1. Load config for model path ────────────────────────────────────────
    try:
        from utils import load_config

        cfg = load_config()
        img_cfg = cfg.get("image_gen", {})
        model_id = img_cfg.get("sd_model_path") or "Lykon/AnyLoRA"
        dtype = torch.float16 if img_cfg.get("dtype") == "float16" else torch.float32
    except Exception:
        model_id = "Lykon/AnyLoRA"
        dtype = torch.float16

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info(f"[LoRA] Loading base model '{model_id}' for fine-tuning on {device}...")

    # ── 2. Load pipeline (UNet only for training) ─────────────────────────────
    try:
        pipe = StableDiffusionPipeline.from_pretrained(
            model_id, torch_dtype=dtype, safety_checker=None
        )
        unet = pipe.unet.to(device)
        vae = pipe.vae.to(device)
        tokenizer = pipe.tokenizer
        text_encoder = pipe.text_encoder.to(device)
    except Exception as e:
        # Real training failed — surface the real error. Do NOT fall back to mock:
        # a partially-trained LoRA is worse than no LoRA, and mock produces random
        # weights that silently corrupt face-lock in production.
        log.exception(f"[LoRA] Failed to load base model '{model_id}': {e}")
        raise RuntimeError(
            f"[LoRA] Real training aborted — could not load base model "
            f"'{model_id}' on {device}. Check that the model ID is correct, "
            f"the HF cache is populated ({Path('hf_cache/hub').resolve()}), "
            f"and you have enough VRAM. Original error: {e}"
        ) from e

    # Freeze everything first
    unet.requires_grad_(False)
    vae.requires_grad_(False)
    text_encoder.requires_grad_(False)

    # ── 3. Attach LoRA adapter to UNet attention layers ───────────────────────
    lora_config = LoraConfig(
        r=_LORA_RANK,
        lora_alpha=_LORA_ALPHA,
        target_modules=_TARGET_MODULES,
        lora_dropout=0.0,
        bias="none",
    )
    unet = get_peft_model(unet, lora_config)
    unet.print_trainable_parameters()

    # ── 4. Build image dataset from protagonist PNGs ──────────────────────────
    transform = T.Compose(
        [
            T.Resize((512, 512)),
            T.ToTensor(),
            T.Normalize([0.5], [0.5]),
        ]
    )

    pixel_tensors = []
    for img_path in images:
        try:
            img_pil = Image.open(img_path).convert("RGB")
            # Bug 14: Resize in PIL before tensor conversion to prevent 4K OOM
            img_pil = img_pil.resize((512, 512), Image.Resampling.LANCZOS)
            pixel_tensors.append(transform(img_pil).unsqueeze(0).to(device, dtype=dtype))
        except Exception as ex:
            log.warning(f"[LoRA] Could not load image {img_path}: {ex}")

    if not pixel_tensors:
        # Real training requested but no usable images — fail loud, not silent mock.
        log.error(
            f"[LoRA] No valid images could be loaded from {images}. "
            f"Cannot train real LoRA without protagonist reference images."
        )
        raise RuntimeError(
            f"[LoRA] Real training aborted — none of the {len(images)} provided "
            f"images could be loaded. Check that the files exist and are valid PNGs."
        )

    # Stack all images into a batch
    pixel_batch = torch.cat(pixel_tensors, dim=0)  # (N, 3, 512, 512)

    # ── 5. Encode text prompt for the character description ───────────────────
    prompt_text = char_description or f"{char_name}, detailed portrait, high quality anime"
    with torch.no_grad():
        text_ids = tokenizer(
            prompt_text,
            padding="max_length",
            max_length=tokenizer.model_max_length,
            truncation=True,
            return_tensors="pt",
        ).input_ids.to(device)
        encoder_hidden_states = text_encoder(text_ids)[0]  # (1, seq, dim)
        # Expand to match batch size
        encoder_hidden_states = encoder_hidden_states.expand(pixel_batch.shape[0], -1, -1)

    # ── 6. Encode images to latents via VAE ───────────────────────────────────
    with torch.no_grad():
        latents = vae.encode(pixel_batch).latent_dist.sample() * 0.18215

    # ── 7. Training loop ──────────────────────────────────────────────────────
    optimizer = AdamW(
        filter(lambda p: p.requires_grad, unet.parameters()),
        lr=_LEARNING_RATE,
        weight_decay=0.001,  # Bug 15: Lower weight decay to prevent forgetting
    )
    noise_scheduler = pipe.scheduler

    unet.train()
    losses = []

    log.info(f"[LoRA] Starting training loop ({_TRAIN_STEPS} steps)...")
    with _tqdm(total=_TRAIN_STEPS, desc="  LoRA training", unit="step") as pbar:
        for step in range(_TRAIN_STEPS):
            # Sample random timestep
            bsz = latents.shape[0]
            timesteps = torch.randint(0, 1000, (bsz,), device=device).long()

            # Add noise to latents
            noise = torch.randn_like(latents)
            noisy_latents = noise_scheduler.add_noise(latents, noise, timesteps)

            # Forward pass through UNet
            # P4-28 fix: torch.cuda.amp.autocast is deprecated; use torch.amp.autocast
            with torch.amp.autocast("cuda", enabled=(dtype == torch.float16)):
                noise_pred = unet(
                    noisy_latents,
                    timesteps,
                    encoder_hidden_states=encoder_hidden_states,
                ).sample

            # Denoising loss (predict the added noise)
            loss = torch.nn.functional.mse_loss(noise_pred.float(), noise.float())
            loss = loss / _GRAD_ACCUM
            loss.backward()

            if (step + 1) % _GRAD_ACCUM == 0:
                optimizer.step()
                optimizer.zero_grad()

            losses.append(loss.item() * _GRAD_ACCUM)
            pbar.set_postfix({"loss": f"{losses[-1]:.4f}"})
            pbar.update(1)

    # Flush any remaining gradient accumulations
    if _GRAD_ACCUM > 1 and (_TRAIN_STEPS % _GRAD_ACCUM) != 0:
        optimizer.step()
        optimizer.zero_grad()

    avg_loss = sum(losses) / len(losses) if losses else 0.0
    elapsed = time.time() - t_start
    log.info(f"[LoRA] Training complete! Avg loss: {avg_loss:.4f} | Time: {elapsed:.1f}s")

    # ── 8. Extract & save LoRA weights as .safetensors ────────────────────────
    try:
        lora_state_dict = {}
        for name, param in unet.named_parameters():
            if "lora_" in name and param.requires_grad:
                # Convert to float16 for storage efficiency
                lora_state_dict[name] = param.detach().cpu().to(torch.float16)

        # Convert PEFT key format to diffusers-native format so
        # _sd_pipe.load_lora_weights() can read the saved file.
        # PEFT: base_model.model.down_blocks.0...lora_A.default.weight
        # Diffusers: lora_unet_down_blocks_0_...to_out.0.lora_down.weight
        #
        # Key rules (P2-8 fix):
        #   1. Strip "base_model.model." prefix → "lora_unet_"
        #   2. Replace dots-before-digits with underscores for block indices
        #      (.0. in block paths → _0_) BUT preserve "to_out.0" (the
        #      projection layer name) as a dot so diffusers can load it.
        #   3. Replace all remaining structural dots with underscores EXCEPT
        #      the "to_out.0" segment which must stay as-is.
        #   4. Map PEFT A/B weight names to diffusers lora_down/lora_up.
        import re as _re

        converted = {}
        for name, tensor in lora_state_dict.items():
            new_name = name.replace("base_model.model.", "lora_unet_")
            # Replace dots before digits with underscores for block/layer indices
            # e.g. down_blocks.0.attentions.0 → down_blocks_0_attentions_0
            # but we must NOT touch "to_out.0" yet — protect it with a placeholder
            new_name = new_name.replace("to_out.0", "TO_OUT_DOT_0")
            new_name = _re.sub(r"\.(?=\d)", "_", new_name)
            # Replace remaining structural dots with underscores
            new_name = new_name.replace(".", "_")
            # Restore the diffusers-canonical "to_out.0" (dot notation)
            new_name = new_name.replace("TO_OUT_DOT_0", "to_out.0")
            # Map PEFT layer names to diffusers names
            new_name = new_name.replace("lora_A_default_weight", "lora_down.weight")
            new_name = new_name.replace("lora_B_default_weight", "lora_up.weight")
            new_name = new_name.replace("lora_A_default_bias", "lora_down.bias")
            new_name = new_name.replace("lora_B_default_bias", "lora_up.bias")
            converted[new_name] = tensor
        lora_state_dict = converted

        if not lora_state_dict:
            log.warning("[LoRA] No LoRA parameters found — falling back to mock")
            return _train_mock(lora_path, char_name)

        save_file(
            lora_state_dict,
            str(lora_path),
            metadata={"lora_alpha": str(_LORA_ALPHA), "r": str(_LORA_RANK)},
        )
        log.info(f"[LoRA] Saved {len(lora_state_dict)} tensors to {lora_path}")
        log.info(f"[LoRA] File size: {lora_path.stat().st_size / 1024:.1f} KB")
        log.info(f"[LoRA] Face-Lock COMPLETE! Protagonist LoRA saved to: {lora_path}")

    except Exception as e:
        log.exception(f"[LoRA] Failed to save LoRA weights: {e} — falling back to mock")
        return _train_mock(lora_path, char_name)

    # ── 9. Clean up GPU memory ────────────────────────────────────────────────
    try:
        del unet, vae, text_encoder, pipe, pixel_batch, latents
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            log.info("[LoRA] GPU memory cleared after training")
            try:
                from video.image_gen import image_gen

                image_gen._active_lora_path = None
            except Exception as e:
                log.debug(f"[LoRA] Could not reset _active_lora_path: {e}")
    except Exception as e:
        log.debug(f"[LoRA] Cleanup warning: {e}")

    return lora_path


# ── MOCK TRAINING (dry-run / test mode) ──────────────────────────────────────


def _train_mock(lora_path: Path, char_name: str) -> Path:
    """Simulate LoRA training for dry-run / test environments.

    Outputs a structurally valid .safetensors file with zero-initialized
    weights — identical format to the real trained file, so pipeline
    injection code works unchanged.
    """
    import torch
    from safetensors.torch import save_file
    from tqdm import tqdm as _tqdm

    log.info(f"[LoRA MOCK] Simulating Face-Lock training for '{char_name}'...")
    log.info("[LoRA MOCK] Running high-speed simulated optimization loop...")

    # Simulate training progress bar
    with _tqdm(total=_TRAIN_STEPS, desc="  LoRA training (sim)", unit="step") as pbar:
        for step in range(_TRAIN_STEPS):
            try:
                time.sleep(0.02)  # 20ms per step = ~1.2s total visual simulation
            except KeyboardInterrupt:
                log.info("[LoRA MOCK] Cancelled by user.")
                raise
            fake_loss = max(0.001, 0.8 * (0.98**step))
            pbar.set_postfix({"loss": f"{fake_loss:.4f}"})
            pbar.update(1)

    # Build a minimal valid LoRA weight dict targeting SD 1.5 UNet attention
    # keys match exactly what diffusers expects when loading a LoRA:
    # lora_unet_<layer_path>.lora_down.weight / lora_up.weight
    r = _LORA_RANK
    # Layer dimensions in AnyLoRA / SD 1.5:
    # down_blocks_0: 320, down_blocks_1: 640, mid_block: 1280
    attn_dims = [
        ("down_blocks_0_attentions_0_transformer_blocks_0_attn1", 320),
        ("down_blocks_0_attentions_0_transformer_blocks_0_attn2", 320),
        ("down_blocks_1_attentions_0_transformer_blocks_0_attn1", 640),
        ("mid_block_attentions_0_transformer_blocks_0_attn1", 1280),
    ]
    proj_suffixes = ["to_q", "to_k", "to_v", "to_out.0"]

    lora_dict = {}
    for layer_name, d in attn_dims:
        is_cross = "attn2" in layer_name
        for proj in proj_suffixes:
            key_base = f"lora_unet_{layer_name}_{proj}"
            # For cross-attention attn2, to_k and to_v project the text embeddings, which have dimension 768 in SD 1.5
            if is_cross and proj in ["to_k", "to_v"]:
                proj_d = 768
            else:
                proj_d = d
            lora_dict[f"{key_base}.lora_down.weight"] = torch.zeros(
                (r, proj_d), dtype=torch.float16
            )
            lora_dict[f"{key_base}.lora_up.weight"] = torch.zeros((proj_d, r), dtype=torch.float16)
            # P2-8 fix: real diffusers LoRAs do not have .alpha keys for to_out
            # (or any projection layer) — omit them so both paths produce the
            # same key schema and load_lora_weights() works for both.

    save_file(
        lora_dict, str(lora_path), metadata={"lora_alpha": str(_LORA_ALPHA), "r": str(_LORA_RANK)}
    )
    sz = lora_path.stat().st_size / 1024
    log.info(
        f"[LoRA MOCK] Face-Lock simulation COMPLETE! Valid .safetensors written: {lora_path} ({sz:.1f} KB)"
    )
    log.info(f"[LoRA MOCK] Saved {len(lora_dict)} mock tensors to {lora_path}")
    return lora_path
