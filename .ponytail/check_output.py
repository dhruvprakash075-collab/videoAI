from PIL import Image
import shutil, os
img = Image.open("external/ComfyUI/studio_outputs/frames/fast_01.png")
print("Size:", img.size)
print("Mode:", img.mode)
print("Format:", img.format)
print("Bytes:", os.path.getsize("external/ComfyUI/studio_outputs/frames/fast_01.png"))
shutil.copy2("external/ComfyUI/studio_outputs/frames/fast_01.png", "fast_output.png")
print("Copied to fast_output.png")
