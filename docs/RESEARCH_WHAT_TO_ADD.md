# What You Can Add to Video.AI: FramePack Motion

*Focus: Local, 6GB VRAM image-to-video motion generation*

---

## 🔴 FramePack (Image-to-Video Motion)

**What:** Next-frame prediction that turns static Stable Diffusion images into 30fps video clips.

**Why it matters:** 
- Adds natural movement (drifting fog, flickering flames, character motion) from a single generated frame.
- **Zero VRAM leaks**: Explicitly designed for 6GB consumer GPUs using O(1) memory scheduling.
- **Ready in codebase**: Code already exists in the project at [framepack_i2v.py](file:///c:/Video.AI/video/image_gen/framepack_i2v.py).

**How to activate:**
1. Install dependencies:
   ```powershell
   pip install framepack
   ```
2. Download model weights:
   ```powershell
   huggingface-cli download lllyasviel/FramePack --local-dir hf_cache\framepack
   ```
